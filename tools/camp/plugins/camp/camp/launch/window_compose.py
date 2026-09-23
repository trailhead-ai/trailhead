"""Composes a window inside a workspace's tmux session and records it.

`compose_window` is the single verb behind AC19 and AC20: given a workspace
session already running in tmux, it either starts a fresh Claude conversation
under an id camp mints itself, or runs an explicit shell command, creates the
window through the `Tmux` seam — reading back the window id AND name tmux
itself assigned on that same creating call, never a value this module
predicted — and writes the result into the workspace's window record before
reporting success.

One thing this module deliberately does NOT do:

- The remote-control and visible-name flags. No method on the harness seam
  composes `--remote-control` or `--name` (AC56 forbids both here) — the
  composed argv is built directly from the harness profile's bare binary
  name plus `--session-id`.

The directory floor (AC21) IS checked here, first thing, before the
conversation id is minted or any tmux call is made: `cwd` is resolved once,
containment against the workspace root is decided on that resolved path (a
symlink spelling cannot smuggle a directory in or out), and the same
resolved path — never the caller's original spelling — is what reaches
`Tmux.new_window` and what the record's relative `cwd` is computed from.
Two distinct refusals, both raised before any tmux call and before the
record is touched:

- :class:`WindowOutsideWorkspace` — `cwd` resolves outside the workspace
  root. Its message names the offending path: the operator supplied it and
  needs to know which one was rejected.
- :class:`WindowAtCredentialStore` — `cwd` resolves at, under, or above a
  declared credential store
  (`camp.launch.eligibility.assert_not_a_credential_store`, unconditional
  and independent of the launch allowlist). Its message deliberately does
  NOT echo the path back — unlike the containment refusal, a credential
  store's location is not information this module hands back over a
  channel an operator reads.

The scrub (AC60) rides INSIDE the composed command, exactly the way
`launch/session.py`'s own pane command carries it (`env -u NAME ... argv`):
a tmux pane inherits the SERVER's environment, fixed when that server
started, so a scrub applied to the `new-window` request itself would do
nothing — the only environment statement `compose_window` makes is the one
baked into the command tokens themselves.
"""

from __future__ import annotations

import os
import shlex
import uuid
from pathlib import Path
from typing import Mapping, Sequence

from ..group.window_record import WindowEntry, append_window_entry
from .eligibility import assert_not_a_credential_store
from .naming import workspace_session_name
from .profile import harness_for, resolve_harness_profile
from .recovery import printable_path
from .session import LaunchError, resolve_launch_environment
from .tmux import UNANSWERED, Tmux


class WindowComposeError(Exception):
    """Raised when a composed window could not be created in tmux."""


class WindowRefused(Exception):
    """Base for the directory floor's refusals (AC21).

    Raised before any tmux call and before the conversation id is minted —
    no window was created and the workspace's window record is unchanged.
    """


class WindowOutsideWorkspace(WindowRefused):
    """`cwd` resolves outside the workspace root.

    The message names the offending path — the operator supplied it and
    needs to know which one was rejected.
    """


class WindowAtCredentialStore(WindowRefused):
    """`cwd` resolves at, under, or above a declared credential store.

    The message deliberately does not echo the path — see
    `camp.launch.eligibility.assert_not_a_credential_store`, the gate this
    wraps.
    """


def compose_window(
    group: dict,
    slug: str,
    ws_dir: Path,
    *,
    cwd: Path,
    window_name: str,
    command: Sequence[str] | None = None,
    tmux: Tmux | None = None,
    env: Mapping[str, str] | None = None,
) -> WindowEntry:
    """Compose one window in the workspace at *slug*'s tmux session.

    *command*, when given, is run verbatim and recorded as the window's
    command line — no conversation id, no scrub, no harness composition at
    all. *command* absent (the default) is the AC19 path: camp mints a
    fresh conversation id itself (`uuid.uuid4()`), composes `<binary>
    --session-id <id>` directly — no method on the harness seam composes
    the two flags AC56 forbids — wraps it in the harness's scrub, and
    records the id with no command line.

    *cwd* is resolved once, first thing: the directory floor (AC21) is
    checked against that resolved path, and the same resolved path is what
    reaches `Tmux.new_window` and what the record's relative `cwd` (AC29)
    is computed from — never the caller's original spelling. See the
    module docstring for the two refusals this raises, both before the
    conversation id is minted or any tmux call is made. *env* supplies HOME
    (and the credential gate's account lookups) for that check; `None`
    (the default) resolves to `os.environ` — the same "caller stated nothing,
    so read the process's own environment" fallback `cli/group.py`'s door
    dispatch already applies ahead of this same gate, never a bare `None`
    forwarded into it, which would hit `assert_not_a_credential_store`'s own
    `Path.home()` fallback instead of the environment this process actually
    runs under.

    Raises :class:`WindowComposeError` if tmux could not create the window.
    The record write happens only after that create succeeds, and happens
    exactly once, before this function returns — the returned
    :class:`~camp.group.window_record.WindowEntry` is the very object that
    was appended, so a caller holding it knows the record already reflects
    it.
    """
    resolved_env = dict(env) if env is not None else dict(os.environ)

    resolved_cwd = Path(cwd).resolve()
    resolved_ws_dir = Path(ws_dir).resolve()
    if resolved_cwd != resolved_ws_dir and resolved_ws_dir not in resolved_cwd.parents:
        raise WindowOutsideWorkspace(
            f"camp: cannot open window — directory {printable_path(resolved_cwd)} "
            f"is outside the workspace root {printable_path(resolved_ws_dir)}; "
            "choose a directory inside the workspace"
        )
    try:
        assert_not_a_credential_store(resolved_cwd, env=resolved_env)
    except LaunchError as exc:
        raise WindowAtCredentialStore(
            "camp: cannot open window — the target directory is a credential "
            "store, which camp will never root a window at; this rule is "
            "fixed in camp and no group configuration can permit it"
        ) from exc
    cwd = resolved_cwd

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
        profile = resolve_harness_profile(group)
        binary = Path(profile.binary).name
        claude_argv = [binary, "--session-id", conversation_id]

        harness = harness_for(group)
        if harness is not None:
            _account, binding, scrub, _launch_env = resolve_launch_environment(
                harness, profile, group, resolved_env
            )
        else:
            binding, scrub = {}, ()
        pane_command = ["env"]
        for var in scrub:
            pane_command += ["-u", var]
        for name, value in binding.items():
            pane_command.append(f"{name}={value}")
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

    relative_cwd = str(Path(cwd).relative_to(resolved_ws_dir))

    entry = WindowEntry(
        window_id=result.window_id,
        name=result.window_name,
        cwd=relative_cwd,
        conversation_id=conversation_id,
        command_line=command_line,
    )
    append_window_entry(ws_dir, entry)

    return entry
