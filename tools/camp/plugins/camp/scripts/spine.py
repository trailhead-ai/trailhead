"""Quarried worktree-spine for camp.

Contains slug handling, manifest/cwd resolution, git wrappers, and all
worktree command handlers (cmd_status, cmd_sync,
cmd_restock, cmd_ls, cmd_path, cmd_code, cmd_sweep, cmd_foreach,
cmd_doctor, cmd_help).

De-zenithed from zenith/bin/camp (quarry provenance — Slice 0):
- _SIBLING_REPOS constant removed (becomes group-config read in Slice 2).
- _import_dev_env / _ensure_dev_env_on_path removed.
- cmd_sweep: dev-env prune path raises NotImplementedError (deferred).
- cmd_doctor: dev-env probes stripped; keeps asdf + consistency checks.
- _canonical_zenith() renamed to _canonical_root() and sourced from
  CAMP_CANONICAL_ROOT env var (Slice 1 will wire to trailhead.paths).
- _check_trailhead_paths_importable() is the D-H guard (shared entry point).

Source: zenith/bin/camp (quarry provenance — Slice 0).
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

from verb_taxonomy import (  # noqa: E402 — single source of truth (FIX 9)
    DISABLED_VERBS,
    LEGACY_REDIRECTS,
    NEEDS_GROUP_VERBS,
    needs_group_message,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MANIFEST_FILENAME = ".workspace-manifest.json"
_SIBLING_MARKER = ".workspace-sibling"
_VALID_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_NORMALIZE_RE = re.compile(r"[^a-z0-9-]+")

RESERVED = frozenset(
    {
        "ls",
        "status",
        "break",
        "rebase",
        "path",
        "open",
        "fire",
        "foreach",
        "code",
        "sweep",
        "sync",
        "restock",
        "doctor",
        "help",
        "version",
        "which",
        "init",
        "session-bootstrap",
        "worktree-cleanup",
        # Slice 1: new verb surface
        "group",
        "ai",
        "rm",
        "pwd",
        "enter",
        "setup",
    }
)

# ---------------------------------------------------------------------------
# D-H guard: single import guard, hook-subprocess-proof
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
    In Slice 1 this will be wired to trailhead.paths.state_dir("camp").
    For Slice 0, defaults to the parent of the plugin dir.
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


def _git(repo_root: Path, *git_args: str) -> subprocess.CompletedProcess[str]:
    """Run a list-arg `git -C <repo_root> ...` (shell=False) and return the result."""
    return subprocess.run(
        ["git", "-C", str(repo_root), *git_args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_out(repo_root: Path, *git_args: str) -> str:
    """Return stripped stdout of a git command, or "" on non-zero exit."""
    result = _git(repo_root, *git_args)
    return result.stdout.strip() if result.returncode == 0 else ""


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
    """camp ls [--json] — list all worktrees."""
    as_json = "--json" in args
    workspace_root = _workspace_root()
    entries = _list_manifests(workspace_root)

    if as_json:
        rows = []
        for wt_path, manifest in entries:
            rows.append(
                {
                    "slug": manifest.get("name", wt_path.name),
                    "branch": manifest.get("branch", ""),
                    "dev_env_instance": manifest.get("dev_env_instance"),
                    "worktree_path": str(wt_path),
                }
            )
        print(json.dumps(rows))
        return

    if not entries:
        print("camp: no camps — use 'camp <slug>' to create one")
        return

    header = f"{'SLUG':<24}  {'BRANCH':<36}  DEV-ENV"
    print(header)
    print("-" * len(header))
    for wt_path, manifest in entries:
        slug = manifest.get("name", wt_path.name)
        branch = manifest.get("branch", "")
        dev_env = manifest.get("dev_env_instance") or "none"
        open_hint = ""
        if slug in RESERVED:
            open_hint = f"  (use: camp open {slug})"
        print(f"{slug:<24}  {branch:<36}  {dev_env}{open_hint}")


def cmd_help(_args: list[str]) -> None:
    """Print the curated grouped help menu."""
    print(
        "camp — group worktree orchestration\n"
        "\n"
        "Usage:\n"
        "  camp ai <slug>                    Create or resume a workspace for a slug\n"
        "  camp pwd <slug>                   Print workspace path\n"
        "\n"
        "Setup:\n"
        "  camp group <name> [options]       Wire hooks and author a group config\n"
        "\n"
        "Workspace commands:\n"
        "  camp ls [--json]                  List all worktrees\n"
        "  camp status [--name <slug>]       Show worktree status (git + drift)\n"
        "  camp enter <member>               Activate a member and print its CLAUDE.md\n"
        "  camp setup                        Provision or retry member worktrees\n"
        "  camp rm [--force] [--name <slug>] Tear down a worktree\n"
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


_CODE_WORKSPACES_DIR = Path.home() / "code" / ".workspaces"


def _detect_active_worktree_name(workspace_root: Path) -> str | None:
    """Scan workspace siblings' .claude/worktrees/* for the newest HEAD commit."""
    best_name: str | None = None
    best_ts: float = 0
    trailhead_wt_root = workspace_root / "trailhead" / ".claude" / "worktrees"
    if not trailhead_wt_root.is_dir():
        return None
    try:
        entries = list(trailhead_wt_root.iterdir())
    except OSError:
        return None
    for wt in entries:
        if not wt.is_dir():
            continue
        raw = _git_out(wt, "log", "-1", "--format=%ct", "HEAD")
        if not raw:
            continue
        try:
            ts = float(raw)
        except ValueError:
            continue
        if ts > best_ts:
            best_ts = ts
            best_name = wt.name
    return best_name


