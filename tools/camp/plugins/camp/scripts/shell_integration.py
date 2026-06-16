"""Shell integration for camp — Slice 7.

Provides two public functions:

cmd_cd(group, slug, *, env=None) -> Path
    Return the resolved workspace directory for the given (group, slug).
    The caller is responsible for printing this to stdout as exactly one line
    with no trailing whitespace. Raises WorkspaceNotFoundError when the
    workspace directory does not exist.

shellenv() -> str
    Return the fish shell function definition that wraps `camp cd` so the
    caller's shell cd's to the workspace directory. Print on stdout; this is
    the output of `camp shellenv`.

    Fish-only constraint: no bash/zsh shim is emitted this pass
    (council/Advocate: fish-only is documented in README + SKILL.md).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class WorkspaceNotFoundError(Exception):
    """Raised by cmd_cd when the workspace directory does not exist."""


def cmd_cd(
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
            f"camp cd: no workspace for slug {slug!r} in group {group_name!r} "
            f"(expected: {ws_dir})\n"
            f"  Run 'camp ai {slug}' to create it."
        )
    return ws_dir


_FISH_FUNCTION = """\
function camp_cd
    set _camp_cd_path (camp cd $argv)
    or return 1
    builtin cd $_camp_cd_path
end
"""


def shellenv() -> str:
    """Return the fish shell function that wraps camp cd.

    The returned string is a complete, syntactically valid fish function
    definition. The caller should print it on stdout for eval/sourcing.

    Fish-only: no bash/zsh shim is emitted this pass.
    """
    return _FISH_FUNCTION
