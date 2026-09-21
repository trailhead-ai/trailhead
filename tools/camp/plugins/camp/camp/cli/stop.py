"""`camp stop <slug> [--json]` — kill a workspace's tmux session.

The door's inverse (`docs/design/the-record-stays-true-reconciliation-and-
stopping.md`, "Stopping states its cost, then pays it, then checks"):
resolves the group exactly as `camp attach` does
(`camp.cli.session._resolve_group_for_attach` — `--group` if given, else
`resolve_from_cwd`), then resolves the slug against that group's own
workspace listing (`cmd_ls_group`), never the door's picker — stopping is
not a verb to pick blind, so no slug refuses outright rather than
degrading to a numbered choice (see this module's `_cmd_stop_cli`
docstring). A slug naming no workspace composes
`camp.launch.stop_workspace.RefusedNoWorkspace`'s message here, at the call
site, exactly as `camp attach`'s own slug-resolution refusals are composed
by `attach/door_target.refusal_message` rather than read back out of the
outcome type.

`stop_workspace` does the actual work (has_session, reconcile, preview,
kill, poll); this module only resolves the target, routes its single
`emit` stream to the two destinations the design doc requires, and renders
the outcome.

Routing `emit`'s single ordered stream onto two streams
------------------------------------------------------
`stop_workspace` calls one `emit(line: str)` for every line it prints,
in order: the reconciliation lines (or the one not-reconciled note) first,
then the preview body (or the one "could not list" line) — see that
function's own docstring. The engine does not tag which segment a line
belongs to, and this task must not change it to. The two segments are
told apart here by the CLOSED vocabulary `camp.launch.window_reconcile`
emits for reconciliation: every reconciliation line and every
not-reconciled reason starts with one of `_RECONCILE_LINE_PREFIXES` below
(`render_changes`' two `Change` renderings, and `reconcile_workspace_
record`'s three `NotReconciled.reason` templates) — see that module's
source for the exhaustive set. Nothing else `stop_workspace` emits (the
preview's count line, its per-window rows, or the "could not list"
sentinel) starts with any of them, so classifying by prefix is exact
without needing the engine to distinguish the two segments itself.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import NoReturn

from ..launch.naming import workspace_session_name
from ..launch.recovery import printable_path
from ..launch.stop import Tmux
from ..launch.stop_workspace import (
    NotRunning,
    RefusedNoWorkspace,
    RefusedTmuxUnanswered,
    StillPresent,
    Stopped,
    exit_status,
    render_human,
    render_json,
    stop_workspace,
)
from ..workspace.verb_taxonomy import needs_group_message
from .parser import CampParser

#: The closed set of line prefixes `camp.launch.window_reconcile` emits —
#: see this module's docstring. `render_changes`' `Dropped`/`Renamed`
#: lines share the `"camp: window record: "` prefix; `reconcile_workspace_
#: record`'s three `NotReconciled` reasons are the other three. Every other
#: line `stop_workspace` emits (the preview's count line, its window rows,
#: and the "could not list" sentinel) starts with none of these.
_RECONCILE_LINE_PREFIXES = (
    "camp: window record: ",
    "camp: window record at ",
    "camp: tmux did not answer for session ",
    "camp: no such tmux session ",
)


def _is_reconcile_line(line: str) -> bool:
    return line.startswith(_RECONCILE_LINE_PREFIXES)


def _refuse(outcome, reason: str, *, as_json: bool) -> NoReturn:
    """One refusal for `camp stop`: `camp stop: <reason>` on stderr under the
    plain form, or `{"ok": false, "outcome": null, "reason": <reason>}` on
    stdout under `--json` — the same `ok`-flagged shape `camp attach`'s
    `_refuse_door` carries. Neither of stop's refusals renders an outcome
    word, so `outcome` is always null, and the exit status is the outcome's
    own (`stop_workspace.exit_status`) rather than a literal spelled here.
    """
    if as_json:
        print(json.dumps({"ok": False, "outcome": None, "reason": reason}))
    else:
        print(printable_path(f"camp stop: {reason}"), file=sys.stderr)
    sys.exit(exit_status(outcome))


def _cmd_stop_cli(args: list[str], env: dict[str, str] | None = None) -> None:
    """`camp stop <slug> [--group <name>] [--json]`.

    No slug and no picker: unlike `camp attach`'s bare form, a missing slug
    refuses outright, naming that a slug is required — stopping is not a
    verb an operator picks blind, so stdin is never read here even when
    interactive. No `--host` leg in this slice.
    """
    from .session import _parsable_groups, _resolve_group_for_attach

    parser = CampParser(verb="stop")
    parser.add_argument("--group")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("slug", nargs="?")
    parsed = parser.parse_args(args)

    if not parsed.slug:
        _die("camp stop: no slug given — name the workspace to stop; stop has no picker")

    resolved_env = dict(env) if env is not None else dict(os.environ)
    groups = _parsable_groups()
    target_group = _resolve_group_for_attach(groups, parsed.group, env=resolved_env)
    if target_group is None:
        _die(needs_group_message("stop"))

    group_name = target_group["group"]["name"]
    tmux = Tmux()

    from ..provision.lifecycle import cmd_ls_group

    listing = cmd_ls_group(target_group, env=resolved_env, tmux=tmux)
    match = next((e for e in listing.entries if e["slug"] == parsed.slug), None)
    if match is None:
        session_name = workspace_session_name(group_name, parsed.slug)
        _refuse(
            RefusedNoWorkspace(slug=parsed.slug, group=group_name, tmux_session=session_name),
            f"no workspace named {parsed.slug} in group {group_name}",
            as_json=parsed.json,
        )

    ws_dir = Path(match["workspace_path"])

    def emit(line: str) -> None:
        if _is_reconcile_line(line):
            print(line, file=sys.stderr)
        elif not parsed.json:
            # Under --json, stdout carries exactly one JSON object and
            # nothing else — the preview lines are still true (they are
            # what the JSON's own preview fields report as data instead),
            # but printing them as human text would break "one JSON value"
            # on stdout.
            print(line)

    outcome = stop_workspace(group_name, parsed.slug, ws_dir, tmux=tmux, emit=emit)

    if isinstance(outcome, RefusedTmuxUnanswered):
        _refuse(outcome, f"tmux did not answer for {outcome.tmux_session}", as_json=parsed.json)

    assert isinstance(outcome, (Stopped, NotRunning, StillPresent))
    if parsed.json:
        print(json.dumps(render_json(outcome)))
    else:
        print(render_human(outcome))
    sys.exit(exit_status(outcome))


def _die(message: str) -> None:
    from ..spine import _die as _spine_die

    _spine_die(message)
