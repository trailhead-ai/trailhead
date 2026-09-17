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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from .eligibility import assert_not_a_credential_store
from .naming import workspace_session_name
from .tmux import Tmux

#: The exact stderr shape tmux prints for a `new-session` refused because the
#: name is already live, confirmed against tmux 3.7c. Matched as a substring
#: of the whole stderr line, never as the whole line, because tmux does not
#: guarantee nothing precedes it.
_DUPLICATE_SESSION_MARKER = "duplicate session:"


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
    any tmux call if *workspace_dir* is at, under, or above a credential
    store — that rule is unconditional and independent of the retired
    launch allowlist, and every call site that roots a session applies it.
    """
    assert_not_a_credential_store(Path(workspace_dir), env=env)

    tmux = tmux if tmux is not None else Tmux()
    name = workspace_session_name(group_name, slug)
    result = tmux.new_session(name, cwd=workspace_dir, env=env)

    if result.returncode == 0:
        return WorkspaceSessionResult(WorkspaceSessionOutcome.CREATED, name)

    stderr = result.stderr or ""
    if _DUPLICATE_SESSION_MARKER in stderr:
        return WorkspaceSessionResult(WorkspaceSessionOutcome.ALREADY_EXISTED, name)
    return WorkspaceSessionResult(WorkspaceSessionOutcome.FAILED, name, error=stderr)