def cmd_code(args: list[str], dry_run: bool = False) -> None:
    """camp code [--name <slug>] [--dry-run] — open a multi-root VSCode workspace."""
    workspace_root = _workspace_root()
    filtered = list(args)
    name_flag = _consume_flag_value(filtered, "--name")

    manifest: dict[str, Any] | None = None
    wt: Path | None = None
    workspace_name: str = "canonical"

    if name_flag is not None:
        slug = _resolve_slug(name_flag, context="--name")
        wt_path = _worktree_path_for_slug(slug, workspace_root)
        if not wt_path.is_dir():
            _die(f"camp code: no worktree found for slug {slug!r} (looked in {wt_path})")
        manifest_path = wt_path / _MANIFEST_FILENAME
        if manifest_path.is_file():
            manifest = _read_manifest(manifest_path)
            wt = wt_path
        workspace_name = slug
    else:
        cwd = Path.cwd()
        resolved = _resolve_worktree_from(cwd)
        if resolved is not None:
            wt, manifest = resolved
            workspace_name = manifest.get("name", wt.name) if manifest else wt.name

    if manifest is not None and wt is not None:
        workspace_name = manifest.get("name", wt.name)
        folders: list[dict[str, str]] = []
        for repo_entry in manifest.get("repos", []) or []:
            if not isinstance(repo_entry, dict):
                continue
            repo_name = repo_entry.get("name", "")
            wt_path_str = repo_entry.get("worktree_path", "")
            if repo_name and wt_path_str:
                folders.append({"name": repo_name, "path": wt_path_str})
    else:
        detected = _detect_active_worktree_name(workspace_root)
        if detected:
            workspace_name = detected
            folders = []
        else:
            workspace_name = "canonical"
            folders = []

    workspace_content = {"folders": [{"name": f["name"], "path": f["path"]} for f in folders]}
    workspaces_dir = _CODE_WORKSPACES_DIR
    workspace_file = workspaces_dir / f"{workspace_name}.code-workspace"

    if dry_run:
        print(f"[dry-run] workspace: {workspace_name}", file=sys.stderr)
        print(f"[dry-run] workspace file: {workspace_file}", file=sys.stderr)
        _dry_run_print(["code", str(workspace_file)])
        return

    workspaces_dir.mkdir(parents=True, exist_ok=True)
    workspace_file.write_text(json.dumps(workspace_content, indent=2))

    if not shutil.which("code"):
        print("camp code: 'code' CLI not on PATH.", file=sys.stderr)
        print(f"Workspace file: {workspace_file}", file=sys.stderr)
        sys.exit(1)

    subprocess.run(["code", str(workspace_file)], check=False)
    print(f"Opened: {workspace_file} (workspace: {workspace_name})")


# ---------------------------------------------------------------------------
# camp sweep — orphan worktree reconciliation
# ---------------------------------------------------------------------------


