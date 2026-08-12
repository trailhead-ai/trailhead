#!/usr/bin/env python3
"""Publish a static site into a lore vault's ``sites/`` tree.

This is the deterministic half of the ``outpost:publish-site`` skill: given a
source directory, a slug, and an already-resolved vault path, it validates the
payload, stages it into a temp directory inside ``<vault>/sites/``, and
atomically renames it into ``<vault>/sites/<slug>/``. Vault *resolution* — the
judgment call of which vault a publish targets — is the skill's job, not this
script's: it always receives an explicit ``--vault-path``, and an optional
``--vault`` name used only for sync targeting.

Publish is atomic: a failure during staging leaves the temp directory cleaned
up and the existing target (if any) untouched. Updating an existing site
requires ``--overwrite`` and replaces the target wholesale — the result
mirrors the new source exactly, deletions included. Without ``--overwrite``,
an existing target refuses the publish and prints a file-level add/change/
remove summary of what the replace would do.

After a successful publish, this script itself runs ``lore sync`` so that
"published" and "synced" can never come apart: the success URL is printed
only when the sync subprocess exits 0. When the resolved vault name is a real
string, sync is scoped (``lore sync --vault <name>``); when it is the default
floor (no ``--vault`` given here), sync stays unscoped (bare ``lore sync``) —
the only targeting that reaches the default-floor vault, since its configured
name is not guaranteed to be the literal string ``"default"``. ``--no-sync``
skips this step entirely for offline/test use and always prints a "NOT
synced" warning in place of the success URL.

Stdlib-only, no dependency on the ``lore`` Python package or on ``trailhead``:
the vaults-root check below mirrors lore's own environment-only fallback
(``$XDG_STATE_HOME/lore/vaults``, or ``$HOME/.local/state/lore/vaults``) so
this script runs standalone wherever ``python3`` and the ``lore`` CLI do.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping

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
# Validation
# ---------------------------------------------------------------------------


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise PublishError(
            f"invalid slug {slug!r}: must match {_SLUG_RE.pattern!r}"
        )


def _validate_source(source: Path) -> int:
    """Validate *source* and return its total payload size in bytes.

    Denylist enforcement: every entry must be a regular file or directory —
    symlinks and other non-regular entries (fifos, devices, sockets) are
    rejected. A root ``index.html`` is required. Uses ``lstat`` throughout so
    a symlink is caught by its own mode bit rather than resolved and treated
    as whatever it points to.
    """
    if not source.is_dir():
        raise PublishError(f"source directory not found: {source}")

    total = 0
    seen_index = False
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
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


def _stage_and_replace(source: Path, sites_dir: Path, target_dir: Path, slug: str) -> None:
    """Copy *source* into a temp dir under *sites_dir*, then swap it into place.

    A dot-prefixed staging directory name keeps it outside any ``sites/*``
    scan pattern while the copy is in progress. On any failure the staging
    directory is removed and *target_dir* is left exactly as it was — an
    interrupted publish never leaves a partial site visible.
    """
    temp_dir = Path(tempfile.mkdtemp(dir=sites_dir, prefix=f".{slug}.stage-"))
    try:
        for path in sorted(source.rglob("*")):
            rel = path.relative_to(source)
            dest = temp_dir / rel
            if path.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    if target_dir.exists():
        shutil.rmtree(target_dir)
    os.rename(temp_dir, target_dir)


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
    """
    if no_sync:
        print("Published locally but NOT synced (--no-sync) — run `lore sync` to share it.")
        return 0

    cmd = ["lore", "sync"]
    if vault_name:
        cmd += ["--vault", vault_name]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("error: `lore` not found on PATH — published locally but NOT synced", file=sys.stderr)
        return 1

    if result.returncode != 0:
        print("error: published locally but NOT synced — `lore sync` failed:", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 1

    print(f"http://127.0.0.1:{sites_port}/{vault_url_segment}/{slug}/")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish a static site into a lore vault's sites/ tree."
    )
    parser.add_argument("source", type=Path, help="Directory containing the site (root index.html required)")
    parser.add_argument("slug", help="Site slug — must match ^[a-z0-9][a-z0-9._-]*$")
    parser.add_argument("--vault-path", required=True, type=Path, help="Resolved vault root directory")
    parser.add_argument(
        "--vault",
        default=None,
        help="Resolved vault's real configured name — omit for the default-floor case",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing site wholesale")
    parser.add_argument("--no-sync", action="store_true", help="Skip the lore sync step")
    parser.add_argument("--sites-port", type=int, default=_DEFAULT_SITES_PORT)
    return parser


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    env = dict(os.environ) if env is None else env
    args = _build_parser().parse_args(argv)

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

    try:
        _assert_direct_child(args.vault_path, _vaults_root(env))
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


if __name__ == "__main__":
    raise SystemExit(main())
