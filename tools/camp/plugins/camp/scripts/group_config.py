"""Group config loader for camp.

Loads TOML group configs from trailhead.paths.config_dir("camp")/groups/<group>.toml.

Schema:
  [group]
  name = "<group-name>"

  [[members]]
  name = "<repo-name>"
  repo_root = "/absolute/path/to/repo"
  bootstrap = ["cmd", "arg1", "arg2"]   # list for subprocess shell=False; optional
  base = "origin/main"                  # branch start-point; optional, default origin/main

  [branch]
  pattern = "worktree-{slug}"            # optional; default "worktree-{slug}"

  [dev_env]                              # optional; warn-and-continue (deferred)
  ...

Bootstrap commands are author-trusted local input. camp runs them list-mode
(subprocess, shell=False). Sharing group configs from untrusted authors is
explicitly out of scope (see D-F in the Step-2 plan).
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GroupConfigError(Exception):
    """Raised when a group config file exists but is malformed or missing required fields."""


class GroupConfigNotFound(Exception):
    """Raised when a group config file does not exist.

    The message includes a first-run hint pointing at groups.example/trailhead.toml.
    """


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_FIRST_RUN_HINT = (
    "copy groups.example/trailhead.toml as a starting point"
)


def load_group(path: Path) -> dict[str, Any]:
    """Load and validate a group TOML config file.

    Returns a normalized dict:
      {
        "group": {"name": str},
        "members": [{"name": str, "repo_root": str, "bootstrap": list[str]}],
        "branch_pattern": str,
      }

    Raises:
        GroupConfigNotFound: If path does not exist.
        GroupConfigError: If the file is malformed or missing required fields.
            The message always names the file and the failing field.
    """
    if not path.is_file():
        raise GroupConfigNotFound(
            f"No group config found at {path!s}; expected a TOML file.\n"
            f"  {_FIRST_RUN_HINT}"
        )

    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise GroupConfigError(f"{path}: TOML parse error — {e}") from e

    # --- [group] section ---
    group_section = raw.get("group")
    if not isinstance(group_section, dict):
        raise GroupConfigError(
            f"{path}: missing required [group] section"
        )

    group_name = group_section.get("name")
    if not isinstance(group_name, str) or not group_name.strip():
        raise GroupConfigError(
            f"{path}: field 'group.name' is required and must be a non-empty string"
        )

    # --- [[members]] section ---
    members_raw = raw.get("members")
    if not isinstance(members_raw, list) or len(members_raw) == 0:
        raise GroupConfigError(
            f"{path}: field 'members' must be a non-empty list of member tables"
        )

    members: list[dict[str, Any]] = []
    for i, m in enumerate(members_raw):
        if not isinstance(m, dict):
            raise GroupConfigError(
                f"{path}: members[{i}] must be a table"
            )

        member_name = m.get("name")
        if not isinstance(member_name, str) or not member_name.strip():
            raise GroupConfigError(
                f"{path}: members[{i}].name is required and must be a non-empty string"
            )

        repo_root = m.get("repo_root")
        if not isinstance(repo_root, str) or not repo_root.strip():
            raise GroupConfigError(
                f"{path}: members[{i}] ('{member_name}'): field 'repo_root' is required "
                "and must be a non-empty string"
            )

        bootstrap_raw = m.get("bootstrap", [])
        if bootstrap_raw is None:
            bootstrap_raw = []
        if not isinstance(bootstrap_raw, list):
            raise GroupConfigError(
                f"{path}: members[{i}] ('{member_name}'): field 'bootstrap' must be a list "
                "of strings (for subprocess shell=False), not a shell string"
            )
        for j, cmd_part in enumerate(bootstrap_raw):
            if not isinstance(cmd_part, str):
                raise GroupConfigError(
                    f"{path}: members[{i}] ('{member_name}'): "
                    f"bootstrap[{j}] must be a string, got {type(cmd_part).__name__!r}"
                )

        base = m.get("base", "origin/main")
        if not isinstance(base, str) or not base.strip():
            raise GroupConfigError(
                f"{path}: members[{i}] ('{member_name}'): field 'base' must be a "
                "non-empty string (the branch start-point, e.g. 'origin/main')"
            )

        members.append(
            {
                "name": member_name,
                "repo_root": repo_root,
                "bootstrap": list(bootstrap_raw),
                "base": base,
            }
        )

    # --- [branch] section (optional) ---
    branch_section = raw.get("branch") or {}
    branch_pattern = branch_section.get("pattern", "worktree-{slug}")
    if not isinstance(branch_pattern, str):
        raise GroupConfigError(
            f"{path}: field 'branch.pattern' must be a string"
        )

    # --- [dev_env] section — warn-and-continue (deferred) ---
    if "dev_env" in raw:
        print(
            f"camp: [dev_env] in {path.name!r} is not yet supported — "
            "dev-env capability deferred; ignored.",
            file=sys.stderr,
        )

    # --- [[shared_vaults]] section (optional) ---
    shared_vaults_raw = raw.get("shared_vaults")
    if shared_vaults_raw is None:
        shared_vaults_raw = []
    if not isinstance(shared_vaults_raw, list):
        raise GroupConfigError(
            f"{path}: field 'shared_vaults' must be a list of tables"
        )

    shared_vaults: list[dict[str, Any]] = []
    for i, sv in enumerate(shared_vaults_raw):
        if not isinstance(sv, dict):
            raise GroupConfigError(
                f"{path}: shared_vaults[{i}] must be a table"
            )

        sv_name = sv.get("name")
        if not isinstance(sv_name, str) or not sv_name.strip():
            raise GroupConfigError(
                f"{path}: shared_vaults[{i}].name is required and must be a non-empty string"
            )

        sv_root = sv.get("root")
        if not isinstance(sv_root, str) or not sv_root.strip():
            raise GroupConfigError(
                f"{path}: shared_vaults[{i}] ('{sv_name}'): field 'root' is required "
                "and must be a non-empty string"
            )

        shared_vaults.append({"name": sv_name, "root": sv_root})

    return {
        "group": {"name": group_name},
        "members": members,
        "branch_pattern": branch_pattern,
        "shared_vaults": shared_vaults,
        "_toml_path": str(path),
    }


def load_all_groups(groups_dir: Path) -> list[dict[str, Any]]:
    """Load all .toml files in groups_dir and return a list of parsed configs.

    Files that fail to parse raise GroupConfigError (caller decides whether to
    surface or skip). Non-.toml files are ignored.

    Args:
        groups_dir: Directory to scan.

    Returns:
        List of parsed group configs (may be empty if directory is empty).
    """
    if not groups_dir.is_dir():
        return []

    configs: list[dict[str, Any]] = []
    for toml_file in sorted(groups_dir.glob("*.toml")):
        configs.append(load_group(toml_file))
    return configs
