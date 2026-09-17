"""The interactive attach handoff: replaces this process, local or remote.

Local: ``tmux attach -t =<derived name>`` — `=`-qualified through
:func:`camp.launch.tmux.target`, the one tmux invocation in camp that
bypasses the `Tmux` class itself (an interactive `exec`, which cannot go
through `subprocess.run`) but still routes its target through the seam's
own normalization. Remote: ``ssh -t <destination>
<camp_bin> attach <ref>``, carrying the per-host camp location the host
declaration already holds and the same fixed connection options the listing
transport pins (``host/transport.py``) — non-interactive authentication,
strict host-identity checking, the connection timeout — differing only by
requesting a terminal. The remote command reuses that module's own
quote-and-join (:func:`camp.host.transport.quote_and_join`), never a second
implementation of it: ssh joins trailing argv elements with a space and hands
the result to the far side's login shell, so an unquoted reference is remote
code execution.

This module never calls :func:`camp.host.transport.run_camp` — the listing
transport captures a remote invocation's output through an injected runner;
this handoff replaces the local process entirely and has no output to
capture.

The handoff itself is an injected seam (:data:`ExecSeam`, mirroring the
``Runner`` seam at ``host/transport.py:174`` and the ``Tmux`` seam at
``launch/stop.py:158``) so it stays testable: a test substitutes a recorder
for the real ``os.execvp`` and asserts on the argv that would have been
exec'd, without the test process ever disappearing. What such a test can
never observe — by construction — is that the real exec actually replaces
the process image or that a pty genuinely reaches the far side; that half is
the operator's own attestation.

Security: like the listing transport, the assembled ssh argv carries slugs,
session references, and the host's camp location as plain command-line
arguments — readable via ``ps`` by other local users on a multi-user
machine.
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Mapping, NoReturn, Sequence

from ..attach.prefix_warning import inside_multiplexer
from ..launch.tmux import Tmux, target
from .config import Host
from .transport import DEFAULT_CONNECT_TIMEOUT_SECONDS, quote_and_join

#: The injected exec seam. Production points this at :func:`default_exec_seam`
#: (``os.execvp``, never returning on success); a test substitutes a recorder.
ExecSeam = Callable[[Sequence[str]], None]


def local_argv(derived_name: str) -> list[str]:
    """argv for the local handoff: ``tmux attach -t <derived_name>``.

    ``derived_name`` must be the resolved session's own derived name (see
    :class:`camp.launch.recovery.SessionCandidate`) — never the harness's own
    session name, which addresses nothing in tmux.
    """
    return ["tmux", "attach", "-t", target(derived_name)]


def door_argv(derived_name: str) -> list[str]:
    """argv for the door's outside-tmux handover: ``tmux attach-session -t
    =<derived_name>``.

    Distinct from :func:`local_argv` (``tmux attach``, the retired ref
    path's own spelling): the door composes the full ``attach-session``
    subcommand name, matching the transcript
    ``docs/design/the-door-creates-or-connects-a-workspace-session.md``'s
    "Handing over the terminal" section pins, since it names both calls —
    ``attach-session`` and ``switch-client`` — by their full names side by
    side.

    ``derived_name`` must be the resolved workspace session's own derived
    name (:func:`~camp.launch.naming.workspace_session_name`) — never the
    harness's own session id.
    """
    return ["tmux", "attach-session", "-t", target(derived_name)]


def remote_argv(
    host: Host,
    ref: str,
    *,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> list[str]:
    """argv for the remote handoff: an interactive ``ssh -t <dest> <camp_bin>
    attach <ref>``.

    Mirrors :func:`camp.host.transport.run_camp`'s own ssh argv assembly —
    the same three fixed options (``BatchMode``, ``StrictHostKeyChecking``,
    ``ConnectTimeout``) — differing only by requesting a terminal (``-t``),
    since the attaching operator needs an interactive pty where the listing
    transport deliberately does not take one.
    """
    remote_command = quote_and_join(host.camp_bin, ["attach", ref])
    return [
        "ssh",
        "-t",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"ConnectTimeout={connect_timeout:g}",
        host.ssh,
        remote_command,
    ]


def default_exec_seam(argv: Sequence[str]) -> None:
    """Actually replace this process with ``argv``. Never returns on success."""
    os.execvp(argv[0], list(argv))


def handoff(argv: Sequence[str], *, exec_seam: ExecSeam = default_exec_seam) -> None:
    """Flush this process's output, then hand it to ``argv`` via ``exec_seam``.

    The flush matters because exec replaces process memory rather than
    draining it — anything printed and not yet flushed (a prefix-conflict
    warning printed just before handoff, among others) is silently lost.

    A failure to start ``argv[0]`` (``FileNotFoundError``, an ``OSError``
    subclass) is camp's own refusal, not a traceback: ``camp: <message>`` on
    stderr, exit 1 — the same shape every other process-start site in the
    repo produces (``provision/tasks.py``'s ``_run_argv``, ``spine.py``'s
    ``_die``).
    """
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        exec_seam(argv)
    except OSError as exc:
        print(f"camp: {exc}", file=sys.stderr)
        sys.exit(1)


def hand_over_to_session(
    tmux: Tmux, derived_name: str, *, env: Mapping[str, str]
) -> NoReturn:
    """Give this terminal to the workspace session *derived_name* and end the
    invocation — the door's handover, shared by `camp attach` and `camp new`.

    Two arms on two different seams, chosen by whether the caller is already
    inside tmux (``TMUX`` set, per
    :func:`camp.attach.prefix_warning.inside_multiplexer`) — see
    ``docs/design/the-door-creates-or-connects-a-workspace-session.md``'s
    "Handing over the terminal". Outside tmux, the door hands off through the
    exec seam, which blocks for the session's life and never returns on
    success. Inside tmux, it runs ``switch-client`` through the ordinary
    :class:`~camp.launch.tmux.Tmux` seam instead — never exec'd, because
    ``switch-client`` returns immediately and an exec'd one would tear down
    the calling pane, and often the whole source session, rather than moving
    the client — and exits on that call's own result, treating a tmux that
    could not be asked at all (``None``) as a failure.

    Never returns: the exec arm's fall-through only runs when a test's
    injected exec seam returns instead of replacing the process, and even
    then the invocation must end here rather than falling back into the
    caller's remaining branches.
    """
    if inside_multiplexer(env):
        switched = tmux.switch_client(derived_name)
        sys.exit(switched.returncode if switched is not None else 1)

    handoff(door_argv(derived_name))
    sys.exit(0)
