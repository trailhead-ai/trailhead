"""Creates the tmux session a workspace's window record will live in.

The one function above :meth:`~camp.launch.tmux.Tmux.new_session` that turns
a resolved workspace — a group, a slug, and the directory camp already
provisioned for it — into a live tmux session: derive the name with
:func:`~camp.launch.naming.workspace_session_name`, the same derivation
`camp list` already uses, and create a detached session at that name, rooted
at the workspace directory, holding one login-shell window. No harness argv,
no environment scrub, no account binding — see the created-session section
of ``docs/design/the-door-creates-or-connects-a-workspace-session.md``.

This is deliberately NOT :func:`~camp.launch.session.launch_session`: that
function mints the retired `camp-<component>-<8hex>` name
(`camp/launch/session.py:744-747`), never the workspace name, and composes a
harness command this function must not.

Three outcomes, not two
------------------------
`tmux new-session -d -s <name>` refuses rather than duplicating: measured on
tmux 3.7c, a repeat call against a live name exits 1 and prints
`duplicate session: <name>`, creating nothing. That is not a failure — it
means another camp created the session between this call's probe-free
attempt and its own, and the caller's answer is "already there", not
"broken". So :func:`create_workspace_session` returns a closed
:class:`WorkspaceSessionOutcome` the caller branches on rather than a string
it has to re-parse: :data:`WorkspaceSessionOutcome.CREATED`,
:data:`WorkspaceSessionOutcome.ALREADY_EXISTED` (recognised only by that
exact stderr shape — see :data:`_DUPLICATE_SESSION_MARKER`), or
:data:`WorkspaceSessionOutcome.FAILED`, carrying tmux's own stderr verbatim
and unsummarized.

Marking a CREATED session and installing the window binding
--------------------------------------------------------------
Only the :data:`WorkspaceSessionOutcome.CREATED` branch — the call that
actually brought the session up — writes three session-LOCAL options
(never `-g`) onto it: `@camp_workspace=1` (the mark camp's window-dispatch
binding reads to decide whether the current session is its own — a
session-NAME heuristic is forgeable, so this is the one signal that
isn't), `@camp_group`, and `@camp_slug`. `ALREADY_EXISTED` (another camp
process won a create race) and `FAILED` write neither: a session this call
did not create was either already marked by whoever did create it, or was
never created at all.

The server-global window-creation-key binding
(:func:`~camp.launch.binding.install_window_key_binding`) is installed on
every CREATED session too — idempotently; see that function's own
docstring for why re-issuing it is cheap and how it decides whether to
print the one-time notice.

The door's own step
-------------------
:func:`create_or_connect_workspace_session` is the probe-then-create step
both doors onto a workspace session share — `camp attach`'s
(`cli/session.py:_open_workspace_door`) and `camp new`'s
(`cli/group.py:_door_dispatch_for_new`). It also composes the
operator-facing *reason* for each of its two failure states, because both
callers report that sentence word for word and only what happened at the
tmux boundary can say it.

What it deliberately does NOT decide is what a caller does with a failure.
`camp attach` refuses and exits non-zero, because the session is all it
has; `camp new` reports a workspace-only success and exits 0, because the
workspace is real on disk either way. That asymmetry stays at the call
sites.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from .binding import install_window_key_binding
from .eligibility import assert_not_a_credential_store
from .naming import workspace_session_name
from .session import LaunchError
from .tmux import DUPLICATE_SESSION_MARKER as _DUPLICATE_SESSION_MARKER
from .tmux import Tmux, target
from .window_reconcile import ReconcileOutcome, reconcile_workspace_record

#: This create is the one call that starts the tmux SERVER when none is
#: running yet — the same operation `camp launch`'s own spawn budgets 30s for
#: (`_SPAWN_TIMEOUT_SECONDS`, `launch/session.py`). `Tmux`'s own default
#: (`TMUX_TIMEOUT_SECONDS`, 5s) is tuned for a quick existence probe, not a
#: server bring-up, so this call states its own budget rather than
#: inheriting that default.
_CREATE_SESSION_TIMEOUT_SECONDS = 30


class WorkspaceSessionOutcome(Enum):
    """The closed vocabulary a workspace-session creation attempt answers."""

    CREATED = "created"
    ALREADY_EXISTED = "already_existed"
    FAILED = "failed"


@dataclass(frozen=True)
class WorkspaceSessionResult:
    """One creation attempt's answer.

    ``error`` is tmux's own stderr, verbatim, and only ever set for
    :data:`WorkspaceSessionOutcome.FAILED` — a caller branches on
    ``outcome``, never on whether ``error`` is set.
    """

    outcome: WorkspaceSessionOutcome
    session_name: str
    error: str | None = None


def create_workspace_session(
    group_name: str,
    slug: str,
    workspace_dir: Path,
    *,
    env: Mapping[str, str] | None = None,
    tmux: Tmux | None = None,
) -> WorkspaceSessionResult:
    """Create the tmux session for the workspace at *slug* in *group_name*.

    Raises :class:`~camp.launch.session.LaunchError` (via
    :func:`~camp.launch.eligibility.assert_not_a_credential_store`) before
    any tmux call this function makes, if *workspace_dir* is at, under, or
    above a credential store — that rule is unconditional and independent
    of the retired launch allowlist. It applies to every call that reaches
    THIS function, and only those: `create_or_connect_workspace_session`'s
    connect arm returns from its own `has_session` probe before this
    function is ever called, so an existing session at the derived name is
    connected to without this check. The gate exists to stop camp rooting a
    session in a credential store; the connect arm roots nothing.
    """
    assert_not_a_credential_store(Path(workspace_dir), env=env)

    tmux = tmux if tmux is not None else Tmux()
    name = workspace_session_name(group_name, slug)
    result = tmux.new_session(
        name, cwd=workspace_dir, env=env, timeout=_CREATE_SESSION_TIMEOUT_SECONDS
    )

    if result.returncode == 0:
        failure = _mark_and_bind(tmux, name, group_name, slug)
        if failure is not None:
            return failure
        return WorkspaceSessionResult(WorkspaceSessionOutcome.CREATED, name)

    stderr = result.stderr or ""
    if _DUPLICATE_SESSION_MARKER in stderr:
        return WorkspaceSessionResult(WorkspaceSessionOutcome.ALREADY_EXISTED, name)
    return WorkspaceSessionResult(WorkspaceSessionOutcome.FAILED, name, error=stderr)


def _mark_and_bind(
    tmux: Tmux, name: str, group_name: str, slug: str
) -> WorkspaceSessionResult | None:
    """Mark a just-created session with the three `@camp_*` session-LOCAL
    options and install the server-global window-creation-key binding.

    The one copy of this step, shared by `create_workspace_session`'s
    CREATED branch and resurrection's engine (`launch/resurrect.py`) after
    it rides `Tmux.new_session_with_window` — both bring a session up
    through a different tmux call, but only this step makes it a camp
    workspace session, so it exists in exactly one place.

    Returns `None` on success. Returns the FAILED `WorkspaceSessionResult`
    from :func:`_abandon_half_marked_session` (session already killed) when
    tmux refused to set one of the three options — the caller returns that
    value as its own outcome rather than reporting CREATED.
    """
    session_target = target(name)
    for key, value in (
        ("@camp_workspace", "1"),
        ("@camp_group", group_name),
        ("@camp_slug", slug),
    ):
        answer = tmux.set_option(session_target, key, value)
        if answer is None or answer.returncode != 0:
            return _abandon_half_marked_session(tmux, name, key, answer)
    install_window_key_binding(tmux)
    return None


def _abandon_half_marked_session(
    tmux: Tmux, name: str, key: str, answer: object
) -> WorkspaceSessionResult:
    """Kill the session tmux just created but would not mark, and report
    FAILED naming the option it refused.

    These three options are what MAKE a tmux session a camp workspace
    session — the key binding's `if-shell` guard dispatches on
    `@camp_workspace`, and `window-dispatch` reads `@camp_group`/`@camp_slug`
    back to decide which workspace it composes into. A session missing any
    of them is one the binding will never fire for, so reporting CREATED
    would name an outcome that did not happen.

    The session is killed rather than left in place because leaving it turns
    a one-time failure into a permanent one: the next create at the same
    name answers from `create_or_connect_workspace_session`'s own
    `has_session` probe and connects the operator straight to the unmarked
    session, with no path back short of killing it by hand. Killing it here
    makes the next attempt an ordinary retry. The kill's own answer is
    discarded deliberately — this path is already reporting a failure, and a
    kill that also failed changes neither the outcome nor the words.

    The binding is NOT installed on this path: a server-global key grab is
    not something to do on the way out of a failed create.
    """
    tmux.kill_session(name)
    detail = "tmux could not be asked"
    if answer is not None:
        stderr = (getattr(answer, "stderr", "") or "").strip()
        detail = stderr or f"tmux exited {answer.returncode}"
    return WorkspaceSessionResult(
        WorkspaceSessionOutcome.FAILED,
        name,
        error=f"camp: tmux refused to mark the session with {key} — {detail}",
    )


class DoorState(Enum):
    """What one pass through the door found at the tmux boundary."""

    CONNECTED = "connected"
    CREATED = "created"
    TMUX_UNANSWERED = "tmux_unanswered"
    CREATE_FAILED = "create_failed"
    CREATE_REFUSED = "create_refused"


def _tmux_unanswered_reason(detail: str) -> str:
    """The operator-facing sentence for :data:`DoorState.TMUX_UNANSWERED`,
    carrying tmux's own words for *why* it could not be asked (the message
    of the `OSError` or `subprocess.TimeoutExpired`
    :meth:`~camp.launch.tmux.Tmux.has_session_with_reason` caught — the only
    way `has_session` itself ever answers `None`). It also points at `camp
    list`, which degrades and still prints rows where the door cannot: every
    outcome the door reports is a claim about the session, so an unanswered
    probe leaves it no half-answer to give.
    """
    return f"tmux did not answer — {detail} — run `camp list` to see what camp can still tell"


@dataclass(frozen=True)
class DoorProbe:
    """One pass through the door: what state it reached, the session name it
    derived getting there, and — for the two failure states only — the
    operator-facing ``reason`` both callers report verbatim.

    ``reconcile_outcome`` is set for every :data:`DoorState.CONNECTED` —
    whichever fold reached it — because each connect reads and corrects the
    window record against tmux before this probe is returned. It is `None`
    for every other state, including :data:`DoorState.CREATED`: the create
    arm has no session to read a record against yet.
    """

    state: DoorState
    session_name: str
    reason: str | None = None
    reconcile_outcome: ReconcileOutcome | None = None


def create_or_connect_workspace_session(
    group_name: str,
    slug: str,
    workspace_dir: Path,
    *,
    env: Mapping[str, str] | None = None,
    tmux: Tmux,
) -> DoorProbe:
    """Probe for the workspace's session and create it when there is none.

    One `has_session` probe decides: present is
    :data:`DoorState.CONNECTED`; absent dispatches
    :func:`create_workspace_session`, whose three answers fold in — created,
    already-existed (another camp won the race, which is a connect), and any
    other failure, which re-probes `has_session` before giving up rather than
    trusting tmux's stderr text alone. A probe tmux never answers at all is
    :data:`DoorState.TMUX_UNANSWERED`, whose reason carries tmux's own words
    for why (see :meth:`~camp.launch.tmux.Tmux.has_session_with_reason`),
    distinct from a create that was attempted and failed
    (:data:`DoorState.CREATE_FAILED`, carrying tmux's own words too).

    :func:`create_workspace_session` also raises two things this function
    catches, once, on the caller's behalf, into two distinct states rather
    than one shared one: :class:`~camp.launch.session.LaunchError` —
    unconditionally, before any tmux call — when the credential-store gate
    refuses, including when it cannot even be evaluated because some
    group's config is unreadable (see
    :func:`~camp.launch.eligibility.assert_not_a_credential_store`), folds
    into :data:`DoorState.CREATE_REFUSED` — a policy refusal, carrying the
    exception's own message; and `OSError` / `subprocess.TimeoutExpired`
    straight out of :meth:`~camp.launch.tmux.Tmux.new_session`, which does
    not swallow them (unlike this seam's other tmux calls) — the one call
    that starts the tmux SERVER when none is running, and so the one most
    likely to time out on a plugin-heavy `tmux.conf` or a loaded machine —
    fold into :data:`DoorState.CREATE_FAILED`, a transient failure, also
    carrying the exception's own message. Both doors share this call, so
    both would otherwise see a raw traceback instead of a refusal for
    either kind. `camp attach` turns `CREATE_REFUSED` into
    `RefusedCreateRefused` and `CREATE_FAILED` into `RefusedCreateFailed`,
    refusing on both with distinct wording; `camp new` reports both on the
    existing workspace-only path, because the workspace this gate is
    guarding a *session* for is already real and usable on disk regardless
    of whether the session comes up — the same reasoning that already
    routes an unanswered tmux and an ordinary create failure there — but
    still distinguishes the two in what it reports.
    :func:`create_workspace_session` itself keeps raising for its OWN direct
    callers (`launch_session`'s own gate, and the tests that exercise it
    directly) — only this shared door step catches any of this.

    *tmux* is required, never defaulted: both callers inject the seam their
    own wiring resolved, and a default constructed here would silently
    bypass it.
    """
    name = workspace_session_name(group_name, slug)
    present, unanswered_reason = tmux.has_session_with_reason(name)

    if present is None:
        return DoorProbe(
            DoorState.TMUX_UNANSWERED, name, reason=_tmux_unanswered_reason(unanswered_reason)
        )

    def connected() -> DoorProbe:
        # Every connect to a running session reconciles its record first —
        # whether the probe saw the session, the create raced a duplicate,
        # or the re-probe after a failed create found it.
        reconcile_outcome = reconcile_workspace_record(workspace_dir, name, tmux)
        return DoorProbe(DoorState.CONNECTED, name, reconcile_outcome=reconcile_outcome)

    if present:
        return connected()

    try:
        result = create_workspace_session(group_name, slug, workspace_dir, env=env, tmux=tmux)
    except LaunchError as exc:
        return DoorProbe(
            DoorState.CREATE_REFUSED,
            name,
            reason=f"refused to create {name} — {exc}",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoorProbe(
            DoorState.CREATE_FAILED,
            name,
            reason=f"could not create {name} — {exc}",
        )
    if result.outcome is WorkspaceSessionOutcome.CREATED:
        return DoorProbe(DoorState.CREATED, name)
    if result.outcome is WorkspaceSessionOutcome.ALREADY_EXISTED:
        return connected()
    if tmux.has_session(name):
        return connected()
    return DoorProbe(
        DoorState.CREATE_FAILED,
        name,
        reason=f"could not create {name} — {result.error}",
    )
