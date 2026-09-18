#!/usr/bin/env python3
"""Publish a static site into a lore vault's ``sites/`` tree, or a camp
workspace's.

This is the deterministic half of the ``outpost:publish-site`` skill: given a
source directory, a slug, and an already-resolved target, it validates the
payload, stages it into a temp directory inside the target's ``sites/``, and
atomically renames it into ``<target>/sites/<slug>/``. Target *resolution* —
the judgment call of which vault or workspace a publish lands in — is the
skill's job, not this script's: exactly one of ``--vault-path`` or
``--workspace-path`` is required.

**Vault target** (``--vault-path``, optionally ``--vault``): a vault path is
required to be an existing direct child of the vaults root, which makes its
basename the vault's configured name, so a disagreeing ``--vault`` means one of
the two is wrong and the publish stops before writing anything. After a
successful publish this target runs ``lore sync`` — see below.

**Workspace target** (``--workspace-path``): the resolved path must be
``<campStateRoot>/<group>/worktrees/<slug>``. The site is addressed as
``/<group>-<slug>/<site-slug>/``, runs no ``lore sync`` (there is nothing to
share — a workspace syncs nowhere and disappears with the workspace), and
takes camp's own per-slug ``fcntl.flock`` lock around the write so a publish
can never race a `camp remove` teardown into a half-swapped or resurrected
``sites/`` tree. Its success output names the workspace the link dies with and
states the link is local to this machine — sharing happens by copying printed
terminal text, not from a cockpit badge, so a workspace link must never look
like a vault one.

Publish is atomic, and so is a replace: staging happens off to the side, and
an existing site is renamed aside before the staged tree is renamed into its
place, so the served directory is only ever the whole old site or the whole
new one — never a partial or missing one, at any point in the sequence or
after any failure in it. Updating an existing site requires ``--overwrite``
and replaces the target wholesale — the result mirrors the new source exactly,
deletions included. Without ``--overwrite``, an existing target refuses the
publish and prints a file-level add/change/remove summary of what the replace
would do.

Validation mirrors the serving daemon's own rules, so nothing that publishes
cleanly can fail to serve: the slug and the vault directory's name must both
be URL-safe, and no payload path segment may contain ``..``, a backslash, or
a NUL byte.

After a successful publish, this script itself runs ``lore sync`` so that
"published" and "synced" can never come apart: the success URL is printed
only when the sync subprocess exits 0, and sync's own output streams straight
through to the console rather than being captured, since sync can degrade and
still exit 0. When the resolved vault name is a real
string, sync is scoped (``lore sync --vault <name>``); when it is the default
floor (no ``--vault`` given here), sync stays unscoped (bare ``lore sync``) —
the only targeting that reaches the default-floor vault, since its configured
name is not guaranteed to be the literal string ``"default"``. ``--no-sync``
skips this step entirely for offline/test use and always prints a "NOT
synced" warning in place of the success URL.

Stdlib-only, no dependency on the ``lore`` Python package, the ``camp``
Python package, or on ``trailhead``: the vaults-root check mirrors lore's own
environment-only fallback (``$XDG_STATE_HOME/lore/vaults``, or
``$HOME/.local/state/lore/vaults``) and the camp-state-root check mirrors
``trailhead.paths.state_dir("camp")`` (``CAMP_STATE_DIR``, else
``$XDG_STATE_HOME/camp``, else ``$HOME/.local/state/camp``) — both hand-mirrors
so this script runs standalone wherever ``python3`` and the ``lore`` CLI do.
This is a deliberate, pinned duplication: importing camp or trailhead is not
an option (see above), so the two resolvers are kept honest by a golden
agreement test in this skill's test suite instead of a shared import — a
silent divergence would not fail loudly, it would publish into a directory
nothing scans.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Mapping

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_WARN_BYTES = 5 * 1024 * 1024
_DEFAULT_SITES_PORT = 7314


class PublishError(Exception):
    """A validation or precondition failure that stops publish before any write."""


# ---------------------------------------------------------------------------
# Vaults-root resolution (environment-only, mirrors lore's own fallback tier)
# ---------------------------------------------------------------------------


def _vaults_root(env: Mapping[str, str]) -> Path:
    """Return the vaults root implied by *env*, honoring XDG overrides.

    Mirrors lore's ``state_dir("lore")/vaults`` — always XDG-basedir, on every
    platform including macOS (no ``~/Library`` fallback).
    """
    base = env.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "lore" / "vaults"
    home = env.get("HOME") or str(Path.home())
    return Path(home) / ".local" / "state" / "lore" / "vaults"


def _assert_vault_dir(vault_path: Path) -> None:
    """Refuse a ``--vault-path`` that is not an existing directory.

    Every write below creates parents, so a mistyped vault name would otherwise
    materialize as a brand-new tree sitting beside the real vaults — a site
    published into a vault that does not exist, syncing nothing.
    """
    if not vault_path.exists():
        raise PublishError(f"vault path does not exist: {vault_path}")
    if not vault_path.is_dir():
        raise PublishError(f"vault path is not a directory: {vault_path}")


def _assert_vault_name_agrees(vault_name: str | None, vault_dir_name: str) -> None:
    """Refuse a ``--vault`` name that disagrees with ``--vault-path``'s basename.

    Because a vault path must be a direct child of the vaults root, its
    basename IS the vault's configured name. If the two disagree, one of the
    two is wrong: the publish would write into one vault and sync another.
    """
    if vault_name is not None and vault_name != vault_dir_name:
        raise PublishError(
            f"--vault {vault_name!r} does not name the vault at --vault-path "
            f"(that vault is {vault_dir_name!r}) — publishing there and syncing "
            f"{vault_name!r} would share nothing"
        )


def _assert_direct_child(vault_path: Path, vaults_root: Path) -> None:
    """Refuse a vault path that is not exactly one segment under the vaults root.

    A vault's configured ``path`` in ``config.json`` can be an explicit
    override pointing anywhere, so this check is the only thing standing
    between a publish and writing outside the standard vault layout.
    """
    resolved_root = vaults_root.resolve()
    resolved_vault = vault_path.resolve()
    if resolved_vault.parent != resolved_root:
        raise PublishError(
            f"{vault_path} is not a direct child of the vaults root "
            f"({resolved_root}) — refusing to publish outside the standard "
            "vault layout"
        )


# ---------------------------------------------------------------------------
# Camp state-root resolution and workspace-path confinement
# ---------------------------------------------------------------------------


def _camp_state_root(env: Mapping[str, str]) -> Path:
    """Return the camp state root implied by *env*.

    A hand mirror of ``trailhead.paths.state_dir("camp", env=env)`` — see the
    module docstring for why this is a deliberate, pinned duplication rather
    than an import. Checks ``CAMP_STATE_DIR`` first (the per-app override,
    used verbatim as the whole state dir, not appended with "camp"), then
    ``XDG_STATE_HOME/camp``, then ``$HOME/.local/state/camp``.
    """
    override = env.get("CAMP_STATE_DIR")
    if override:
        return Path(override)
    base = env.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "camp"
    home = env.get("HOME") or str(Path.home())
    return Path(home) / ".local" / "state" / "camp"


def _parse_workspace_path(workspace_path: Path, camp_root: Path) -> tuple[str, str]:
    """Return (group, slug) for a workspace path, refusing anything else.

    The resolved path must be exactly ``<campStateRoot>/<group>/worktrees/<slug>``.
    Both sides are resolved (symlinks followed) before the containment check,
    so a path that structurally looks like a legal workspace location but
    resolves elsewhere via a symlink is refused — the question is whether the
    caller can COMPUTE an escaping path, not whether the raw string sits
    outside the tree.
    """
    resolved_root = camp_root.resolve()
    resolved_ws = workspace_path.resolve()
    try:
        rel = resolved_ws.relative_to(resolved_root)
    except ValueError:
        raise PublishError(
            f"{workspace_path} resolves outside the camp state root "
            f"({resolved_root}) — refusing to publish outside a workspace"
        )
    parts = rel.parts
    if len(parts) != 3 or parts[1] != "worktrees":
        raise PublishError(
            f"{workspace_path} does not resolve to "
            f"<campStateRoot>/<group>/worktrees/<slug> (got {rel} under "
            f"{resolved_root})"
        )
    group, _, slug = parts
    return group, slug


def _validate_workspace_key(key: str) -> None:
    """Refuse a group-slug key the serving daemon would not accept.

    ``<group>-<slug>`` becomes the first URL segment, gated on the same
    pattern a slug must match — mirrors ``_validate_vault_name``.
    """
    if not _SLUG_RE.match(key):
        raise PublishError(
            f"workspace key {key!r} is not URL-safe: must match "
            f"{_SLUG_RE.pattern!r} — a site published under it would not be served"
        )


def _assert_workspace_dir(workspace_path: Path) -> None:
    """Refuse a ``--workspace-path`` that is not an existing directory."""
    if not workspace_path.exists():
        raise PublishError(f"workspace path does not exist: {workspace_path}")
    if not workspace_path.is_dir():
        raise PublishError(f"workspace path is not a directory: {workspace_path}")


def _assert_workspace_still_there(workspace_path: Path) -> None:
    """Refuse if *workspace_path* has disappeared since it was last checked.

    Called immediately before the swap-in rename, while holding the slug
    lock: a `camp remove` teardown cannot proceed past that lock, but this
    re-check is the last line of defense against any other cause of the
    workspace directory vanishing mid-publish, so a race never resurrects a
    ``sites/`` tree after teardown or half-swaps one into a directory that no
    longer exists.
    """
    if not workspace_path.exists():
        raise PublishError(
            f"workspace directory disappeared during publish: {workspace_path} "
            "— refusing"
        )


# ---------------------------------------------------------------------------
# Camp's slug lock (mirrors camp.group.manifest.reconcile_lock)
# ---------------------------------------------------------------------------


def _lock_path_for(ws_dir: Path) -> Path:
    """Return the slug-scoped lockfile path for a workspace dir.

    A SIBLING of the workspace dir — ``<worktrees-root>/<slug>.lock`` — never
    a file inside it, so a `camp remove` teardown's ``rmtree`` of the
    workspace dir can never delete the inode this publish is flocking.
    """
    ws_dir = Path(ws_dir)
    return ws_dir.parent / f"{ws_dir.name}.lock"


@contextmanager
def _workspace_lock(ws_dir: Path):
    """Acquire camp's own per-slug lock guarding writes into *ws_dir*.

    Mirrors ``camp.group.manifest.reconcile_lock`` exactly (stdlib-only:
    ``fcntl`` and ``os``), including the inode re-check after acquire: a
    lockfile reaped (by `camp remove`'s teardown, once a slug is fully torn
    down) while this publish blocked must not hand it an orphaned inode, so
    after every ``flock`` the held inode is compared against the current
    file at ``lock_path`` and retried on the current file if they differ.
    """
    lock_path = _lock_path_for(ws_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        lock_fd = open(str(lock_path), "w")
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            try:
                path_stat = os.stat(lock_path)
            except FileNotFoundError:
                lock_fd.close()
                continue
            fd_stat = os.fstat(lock_fd.fileno())
            if (path_stat.st_dev, path_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
                lock_fd.close()
                continue
        except BaseException:
            lock_fd.close()
            raise
        break
    try:
        yield
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fd.close()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise PublishError(
            f"invalid slug {slug!r}: must match {_SLUG_RE.pattern!r}"
        )


def _validate_vault_name(name: str) -> None:
    """Refuse a vault directory name the serving daemon would not accept.

    The vault directory's basename is the first URL segment of a published
    site. The daemon gates that segment on the same pattern a slug must match,
    so a vault named anything else publishes fine and then 404s on every
    request — an error here is the only place that mismatch is visible.
    """
    if not _SLUG_RE.match(name):
        raise PublishError(
            f"vault directory name {name!r} is not URL-safe: must match "
            f"{_SLUG_RE.pattern!r} — a site published under it would not be served"
        )


def _validate_segments(rel: Path) -> None:
    """Refuse a payload path whose segments the serving daemon would reject.

    The daemon validates every segment of a request path and rejects any that
    contains ``..``, a backslash, or a NUL byte — the ``..`` check is a
    substring test, not an equality test, so an innocuous-looking
    ``notes..v2.html`` is unservable. Rejecting the same shapes here keeps
    "publishes cleanly" and "serves cleanly" from coming apart.
    """
    for segment in rel.parts:
        if ".." in segment or "\\" in segment or "\0" in segment:
            raise PublishError(
                f"unservable path segment {segment!r} in site payload ({rel}): "
                "a segment may not contain '..', a backslash, or a NUL byte"
            )


def _validate_source(source: Path) -> int:
    """Validate *source* and return its total payload size in bytes.

    Denylist enforcement: every entry must be a regular file or directory —
    symlinks and other non-regular entries (fifos, devices, sockets) are
    rejected — and every path segment must be one the daemon will serve. No
    entry whose name folds to ``.git`` is allowed at any depth, file or
    directory: it would publish into ``sites/<slug>/`` and then reach `lore
    sync`'s bare ``git add -A``, which either records it as a gitlink (sync exits
    0 while teammates receive none of the site) or — for a commitless nested repo
    — fails fatally and breaks that vault's sync until the directory is
    hand-deleted. The comparison is case-insensitive (``casefold``): on a
    case-insensitive filesystem (macOS default) git's own ``.git``-name
    protection skips a ``.GIT``/``.Git`` entry too, so a case-sensitive check
    here would let one publish and silently never sync. A root ``index.html`` is
    required. Uses ``lstat`` throughout
    so a symlink is caught by its own mode bit rather than resolved and
    treated as whatever it points to.
    """
    if not source.is_dir():
        raise PublishError(f"source directory not found: {source}")

    total = 0
    seen_index = False
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        _validate_segments(rel)
        if any(segment.casefold() == ".git" for segment in rel.parts):
            raise PublishError(
                f"nested .git entry not allowed in site payload: {rel} — it "
                "would corrupt the vault's own `lore sync`"
            )
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise PublishError(f"symlink not allowed in site payload: {rel}")
        if stat.S_ISDIR(st.st_mode):
            continue
        if not stat.S_ISREG(st.st_mode):
            raise PublishError(f"non-regular file not allowed in site payload: {rel}")
        if rel == Path("index.html"):
            seen_index = True
        total += st.st_size

    if not seen_index:
        raise PublishError("missing root index.html")

    return total


# ---------------------------------------------------------------------------
# Diff summary (for the refuse-without-overwrite path)
# ---------------------------------------------------------------------------


def _relative_files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def _diff_summary(source: Path, target: Path) -> str:
    src_files = _relative_files(source)
    tgt_files = _relative_files(target)
    added = sorted(src_files - tgt_files)
    removed = sorted(tgt_files - src_files)
    changed = sorted(
        rel
        for rel in src_files & tgt_files
        if (source / rel).read_bytes() != (target / rel).read_bytes()
    )

    lines = []
    if added:
        lines.append("  add: " + ", ".join(added))
    if changed:
        lines.append("  change: " + ", ".join(changed))
    if removed:
        lines.append("  remove: " + ", ".join(removed))
    return "\n".join(lines) if lines else "  (no file differences)"


# ---------------------------------------------------------------------------
# Atomic stage + replace
# ---------------------------------------------------------------------------


def _reserve_sibling(sites_dir: Path, prefix: str) -> Path:
    """Return an unused dot-prefixed path inside *sites_dir*.

    ``mkdtemp`` picks the name and proves it was free; removing the directory
    it created hands back a name that ``os.rename`` can move a whole tree onto.
    Same-directory placement is what keeps every rename below a metadata-only
    operation on one filesystem.
    """
    reserved = Path(tempfile.mkdtemp(dir=sites_dir, prefix=prefix))
    reserved.rmdir()
    return reserved


def _stage_and_replace(
    source: Path,
    sites_dir: Path,
    target_dir: Path,
    slug: str,
    *,
    pre_swap_check: Callable[[], None] | None = None,
) -> None:
    """Copy *source* into a temp dir under *sites_dir*, then swap it into place.

    A dot-prefixed staging directory name keeps it outside any ``sites/*`` scan
    pattern while the copy is in progress. The swap never deletes the live tree
    first: an existing site is renamed aside, the staged tree is renamed into
    its place, and only then is the set-aside copy deleted. So at every instant
    ``target_dir`` is either the whole old site or the whole new one — a
    failure anywhere in the sequence restores the old site rather than leaving
    a half-published or missing one, and a reader that opened the old tree
    keeps reading it until it closes.

    ``pre_swap_check``, when given, is called immediately before the swap
    begins (after staging, before either rename) — the workspace target uses
    it to re-verify the workspace directory is still there while holding the
    slug lock, the last line of defense against the workspace disappearing
    mid-publish.

    Both recovery blocks below catch ``BaseException``, not ``Exception``: a
    Ctrl-C (``KeyboardInterrupt``) mid-copy or mid-swap must still trigger the
    same cleanup/restore as an ordinary failure, or it leaves a stray staging
    tree in ``sites/`` for the next `lore sync` to commit, or a swap caught
    with the target renamed aside and nothing renamed back in its place.
    """
    temp_dir = Path(tempfile.mkdtemp(dir=sites_dir, prefix=f".{slug}.stage-"))
    try:
        for path in sorted(source.rglob("*")):
            if not temp_dir.exists():
                # The staging root itself vanished mid-copy (e.g. a workspace
                # teardown race). `mkdir(parents=True, ...)` below would
                # otherwise silently RESURRECT the deleted directory tree —
                # check first so a vanished target refuses instead.
                raise PublishError(
                    f"staging directory disappeared during publish: {temp_dir}"
                )
            rel = path.relative_to(source)
            dest = temp_dir / rel
            if path.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    aside_dir: Path | None = None
    try:
        if pre_swap_check is not None:
            pre_swap_check()
        if target_dir.exists():
            aside_dir = _reserve_sibling(sites_dir, f".{slug}.previous-")
            os.rename(target_dir, aside_dir)
        os.rename(temp_dir, target_dir)
    except BaseException:
        if aside_dir is not None and not target_dir.exists():
            try:
                os.rename(aside_dir, target_dir)
            except OSError:
                # The restore is the last recovery step there is; report where
                # the previous site now lives and let the original failure be
                # the one raised.
                print(
                    f"error: could not restore the previous site — it is at {aside_dir}",
                    file=sys.stderr,
                )
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    if aside_dir is not None:
        shutil.rmtree(aside_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Sync gate
# ---------------------------------------------------------------------------


def _sync_and_report(
    *, vault_name: str | None, no_sync: bool, sites_port: int, vault_url_segment: str, slug: str
) -> int:
    """Run the sync gate, printing the success URL only when sync exits 0.

    ``vault_name`` is the resolved vault's real configured name, or ``None``
    when resolution landed on the default floor. A real name syncs scoped
    (``--vault <name>``); ``None`` syncs bare (every configured vault) — the
    only targeting verified correct for the default floor, since its
    configured name need not be the literal string ``"default"``.

    Sync's own stdout and stderr are inherited, not captured: sync can degrade
    (no remote configured, network down) and still exit 0, so its output is the
    operator's only evidence of what actually reached the remote. The exit code
    still gates the URL; the streamed output is what makes a degraded success
    legible.
    """
    if no_sync:
        print("Published locally but NOT synced (--no-sync) — run `lore sync` to share it.")
        return 0

    cmd = ["lore", "sync"]
    if vault_name:
        cmd += ["--vault", vault_name]

    sys.stdout.flush()
    try:
        result = subprocess.run(cmd)
    except FileNotFoundError:
        print("error: `lore` not found on PATH — published locally but NOT synced", file=sys.stderr)
        return 1

    if result.returncode != 0:
        print(
            "error: published locally but NOT synced — `lore sync` exited "
            f"{result.returncode} (its output is above).",
            file=sys.stderr,
        )
        return 1

    print(f"http://127.0.0.1:{sites_port}/{vault_url_segment}/{slug}/")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish a static site into a lore vault's or a camp workspace's sites/ tree."
    )
    parser.add_argument("source", type=Path, help="Directory containing the site (root index.html required)")
    parser.add_argument("slug", help="Site slug — must match ^[a-z0-9][a-z0-9._-]*$")
    parser.add_argument(
        "--vault-path",
        default=None,
        type=Path,
        help="Resolved vault root directory — mutually exclusive with --workspace-path",
    )
    parser.add_argument(
        "--vault",
        default=None,
        help="Resolved vault's real configured name — omit for the default-floor case",
    )
    parser.add_argument(
        "--workspace-path",
        default=None,
        type=Path,
        help=(
            "Resolved camp workspace directory "
            "(<campStateRoot>/<group>/worktrees/<slug>) — mutually exclusive "
            "with --vault-path"
        ),
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing site wholesale")
    parser.add_argument("--no-sync", action="store_true", help="Skip the lore sync step")
    parser.add_argument("--sites-port", type=int, default=_DEFAULT_SITES_PORT)
    return parser


def _publish_to_vault(
    args: argparse.Namespace, env: Mapping[str, str]
) -> int:
    try:
        _assert_vault_dir(args.vault_path)
        _assert_direct_child(args.vault_path, _vaults_root(env))
        _assert_vault_name_agrees(args.vault, args.vault_path.resolve().name)
        _validate_vault_name(args.vault_path.resolve().name)
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    vault_path = args.vault_path.resolve()
    sites_dir = vault_path / "sites"
    sites_dir.mkdir(parents=True, exist_ok=True)
    target_dir = sites_dir / args.slug

    if target_dir.exists() and not args.overwrite:
        print(f"error: {target_dir} already exists.", file=sys.stderr)
        print(_diff_summary(args.source, target_dir), file=sys.stderr)
        print("Re-run with --overwrite to replace it.", file=sys.stderr)
        return 1

    try:
        _stage_and_replace(args.source, sites_dir, target_dir, args.slug)
    except Exception as exc:
        print(f"error: publish failed: {exc}", file=sys.stderr)
        return 1

    return _sync_and_report(
        vault_name=args.vault,
        no_sync=args.no_sync,
        sites_port=args.sites_port,
        vault_url_segment=vault_path.name,
        slug=args.slug,
    )


def _publish_to_workspace(
    args: argparse.Namespace, env: Mapping[str, str]
) -> int:
    try:
        _assert_workspace_dir(args.workspace_path)
        group, ws_slug = _parse_workspace_path(args.workspace_path, _camp_state_root(env))
        key = f"{group}-{ws_slug}"
        _validate_workspace_key(key)
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    workspace_path = args.workspace_path.resolve()
    sites_dir = workspace_path / "sites"
    target_dir = sites_dir / args.slug

    with _workspace_lock(workspace_path):
        try:
            _assert_workspace_still_there(workspace_path)
        except PublishError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        sites_dir.mkdir(parents=True, exist_ok=True)

        if target_dir.exists() and not args.overwrite:
            print(f"error: {target_dir} already exists.", file=sys.stderr)
            print(_diff_summary(args.source, target_dir), file=sys.stderr)
            print("Re-run with --overwrite to replace it.", file=sys.stderr)
            return 1

        try:
            _stage_and_replace(
                args.source,
                sites_dir,
                target_dir,
                args.slug,
                pre_swap_check=lambda: _assert_workspace_still_there(workspace_path),
            )
        except Exception as exc:
            print(f"error: publish failed: {exc}", file=sys.stderr)
            return 1

    print(f"http://127.0.0.1:{args.sites_port}/{key}/{args.slug}/")
    print(f"Local to this machine only — this link dies with the {key} workspace.")
    return 0


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    env = dict(os.environ) if env is None else env
    args = _build_parser().parse_args(argv)

    if bool(args.vault_path) == bool(args.workspace_path):
        if args.vault_path is None:
            print(
                "error: exactly one of --vault-path or --workspace-path is required",
                file=sys.stderr,
            )
        else:
            print(
                "error: --vault-path and --workspace-path are mutually exclusive",
                file=sys.stderr,
            )
        return 1

    try:
        _validate_slug(args.slug)
        total_size = _validate_source(args.source)
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if total_size > _WARN_BYTES:
        print(
            f"warning: site payload is {total_size} bytes (over the 5 MB advisory limit) — publishing anyway",
            file=sys.stderr,
        )

    if args.workspace_path is not None:
        return _publish_to_workspace(args, env)
    return _publish_to_vault(args, env)


if __name__ == "__main__":
    raise SystemExit(main())
