"""Shell integration for camp.

Provides one public function:

cmd_pwd(group, slug, *, env=None) -> Path
    Return the resolved workspace directory for the given (group, slug).
    The caller is responsible for printing this to stdout as exactly one line
    with no trailing whitespace. Raises WorkspaceNotFoundError when the
    workspace directory does not exist.

Shell usage: cd "$(camp pwd <slug>)" — users can wrap this in their own
alias or shell function as they like.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class WorkspaceNotFoundError(Exception):
    """Raised by cmd_pwd when the workspace directory does not exist."""


def cmd_pwd(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    """Return the resolved workspace directory for (group, slug).

    Args:
        group: Loaded group config dict.
        slug:  Worktree slug.
        env:   Optional env override for hermetic tests.

    Returns:
        Absolute Path to the workspace directory (central_state_dir(group)/worktrees/<slug>).

    Raises:
        WorkspaceNotFoundError: If the workspace directory does not exist.
    """
    from group_resolve import central_state_dir

    group_name = group["group"]["name"]
    ws_dir = central_state_dir(group_name, env=env) / "worktrees" / slug
    if not ws_dir.exists():
        raise WorkspaceNotFoundError(
            f"camp pwd: no workspace for slug {slug!r} in group {group_name!r} "
            f"(expected: {ws_dir})\n"
            f"  Run 'camp ai {slug}' to create it."
        )
    return ws_dir
