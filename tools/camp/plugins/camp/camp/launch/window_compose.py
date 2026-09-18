"""Composes a window inside a workspace's tmux session and records it.

`compose_window` is the single verb behind AC19 and AC20: given a workspace
session already running in tmux, it either starts a fresh Claude conversation
under an id camp mints itself, or runs an explicit shell command, creates the
window through the `Tmux` seam — reading back the window id AND name tmux
itself assigned on that same creating call, never a value this module
predicted — and writes the result into the workspace's window record before
reporting success.

Two things this module deliberately does NOT do, both owned by a later
slice:

- The remote-control and visible-name flags. `trailhead.harness.claude_code`
  offers `session_launch`, but that method unconditionally adds
  `--remote-control` and `--name` (AC56 forbids both here), so this module
  never calls it — the composed argv is built directly from the harness
  profile's bare binary name plus `--session-id`.
- The directory floor (AC21). `compose_window` takes *cwd* as given and
  computes its workspace-relative form; it does not check containment or
  the credential-store denylist. The insertion point for that refusal is
  the top of this function, before the conversation id is minted or any
  tmux call is made — see `task/the-directory-floor-refuse-the-window-record-nothing`.

The scrub (AC60) rides INSIDE the composed command, exactly the way
`launch/session.py`'s own pane command carries it (`env -u NAME ... argv`):
a tmux pane inherits the SERVER's environment, fixed when that server
started, so a scrub applied to the `new-window` request itself would do
nothing. `compose_window` never passes an `env=` operand to
`Tmux.new_window` for this reason — the only environment statement it makes
is the one baked into the command tokens themselves.
"""

from __future__ import annotations

import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..group.window_record import WindowEntry, append_window_entry
from .naming import workspace_session_name
from .profile import harness_for, resolve_harness_profile
from .tmux import UNANSWERED, Tmux


class WindowComposeError(Exception):
    """Raised when a composed window could not be created in tmux."""


@dataclass(frozen=True)
class WindowComposeResult:
    """What one `compose_window` call produced, after the record write."""

    window_id: str
    window_name: str
    cwd: str
    conversation_id: str | None
    command_line: str | None


def compose_window(
    group: dict,
    slug: str,
    ws_dir: Path,
    *,
    cwd: Path,
    window_name: str,
    command: Sequence[str] | None = None,
    tmux: Tmux | None = None,
) -> WindowComposeResult:
    """Compose one window in the workspace at *slug*'s tmux session.

    *command*, when given, is run verbatim and recorded as the window's
    command line — no conversation id, no scrub, no harness composition at
    all. *command* absent (the default) is the AC19 path: camp mints a
    fresh conversation id itself (`uuid.uuid4()`, the same pattern
    `launch/session.py` already applies), composes `<binary> --session-id
    <id>` directly rather than through the harness's `session_launch` (which
    unconditionally adds the two flags AC56 forbids), wraps it in the
    harness's scrub, and records the id with no command line.

    *cwd* is taken as given — see the module docstring for where the
    directory floor's refusal belongs; this function only computes *cwd*'s
    workspace-relative form for the record (AC29).

    Raises :class:`WindowComposeError` if tmux could not create the window.
    The record write happens only after that create succeeds, and happens
    exactly once, before this function returns — a caller that reads
    `WindowComposeResult` back knows the record already reflects it.
    """
    tmux = tmux if tmux is not None else Tmux()
    group_name = group["group"]["name"]
    session_name = workspace_session_name(group_name, slug)

    conversation_id: str | None = None
    command_line: str | None = None

    if command is not None:
        pane_command = list(command)
        command_line = shlex.join(pane_command)
    else:
        conversation_id = str(uuid.uuid4())
        binary = Path(resolve_harness_profile(group).binary).name
        claude_argv = [binary, "--session-id", conversation_id]

        harness = harness_for(group)
        scrub = harness.session_launch_env_unset() if harness is not None else None
        pane_command = ["env"]
        for var in scrub or ():
            pane_command += ["-u", var]
        pane_command += claude_argv

    result = tmux.new_window(
        session_name,
        cwd=cwd,
        window_name=window_name,
        command=pane_command,
    )
    if result is None or result is UNANSWERED:
        raise WindowComposeError(
            f"camp: could not create a window in tmux session {session_name!r}"
        )

    relative_cwd = str(Path(cwd).relative_to(Path(ws_dir)))

    entry = WindowEntry(
        window_id=result.window_id,
        name=result.window_name,
        cwd=relative_cwd,
        conversation_id=conversation_id,
        command_line=command_line,
    )
    append_window_entry(ws_dir, entry)

    return WindowComposeResult(
        window_id=result.window_id,
        window_name=result.window_name,
        cwd=relative_cwd,
        conversation_id=conversation_id,
        command_line=command_line,
    )
