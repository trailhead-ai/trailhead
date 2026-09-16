"""The hidden ``camp inject --drain`` command group.

The PostToolUse hook handler for the claude-hook inject strategy. It fires on
every Bash tool call, so it must stay near the heavy spine module — it never
imports that, and ``dispatch.main`` routes to it BEFORE the spine module-load.
It DOES need ``trailhead.paths`` (to derive the central state dir the queue now
lives under — see ``camp.launch.inject``'s module docstring for why), so when
no ``--workspace`` is given it lazily calls the same cheap, pure-filesystem-walk
``_bootstrap.ensure_trailhead_importable()`` every other command uses; an
explicit ``--workspace`` skips even that.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _cmd_inject_cli(args: list[str]) -> None:
    """camp inject --drain [--workspace <dir>] — drain the inject queue (hidden).

    The PostToolUse hook handler for the claude-hook inject strategy. Reads the
    queue and emits the Claude Code additionalContext JSON, then clears it; an
    empty queue emits nothing. Without --workspace, the queue root is located
    by mapping the session's cwd to (group, slug) — Claude Code runs
    PostToolUse hooks in the session's current cwd, which is typically a
    member worktree (`central_state_dir(group)/worktrees/<slug>/<member>`) —
    via `resolve_group_slug_from_cwd`, then `central_queue_dir(group, slug)`.
    If cwd doesn't resolve to a (group, slug), there is nothing to drain
    (no-op safe). Resilient: never crashes a tool call — any error → exit 0.

    --workspace, when given, names the queue root directly (bypassing the cwd
    resolution) — used by tests and any caller that already knows it.

    Argument parsing goes through `CampParser` like every other verb, but its
    refusal is swallowed: this route is a PostToolUse hook, and a non-zero exit
    here surfaces as a warning on EVERY Bash tool call. A malformed invocation
    is camp calling its own hidden route wrongly, never an operator typo, so the
    honest response is to drain nothing and exit 0 rather than to break the
    session's tooling over it. `parser` imports only argparse — the module this
    route stays clear of is the heavy `spine`, not the parser seam.
    """
    from .parser import CampParser

    parser = CampParser(verb="inject")
    parser.add_argument("--drain", action="store_true")
    parser.add_argument("--workspace", metavar="DIR")
    try:
        workspace = parser.parse_args(args).workspace
    except SystemExit:
        sys.exit(0)

    code = 0
    try:
        from ..launch.inject import central_queue_dir, drain_queue, resolve_group_slug_from_cwd

        if workspace:
            queue_root: Path | None = Path(workspace)
        else:
            from _bootstrap import ensure_trailhead_importable

            ensure_trailhead_importable()
            import trailhead.paths as _paths

            camp_state_dir = _paths.state_dir("camp")
            resolved = resolve_group_slug_from_cwd(Path.cwd(), camp_state_dir)
            if resolved is None:
                queue_root = None
            else:
                group_name, slug = resolved
                queue_root = central_queue_dir(group_name, slug)

        if queue_root is not None:
            code = drain_queue(queue_root)
    except Exception:
        # Never crash a tool call.
        code = 0
    sys.exit(code)
