"""Group resolution for camp — marker-first cwd/--group resolution (D-G).

Resolution algorithm (marker-first):
1. Walk the cwd path from the deepest point upward, collecting every
   `…/.claude/worktrees/<slug>` segment found.
2. For each such segment (innermost first), check whether the parent matches
   a member `repo_root` in any loaded group config.
3. The first match wins: return (group_name, slug).
4. If no worktree segment matched any group, fall back to a plain repo-root
   walk (same path, no `.claude/worktrees/` in it) — this is the fleet-view
   path: returns (group_name, slug=None).
5. If still no match: raise GroupResolutionError with the legible
   "no group resolved from cwd, pass --group" message.

A repo listed in two groups is an error at resolve time and at eager
validate_no_overlap time: raises GroupResolutionError naming both groups + the repo.

group names are validated with validate_group_name before use in any path
construction (D-E confinement).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# The Step-1 resolver is the canonical owner of camp's state/config paths.
# Imported lazily inside central_state_dir so that module-level import of
# group_resolve.py succeeds even if trailhead is not installed (the D-H guard
# in cli/camp catches that case before any command runs).


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GroupResolutionError(Exception):
    """Raised when the group cannot be resolved from cwd / --group."""


class GroupConfinementError(Exception):
    """Raised when a group name fails the path-confinement check (D-E).

    Mirrors trailhead.paths.PathResolutionError's contract for the group
    segment camp appends to state_dir("camp")/<group>/.
    """


# ---------------------------------------------------------------------------
# D-E: group-name confinement
# ---------------------------------------------------------------------------

_INVALID_GROUP_CHARS = frozenset(("/", "\\", ".."))


def validate_group_name(name: str) -> None:
    """Validate that `name` is safe to use as a single path segment.

    Rejects names containing:
      - path separators (/ or \\)
      - '..' components
      - null bytes or other control characters

    Raises:
        GroupConfinementError: with the bad name in the message.

    This mirrors the paths._validate_app rule for the <group> segment camp
    appends to state_dir("camp")/<group>/. We replicate rather than import
    _validate_app because that is a private helper; the rule is identical.
    """
    if not name:
        raise GroupConfinementError(
            "camp: group name must not be empty"
        )
    if (
        "/" in name
        or "\\" in name
        or ".." in name
        or os.sep in name
        or "\x00" in name
    ):
        raise GroupConfinementError(
            f"camp: group name {name!r} must not contain path separators, "
            "backslashes, '..', or null bytes (D-E confinement)"
        )


# ---------------------------------------------------------------------------
# Central state path — via trailhead.paths exclusively (no __file__ anchor)
# ---------------------------------------------------------------------------


def central_state_dir(
    group: str,
    *,
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Return the central state directory for a group: state_dir("camp")/<group>/.

    Validates the group name before constructing any path (D-E).

    Args:
        group:    Group name. Must pass validate_group_name.
        env:      Override os.environ (for hermetic tests). Defaults to os.environ.
        platform: Override sys.platform (for hermetic tests). Defaults to sys.platform.

    Returns:
        Absolute Path state_dir("camp")/<group>/  (directory may not exist).

    Raises:
        GroupConfinementError: If group name fails D-E validation.
        trailhead.paths.PathResolutionError: If the resolver cannot derive the dir.
    """
    validate_group_name(group)
    import trailhead.paths as _paths  # lazy: guard already ran at entry point

    kwargs: dict[str, Any] = {}
    if env is not None:
        kwargs["env"] = env
    if platform is not None:
        kwargs["platform"] = platform
    base = _paths.state_dir("camp", **kwargs)
    return base / group


# ---------------------------------------------------------------------------
# Internal: walk cwd for .claude/worktrees/<slug> segments
# ---------------------------------------------------------------------------

_WORKTREE_MARKER = ".claude/worktrees"
_CLAUDE_PART = ".claude"
_WORKTREES_PART = "worktrees"


def _collect_worktree_segments(cwd: Path) -> list[tuple[Path, str]]:
    """Walk cwd from deepest to shallowest, collecting (parent_of_segment, slug)
    for every `…/.claude/worktrees/<slug>` found in the path.

    Returns list of (repo_candidate, slug) in innermost-first order.

    The `repo_candidate` is the path immediately above the `.claude/` directory
    (i.e. the repo root the worktree belongs to).
    """
    parts = cwd.resolve().parts  # tuple of path components
    segments: list[tuple[Path, str]] = []

    for i, part in enumerate(parts):
        if part == _WORKTREES_PART and i >= 2:
            # Check that parts[i-1] == ".claude" and i+1 exists (slug)
            if parts[i - 1] == _CLAUDE_PART and i + 1 < len(parts):
                slug = parts[i + 1]
                # repo_candidate = everything up to (but not including) .claude/
                repo_candidate = Path(*parts[: i - 1])
                segments.append((repo_candidate, slug))

    # innermost first = last found in left-to-right scan
    segments.reverse()
    return segments