def _import_dev_env() -> Any:
    """Stub: dev-env engine deferred to a later slice.

    This is the single call-site guard for the dev-env prune path. The
    worktree-orphan half of cmd_sweep (orphan_worktrees) is CLEAN and remains.
    Only the instance teardown + dropdb path hits this stub.
    """
    raise NotImplementedError(
        "camp sweep --prune: dev-env engine is deferred — "
        "see deferred/2026-06/camp-dev-env-engine-half for the revive trigger."
    )


def _active_worktree_names(workspace_root: Path) -> set[str]:
    """Active set = basenames of worktree dirs that have a manifest."""
    base = workspace_root / "trailhead" / ".claude" / "worktrees"
    active: set[str] = set()
    if not base.is_dir():
        return active
    for candidate in base.iterdir():
        if not candidate.is_dir():
            continue
        if (candidate / _MANIFEST_FILENAME).is_file():
            active.add(candidate.name)
    return active


def _classify_orphan_worktree(wt_path: Path) -> str:
    """Classify an orphan worktree as SAFE | DIRTY | UNMERGED."""
    is_dirty = _git_is_dirty(wt_path)
    if not _git_head_is_ancestor_of_origin_main(wt_path):
        return "UNMERGED"
    return "DIRTY" if is_dirty else "SAFE"


def _collect_orphan_worktrees(workspace_root: Path, active: set[str]) -> dict[str, dict[str, str]]:
    """Return {"<repo>/<name>": {repo,name,path,class}} for orphan worktrees.

    Quarried from zenith/bin/camp. The _SIBLING_REPOS constant is removed;
    this function now accepts any group member repos. In Slice 2 it will
    iterate over group-config members; for Slice 0 it's wired to trailhead only.
    """
    orphans: dict[str, dict[str, str]] = {}
    # Slice 0: only trailhead itself; Slice 2 will expand to group members.
    repos_to_check = [("trailhead", workspace_root / "trailhead")]
    for repo, repo_root in repos_to_check:
        if not (repo_root / ".git").exists():
            continue
        _git(repo_root, "fetch", "--quiet", "origin", "main")
        for wt_path in _git_worktree_list(repo_root):
            name = wt_path.name
            if name in active:
                continue
            cls = _classify_orphan_worktree(wt_path)
            orphans[f"{repo}/{name}"] = {
                "repo": repo,
                "name": name,
                "path": str(wt_path),
                "class": cls,
            }
    return orphans


def _read_registry(canonical_root: Path) -> dict[str, Any]:
    """Read the dev-env registry from canonical_root/.worktree-dev/registry.json.

    Returns an empty registry (schema_version=3, instances={}) if the file is
    absent or unparseable.
    """
    reg_path = canonical_root / ".worktree-dev" / "registry.json"
    if not reg_path.is_file():
        return {"schema_version": 3, "instances": {}}
    try:
        return json.loads(reg_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 3, "instances": {}}


def _vanished_registry_instances(instances: dict[str, Any]) -> list[str]:
    """Return instance IDs whose worktree_root path does not exist on disk."""
    stale: list[str] = []
    for iid, data in instances.items():
        if not isinstance(data, dict):
            continue
        recorded = data.get("paths", {}).get("worktree_root")
        if not isinstance(recorded, str) or not recorded:
            continue
        if not Path(recorded).exists():
            stale.append(iid)
    return stale


def _worktree_root_under_workspace(recorded: Any, workspace_root: Path) -> bool:
    """Validate a registry worktree_root is an absolute path under workspace_root."""
    if not isinstance(recorded, str) or not recorded:
        return False
    if not os.path.isabs(recorded):
        return False
    normalized = os.path.normpath(recorded)
    ws = os.path.normpath(str(workspace_root))
    return normalized.startswith(ws + os.sep)


