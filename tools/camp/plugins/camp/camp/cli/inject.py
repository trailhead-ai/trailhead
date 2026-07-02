"""The hidden ``camp inject --drain`` command group.

The PostToolUse hook handler for the claude-hook inject strategy. It fires on
every Bash tool call, so it must stay near-free — this module imports only
stdlib at load time and pulls ``camp.harness.inject`` lazily inside the handler,
and ``dispatch.main`` routes to it BEFORE the bootstrap walk and the heavy spine
module-load.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _cmd_inject_cli(args: list[str]) -> None:
    """camp inject --drain [--workspace <dir>] — drain the inject queue (hidden).

    The PostToolUse hook handler for the claude-hook inject strategy. Reads the
    workspace inject queue and emits the Claude Code additionalContext JSON, then
    clears the queue; an empty queue emits nothing. Without --workspace, the
    workspace root is located by walking UP from the cwd to the nearest ancestor
    containing a `.camp/` dir — Claude Code runs PostToolUse hooks in the session's
    current cwd, which may be a member worktree (<workspace>/<member>) rather than
    the workspace root. If no `.camp/` ancestor is found, the cwd is drained as-is
    (no-op safe). Resilient: never crashes a tool call — any error → exit 0.

    The --workspace parse is inlined here (rather than reusing
    spine._consume_flag_value) so the per-Bash-call drain path never imports the
    heavy spine module.
    """
    workspace: str | None = None
    for i, arg in enumerate(args):
        if arg == "--workspace" and i + 1 < len(args):
            workspace = args[i + 1]
            break
        if arg.startswith("--workspace="):
            workspace = arg[len("--workspace="):]
            break

    try:
        from camp.harness.inject import drain_queue, find_workspace_root
        ws_dir = Path(workspace) if workspace else find_workspace_root(Path.cwd())
        code = drain_queue(ws_dir)
    except Exception:
        # Never crash a tool call.
        code = 0
    sys.exit(code)
