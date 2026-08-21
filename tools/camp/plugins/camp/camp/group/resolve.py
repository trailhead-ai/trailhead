"""Group resolution for camp — state-dir path-parsing cwd/--group resolution.

Resolution algorithm (state-dir-path-parsing):
1. cwd.relative_to(camp_state_dir) → if len(parts) >= 3 and parts[1] == "worktrees"
   → return (group=parts[0], slug=parts[2]), verifying the group is configured.
   This covers the unified workspace layout:
       central_state_dir(group)/worktrees/<slug>/<member>/...
2. else walk cwd upward looking for a member `repo_root` match in the group
   configs → return (group_name, slug=None). This distinguishes a canonical
   member repo from a non-member dir by pure path arithmetic (no on-disk scan).
3. else raise GroupResolutionError with the legible
   "no group resolved from cwd, pass --group" message.

Both cwd and camp_state_dir are resolved with .resolve() before the prefix check.
camp_state_dir is injectable for hermetic tests; production derives it lazily via
trailhead.paths.state_dir("camp", env=...).

A repo listed in two groups is an error at resolve time and at eager
validate_no_overlap time: raises GroupResolutionError naming both groups + the repo.

group names are validated with validate_group_name before use in any path
construction. Group names are constrained to the same charset as slugs
(``^[a-z0-9-]+$``): this both path-confines them (no separators / '..' / null
bytes) and rules out shell metacharacters, whitespace, and control characters as
a defense-in-depth second layer behind the shellenv ``cd`` wrapper's quoting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# The Step-1 resolver is the canonical owner of camp's state/config paths.
# Imported lazily inside central_state_dir so that module-level import of
# group_resolve.py succeeds even if trailhead is not installed (the import guard
# in cli/camp catches that case before any command runs).


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GroupResolutionError(Exception):
    """Raised when the group cannot be resolved from cwd / --group."""


class GroupConfinementError(Exception):
    """Raised when a group name, or a workspace slug, fails path confinement.

    Mirrors trailhead.paths.PathResolutionError's contract for the group
    segment camp appends to state_dir("camp")/<group>/, and for the slug
    segment workspace_dir appends under it — see validate_group_name and
    validate_workspace_slug.
    """


# ---------------------------------------------------------------------------
# group-name confinement
# ---------------------------------------------------------------------------

# Group names share the slug charset (spine._VALID_SLUG_RE). Replicated rather
# than imported to avoid a cross-domain import for a one-liner rule: lowercase
# letters, digits, and hyphens only.
# `\Z` (not `$`) anchors the END OF STRING: `$` also matches just before a
# trailing newline, which would let a group name like "valid\n" slip through and
# defeat the control-character guarantee.
_VALID_GROUP_RE = re.compile(r"^[a-z0-9-]+\Z")


def validate_group_name(name: str) -> None:
    """Validate that `name` is safe to use as a single path segment.

    Group names are constrained to the slug charset ``^[a-z0-9-]+$``. This
    path-confines them (no separators / '..' / null bytes) AND rules out shell
    metacharacters, whitespace, and control characters, so a group name can never
    carry a payload into the shellenv ``cd`` wrapper that captures the workspace
    path camp prints — a defense-in-depth second layer behind that wrapper's
    quoting. It also keeps group names symmetric with slugs.

    Raises:
        GroupConfinementError: with the bad name in the message.
    """
    if not name:
        raise GroupConfinementError("camp: group name must not be empty")
    if not _VALID_GROUP_RE.match(name):
        raise GroupConfinementError(
            f"camp: group name {name!r} must contain only lowercase letters, "
            "digits, and hyphens (^[a-z0-9-]+$)"
        )


# ---------------------------------------------------------------------------
# workspace-slug confinement
# ---------------------------------------------------------------------------

# A slug consumed from a STORED record (e.g. a session transcript) is untrusted
# in a way a freshly-captured slug is not: spine._resolve_slug validates the tighter
# capture-time charset once, at write time, but a hand-edited or otherwise
# corrupted record bypasses that check entirely and is read back here as plain
# text. This guard only needs to keep the joined path confined under the
# worktrees root — it does not re-impose the wider capture-time charset (a
# stored slug carrying e.g. spaces or shell metacharacters is still a valid,
# resolvable workspace; only a path separator or a literal '.'/'..' segment can
# walk workspace_dir's result outside the root).
_SLUG_PATH_ESCAPE_RE = re.compile(r"[/\\\x00]")


def validate_workspace_slug(slug: str) -> None:
    """Validate that `slug` is safe to join as a single path segment.

    Guards `workspace_dir`'s slug argument, which callers re-derive from a
    STORED record rather than from a slug this process just validated. Rejecting
    here, at the single place all of them call through, protects every current
    and future reader without each one re-implementing the check.

    Raises:
        GroupConfinementError: if slug is empty, contains a path separator or
            NUL byte, or is exactly '.' or '..'.
    """
    if not slug or _SLUG_PATH_ESCAPE_RE.search(slug) or slug in (".", ".."):
        raise GroupConfinementError(
            f"camp: slug {slug!r} is not safe to use as a workspace path segment"
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

    Validates the group name before constructing any path.

    Args:
        group:    Group name. Must pass validate_group_name.
        env:      Override os.environ (for hermetic tests). Defaults to os.environ.
        platform: Override sys.platform (for hermetic tests). Defaults to sys.platform.

    Returns:
        Absolute Path state_dir("camp")/<group>/  (directory may not exist).

    Raises:
        GroupConfinementError: If group name fails confinement validation.
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
# Internal: repo_root matching for the canonical-member-repo fallback
# ---------------------------------------------------------------------------

_WORKTREES_PART = "worktrees"


def _repo_root_matches(candidate: Path, member: dict[str, Any]) -> bool:
    """Return True if candidate matches member's repo_root (resolved)."""
    try:
        declared = Path(member["repo_root"]).resolve()
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return False
    return resolved == declared


