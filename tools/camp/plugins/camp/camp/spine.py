"""Worktree-spine for camp.

Contains slug handling, manifest/cwd resolution, git wrappers, and the
worktree command handlers (cmd_status, cmd_sync, cmd_ls, cmd_path,
cmd_foreach, cmd_doctor, cmd_help).

Notable structure:
- No _SIBLING_REPOS constant — the member set comes from the group config.
- cmd_doctor: dev-env probes stripped; keeps asdf + consistency checks.
- _canonical_root() is sourced from the CAMP_CANONICAL_ROOT env var.
- _check_trailhead_paths_importable() is the shared import guard.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any, NoReturn

from .gitutil import _git_is_dirty, _git_out, _git_repo_status
from .workspace.verb_taxonomy import (
    DISABLED_VERBS,
    LEGACY_REDIRECTS,
    NEEDS_GROUP_VERBS,
    VERB_ALIASES,
    bare_slug_message,
    needs_group_message,
    resolve_verb,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MANIFEST_FILENAME = ".workspace-manifest.json"
_SIBLING_MARKER = ".workspace-sibling"
_VALID_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_NORMALIZE_RE = re.compile(r"[^a-z0-9-]+")

# RESERVED — every token that must NOT be dispatched as a bare slug. It is a
# SUPERSET of the verb taxonomy: the taxonomy-OWNED tokens are
# DERIVED from verb_taxonomy so adding/renaming an alias, legacy redirect, disabled
# verb, or needs-group verb is a single-place edit (no drift vs. the dispatchers).
# The remainder — verbs the taxonomy tables do not model — stays an explicit
# literal. The membership-parity test (test_verb_aliases) pins the full union, so
# drift in EITHER half fails loudly rather than silently changing slug validation.
_TAXONOMY_RESERVED = (
    set(VERB_ALIASES)  # alias keys: rm, ls
    | set(LEGACY_REDIRECTS)  # legacy keys: open, break, init, ai, enter
    | set(DISABLED_VERBS)  # restock, sweep, code, fire
    | set(NEEDS_GROUP_VERBS)  # new, remove, pwd, activate, setup
)

_STATIC_RESERVED = frozenset(
    {
        # Canonical group-aware/fleet verbs the taxonomy tables do not model.
        "group",
        "list",
        "status",
        "sync",
        "rebase",
        "path",
        "foreach",
        "doctor",
        # Meta verbs.
        "help",
        "version",
        "which",
        # Hook-handler subcommands (dispatched pre-group-resolve in cli/camp).
        "session-bootstrap",
        "worktree-cleanup",
    }
)

RESERVED = frozenset(_STATIC_RESERVED | _TAXONOMY_RESERVED)

# ---------------------------------------------------------------------------
# Single import guard, hook-subprocess-proof
# ---------------------------------------------------------------------------


def _check_trailhead_paths_importable(
    *,
    _raise_import_error: bool = False,
    _out: IO[str] | None = None,
) -> bool:
    """Guard for trailhead.paths import.

    Returns True when the module is importable.
    Returns False (and prints a legible message to _out or stderr) when not.

    The _raise_import_error seam allows tests to simulate the missing-package
    case without actually uninstalling the package.
    """
    out = _out if _out is not None else sys.stderr
    if _raise_import_error:
        print(
            "camp: trailhead package is not importable — "
            "run 'trailhead install' or 'pip install -e .' in the trailhead repo "
            "to make 'import trailhead.paths' work from any cwd.",
            file=out,
        )
        return False
    try:
        import trailhead.paths  # noqa: F401

        return True
    except ImportError:
        print(
            "camp: trailhead package is not importable — "
            "run 'trailhead install' or 'pip install -e .' in the trailhead repo "
            "to make 'import trailhead.paths' work from any cwd.",
            file=out,
        )
        return False


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


def _canonical_root() -> Path:
    """Return the canonical camp root.

    CAMP_CANONICAL_ROOT overrides the derived path for test isolation.
    Defaults to the parent of the plugin dir.
    """
    override = os.environ.get("CAMP_CANONICAL_ROOT")
    if override:
        return Path(override)
    # Default: tools/camp/plugins/camp -> tools/camp (tool root)
    return Path(__file__).resolve().parents[3]


def _workspace_root() -> Path:
    """Return WORKSPACE_ROOT env var, defaulting to $HOME/code."""
    configured = os.environ.get("WORKSPACE_ROOT")
    if configured:
        return Path(configured)
    return Path.home() / "code"


# ---------------------------------------------------------------------------
# Dry-run seam
# ---------------------------------------------------------------------------


def _is_dry_run(argv: list[str]) -> bool:
    return bool(os.environ.get("CAMP_DRY_RUN")) or "--dry-run" in argv


def _dry_run_print(cmd: list[str], *, env_extras: dict[str, str] | None = None) -> None:
    parts = []
    if env_extras:
        for k, v in sorted(env_extras.items()):
            parts.append(f"{k}={v!r}")
    parts.extend(cmd)
    print(f"[dry-run] would exec: {' '.join(parts)}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Slug handling
# ---------------------------------------------------------------------------


def _normalize_slug(raw: str) -> tuple[str, bool]:
    """Return (normalized, was_changed).

    Lowercases, replaces non-[a-z0-9-] with '-', trims leading/trailing '-'.
    """
    lowered = raw.lower()
    replaced = _NORMALIZE_RE.sub("-", lowered)
    trimmed = replaced.strip("-")
    return trimmed, trimmed != raw


_RAW_DANGEROUS_RE = re.compile(r"[/\\$`|;&\x00-\x1f]|\.\.")


def _validate_slug(slug: str) -> None:
    """Validate that slug matches ^[a-z0-9-]+$.

    Raises SystemExit(1) with a clear message if invalid.
    """
    if not slug or not _VALID_SLUG_RE.match(slug):
        _die(
            f"camp: invalid slug {slug!r} — must match [a-z0-9-]+ "
            f"(no slashes, spaces, or shell metacharacters)"
        )


def _resolve_slug(raw: str, *, context: str = "argument") -> str:
    """Normalize and validate a slug, printing a notice to stderr if normalized.

    Rejects raw input that contains path-traversal sequences (e.g. '..', '/')
    or shell metacharacters before normalization.
    """
    if _RAW_DANGEROUS_RE.search(raw):
        _die(
            f"camp: invalid slug from {context}: {raw!r} — contains path-traversal "
            f"or shell metacharacters "
            f"(no '..', '/', '\\\\', '$', '`', '|', ';', '&')"
        )

    normalized, changed = _normalize_slug(raw)
    if not normalized:
        _die(f"camp: slug from {context}: {raw!r} has no usable characters after normalization")
    if changed:
        print(f"camp: using normalized slug {normalized!r} (from {raw!r})", file=sys.stderr)
    _validate_slug(normalized)
    return normalized


# ---------------------------------------------------------------------------
# Manifest / cwd resolution
# ---------------------------------------------------------------------------


def _consume_flag_value(args: list[str], flag: str) -> str | None:
    """Consume the first `--flag <value>` / `--flag=value` from args, in place."""
    prefix = f"{flag}="
    i = 0
    while i < len(args):
        if args[i] == flag and i + 1 < len(args):
            value = args[i + 1]
            del args[i : i + 2]
            return value
        if args[i].startswith(prefix):
            value = args[i][len(prefix) :]
            del args[i]
            return value
        i += 1
    return None


def _read_manifest(path: Path) -> dict[str, Any] | None:
    """Read and parse a manifest file; return None on any error."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_worktree_from(start: Path) -> tuple[Path, dict[str, Any]] | None:
    """Walk up from start looking for a manifest or sibling marker.

    Returns (worktree_path, manifest) or None if not inside any worktree.
    """
    current = start.resolve()
    visited: set[Path] = set()
    while current not in visited:
        visited.add(current)

        manifest_path = current / _MANIFEST_FILENAME
        if manifest_path.is_file():
            manifest = _read_manifest(manifest_path)
            if manifest is not None:
                return current, manifest

        marker_path = current / _SIBLING_MARKER
        if marker_path.is_file():
            try:
                target = Path(marker_path.read_text().strip())
            except OSError:
                pass
            else:
                if target.is_file():
                    manifest = _read_manifest(target)
                    if manifest is not None:
                        return target.parent, manifest

        parent = current.parent
        if parent == current:
            break
        current = parent

    return None