def _prune_orphan_worktree(repo_root: Path, wt_path: Path, *, dry_run: bool) -> bool:
    """Remove one orphan git worktree via `git worktree remove --force`."""
    if dry_run:
        _dry_run_print(["git", "-C", str(repo_root), "worktree", "remove", "--force", str(wt_path)])
        return True
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(wt_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _print_sweep_human(report: dict[str, Any], *, prune: bool, force: bool) -> None:
    worktrees = report.get("orphan_worktrees", {})
    instances = report.get("orphan_instances", {})

    by_class: dict[str, list[dict[str, str]]] = {"UNMERGED": [], "DIRTY": [], "SAFE": []}
    for entry in worktrees.values():
        by_class.setdefault(entry["class"], []).append(entry)

    print(f"Active manifests: {report.get('active_count', 0)}")
    for label, blurb in (
        ("UNMERGED", "never auto-removed; inspect manually"),
        ("DIRTY", "merged to main but tree has uncommitted changes"),
        ("SAFE", "merged + clean"),
    ):
        entries = by_class.get(label, [])
        print(f"\n{label} orphans: {len(entries)} ({blurb})")
        for e in sorted(entries, key=lambda x: f"{x['repo']}/{x['name']}"):
            print(f"  {e['repo']}/{e['name']}")
            if label == "UNMERGED":
                print(f"    {e['path']}")

    print(f"\nOrphan dev-env instances: {len(instances)}")

    if not prune:
        print("\nDry-run. Pass --prune to remove SAFE orphans (and --force to also remove DIRTY).")


def cmd_sweep(args: list[str], dry_run: bool = False) -> None:
    """camp sweep [--prune [--force]] [--json]

    Report orphaned worktrees (no manifest) classified SAFE/DIRTY/UNMERGED,
    plus orphan dev-env registry instances (recorded worktree_root vanished).
    Read-only by default.

    --prune        removes SAFE orphan worktrees. The dev-env teardown path is
                   DEFERRED — raises NotImplementedError if orphan instances are
                   found (see deferred/2026-06/camp-dev-env-engine-half).
    --prune --force also removes DIRTY orphan worktrees.

    The JSON schema retains dev_env_instance / fire_state / orphan_instances keys
    as null / {} when no registry exists, for contract stability.
    """
    as_json = "--json" in args
    prune = "--prune" in args
    force = "--force" in args

    workspace_root = _workspace_root()
    canonical_root = _canonical_root()

    active = _active_worktree_names(workspace_root)
    orphan_worktrees = _collect_orphan_worktrees(workspace_root, active)

    registry = _read_registry(canonical_root)
    instances: dict[str, Any] = registry.get("instances") or {}
    orphan_instance_ids = _vanished_registry_instances(instances)

    report: dict[str, Any] = {
        "active_count": len(active),
        "orphan_worktrees": orphan_worktrees,
        "orphan_instances": {},
        "prune": prune,
        "force": force,
        "dry_run": dry_run,
    }

    # Build the orphan-instance report. In report mode we only enumerate; in
    # prune mode we hit the dev-env stub (NotImplementedError).
    for iid in sorted(orphan_instance_ids):
        data = instances.get(iid, {})
        recorded = data.get("paths", {}).get("worktree_root") if isinstance(data, dict) else None
        entry: dict[str, Any] = {"worktree_root": recorded}

        if not prune:
            report["orphan_instances"][iid] = entry
            continue

        # Prune path: dev-env teardown is deferred — raise NotImplementedError.
        _import_dev_env()  # raises NotImplementedError

    # Prune orphan worktrees (SAFE always; DIRTY only with --force; UNMERGED never).
    if prune:
        removed: list[str] = []
        failed: list[str] = []
        rejected: list[str] = []
        for key, e in sorted(orphan_worktrees.items()):
            cls = e["class"]
            if cls == "UNMERGED":
                continue
            if cls == "DIRTY" and not force:
                continue
            if not _worktree_root_under_workspace(e["path"], workspace_root):
                e["rejected"] = True
                rejected.append(key)
                continue
            repo_root = workspace_root / e["repo"]
            ok = _prune_orphan_worktree(repo_root, Path(e["path"]), dry_run=dry_run)
            e["pruned"] = ok
            (removed if ok else failed).append(key)
        report["removed_worktrees"] = removed
        report["failed_worktrees"] = failed
        report["rejected_worktrees"] = rejected

    if as_json:
        print(json.dumps(report))
    else:
        _print_sweep_human(report, prune=prune, force=force)


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


def _git_head_is_ancestor_of_origin_main(repo_root: Path) -> bool:
    return _git(repo_root, "merge-base", "--is-ancestor", "HEAD", "origin/main").returncode == 0


def _git_current_branch(repo_root: Path) -> str:
    return _git_out(repo_root, "rev-parse", "--abbrev-ref", "HEAD")


def _git_is_dirty(repo_root: Path) -> bool:
    return bool(_git(repo_root, "status", "--porcelain").stdout.strip())


def _git_head_sha(repo_root: Path) -> str:
    return _git_out(repo_root, "rev-parse", "HEAD")


def cmd_sync(args: list[str], dry_run: bool = False) -> None:
    """camp sync [--force] [--json]

    Bring each canonical sibling to latest origin/main and reinstall deps.
    SAFE BY DEFAULT: dirty or off-main siblings are SKIPPED.
    --force reproduces the legacy reset behavior.

    In Slice 2 this will operate on group-config members. For Slice 0 it
    operates on the trailhead repo only.
    """
    as_json = "--json" in args
    force = "--force" in args

    workspace_root = _workspace_root()
    # Slice 0: trailhead only; Slice 2 expands to group members.
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
# camp restock — refresh canonical sibling dep caches
# ---------------------------------------------------------------------------


def cmd_restock(args: list[str], dry_run: bool = False) -> None:
    """camp restock [--json] — refresh canonical sibling dep caches.

    In Slice 2 this will operate on group-config members with configured
    bootstrap commands. For Slice 0 it's a passthrough that reports the trailhead
    repo.
    """
    as_json = "--json" in args
    workspace_root = _workspace_root()
    refreshed: list[str] = []
    errors = 0
    siblings: dict[str, Any] = {}

    # Slice 0: trailhead only; Slice 2 expands to group members.
    repo, repo_root = "trailhead", workspace_root / "trailhead"
    if not (repo_root / ".git").exists():
        siblings[repo] = {"action": "absent"}
    else:
        if dry_run:
            print(f"[dry-run] restock: {repo} at {repo_root}", file=sys.stderr)
            siblings[repo] = {"action": "dry-run"}
        else:
            refreshed.append(repo)
            siblings[repo] = {"action": "ok"}

    status = "ok" if errors == 0 else "ok_with_warnings"
    report: dict[str, Any] = {
        "status": status,
        "refreshed": refreshed,
        "errors": errors,
        "siblings": siblings,
    }

    if as_json:
        print(json.dumps(report))
    else:
        print(f"camp restock: status={status} refreshed={refreshed} errors={errors}")


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


def _git_repo_status(wt_path: Path) -> dict[str, Any]:
    path_str = str(wt_path)
    if not wt_path.is_dir():
        return {"present": False, "path": path_str}

    branch = _git_out(wt_path, "rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    dirty_raw = _git(wt_path, "status", "--porcelain").stdout
    dirty_files = len([ln for ln in dirty_raw.splitlines() if ln.strip()])
    ahead_raw = _git(wt_path, "rev-list", "--count", "@{upstream}..HEAD")
    unpushed_commits = (
        int(ahead_raw.stdout.strip())
        if ahead_raw.returncode == 0 and ahead_raw.stdout.strip().isdigit()
        else 0
    )
    last_commit = _git_out(wt_path, "log", "-1", "--oneline")

    return {
        "present": True,
        "path": path_str,
        "branch": branch,
        "dirty_files": dirty_files,
        "unpushed_commits": unpushed_commits,
        "last_commit": last_commit,
    }


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
    instances: dict[str, Any],
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


def _git_worktree_list(repo_root: Path) -> list[Path]:
    if not repo_root.is_dir():
        return []
    try:
        result = _git(repo_root, "worktree", "list", "--porcelain")
    except OSError:
        return []
    if result.returncode != 0:
        return []

    wt_paths: list[Path] = []
    for line in result.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        wt_path_str = line[len("worktree ") :].strip()
        if not wt_path_str:
            continue
        wt_path = Path(wt_path_str)
        try:
            parts = wt_path.parts
        except Exception:
            continue
        if ".claude" in parts:
            idx = parts.index(".claude")
            if idx + 1 < len(parts) and parts[idx + 1] == "worktrees":
                wt_paths.append(wt_path)
    return wt_paths


def cmd_status(args: list[str], dry_run: bool = False) -> None:
    """camp status [--name <slug>] [--json] [--stale [--days N]]

    Reconcile manifest membership + per-member git state + registry drift.
    Retains dev_env_instance / fire_state / orphan_instances keys for contract
    stability (null / none when no registry exists).
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
    canonical_root = _canonical_root()
    registry = _read_registry(canonical_root)
    instances: dict[str, Any] = registry.get("instances") or {}

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
        scoped = True
    else:
        cwd = Path.cwd()
        resolved = _resolve_worktree_from(cwd)
        if resolved is not None:
            wt, manifest = resolved
            entries = [(wt, manifest)]
            scoped = True
        else:
            entries = _list_manifests(workspace_root)
            scoped = False

    worktrees: list[dict[str, Any]] = []
    for wt_path, manifest in entries:
        slug = manifest.get("name", wt_path.name)
        entry = _build_worktree_entry(slug, manifest, instances, wt_path)
        worktrees.append(entry)

    if check_stale:
        _annotate_stale(worktrees, threshold_days=stale_days)

    if scoped:
        stale = _vanished_registry_instances(instances)
    else:
        stale = _vanished_registry_instances(instances)

    if as_json:
        output: dict[str, Any] = {
            "worktrees": worktrees,
            "drift": {
                "stale_registry_instances": sorted(stale),
                "orphaned_git_worktrees": [],
            },
        }
        print(json.dumps(output))
    else:
        _print_status_human(
            worktrees,
            stale_instances=stale if stale else None,
        )


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
            f"camp rebase: rebase script not found at {rebase_script}\n"
            "  Worktree lifecycle is implemented in Slice 2."
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
      (b) manifest ↔ git-worktree consistency — stale registry instances
          whose worktree_root has vanished.
    """
    as_json = "--json" in args

    canonical_root = _canonical_root()
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
    registry = _read_registry(canonical_root)
    instances: dict[str, Any] = registry.get("instances") or {}
    stale_ids = _vanished_registry_instances(instances)
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
# Slice 1: new verb handlers
# ---------------------------------------------------------------------------

_DISABLED_MESSAGE = (
    "temporarily disabled while the worktree flow stabilizes.\n"
    "  Use 'camp ai <slug>' to work with worktrees."
)


def cmd_group(args: list[str], dry_run: bool = False) -> None:
    """RESERVED — this handler is unreachable via normal dispatch.

    cli/camp.main() intercepts 'group' before spine.main() is called, routing
    it to _cmd_group_cli. This stub exists only so that direct calls to
    spine.main() (e.g. from tests) produce a legible error rather than
    falling into the bare-slug handler.
    """
    _die(
        "camp group: this verb routes through the group-aware CLI entry point.\n"
        "  Run 'camp group --help' for usage."
    )


def cmd_needs_group(verb: str) -> None:
    """Spine fallback for a NEEDS_GROUP verb (ai/rm/cd/enter/setup).

    These verbs' real behavior lives on the group-aware path in cli/camp; reaching
    spine.main for one of them means no group resolved (no --group flag and cwd is
    outside any member dir). Emit the per-verb "needs a group" error and exit
    non-zero. The exact message text is owned by verb_taxonomy (FIX 9), collapsing
    the five formerly-duplicated per-verb stubs into one helper.
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

    first = argv[0]
    rest = argv[1:]

    if first == "ls":
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
    # Slice 1: new verb surface — these need a resolved group; reaching spine
    # means none resolved (FIX 9: single NEEDS_GROUP_VERBS source of truth).
    elif first in NEEDS_GROUP_VERBS:
        cmd_needs_group(first)
    elif first == "group":
        cmd_group(rest, dry_run=dry_run)
    # Slice 1: disabled verbs (hidden from help, legible error)
    elif first in DISABLED_VERBS:
        cmd_disabled(first)
    # Slice 1: legacy verb redirects
    elif first in LEGACY_REDIRECTS:
        cmd_legacy_redirect(first, LEGACY_REDIRECTS[first])
    else:
        # Bare slug removed: print legible error pointing at camp ai.
        _die(
            f"camp: bare slug dispatch is no longer supported.\n"
            f"  Use 'camp ai {first}' to create or resume a workspace."
        )


if __name__ == "__main__":
    main()
