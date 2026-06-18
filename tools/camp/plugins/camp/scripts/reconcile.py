"""Worktree lifecycle reconciler for camp — Slice 2 (unified workspace layout).

reconcile_worktree(group, slug):
    Idempotent create-or-reconcile. For each group member:
    - Ensures central_state_dir(group)/worktrees/<slug>/<member> exists on branch
      worktree-<slug>, branched off the member's configured `base` (default
      origin/main). The base ref is only used when it already resolves locally;
      the actual `git fetch` is deferred to the Slice 3 async provisioner, so a
      missing base falls back to HEAD rather than failing synchronous bring-up.
    - Existence-guard before git worktree add (never blindly re-add).
    - Bootstraps each member's configured bootstrap list in parallel (shell=False).
    - Writes the central manifest atomically only after ALL members succeed.

reconcile_break(group, slug):
    Removes each member's worktree + the central manifest.
    - Removal confinement: BOTH the manifest-supplied target AND the workspace dir
      (central_state_dir(group)/worktrees/<slug>) are .resolve()'d before the check,
      then the target must be is_relative_to the resolved workspace dir. A
      symlink-escaping worktree_path is rejected. An old-layout path (outside the
      workspace dir) raises a legible legacy-layout error rather than half-applying.
    - Dirty worktree blocks break unless force=True.
    - Break atomicity symmetry: manifest is not left listing a removed member.

A slug-scoped file lock guards concurrent reconcile_worktree calls so two
terminals racing camp <slug> don't both git-worktree-add the same path.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from manifest import (
    manifest_path_for,
    read_central_manifest,
    reconcile_lock,
    remove_central_manifest,
    workspace_dir,
    write_central_manifest,
)

# Thread-local lock registry for concurrent-run guard (same process; file lock
# guards cross-process concurrency).
_SLUG_LOCKS: dict[str, threading.Lock] = {}
_SLUG_LOCKS_META = threading.Lock()


class ReconcileError(Exception):
    """Raised on a non-recoverable reconcile failure.

    The message always names the member and the reason.
    """


class ConfinementError(Exception):
    """Raised when a worktree path is outside the resolved workspace dir."""


class LegacyLayoutError(Exception):
    """Raised when a manifest carries an old-layout worktree_path.

    Old layout: <repo_root>/.claude/worktrees/<slug> (outside the unified
    workspace dir). camp cannot safely remove it; the message points the user at
    a manual `git worktree remove`.
    """


# Default branch base for new worktree branches (per-member overridable).
DEFAULT_BASE = "origin/main"

# Per-member git fetch timeout (seconds) for the async provisioner. An
# unreachable remote fails that member instead of hanging the whole bring-up.
FETCH_TIMEOUT_SECONDS = 120

# Consecutive path segments that mark the retired per-repo worktree layout
# (<repo_root>/.claude/worktrees/<slug>). The unified workspace layout never has
# ".claude" immediately followed by "worktrees", so this pair only appears in an
# old-layout manifest path (and won't false-positive on a state dir nested under
# some ".claude" ancestor).
_OLD_LAYOUT_MARKER = (".claude", "worktrees")


def _is_old_layout_path(path: Path) -> bool:
    parts = path.parts
    a, b = _OLD_LAYOUT_MARKER
    return any(parts[i] == a and parts[i + 1] == b for i in range(len(parts) - 1))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _slug_lock(slug: str) -> threading.Lock:
    """Return a per-slug threading.Lock (created on demand)."""
    with _SLUG_LOCKS_META:
        if slug not in _SLUG_LOCKS:
            _SLUG_LOCKS[slug] = threading.Lock()
        return _SLUG_LOCKS[slug]


def _branch_name(slug: str, branch_pattern: str) -> str:
    """Expand the branch pattern for the slug."""
    return branch_pattern.format(slug=slug)


def _worktree_path(
    group_name: str,
    slug: str,
    member_name: str,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    """Return the member's worktree path under the unified workspace dir:
    central_state_dir(group)/worktrees/<slug>/<member>."""
    return workspace_dir(group_name, slug, env=env) / member_name


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_is_dirty(path: Path) -> bool:
    result = _git(path, "status", "--porcelain")
    return bool(result.stdout.strip())


def _branch_exists_locally(repo_root: Path, branch: str) -> bool:
    result = _git(repo_root, "branch", "--list", branch)
    return bool(result.stdout.strip())


def _ref_resolves(repo_root: Path, ref: str) -> bool:
    """Return True if `ref` resolves to a commit in repo_root (local clone)."""
    result = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return result.returncode == 0


def _worktree_registered(repo_root: Path, wt_path: Path) -> bool:
    """Return True if wt_path is already listed in git's worktree registry."""
    result = _git(repo_root, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            registered = line[len("worktree "):].strip()
            try:
                if Path(registered).resolve() == wt_path.resolve():
                    return True
            except Exception:
                pass
    return False


def _fetch_base(
    repo_root: Path, base: str, *, timeout: float = FETCH_TIMEOUT_SECONDS
) -> None:
    """Fetch the member's base ref under a timeout (Slice 3 async provisioner).

    The base looks like "origin/main"; the remote is the part before the first
    "/". A non-remote base (no "/") is a local ref and skips the fetch. A
    subprocess.TimeoutExpired propagates so the caller fails that member rather
    than hanging on an unreachable remote.

    A non-timeout fetch FAILURE (auth reject, bad URL, ref absent, host down) is
    fatal ONLY when the base ref does not already resolve locally: in that case
    branching off HEAD would silently put the member on the wrong base, so we
    raise ReconcileError (the caller flips the member to failed + reason). If the
    base ref already resolves locally (cached from a prior fetch), a fetch failure
    is non-fatal — proceed with the cached ref.
    """
    remote, _, ref = base.partition("/")
    if not ref:
        return  # local ref — nothing to fetch
    result = subprocess.run(
        ["git", "-C", str(repo_root), "fetch", remote, ref],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0 and not _ref_resolves(repo_root, base):
        raise ReconcileError(
            f"camp: git fetch failed for base {base!r} in {repo_root} and the ref "
            f"does not resolve locally: {result.stderr.strip() or result.stdout.strip()}"
        )


def _move_worktree(
    member: dict[str, Any], stage: Path, wt_path: Path, repo_root: Path
) -> None:
    """git worktree move <stage> <wt_path>, preserving the git-internal admin name.

    On failure, raise ReconcileError but leave <stage> registered so the next
    reconcile resumes at the move step (recovery is idempotent, never
    destructive — see _add_worktree_for_member's state machine).
    """
    result = _git(repo_root, "worktree", "move", str(stage), str(wt_path))
    if result.returncode != 0:
        raise ReconcileError(
            f"camp: git worktree move failed for member {member['name']!r} "
            f"({stage} → {wt_path}): {result.stderr.strip() or result.stdout.strip()}"
        )


def _add_worktree_for_member(
    member: dict[str, Any],
    wt_path: Path,
    branch: str,
    repo_root: Path,
    *,
    base: str = DEFAULT_BASE,
    slug: str,
) -> None:
    """Add a git worktree for one member at wt_path, branched off `base`.

    Admin-name control (Slice 1): git derives a worktree's INTERNAL admin name
    (the dir under `.git/worktrees/<name>`, which Claude Code surfaces as
    `workspace.git_worktree`) from the basename of the add path, de-duplicating
    collisions as `<name>`, `<name>1`, …. Every camp member worktree is leaf-named
    after the MEMBER (`trailhead`), so they all collide and git reports useless
    names like `trailhead5`. To make the admin name the SLUG instead, we
    `git worktree add` at a staging path whose basename IS the slug, then
    `git worktree move` it into the `<member>` folder — `git worktree move`
    preserves the admin name, so the folder keeps the member name while
    `git_worktree` reports the slug.

    Existence-guard: if wt_path already exists (directory present OR registered),
    skip — idempotent re-run.

    Recoverable partial state: the add and move are two phases. The entry is a
    state machine checked IN THIS ORDER, so an interrupt/FS error between the two
    phases is recoverable on the next reconcile rather than bricking the worktree:
      1. wt_path present (dir or registered)           → done, skip.
      2. stage registered (added but not yet moved)    → resume at the MOVE step
         (do NOT re-add — git rejects the duplicate path).
      3. otherwise                                     → fresh add, then move.

    member == slug short-circuit: when stage == wt_path the add path's basename is
    already the slug, so add directly at wt_path with no move (but still add).

    Branch-base policy: a new branch is created off `base` (default origin/main,
    per-member overridable). Because the `git fetch` is deferred to the Slice 3
    async provisioner, the base ref is only used when it already resolves in the
    local clone; otherwise it falls back to HEAD so synchronous bring-up never
    fails on a not-yet-fetched remote ref.

    Raises ReconcileError on git failure or a confinement violation.
    """
    # 1. Existence-guard — final path already present (idempotent no-op).
    #    NB: _worktree_registered uses Path.resolve(), which FOLLOWS symlinks; on
    #    an overlay/bind-mount FS two distinct paths could resolve equal and cause
    #    a false-positive skip. Acceptable for camp's state-dir layout (no such
    #    aliasing), but noted so a future FS change revisits it.
    if wt_path.is_dir() or _worktree_registered(repo_root, wt_path):
        return

    # Stage is a sibling of wt_path whose basename is the slug (see docstring).
    stage = wt_path.parent / slug

    # Confinement (defense-in-depth): a future caller that bypasses the
    # ^[a-z0-9-]+$ slug validation must not be able to make <stage> escape the
    # workspace via a "../"-laden slug. Mirror reconcile_break's resolve-then-
    # relative_to check. Runs BEFORE any mkdir/git call.
    parent_resolved = wt_path.parent.resolve()
    try:
        stage.resolve().relative_to(parent_resolved)
    except ValueError:
        raise ReconcileError(
            f"camp: refusing worktree add for member {member['name']!r} — stage "
            f"path {stage} escapes the workspace dir {wt_path.parent} "
            f"(slug {slug!r})"
        )

    direct = stage == wt_path  # member == slug → no move needed

    # 2. Partial-state recovery: stage was added but not moved → resume at move.
    if not direct and _worktree_registered(repo_root, stage):
        _move_worktree(member, stage, wt_path, repo_root)
        return

    # 3. Fresh add (at stage, or directly at wt_path when member == slug).
    add_target = wt_path if direct else stage
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    if _branch_exists_locally(repo_root, branch):
        result = _git(repo_root, "worktree", "add", str(add_target), branch)
    else:
        start_point = base if _ref_resolves(repo_root, base) else "HEAD"
        result = _git(
            repo_root, "worktree", "add", "-b", branch, str(add_target), start_point
        )

    if result.returncode != 0:
        raise ReconcileError(
            f"camp: git worktree add failed for member {member['name']!r} "
            f"at {add_target}: {result.stderr.strip() or result.stdout.strip()}"
        )

    if not direct:
        _move_worktree(member, stage, wt_path, repo_root)


def _run_bootstrap(member: dict[str, Any], wt_path: Path) -> None:
    """Run a member's bootstrap command in the worktree directory.

    bootstrap is a flat list of strings representing a single command +
    its arguments (shell=False, D-F trust boundary). E.g.:
        ["pip", "install", "-e", "."]

    An empty list means no bootstrap; a no-op.
    Raises ReconcileError if the command exits non-zero.
    """
    bootstrap = member.get("bootstrap") or []
    if not bootstrap:
        return

    if not all(isinstance(part, str) for part in bootstrap):
        raise ReconcileError(
            f"camp: bootstrap for member {member['name']!r} must be a list of strings "
            f"(D-F: shell=False); got non-string elements"
        )

    result = subprocess.run(
        bootstrap,
        cwd=str(wt_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReconcileError(
            f"camp: bootstrap failed for member {member['name']!r} "
            f"(exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )


def _remove_worktree_for_member(
    member: dict[str, Any],
    wt_path: Path,
    repo_root: Path,
    workspace_dir: Path,
    *,
    force: bool,
) -> None:
    """Remove a git worktree for one member.

    Confinement: BOTH wt_path and workspace_dir are .resolve()'d before the check,
    then wt_path must be is_relative_to the resolved workspace_dir — so a
    symlink-escaping worktree_path is rejected.

    Raises ConfinementError if the path escapes the workspace dir.
    Raises ReconcileError if git worktree remove fails.
    """
    try:
        wt_resolved = wt_path.resolve()
        ws_resolved = workspace_dir.resolve()
        wt_resolved.relative_to(ws_resolved)
    except ValueError:
        raise ConfinementError(
            f"camp: worktree path {wt_path} resolves outside the workspace dir "
            f"{workspace_dir} for member {member['name']!r} — refusing removal"
        )

    if not wt_path.is_dir():
        return  # Already gone — idempotent

    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(wt_path))

    result = _git(repo_root, *args)
    if result.returncode != 0:
        raise ReconcileError(
            f"camp: git worktree remove failed for member {member['name']!r} "
            f"at {wt_path}: {result.stderr.strip() or result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reconcile_worktree(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Idempotent create-or-reconcile a worktree set for (group, slug).

    For each member:
      1. Ensures <repo_root>/.claude/worktrees/<slug> exists on branch worktree-<slug>.
      2. Runs each member's bootstrap commands (shell=False, D-F).
    Then writes the central manifest atomically.

    Concurrent-run guard: a per-slug lock (threading + file lock) prevents two
    concurrent reconcile_worktree calls from both running git worktree add.

    Returns a result dict with:
        member_count:  int
        members:       list of {"name", "worktree_path"}
        manifest_path: str
        bootstrap:     "ok" | "skipped"

    Raises:
        ReconcileError: on git or bootstrap failure (with member name in message).
    """
    group_name: str = group["group"]["name"]
    members: list[dict[str, Any]] = group["members"]
    branch_pattern: str = group.get("branch_pattern", "worktree-{slug}")
    branch = _branch_name(slug, branch_pattern)

    # Get the slug-scoped lock (guards concurrent same-process calls)
    lock = _slug_lock(f"{group_name}/{slug}")

    with lock:
        mpath = manifest_path_for(group_name, slug, env=env)

        # Also acquire a file-level lock to guard cross-process concurrency
        with reconcile_lock(mpath.parent):
            # -- Phase 1: Create member worktrees (existence-guarded)
            member_results: list[dict[str, Any]] = []
            for member in members:
                repo_root = Path(member["repo_root"])
                wt_path = _worktree_path(
                    group_name, slug, member["name"], env=env
                )
                base = member.get("base") or DEFAULT_BASE
                _add_worktree_for_member(
                    member, wt_path, branch, repo_root, base=base, slug=slug
                )
                member_results.append({
                    "name": member["name"],
                    "repo_root": str(repo_root),
                    "worktree_path": str(wt_path),
                })

            # -- Phase 2: Bootstrap members in parallel (shell=False, D-F)
            any_bootstrap_failure: Exception | None = None
            if any(bool(m.get("bootstrap")) for m in members):
                with ThreadPoolExecutor(max_workers=len(members)) as executor:
                    futures = {}
                    for member, mr in zip(members, member_results):
                        wt_path = Path(mr["worktree_path"])
                        fut = executor.submit(_run_bootstrap, member, wt_path)
                        futures[fut] = member

                    for fut in as_completed(futures):
                        member = futures[fut]
                        try:
                            fut.result()
                        except Exception as e:
                            any_bootstrap_failure = e
                            break

            if any_bootstrap_failure is not None:
                raise any_bootstrap_failure

            # -- Phase 3: Write central manifest atomically (only after all succeed)
            manifest_data: dict[str, Any] = {
                "schema_version": 1,
                "group": group_name,
                "slug": slug,
                "branch": branch,
                "members": member_results,
            }
            write_central_manifest(mpath, manifest_data)

    return {
        "slug": slug,
        "member_count": len(member_results),
        "members": [mr["name"] for mr in member_results],
        "manifest_path": str(mpath),
        "bootstrap": "ok",
    }


def reconcile_break(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Remove a worktree set for (group, slug).

    Algorithm:
      1. Read the central manifest to get member worktree paths.
      2. Check all members for dirty trees (abort unless force=True).
      3. Confinement pre-check (BEFORE any removal): each worktree_path must
         resolve inside the resolved workspace dir
         (central_state_dir(group)/worktrees/<slug>). A symlink-escaping path is
         rejected (ConfinementError); an old-layout path under a repo_root is
         rejected with a legible LegacyLayoutError. The pre-check aborts the whole
         break — never a half-applied removal.
      4. Remove each member worktree via git worktree remove.
      5. Remove the central manifest ONLY if all removals succeeded (break
         atomicity symmetry — never leave a manifest listing a removed member).

    Returns a result dict with status, removed members, and any errors.

    Raises:
        ManifestError: If the manifest is malformed.
        ConfinementError: If a worktree path resolves outside the workspace dir.
        LegacyLayoutError: If a worktree path uses the retired per-repo layout.
        ReconcileError: If a member worktree is dirty and force=False.
    """
    group_name: str = group["group"]["name"]
    mpath = manifest_path_for(group_name, slug, env=env)
    ws_dir = workspace_dir(group_name, slug, env=env)
    ws_resolved = ws_dir.resolve()

    # Read current manifest
    manifest_data = read_central_manifest(mpath)
    member_entries = manifest_data.get("members", [])

    # Dirty-check before removal
    if not force:
        dirty = []
        for entry in member_entries:
            wt_path = Path(entry["worktree_path"])
            if wt_path.is_dir() and _git_is_dirty(wt_path):
                dirty.append(entry["name"])
        if dirty:
            raise ReconcileError(
                f"camp: dirty worktrees in slug {slug!r}: {', '.join(dirty)} "
                "(pass force=True to discard changes)"
            )

    # Confinement pre-check: validate all paths BEFORE removing anything.
    for entry in member_entries:
        wt_path = Path(entry["worktree_path"])
        try:
            wt_path.resolve().relative_to(ws_resolved)
            continue  # inside the workspace dir — OK
        except ValueError:
            pass

        # Outside the workspace dir. Distinguish a retired per-repo layout path
        # (legible legacy error) from an arbitrary/symlink-escaping path.
        if _is_old_layout_path(wt_path):
            raise LegacyLayoutError(
                f"camp: member {entry['name']!r} worktree_path {wt_path} uses the "
                f"retired per-repo layout (outside the workspace dir {ws_dir}). "
                f"camp will not remove it — run `git worktree remove {wt_path}` manually."
            )
        raise ConfinementError(
            f"camp: worktree path {wt_path} resolves outside the workspace dir "
            f"{ws_dir} for member {entry['name']!r} — refusing removal"
        )

    # Remove each member worktree; track removals for atomicity symmetry
    removed: list[str] = []
    errors: list[str] = []
    member_for_entry: dict[str, dict[str, Any]] = {}

    # Build a lookup from name to group config member
    for m in group["members"]:
        member_for_entry[m["name"]] = m

    for entry in member_entries:
        name = entry["name"]
        wt_path = Path(entry["worktree_path"])
        repo_root = Path(entry["repo_root"])
        member = member_for_entry.get(name, {"name": name})

        try:
            _remove_worktree_for_member(
                member, wt_path, repo_root, ws_dir, force=force
            )
            removed.append(name)
        except (ConfinementError, LegacyLayoutError):
            raise
        except Exception as e:
            errors.append(f"{name}: {e}")

    # Break atomicity symmetry: only remove the manifest if all removals succeeded
    # (or if the members that failed were already absent).
    # Do NOT leave a manifest listing members whose worktrees are already removed.
    if not errors:
        remove_central_manifest(mpath)
        # Remove the now-camp-owned workspace dir itself (.camp, .claude,
        # setup.log, .session.lock, doc files). Leaving it behind makes the next
        # `camp ai <slug>` see ws_dir.exists() True and wrongly resume a
        # torn-down session. Confinement: the resolved workspace dir MUST sit
        # under the resolved worktrees root (central_state_dir/worktrees) anchored
        # independently of ws_dir — never rmtree an unconfined path (symlink
        # escape, old layout, etc.).
        if ws_dir.exists():
            from group_resolve import central_state_dir
            worktrees_root = (central_state_dir(group_name, env=env) / "worktrees").resolve()
            try:
                ws_resolved.relative_to(worktrees_root)
            except ValueError:
                raise ConfinementError(
                    f"camp: workspace dir {ws_dir} resolves outside the worktrees "
                    f"root {worktrees_root} — refusing removal"
                )
            shutil.rmtree(ws_resolved)
        status = "ok"
    else:
        # Some removals failed. Update the manifest to reflect reality:
        # remove entries for members that were successfully removed so the
        # manifest never lists a member whose worktree is gone.
        remaining = [
            e for e in member_entries
            if e["name"] not in removed
        ]
        if remaining:
            updated = dict(manifest_data)
            updated["members"] = remaining
            write_central_manifest(mpath, updated)
        else:
            remove_central_manifest(mpath)
        status = "ok_with_errors"

    return {
        "status": status,
        "slug": slug,
        "removed": removed,
        "errors": errors,
    }


def format_success_summary(result: dict[str, Any]) -> str:
    """Format a D-I success summary line for camp <slug>.

    Format: "worktree-<slug>: N member(s) — <names> (bootstrap: ok) | manifest: <path>"
    """
    slug = result.get("slug", "?")
    count = result.get("member_count", 0)
    members = result.get("members", [])
    names = ", ".join(members) if members else "(none)"
    bootstrap_status = result.get("bootstrap", "ok")
    manifest_path = result.get("manifest_path", "?")

    # Derive branch name from manifest path or members
    # The slug is embedded in the manifest path as .../<slug>/manifest.json
    return (
        f"worktree-{slug}: {count} member(s) — {names} "
        f"(bootstrap: {bootstrap_status}) | manifest: {manifest_path}"
    )
