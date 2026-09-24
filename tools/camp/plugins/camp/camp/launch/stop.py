"""The tmux seam's re-export point, plus the shared re-poll-until-gone wait.

The tmux seam itself — `Tmux`, its tri-state answers, and the `=`-target
property — lives in `camp.launch.tmux`, not here; this module imports and
re-exports it (`Tmux`, `TmuxSession`, `SessionListing`, `UNANSWERED`,
`_Unanswered`) so every existing importer of `camp.launch.stop` keeps working
unchanged. New code should import the seam from `camp.launch.tmux` directly.

`poll_for_absence` is `camp.launch.stop_workspace.stop_workspace`'s
re-poll-until-gone wait after a kill: tmux tears a session down server-side
as the call returns, so this is a small budget for a busy server rather than
a wait for a process to die. Success is absence, not issuance — a kill that
returns is not proof the name is gone, so the caller polls until it is or the
budget expires.
"""

from __future__ import annotations

import subprocess  # noqa: F401 — kept for `stop.subprocess.run` re-export, see below
import time
from typing import Any, Callable

from .tmux import (  # noqa: F401 — re-exported for this module's existing importers
    TMUX_TIMEOUT_SECONDS,
    UNANSWERED,
    SessionListing,
    Tmux,
    TmuxSession,
    _NO_SERVER_STDERR_RE,
    _Unanswered,
)

#: ``subprocess`` is kept imported here (unused by this module's own code)
#: because the test suite patches ``stop.subprocess.run`` to drive the tmux
#: seam through this module's name — patching an attribute on the shared
#: ``subprocess`` module object affects every importer of it, including
#: ``camp.launch.tmux``, so the patch still reaches the seam even though the
#: seam itself now lives there.
#:
#: The names imported above but not referenced in this module's own code
#: (`TMUX_TIMEOUT_SECONDS`, `UNANSWERED`, `SessionListing`, `TmuxSession`,
#: `_NO_SERVER_STDERR_RE`) are deliberate re-exports, not dead imports — see
#: the module docstring.
__all__ = [
    "TMUX_TIMEOUT_SECONDS",
    "UNANSWERED",
    "SessionListing",
    "Tmux",
    "TmuxSession",
    "_NO_SERVER_STDERR_RE",
    "_Unanswered",
]

#: How long to keep re-polling for the name's absence after the kill, and how
#: often. tmux tears the session down server-side as the call returns, so this
#: is a small budget for a busy server rather than a wait for a process to die.
#:
#: The budget is WALL CLOCK, measured on a monotonic clock rather than summed
#: from the sleeps: each re-poll asks tmux a question that may itself take up to
#: `TMUX_TIMEOUT_SECONDS` to answer, so counting only the sleeps would bound the
#: idle time and leave the time an operator actually waits unbounded.
POLL_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.1


def poll_for_absence(
    tmux: Any,
    name: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    poll_timeout: float = POLL_TIMEOUT_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> bool | None:
    """Poll ``tmux.has_session(name)`` until tmux reports the name gone, the
    budget expires, or tmux stops answering.

    Returns ``True`` the moment tmux answers the name is gone, ``False`` if
    the budget expires while tmux still reports it present, and ``None`` the
    moment tmux answers ``None`` (did not answer at all) — the only three
    readings a caller may act on. Absence of the name is the only evidence of
    success this seam accepts (see the module docstring): an unanswered
    question must never be read as absence, and must never be conflated with
    "still present" either, since neither is what was observed.

    `camp.launch.stop_workspace.stop_workspace` (`camp stop`) is the sole
    caller: the budget is WALL CLOCK, measured on `monotonic` rather than
    summed from the sleeps, because each `has_session` call may itself cost
    up to `TMUX_TIMEOUT_SECONDS` — counting only the sleeps would leave the
    time an operator actually waits unbounded.
    """
    deadline = monotonic() + poll_timeout
    while True:
        present = tmux.has_session(name)
        if present is None:
            return None
        if not present:
            return True
        if monotonic() >= deadline:
            return False
        sleep(poll_interval)