def _repo_root_matches(candidate: Path, member: dict[str, Any]) -> bool:
    """Return True if candidate matches member's repo_root (resolved)."""
    try:
        declared = Path(member["repo_root"]).resolve()
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return False
    return resolved == declared


def _find_groups_for_repo(
    repo_path: Path, group_configs: list[dict[str, Any]]
) -> list[str]:
    """Return group names where repo_path matches any member's repo_root."""
    matching: list[str] = []
    for cfg in group_configs:
        for m in cfg["members"]:
            if _repo_root_matches(repo_path, m):
                matching.append(cfg["group"]["name"])
                break
    return matching


def _find_groups_for_cwd_direct(
    cwd: Path, group_configs: list[dict[str, Any]]
) -> list[str]:
    """Return group names where cwd or any of its parents is a member repo_root."""
    current = cwd.resolve()
    visited: set[Path] = set()
    while current not in visited:
        visited.add(current)
        groups = _find_groups_for_repo(current, group_configs)
        if groups:
            return groups
        parent = current.parent
        if parent == current:
            break
        current = parent
    return []


# ---------------------------------------------------------------------------
# Overlap validation (eager, for `camp config validate` and at resolve time)
# ---------------------------------------------------------------------------


def validate_no_overlap(group_configs: list[dict[str, Any]]) -> None:
    """Raise GroupResolutionError if any repo_root appears in more than one group.

    Args:
        group_configs: list of parsed group config dicts.

    Raises:
        GroupResolutionError: naming both groups + the repo_root.
    """
    # Map repo_root → list of group names
    seen: dict[str, list[str]] = {}
    for cfg in group_configs:
        gname = cfg["group"]["name"]
        for m in cfg["members"]:
            rr = str(Path(m["repo_root"]).resolve())
            seen.setdefault(rr, []).append(gname)

    for rr, groups in seen.items():
        if len(groups) > 1:
            raise GroupResolutionError(
                f"camp: repo '{rr}' is listed in multiple groups: "
                f"{', '.join(sorted(groups))} — each repo must belong to exactly one group"
            )


# ---------------------------------------------------------------------------
# Public resolution API
# ---------------------------------------------------------------------------


def resolve_from_cwd(
    cwd: Path, group_configs: list[dict[str, Any]]
) -> tuple[str, str | None]:
    """Resolve (group_name, slug) from cwd using marker-first resolution (D-G).

    Algorithm:
      1. Collect all .claude/worktrees/<slug> segments in cwd (innermost first).
      2. For each segment, confirm parent matches a member repo_root in some group.
         - If one match → return (group, slug).
         - If two or more groups match the same repo → error (overlap).
      3. If no worktree segment matched, walk cwd upward looking for a plain
         repo-root match → return (group, None) for the fleet-view fallback.
      4. If still no match → GroupResolutionError("no group resolved from cwd, pass --group").

    Args:
        cwd:           Current working directory (absolute path).
        group_configs: Loaded group config dicts.

    Returns:
        (group_name, slug) where slug may be None for the repo-root fleet-view case.

    Raises:
        GroupResolutionError: On no-match or overlap.
    """
    # Step 1: marker-first — check .claude/worktrees/<slug> segments
    segments = _collect_worktree_segments(cwd)
    for repo_candidate, slug in segments:
        matching_groups = _find_groups_for_repo(repo_candidate, group_configs)
        if len(matching_groups) == 1:
            return matching_groups[0], slug
        if len(matching_groups) > 1:
            # Overlap: same repo in multiple groups
            raise GroupResolutionError(
                f"camp: repo '{repo_candidate}' is listed in multiple groups: "
                f"{', '.join(sorted(matching_groups))} — each repo must belong to exactly one group"
            )

    # Step 2: fleet-view fallback — plain repo-root walk (slug=None)
    current = cwd.resolve()
    visited: set[Path] = set()
    while current not in visited:
        visited.add(current)
        matching_groups = _find_groups_for_repo(current, group_configs)
        if len(matching_groups) == 1:
            return matching_groups[0], None
        if len(matching_groups) > 1:
            repo_str = str(current)
            raise GroupResolutionError(
                f"camp: repo '{repo_str}' is listed in multiple groups: "
                f"{', '.join(sorted(matching_groups))} — each repo must belong to exactly one group"
            )
        parent = current.parent
        if parent == current:
            break
        current = parent

    raise GroupResolutionError(
        "camp: no group resolved from cwd, pass --group"
    )


def resolve_group_override(
    group_name: str, group_configs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return the group config matching group_name (for --group override).

    Args:
        group_name:    The requested group name.
        group_configs: Loaded group config dicts.

    Returns:
        The matching group config dict.

    Raises:
        GroupResolutionError: If no group with that name exists.
    """
    for cfg in group_configs:
        if cfg["group"]["name"] == group_name:
            return cfg
    known = [c["group"]["name"] for c in group_configs]
    raise GroupResolutionError(
        f"camp: unknown group {group_name!r} (known: {', '.join(known) or 'none'})"
    )