def _find_groups_for_repo(repo_path: Path, group_configs: list[dict[str, Any]]) -> list[str]:
    """Return group names where repo_path matches any member's repo_root."""
    matching: list[str] = []
    for cfg in group_configs:
        for m in cfg["members"]:
            if _repo_root_matches(repo_path, m):
                matching.append(cfg["group"]["name"])
                break
    return matching


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
    cwd: Path,
    group_configs: list[dict[str, Any]],
    *,
    camp_state_dir: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str | None]:
    """Resolve (group_name, slug) from cwd via state-dir path parsing.

    Algorithm:
      1. cwd relative to camp_state_dir → if the relative path is
         <group>/worktrees/<slug>/... (len >= 3 and parts[1] == "worktrees"),
         return (group, slug) once the group is confirmed configured.
      2. else walk cwd upward for a member repo_root match → (group, None)
         (canonical member repo fleet-view case).
      3. else GroupResolutionError("no group resolved from cwd, pass --group").

    Args:
        cwd:            Current working directory (absolute path).
        group_configs:  Loaded group config dicts.
        camp_state_dir: The camp state root (state_dir("camp")). If omitted, it is
                        derived lazily from trailhead.paths.state_dir("camp", env=env).
        env:            Optional env override forwarded to the lazy state_dir derive.

    Returns:
        (group_name, slug) where slug may be None for the canonical-member-repo case.

    Raises:
        GroupResolutionError: On no-match or overlap.
    """
    resolved_cwd = cwd.resolve()

    if camp_state_dir is None:
        import trailhead.paths as _paths  # lazy: guard already ran at entry point

        kwargs: dict[str, Any] = {}
        if env is not None:
            kwargs["env"] = env
        camp_state_dir = _paths.state_dir("camp", **kwargs)
    camp_state = camp_state_dir.resolve()

    # Step 1: state-dir prefix parse — <group>/worktrees/<slug>/...
    try:
        rel_parts = resolved_cwd.relative_to(camp_state).parts
    except ValueError:
        rel_parts = ()

    if len(rel_parts) >= 3 and rel_parts[1] == _WORKTREES_PART:
        group_name = rel_parts[0]
        slug = rel_parts[2]
        for cfg in group_configs:
            if cfg["group"]["name"] == group_name:
                validate_group_name(group_name)
                return group_name, slug
        # Under the state dir but no configured group → fall through to the
        # repo_root walk (a stray dir under the state dir is not a workspace).

    # Step 2: canonical-member-repo fallback — walk cwd upward (slug=None).
    current = resolved_cwd
    visited: set[Path] = set()
    while current not in visited:
        visited.add(current)
        matching_groups = _find_groups_for_repo(current, group_configs)
        if len(matching_groups) == 1:
            return matching_groups[0], None
        if len(matching_groups) > 1:
            raise GroupResolutionError(
                f"camp: repo '{current}' is listed in multiple groups: "
                f"{', '.join(sorted(matching_groups))} — each repo must belong to exactly one group"
            )
        parent = current.parent
        if parent == current:
            break
        current = parent

    raise GroupResolutionError("camp: no group resolved from cwd, pass --group")


def resolve_group_override(group_name: str, group_configs: list[dict[str, Any]]) -> dict[str, Any]:
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