def _worktree_path_for_slug(slug: str, workspace_root: Path) -> Path:
    """Return the worktree path for the given slug (under workspace_root)."""
    return workspace_root / "trailhead" / ".claude" / "worktrees" / slug


def _list_manifests(workspace_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Walk workspace_root/trailhead/.claude/worktrees/*/manifest and return pairs."""
    base = workspace_root / "trailhead" / ".claude" / "worktrees"
    results: list[tuple[Path, dict[str, Any]]] = []
    if not base.is_dir():
        return results
    for candidate in sorted(base.iterdir()):
        manifest_path = candidate / _MANIFEST_FILENAME
        if manifest_path.is_file():
            manifest = _read_manifest(manifest_path)
            if manifest is not None:
                results.append((candidate, manifest))
    return results


# ---------------------------------------------------------------------------
# Error / exit helpers
# ---------------------------------------------------------------------------


def _die(message: str, code: int = 1) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(code)


def _no_worktree_error() -> NoReturn:
    _die(
        "camp: not inside a worktree and no --name given.\n"
        "  Use 'camp ls' to list active worktrees.\n"
        "  Use '--name <slug>' to target a specific worktree from any directory."
    )


# ---------------------------------------------------------------------------
# Resolve target from cwd or --name
# ---------------------------------------------------------------------------


def _resolve_target(
    args: list[str],
    *,
    allow_missing: bool = False,
) -> tuple[str, Path]:
    """Parse --name from args (consuming it) and resolve (slug, wt_path).

    Falls back to cwd walk-up if --name is not provided.
    Returns (slug, worktree_path). Exits non-zero if resolution fails.
    """
    workspace_root = _workspace_root()
    name = _consume_flag_value(args, "--name")

    if name is not None:
        slug = _resolve_slug(name, context="--name")
        wt_path = _worktree_path_for_slug(slug, workspace_root)
        if not allow_missing and not wt_path.is_dir():
            _die(f"camp: no worktree found for slug {slug!r} (looked in {wt_path})")
        return slug, wt_path

    # cwd walk-up
    cwd = Path.cwd()
    resolved = _resolve_worktree_from(cwd)
    if resolved is None:
        _no_worktree_error()

    wt, manifest = resolved
    slug = manifest.get("name", "")
    if not slug:
        _die("camp: manifest is missing 'name' field")
    _validate_slug(slug)
    return slug, wt


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_path(args: list[str], dry_run: bool = False) -> None:
    """camp path [--name <slug>] — print worktree directory."""
    slug, wt_path = _resolve_target(args)
    print(str(wt_path))


def cmd_ls(args: list[str]) -> None:
    """camp ls [--json] — list all worktrees (no-group fallback).

    The standalone fallback used when no group resolves from cwd. It reads the
    legacy worktree registry (.workspace-manifest.json), normalizes each entry to
    the shared list shape, and renders via lifecycle.render_workspace_list —
    the SAME renderer the group-aware `camp list` uses — so the human + --json
    surface is identical regardless of cwd. `group` is None here (the
    standalone registry is not group-scoped).
    """
    from .provision.lifecycle import render_workspace_list

    as_json = "--json" in args
    workspace_root = _workspace_root()
    entries = [
        {
            "slug": manifest.get("name", wt_path.name),
            "branch": manifest.get("branch", ""),
            "workspace_path": str(wt_path),
            "group": None,
        }
        for wt_path, manifest in _list_manifests(workspace_root)
    ]
    render_workspace_list(entries, as_json=as_json)


def cmd_help(_args: list[str]) -> None:
    """Print the curated grouped help menu."""
    print(
        "camp — group worktree orchestration\n"
        "\n"
        "Usage:\n"
        "  camp new <slug>                   Create or enter a workspace for a slug\n"
        "  camp pwd <slug>                   Print workspace path\n"
        "\n"
        "Setup:\n"
        "  camp group <name> [options]       Wire hooks and author a group config\n"
        "\n"
        "Workspace commands:\n"
        "  camp list [--json]                List all worktrees (alias: ls)\n"
        "  camp status [--name <slug>]       Show worktree status (git + drift)\n"
        "  camp activate <member>            Activate a member and print its CLAUDE.md\n"
        "  camp setup                        Provision or retry member worktrees\n"
        "  camp remove [--force] [--name <slug>] Tear down a worktree (alias: rm)\n"
        "  camp sync [--force]               Fast-forward canonical siblings to origin/main\n"
        "  camp rebase [--onto <branch>]     Rebase worktree branches onto origin/main\n"
        "  camp foreach [--fail-fast] <cmd>  Run a command in each member worktree\n"
        "\n"
        "Health:\n"
        "  camp doctor [--json]              Read-only workspace health check\n"
        "\n"
        "Flags:\n"
        "  --name <slug>    Target a specific worktree from any cwd\n"
        "  --dry-run        Print what camp would exec; do not run it\n"
        "  CAMP_DRY_RUN=1   Equivalent to --dry-run\n"
    )


# ---------------------------------------------------------------------------
# camp sync — update canonical siblings
# ---------------------------------------------------------------------------


def _sibling_under_workspace(repo_root: Path, workspace_root: Path) -> bool:
    """Validate a canonical sibling path is a proper subdirectory of WORKSPACE_ROOT."""
    try:
        resolved = repo_root.resolve()
        ws = workspace_root.resolve()
    except OSError:
        return False
    resolved_s = str(resolved)
    ws_s = str(ws)
    return resolved_s.startswith(ws_s + os.sep)


def _git_current_branch(repo_root: Path) -> str:
    return _git_out(repo_root, "rev-parse", "--abbrev-ref", "HEAD")


def _git_head_sha(repo_root: Path) -> str:
    return _git_out(repo_root, "rev-parse", "HEAD")


def cmd_sync(args: list[str], dry_run: bool = False) -> None:
    """camp sync [--force] [--json]

    Bring each canonical sibling to latest origin/main and reinstall deps.
    SAFE BY DEFAULT: dirty or off-main siblings are SKIPPED.
    --force reproduces the legacy reset behavior.

    Currently operates on the trailhead repo only; operating on group-config
    members is future work.
    """
    as_json = "--json" in args
    force = "--force" in args

    workspace_root = _workspace_root()
    # Currently trailhead only; group members are a future expansion.
    sibling_repos = [("trailhead", workspace_root / "trailhead")]

    siblings: dict[str, Any] = {}
    moved: list[str] = []
    errors = 0

    for repo, repo_root in sibling_repos:
        entry: dict[str, Any] = {}

        if not (repo_root / ".git").exists():
            entry["action"] = "absent"
            siblings[repo] = entry
            continue

        if not _sibling_under_workspace(repo_root, workspace_root):
            entry["action"] = "rejected"
            entry["rejected"] = True
            siblings[repo] = entry
            continue

        if dry_run:
            _dry_run_print(["git", "-C", str(repo_root), "fetch", "origin", "--quiet"])
        else:
            subprocess.run(
                ["git", "-C", str(repo_root), "fetch", "origin", "--quiet"],
                capture_output=True,
                text=True,
                check=False,
            )

        is_dirty = _git_is_dirty(repo_root)
        branch = _git_current_branch(repo_root)
        on_main = branch == "main"

        if not force and is_dirty:
            entry["action"] = "skip-dirty"
            siblings[repo] = entry
            continue
        if not force and not on_main:
            entry["action"] = "skip-off-main"
            entry["branch"] = branch
            siblings[repo] = entry
            continue

        action = "reset-force" if force else "ff"
        entry["action"] = action
        before = _git_head_sha(repo_root)

        if dry_run:
            if force:
                _dry_run_print(["git", "-C", str(repo_root), "reset", "--hard", "origin/main"])
            else:
                _dry_run_print(["git", "-C", str(repo_root), "merge", "--ff-only", "origin/main"])
            siblings[repo] = entry
            continue

        if force:
            subprocess.run(
                ["git", "-C", str(repo_root), "checkout", "main", "--quiet"],
                capture_output=True,
                text=True,
                check=False,
            )
            update = subprocess.run(
                ["git", "-C", str(repo_root), "reset", "--hard", "origin/main", "--quiet"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            update = subprocess.run(
                ["git", "-C", str(repo_root), "merge", "--ff-only", "origin/main"],
                capture_output=True,
                text=True,
                check=False,
            )

        if update.returncode != 0:
            entry["error"] = update.stderr.strip() or "update failed"
            errors += 1
            siblings[repo] = entry
            continue

        after = _git_head_sha(repo_root)
        if after != before:
            moved.append(repo)

        siblings[repo] = entry

    status = "ok" if errors == 0 else "ok_with_warnings"
    report = {"status": status, "moved": moved, "errors": errors, "siblings": siblings}

    if as_json:
        print(json.dumps(report))
    else:
        print(f"camp sync: status={status} moved={moved} errors={errors}")
        for repo, e in siblings.items():
            print(f"  {repo}: {e.get('action', '?')}")


# ---------------------------------------------------------------------------
# camp foreach
# ---------------------------------------------------------------------------


def cmd_foreach(args: list[str], dry_run: bool = False) -> None:
    """camp foreach [--name <slug>] [--fail-fast] [--json] <cmd…>

    Run <cmd> in each member worktree of the resolved camp. shell=False.
    """
    fail_fast = "--fail-fast" in args
    as_json = "--json" in args
    filtered = [a for a in args if a not in ("--fail-fast", "--json")]

    slug, wt = _resolve_target(filtered)

    manifest_path = wt / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        _die(f"camp foreach: manifest not found at {manifest_path}")
    manifest = _read_manifest(manifest_path)
    if manifest is None:
        _die(f"camp foreach: could not read manifest at {manifest_path}")

    repos = manifest.get("repos") or []
    cmd_list = filtered
    if not cmd_list:
        _die(
            "camp foreach: a command is required\n"
            "  usage: camp foreach [--name <slug>] [--fail-fast] [--json] <cmd…>"
        )

    results: list[dict[str, Any]] = []
    any_failed = False

    for repo_entry in repos:
        if not isinstance(repo_entry, dict):
            continue
        repo_name = repo_entry.get("name", "?")
        wt_path_str = repo_entry.get("worktree_path", "")
        wt_path = Path(wt_path_str) if wt_path_str else wt

        if not wt_path.is_dir():
            if not as_json:
                print(
                    f"camp foreach: skip {repo_name!r} — worktree absent at {wt_path}",
                    file=sys.stderr,
                )
            results.append(
                {
                    "repo": repo_name,
                    "worktree_path": str(wt_path),
                    "skipped": True,
                    "reason": "absent",
                }
            )
            continue

        if dry_run:
            print(f"[dry-run] {repo_name}: would exec in {wt_path}: {cmd_list}", file=sys.stderr)
            results.append(
                {
                    "repo": repo_name,
                    "worktree_path": str(wt_path),
                    "dry_run": True,
                    "argv": cmd_list,
                }
            )
            continue

        proc = subprocess.run(cmd_list, capture_output=True, text=True, cwd=str(wt_path))
        entry: dict[str, Any] = {
            "repo": repo_name,
            "worktree_path": str(wt_path),
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
        results.append(entry)

        if not as_json:
            print(f"=== {repo_name} [exit {proc.returncode}] ===")
            if proc.stdout:
                sys.stdout.write(proc.stdout)
            if proc.stderr:
                sys.stderr.write(proc.stderr)

        if proc.returncode != 0:
            any_failed = True
            if fail_fast:
                break

    if as_json:
        print(json.dumps(results))

    if any_failed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# status helpers
# ---------------------------------------------------------------------------


def _last_commit_epoch(wt_path: Path) -> float | None:
    if not wt_path.is_dir():
        return None
    raw = _git_out(wt_path, "log", "-1", "--format=%ct")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _activity_epoch(repos: list[dict[str, Any]]) -> float | None:
    best: float | None = None
    for repo in repos:
        wt_path_str = repo.get("path", "")
        if not wt_path_str:
            continue
        epoch = _last_commit_epoch(Path(wt_path_str))
        if epoch is not None:
            if best is None or epoch > best:
                best = epoch
    return best


def _build_worktree_entry(
    slug: str,
    manifest: dict[str, Any],
    wt_path: Path,
) -> dict[str, Any]:
    branch = manifest.get("branch", "")
    manifest_path_str = str(wt_path / _MANIFEST_FILENAME)
    dev_env_instance = manifest.get("dev_env_instance")

    repos_json: list[dict[str, Any]] = []
    for repo_entry in manifest.get("repos", []) or []:
        if not isinstance(repo_entry, dict):
            continue
        repo_name = repo_entry.get("name", "")
        wt_path_str = repo_entry.get("worktree_path", "")
        wt = Path(wt_path_str) if wt_path_str else wt_path
        repo_status = _git_repo_status(wt)
        repos_json.append({"name": repo_name, **repo_status})

    return {
        "slug": slug,
        "branch": branch,
        "manifest_path": manifest_path_str,
        "dev_env_instance": dev_env_instance,
        "fire_state": None,
        "repos": repos_json,
    }


def _annotate_stale(worktrees: list[dict[str, Any]], *, threshold_days: int) -> None:
    now = time.time()
    for wt in worktrees:
        repos = wt.get("repos", [])
        epoch = _activity_epoch(repos)
        if epoch is None:
            idle_days = threshold_days
        else:
            elapsed = now - epoch
            idle_days = int(elapsed / 86400)
        wt["idle_days"] = idle_days
        wt["stale"] = idle_days >= threshold_days


def _print_status_human(
    worktrees: list[dict[str, Any]],
    *,
    stale_instances: list[str] | None = None,
    orphaned_git_worktrees: list[dict[str, str]] | None = None,
) -> None:
    if not worktrees:
        print("camp status: no active worktrees — use 'camp <slug>' to create one")
        return

    print(f"{'SLUG':<24}  {'BRANCH':<30}  {'FIRE':<16}  REPOS")
    print("-" * 80)
    for wt in worktrees:
        slug = wt.get("slug", "?")
        branch = wt.get("branch", "")
        fire = wt.get("fire_state") or "none"
        repos = wt.get("repos", [])
        parts = []
        for r in repos:
            name = r.get("name", "?")
            if not r.get("present", True):
                parts.append(f"{name}[MISSING]")
            else:
                dirty = r.get("dirty_files", 0)
                ahead = r.get("unpushed_commits", 0)
                flags = ""
                if dirty:
                    flags += f" +{dirty}dirty"
                if ahead:
                    flags += f" +{ahead}ahead"
                parts.append(f"{name}{flags}")
        repo_str = "  ".join(parts) if parts else "(no repos)"
        stale_marker = ""
        if wt.get("stale"):
            idle = wt.get("idle_days", 0)
            stale_marker = f"  [STALE {idle}d]"
        print(f"{slug:<24}  {branch:<30}  {fire:<16}  {repo_str}{stale_marker}")

    if stale_instances:
        print(
            f"\nDrift: {len(stale_instances)} stale registry instance(s): "
            f"{', '.join(stale_instances)}"
        )


def cmd_status(args: list[str], dry_run: bool = False) -> None:
    """camp status [--name <slug>] [--json] [--stale [--days N]]

    Reconcile manifest membership + per-member git state. Each worktree entry
    retains the dev_env_instance / fire_state keys (null / none) and the drift
    block retains stale_registry_instances / orphaned_git_worktrees (always
    empty) for output-shape stability.
    """
    as_json = "--json" in args
    filtered_args = [a for a in args if a != "--json"]

    check_stale = "--stale" in filtered_args
    filtered_args = [a for a in filtered_args if a != "--stale"]

    stale_days = 7
    i = 0
    while i < len(filtered_args):
        if filtered_args[i] == "--days" and i + 1 < len(filtered_args):
            try:
                stale_days = int(filtered_args[i + 1])
            except ValueError:
                _die(
                    f"camp status: --days requires an integer argument, "
                    f"got {filtered_args[i + 1]!r}"
                )
            del filtered_args[i : i + 2]
            continue
        elif filtered_args[i].startswith("--days="):
            try:
                stale_days = int(filtered_args[i][len("--days=") :])
            except ValueError:
                _die(f"camp status: --days= requires an integer argument, got {filtered_args[i]!r}")
            del filtered_args[i]
            continue
        i += 1

    workspace_root = _workspace_root()

    name = _consume_flag_value(list(filtered_args), "--name")

    if name is not None:
        slug = _resolve_slug(name, context="--name")
        wt_path = _worktree_path_for_slug(slug, workspace_root)
        if not wt_path.is_dir():
            _die(f"camp: no worktree found for slug {slug!r} (looked in {wt_path})")
        manifest_path = wt_path / _MANIFEST_FILENAME
        if not manifest_path.is_file():
            _die(f"camp: manifest not found at {manifest_path}")
        manifest = _read_manifest(manifest_path)
        if manifest is None:
            _die(f"camp: could not read manifest at {manifest_path}")
        entries = [(wt_path, manifest)]
    else:
        cwd = Path.cwd()
        resolved = _resolve_worktree_from(cwd)
        if resolved is not None:
            wt, manifest = resolved
            entries = [(wt, manifest)]
        else:
            entries = _list_manifests(workspace_root)

    worktrees: list[dict[str, Any]] = []
    for wt_path, manifest in entries:
        slug = manifest.get("name", wt_path.name)
        entry = _build_worktree_entry(slug, manifest, wt_path)
        worktrees.append(entry)

    if check_stale:
        _annotate_stale(worktrees, threshold_days=stale_days)

    # Registry-drift detection is a placeholder: no writer of registry.json
    # exists, so there are never stale instances or orphaned git worktrees.
    if as_json:
        output: dict[str, Any] = {
            "worktrees": worktrees,
            "drift": {
                "stale_registry_instances": [],
                "orphaned_git_worktrees": [],
            },
        }
        print(json.dumps(output))
    else:
        _print_status_human(worktrees)


# ---------------------------------------------------------------------------
# camp break
# ---------------------------------------------------------------------------


def _parse_last_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    return {}


def cmd_rebase(args: list[str], dry_run: bool = False) -> None:
    """camp rebase [--onto <branch>] [--name <slug>]"""
    filtered = list(args)
    onto = _consume_flag_value(filtered, "--onto")
    slug, _wt_path = _resolve_target(filtered)

    canonical_root = _canonical_root()
    rebase_script = canonical_root / "scripts" / "pickup-rebase.sh"

    cmd = ["bash", str(rebase_script), slug]
    if onto is not None:
        cmd.extend(["--onto", onto])

    if dry_run:
        _dry_run_print(cmd)
        return

    if not rebase_script.is_file():
        _die(
            f"camp rebase: rebase script not found at {rebase_script}"
        )

    result = subprocess.run(cmd, capture_output=True, text=True)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    parsed = _parse_last_json_line(stdout)
    status = parsed.get("status", "failed" if result.returncode != 0 else "ok")

    if stderr:
        print(stderr, file=sys.stderr)

    if status == "ok":
        name = parsed.get("name") or slug
        print(f"camp rebase: {name!r} rebased successfully")
        return

    if status == "conflict":
        conflicted = parsed.get("conflicted_repo") or "unknown"
        _die(
            f"camp rebase: conflict in repo {conflicted!r}.\n"
            f"  Resolve the conflict in {conflicted!r}, then run 'camp rebase' again."
        )

    _die(f"camp rebase: failed — {parsed.get('reason') or 'unknown error'}")


# ---------------------------------------------------------------------------
# camp doctor — minimal, worktree-relevant checks only
# ---------------------------------------------------------------------------


def _doctor_asdf_present() -> bool:
    seam = os.environ.get("CAMP_TEST_ASDF_PRESENT")
    if seam == "0":
        return False
    if seam == "1":
        return True
    return bool(shutil.which("asdf"))


def cmd_doctor(args: list[str], dry_run: bool = False) -> None:
    """camp doctor [--json]

    Minimal read-only workspace health check (worktree-relevant checks only).
    Dev-env probes (port conflicts, instance checks) are deferred.

    Checks:
      (a) asdf present — asdf resolvable/installed.
      (b) manifest ↔ git-worktree consistency — a placeholder that always
          passes (no writer of registry.json exists to produce drift).
    """
    as_json = "--json" in args

    checks: list[dict[str, Any]] = []
    any_failed = False

    # --- check (a): asdf present ---
    asdf_ok = _doctor_asdf_present()
    if not asdf_ok:
        any_failed = True
    checks.append(
        {
            "check": "asdf",
            "description": "asdf installed and resolvable",
            "pass": asdf_ok,
            "details": "asdf found" if asdf_ok else "asdf not found on PATH",
        }
    )

    # --- check (b): manifest ↔ registry consistency ---
    # Placeholder: no writer of registry.json exists, so drift is never detected.
    stale_ids: list[str] = []
    consistency_ok = not stale_ids
    if not consistency_ok:
        any_failed = True
    checks.append(
        {
            "check": "consistency",
            "description": "manifest ↔ git-worktree ↔ registry consistency",
            "pass": consistency_ok,
            "details": (
                f"stale registry instances: {stale_ids}" if stale_ids else "no drift detected"
            ),
            "stale_registry_instances": stale_ids,
        }
    )

    if as_json:
        report = {"pass": not any_failed, "checks": checks}
        print(json.dumps(report))
    else:
        print("camp doctor:")
        for c in checks:
            status = "PASS" if c["pass"] else "FAIL"
            print(f"  [{status}] {c['description']}")
            if not c["pass"]:
                details = c.get("details")
                if isinstance(details, list):
                    for d in details:
                        print(f"         {d}")
                elif details:
                    print(f"         {details}")

    if any_failed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Canonical verb handlers
# ---------------------------------------------------------------------------

_DISABLED_MESSAGE = (
    "temporarily disabled while the worktree flow stabilizes.\n"
    "  Use 'camp new <slug>' to work with worktrees."
)


def cmd_needs_group(verb: str) -> None:
    """Spine fallback for a NEEDS_GROUP verb (new/remove/pwd/activate/setup).

    These verbs' real behavior lives on the group-aware path in cli/camp; reaching
    spine.main for one of them means no group resolved (no --group flag and cwd is
    outside any member dir). Emit the per-verb "needs a group" error and exit
    non-zero. The exact message text is owned by verb_taxonomy, the single
    source for every per-verb "needs a group" message.
    """
    _die(needs_group_message(verb))


def cmd_disabled(verb: str) -> None:
    """Print the standard disabled message and exit non-zero."""
    print(
        f"camp {verb}: {_DISABLED_MESSAGE}",
        file=sys.stderr,
    )
    sys.exit(1)


def cmd_legacy_redirect(old_verb: str, new_verb: str) -> None:
    """Print a redirect message for a renamed verb and exit non-zero."""
    print(
        f"camp {old_verb}: this command has been renamed — use 'camp {new_verb}' instead.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------


def main() -> None:
    argv = sys.argv[1:]

    dry_run = _is_dry_run(argv)
    argv = [a for a in argv if a != "--dry-run"]

    if not argv:
        cmd_help([])
        return

    # One resolver classifies alias→disabled→legacy in a single
    # defined order, shared with cli/camp's group-aware router, so a token resolves
    # identically at both entry points. Disabled/legacy are handled here up front;
    # a "live" kind falls through to the verb dispatch on the canonical name.
    first, kind = resolve_verb(argv[0])
    rest = argv[1:]

    if kind == "disabled":
        cmd_disabled(first)
        return
    if kind == "legacy":
        cmd_legacy_redirect(first, LEGACY_REDIRECTS[first])
        return

    if first == "list":
        cmd_ls(rest)
    elif first == "foreach":
        cmd_foreach(rest, dry_run=dry_run)
    elif first == "sync":
        cmd_sync(rest, dry_run=dry_run)
    elif first == "doctor":
        cmd_doctor(rest, dry_run=dry_run)
    elif first == "status":
        cmd_status(rest, dry_run=dry_run)
    elif first == "rebase":
        cmd_rebase(rest, dry_run=dry_run)
    elif first == "path":
        cmd_path(rest, dry_run=dry_run)
    elif first in ("help", "--help", "-h"):
        cmd_help(rest)
    # Canonical verb surface — these need a resolved group; reaching spine
    # means none resolved (single NEEDS_GROUP_VERBS source of truth).
    elif first in NEEDS_GROUP_VERBS:
        cmd_needs_group(first)
    else:
        # Bare slug removed: print legible error pointing at camp new
        # (shared message, defined in verb_taxonomy).
        _die(bare_slug_message(first))


if __name__ == "__main__":
    main()
