"""Records the conversation a harness session-start hook reports against
the camp workspace window it started in.

A conversation's id cannot be recovered once the conversation stops, and
resurrection brings a workspace's windows back from the ids recorded here.
The moment a harness starts a session is the one moment the id is known
for certain, so camp learns it there: the harness runs camp's session-start
hook, hands it a payload naming the session, and the hook records that id
against the tmux window the session is running in. The operator starts a
conversation however they like — a plain `claude` in any pane, a resume, a
cleared context — and the record follows.

Which window, and whether it is camp's
----------------------------------------
tmux exports ``TMUX_PANE`` to every process in a pane, and a hook inherits
it from the session that ran it. :func:`capture_conversation` asks tmux
where that pane sits, then reads the `@camp_*` session-local options
workspace-session creation writes (see `camp.launch.workspace_session`).
Only a session carrying `@camp_workspace` is camp's; group and slug are
read back from the options, never inferred from a session name, and the
stored slug is re-validated before it reaches a path join, exactly as every
other reader of a stored slug does.

What is recorded, and what is refused
---------------------------------------
One entry per window (`record_window_entry`): a later conversation in the
same window replaces the earlier one. The entry's directory is the pane's
current directory, relative to the workspace root, held to the same floor
every other window-rooting path applies — inside the workspace, and clear
of any credential store. A pane outside that floor, a session that is not
a marked workspace session, a payload naming no usable session, a record
camp cannot parse, and a workspace lock it cannot take in time all record
nothing.

Posture
-------
This runs inside the harness's session start, for every session on the
machine, most of which have nothing to do with camp. It never raises, never
prints, and bounds every wait: a hook that failed loudly or hung would
charge that cost to every session to record a fact most of them do not
have.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping, Sequence

from ..group.manifest import LockTimeout
from ..group.manifest import workspace_dir as _real_workspace_dir
from ..group.resolve import (
    GroupConfinementError,
    GroupResolutionError,
    resolve_group_override,
    validate_workspace_slug,
)
from ..group.window_record import WindowEntry, WindowRecordError, record_window_entry
from .eligibility import assert_not_a_credential_store
from .profile import harness_for
from .session import LaunchError
from .window_reconcile import RECONCILE_LOCK_TIMEOUT_SECONDS


def capture_conversation(
    payload: str,
    *,
    pane: str | None,
    tmux,
    all_configs: Sequence[dict],
    env: Mapping[str, str],
    workspace_dir_fn: Callable[..., Path] = _real_workspace_dir,
) -> WindowEntry | None:
    """Record the conversation *payload* names against the window *pane*
    sits in, and return the entry written — or ``None`` when nothing was
    recorded, for any of the reasons the module docstring lists.

    *pane* is the session's ``TMUX_PANE`` (``None`` outside tmux). *payload*
    is the raw text the harness handed the hook; only the group's harness
    reads it.
    """
    if not pane:
        return None

    where = tmux.pane_window(pane)
    if where is None:
        return None
    if tmux.show_option(where.session_id, "@camp_workspace") != "1":
        return None
    group_name = tmux.show_option(where.session_id, "@camp_group")
    slug = tmux.show_option(where.session_id, "@camp_slug")
    if not group_name or not slug:
        return None

    try:
        validate_workspace_slug(slug)
        group = resolve_group_override(group_name, list(all_configs))
    except (GroupConfinementError, GroupResolutionError):
        return None

    harness = harness_for(group)
    if harness is None:
        return None
    conversation_id = harness.session_start_hook_session_id(payload)
    if conversation_id is None:
        return None

    ws_dir = Path(workspace_dir_fn(group_name, slug, env=dict(env))).resolve()
    cwd = Path(where.current_path).resolve()
    if cwd != ws_dir and ws_dir not in cwd.parents:
        return None
    try:
        assert_not_a_credential_store(cwd, env=env)
    except LaunchError:
        return None

    entry = WindowEntry(
        window_id=where.window_id,
        name=where.window_name,
        cwd=str(cwd.relative_to(ws_dir)),
        conversation_id=conversation_id,
    )
    try:
        record_window_entry(ws_dir, entry, lock_timeout=RECONCILE_LOCK_TIMEOUT_SECONDS)
    except (LockTimeout, WindowRecordError, OSError):
        return None
    return entry
