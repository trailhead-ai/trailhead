"""camp init <group> — idempotent hook wiring.

For each member repo in the group config, calls hooks_writer to wire
SessionStart (session-bootstrap) and WorktreeRemove (worktree-cleanup)
into the member's .claude/settings.json.

Also surfaces the eager config-overlap validation (Slice 1) before writing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from hooks_writer import write_hooks_for_member
from group_resolve import validate_no_overlap


def run_init(
    group: dict[str, Any],
    camp_bin: str,
    *,
    group_configs: list[dict[str, Any]] | None = None,
) -> None:
    """Wire camp hooks into each member's .claude/settings.json.

    Args:
        group:        Parsed group config dict (from group_config.load_group).
        camp_bin:     Absolute path to the camp binary.
        group_configs: All loaded group configs (for overlap validation).
                      If None, skips cross-group validation.

    Raises:
        GroupResolutionError: If a repo is listed in multiple groups.
    """
    # Eager config-overlap validation (Slice 1)
    if group_configs is not None:
        validate_no_overlap(group_configs)

    members = group["members"]
    for member in members:
        repo_root = Path(member["repo_root"])
        write_hooks_for_member(repo_root, camp_bin)
