"""TOML rendering and pre-write validation for camp group config authoring.

Owns three responsibilities:
- render_group_toml: hand-render a deterministic TOML string for [group] /
  [[members]] / [branch]; re-parsed by load_group for round-trip validation.
- build_stub_toml: emit a commented placeholder template the user edits before
  running `camp init <group> --member ...`.
- validate_scaffold: run group-name confinement, repo-root existence/git check,
  and no-overlap across groups before any file is written.

No file I/O in this module — callers write the rendered string atomically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ScaffoldError(Exception):
    """Raised when pre-write validation of a group scaffold fails."""


# ---------------------------------------------------------------------------
# TOML basic-string escaping
# ---------------------------------------------------------------------------


def _escape_toml_basic_string(value: str) -> str:
    r"""Escape a string for use inside TOML double-quoted basic strings.

    TOML basic-string escape sequences (spec 1.0):
      \\, \", \b, \f, \n, \r, \t, \uXXXX, \UXXXXXXXX
    """
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("\b", "\\b")
    value = value.replace("\f", "\\f")
    value = value.replace("\n", "\\n")
    value = value.replace("\r", "\\r")
    value = value.replace("\t", "\\t")
    return value


def _toml_string(value: str) -> str:
    """Return value as a TOML basic string (double-quoted, escaped)."""
    return f'"{_escape_toml_basic_string(value)}"'


# ---------------------------------------------------------------------------
# Member path normalization
# ---------------------------------------------------------------------------


def _normalize_repo_root(repo_root: str) -> str:
    """Expand ~ and resolve to an absolute path string.

    Path.resolve() does NOT expand ~; expanduser() must come first.
    """
    return str(Path(repo_root).expanduser().resolve())


def _normalize_members(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a new list of member dicts with repo_root expanded and resolved."""
    result = []
    for m in members:
        result.append(
            {
                "name": m["name"],
                "repo_root": _normalize_repo_root(m["repo_root"]),
            }
        )
    return result


# ---------------------------------------------------------------------------
# render_group_toml
# ---------------------------------------------------------------------------


