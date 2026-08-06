"""The two decisions the drain loop makes that are worth a type, not prose.

Everything else the coordinator does is prose (`skills/execute/SKILL.md`) —
dispatch, the pool, status edges. These two are here because each is a
*classification of another tool's JSON*, and prose that classifies JSON drifts
from the JSON silently:

**The sync gate.** `camp sync --json` sets its top-level `status` to
``ok_with_warnings`` **only when ``errors > 0``** — a member left un-synced
because it was dirty, off main, or absent reports ``ok`` at the top level with
the real signal buried in that member's own ``action``. A loop that gates on the
top-level status therefore builds every task on top of a stale base and never
notices. :func:`classify_sync` reads the per-member map instead, and treats an
action it does not recognize as blocking rather than as clean — a new camp action
must be classified deliberately, not defaulted into "fine".

The per-member map is keyed ``members`` by camp's group-config implementation
(``camp/provision/lifecycle.py``'s ``cmd_sync_group``) and ``siblings`` by its
spine implementation (``camp/spine.py``'s ``cmd_sync``). Both are live; this
reads whichever is present.

**Teardown eligibility.** The drain's ephemeral workspace per task is removed at
monitor-terminal — but "terminal for the monitor" is not the same as "nothing
left for a human to do here". Only ``MERGED`` means the work landed and the
workspace is disposable. ``READY`` (awaiting the human approval signal),
``BLOCKED``, and ``STOPPED`` are all terminal for the monitor while still naming
something an operator may need the workspace to finish, so they preserve it and
the loop lists it still-standing. A missing or empty outcome file is the crash
signal (see ``portage.monitor_outcome``) and preserves the workspace too, as does
an expired monitor deadline — an expiry means the loop lost track of the PR, not
that the work is disposable. In degraded mode (portage absent) there is no
monitor to reach a terminal state at all, so teardown happens at push.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The monitor grammar this gate classifies is mirrored from portage in exactly
# one place inside the drain package (see `report.parse_monitor_outcome`), so
# the teardown gate and the report's own cap resolution can never drift into
# disagreeing about what a monitor line says.
from .report import parse_monitor_outcome

__all__ = [
    "SYNCED_ACTIONS",
    "BLOCKING_SYNC_ACTIONS",
    "SyncVerdict",
    "classify_sync",
    "TeardownDecision",
    "teardown_decision",
]

#: The per-member `action` values that mean "this member is now at origin/main".
#: Everything else — named in :data:`BLOCKING_SYNC_ACTIONS` or not — blocks.
SYNCED_ACTIONS = frozenset({"ff", "reset-force", "up-to-date", "noop"})

#: The three silent skips the top-level status hides, named so a refusal can
#: quote camp's own vocabulary back to the operator.
BLOCKING_SYNC_ACTIONS = ("skip-dirty", "skip-off-main", "absent")

_MEMBER_KEYS = ("members", "siblings")


@dataclass(frozen=True)
class SyncVerdict:
    """Whether `camp sync` left every member on origin/main, and why not."""

    ok: bool
    blocking: list[tuple[str, str]] = field(default_factory=list)
    reason: str = ""


def classify_sync(report: dict) -> SyncVerdict:
    """Classify one `camp sync --json` report into a go / no-go for the drain.

    Blocking, in order of what an operator most needs told: any member whose
    ``action`` is not in :data:`SYNCED_ACTIONS` (each named with its action), a
    report carrying no per-member map at all, or a top-level ``status`` other
    than ``ok``. See the module docstring for why the top-level status is never
    the primary signal.
    """
    members = None
    for key in _MEMBER_KEYS:
        candidate = report.get(key)
        if isinstance(candidate, dict):
            members = candidate
            break

    if members is None:
        return SyncVerdict(
            ok=False,
            reason=(
                "camp sync --json carried no per-member report (neither `members` nor "
                "`siblings`) — the drain cannot confirm any member is at origin/main"
            ),
        )

    blocking = [
        (name, str((entry or {}).get("action", "")))
        for name, entry in members.items()
        if str((entry or {}).get("action", "")) not in SYNCED_ACTIONS
    ]
    if blocking:
        detail = ", ".join(f"{name} ({action or 'no action reported'})" for name, action in blocking)
        return SyncVerdict(
            ok=False,
            blocking=blocking,
            reason=f"camp sync left members off origin/main: {detail}",
        )

    status = report.get("status")
    if status != "ok":
        return SyncVerdict(
            ok=False,
            reason=f"camp sync reported status={status!r} — every member synced, but the sync itself errored",
        )

    return SyncVerdict(ok=True)


@dataclass(frozen=True)
class TeardownDecision:
    """Whether this task's ephemeral camp workspace may be removed, and why."""

    teardown: bool
    crashed: bool = False
    reason: str = ""


def teardown_decision(
    monitor_outcome_line: str | None, *, degraded: bool = False, expired: bool = False
) -> TeardownDecision:
    """Decide whether to `camp remove` this task's workspace.

    ``monitor_outcome_line`` is the raw text of portage monitor's outcome file,
    or ``None``/empty when that file could not be read at all. ``degraded`` is
    the drain's portage-absent mode (teardown happens at push, since no monitor
    will ever run). ``expired`` marks a slot the monitor deadline already
    reclaimed. See the module docstring for the full rationale on each branch.
    """
    if expired:
        return TeardownDecision(
            teardown=False,
            reason="monitor deadline expired — the loop lost track of the PR; workspace preserved",
        )
    if degraded:
        return TeardownDecision(
            teardown=True,
            reason="portage absent (degraded) — no monitor will run; torn down at push",
        )
    if not monitor_outcome_line or not monitor_outcome_line.strip():
        return TeardownDecision(
            teardown=False,
            crashed=True,
            reason="monitor left no readable outcome file (crashed) — workspace preserved",
        )

    token, _argument = parse_monitor_outcome(monitor_outcome_line)
    if token == "MERGED":
        return TeardownDecision(teardown=True, reason="monitor reported the PR merged")
    if token is not None:
        return TeardownDecision(
            teardown=False,
            reason=(
                f"monitor reported {token} — terminal for the monitor, but a human may still "
                "need this workspace; preserved and reported still-standing"
            ),
        )
    return TeardownDecision(
        teardown=False,
        reason=(
            "monitor's outcome file did not carry a recognized terminal token — workspace "
            "preserved rather than removed on an unparseable signal"
        ),
    )