def render_group_toml(
    group_name: str,
    members: list[dict[str, Any]],
    branch_pattern: str,
    lore_scopes: list[dict[str, Any]] | None = None,
    extra_tables: dict[str, Any] | None = None,
) -> str:
    """Render a deterministic TOML string for a group config.

    Emits the core schema:
      [group].name
      [[members]] name, repo_root, bootstrap = []
      [branch].pattern
    plus any [[lore_scopes]] (scope, name) entries supplied — so re-authoring a
    group preserves a hand-added binding instead of silently dropping it.

    `extra_tables` generalizes that same carry-through to every OTHER top-level
    table this function does not itself know how to render (e.g. `[tasks.*]`,
    `[harness]`, `[release]`, `[[shared_vaults]]`) — a raw tomllib-parsed dict
    keyed by top-level table name, re-emitted generically (nested tables, arrays
    of tables, and scalar/array values) rather than by adding another
    table-specific parameter. Callers are expected to pass the raw parse of the
    existing config, minus the keys this function already renders itself
    ("group", "members", "branch", "lore_scopes"), so a --force re-author never
    silently drops a hand-added table.

    Args:
        group_name:     Group name for [group].name.
        members:        List of dicts with "name" and "repo_root" keys.
                        repo_root values are passed through expanduser().resolve().
        branch_pattern: Value for [branch].pattern.
        lore_scopes:    Optional list of {"scope", "name"} dicts to emit as
                        [[lore_scopes]] entries (declared order preserved).
        extra_tables:   Optional dict of {table_name: value} for any other
                        top-level table to re-emit verbatim (value is a dict for
                        a table, or a list of dicts for an array of tables).

    Returns:
        A TOML string that round-trips through load_group.
    """
    normalized = _normalize_members(members)

    lines: list[str] = []

    lines.append("[group]")
    lines.append(f"name = {_toml_string(group_name)}")
    lines.append("")

    for m in normalized:
        lines.append("[[members]]")
        lines.append(f"name = {_toml_string(m['name'])}")
        lines.append(f"repo_root = {_toml_string(m['repo_root'])}")
        lines.append("bootstrap = []")
        lines.append("")

    lines.append("[branch]")
    lines.append(f"pattern = {_toml_string(branch_pattern)}")
    lines.append("")

    for ls in lore_scopes or []:
        lines.append("[[lore_scopes]]")
        lines.append(f"scope = {_toml_string(ls['scope'])}")
        lines.append(f"name = {_toml_string(ls['name'])}")
        lines.append("")

    for table_name, value in (extra_tables or {}).items():
        if isinstance(value, list):
            _render_array_table(table_name, value, lines)
        elif isinstance(value, dict):
            _render_table(table_name, value, lines)
        else:
            raise TypeError(
                f"extra_tables[{table_name!r}] must be a dict or list of dicts, "
                f"got {type(value).__name__}"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generic TOML sub-tree serialization (for extra_tables carry-through)
# ---------------------------------------------------------------------------


def _toml_value(value: Any) -> str:
    """Render a scalar or list-of-scalars as a TOML value literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value type: {type(value).__name__}")


def _split_table(table: dict[str, Any]) -> tuple[dict, dict, dict]:
    """Split a table dict into (scalars, subtables, array-of-subtables)."""
    scalars: dict[str, Any] = {}
    subtables: dict[str, Any] = {}
    array_tables: dict[str, Any] = {}
    for key, value in table.items():
        if isinstance(value, dict):
            subtables[key] = value
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            array_tables[key] = value
        else:
            scalars[key] = value
    return scalars, subtables, array_tables


def _render_table(dotted_key: str, table: dict[str, Any], lines: list[str]) -> None:
    """Append a `[dotted_key]` table (and any nested tables) to lines."""
    scalars, subtables, array_tables = _split_table(table)

    lines.append(f"[{dotted_key}]")
    for key, value in scalars.items():
        lines.append(f"{key} = {_toml_value(value)}")
    lines.append("")

    for key, value in subtables.items():
        _render_table(f"{dotted_key}.{key}", value, lines)
    for key, value in array_tables.items():
        _render_array_table(f"{dotted_key}.{key}", value, lines)


def _render_array_table(dotted_key: str, items: list[dict[str, Any]], lines: list[str]) -> None:
    """Append `[[dotted_key]]` entries (and any nested tables) to lines."""
    for item in items:
        scalars, subtables, array_tables = _split_table(item)

        lines.append(f"[[{dotted_key}]]")
        for key, value in scalars.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")

        for key, value in subtables.items():
            _render_table(f"{dotted_key}.{key}", value, lines)
        for key, value in array_tables.items():
            _render_array_table(f"{dotted_key}.{key}", value, lines)


# ---------------------------------------------------------------------------
# build_stub_toml
# ---------------------------------------------------------------------------


def build_stub_toml(group_name: str) -> str:
    """Build a commented placeholder TOML template for a new group config.

    The [group].name is filled in with the actual group_name. The [[members]]
    section uses placeholder values for the user to fill in. The template is
    valid TOML (comments stripped) so it can be parsed immediately.

    Args:
        group_name: The group name to fill into [group].name.

    Returns:
        A TOML string (with comments) containing the group scaffold.
    """
    lines: list[str] = [
        "# camp group config — edit repo_root values then run:",
        f"# camp init {group_name} --member NAME=PATH [...]",
        "",
        "[group]",
        f"name = {_toml_string(group_name)}",
        "",
        "[[members]]",
        '# name = "my-repo"',
        '# repo_root = "/absolute/path/to/repo"',
        "bootstrap = []",
        "",
        "[branch]",
        'pattern = "worktree-{slug}"',
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# validate_scaffold
# ---------------------------------------------------------------------------


def validate_scaffold(
    group_name: str,
    members: list[dict[str, Any]],
    *,
    other_configs: list[dict[str, Any]],
    allow_missing: bool,
) -> None:
    """Validate a group scaffold before any file is written.

    Checks (in order):
      1. group_name passes validate_group_name (confinement).
      2. Each repo_root exists on disk and contains a .git entry, unless
         allow_missing=True.
      3. No repo_root appears twice within members (intra-group dupe).
      4. No repo_root already claimed by a member in other_configs (overlap).

    Args:
        group_name:    Candidate group name.
        members:       List of dicts with "name" and "repo_root" keys.
        other_configs: Already-loaded group configs to check for overlap.
        allow_missing: When True, skip the existence/git check.

    Raises:
        GroupConfinementError: If group_name is invalid (from validate_group_name).
        ScaffoldError:         If any other validation check fails.
    """
    from .resolve import validate_group_name, validate_no_overlap

    # 1. Group name confinement (raises GroupConfinementError on failure)
    validate_group_name(group_name)

    normalized = _normalize_members(members)

    # 2. Existence + git check
    if not allow_missing:
        for m in normalized:
            rr = Path(m["repo_root"])
            if not rr.exists():
                raise ScaffoldError(
                    f"camp: repo_root {str(rr)!r} for member {m['name']!r} does not exist"
                )
            if not (rr / ".git").exists():
                raise ScaffoldError(
                    f"camp: repo_root {str(rr)!r} for member {m['name']!r} is not a git repo "
                    "(no .git found)"
                )

    # 3. Intra-group duplicate check
    seen_roots: dict[str, str] = {}
    for m in normalized:
        rr = m["repo_root"]
        if rr in seen_roots:
            raise ScaffoldError(
                f"camp: repo_root {rr!r} is listed twice in group {group_name!r} "
                f"(members {seen_roots[rr]!r} and {m['name']!r})"
            )
        seen_roots[rr] = m["name"]

    # 4. Cross-group overlap via validate_no_overlap
    # Build a candidate config in the same shape load_group returns
    candidate = {
        "group": {"name": group_name},
        "members": [
            {"name": m["name"], "repo_root": m["repo_root"], "bootstrap": []} for m in normalized
        ],
        "branch_pattern": "worktree-{slug}",
        "shared_vaults": [],
        "_toml_path": f"<candidate:{group_name}>",
    }
    from .resolve import GroupResolutionError

    try:
        validate_no_overlap(other_configs + [candidate])
    except GroupResolutionError as e:
        raise ScaffoldError(str(e)) from e
