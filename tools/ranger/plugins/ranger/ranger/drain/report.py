"""The durable exit report + outcome-file substrate for a ranger drain.

Mirrors ``ranger.sweep.report``'s write core (see that module's docstring for
the full rationale on atomic 0600/0700 writes, corrupt-state refusal, and the
credential scrub funnel) closely enough that the atomic-write primitive
(``_write_0600``), the credential scrubber (``scrub_credentials``), and the
per-task outcome-file primitives (``outcomes_dir`` / ``outcome_path`` /
``read_outcome``) are imported straight from it rather than duplicated — the
outcome-file contract (0700 dir, single-path-component task-name
confinement, missing/empty file synthesizes a ``FAILED`` line) is identical
between a refine sweep's agent return and a drain's, so there is nothing to
specialize.

**What is different about a drain report.** A sweep's report is a flat set
of once-and-done buckets; a drain's ``pushed`` bucket is not final the
moment an executor agent returns — the pushed branch still has to clear
portage's monitor (CI, review, merge) before the task is *actually* done.
So ``pushed`` carries four **substates** — ``merged``, ``in-flight``,
``awaiting-human-approval``, ``monitor-timeout`` — and this module owns the
durable bookkeeping a later loop slice needs to track that: which tasks are
currently occupying the concurrency cap (``in_flight`` in the state file,
keyed by task id, each carrying a monitor deadline), so the cap survives a
restart exactly the way the report itself does. A task's cap slot is
reclaimed either when a monitor-terminal outcome is recorded
(:func:`resolve_monitor_outcome`) or when its deadline passes
(:func:`expire_in_flight`) — the latter renders as the distinct
``monitor-timeout`` substate and, per the loop's contract, never deletes the
task's ephemeral camp workspace: an expired monitor deadline means the loop
lost track of the PR, not that the work is disposable. That preservation is
enforced at render time too — a ``monitor-timeout`` workspace is listed in
its own section and never carries a ``camp remove`` command, even if the
loop hands it to :func:`finish` alongside the ordinary still-standing ones.
A task that leaves ``pushed`` for a terminal bucket has its pushed line
cleared as it goes, so no task ever renders under two buckets at once.

**Bucket set**: ``pushed``, ``blocked``, ``failed``, ``crashed``,
``skipped``, ``dropped``. ``pushed`` is the only bucket that also carries a
substate; the other five are flat, one line per task, exactly like a
sweep's. ``blocked``/``failed``/``skipped`` come straight off the drain
outcome grammar's ``BLOCKED``/``FAILED``/``SKIPPED`` tokens (see
:data:`DRAIN_OUTCOME_TOKENS` below). ``blocked`` is **only** ever an
executor agent's own operator-question park — the one that writes a
``## Refine — unresolved`` section onto the record for an operator to
answer. A portage *monitor*'s ``BLOCKED`` line means a PR it could not get
green, with no question parked anywhere, so it lands in ``failed``
(:func:`resolve_monitor_outcome`) rather than sending an operator to a
re-entry ritual with nothing to answer. ``crashed`` is reserved for a task
whose portage *monitor* left no readable outcome file at all — a distinct
signal from ``monitor-timeout``: a crash is the monitor process itself never
writing anything (see ``portage.monitor_outcome``'s module docstring), while
a timeout is this report's own deadline elapsing on a monitor that may still
be running. ``dropped`` is reserved for a task the drain queue named but
never dispatched this run at all (the concurrency cap was already full) —
distinct from every other bucket in that no agent, and no monitor, ever ran.

**Outcome grammar** (this module is its only home — ``ranger.cli.drain``'s
``record`` verb parses through it): ``PUSHED <branch> <sha> <diffstat>`` |
``BLOCKED <reason>`` | ``FAILED <reason>`` | ``SKIPPED <reason>``, every
token's argument mandatory, first-line-only parsing.

**PR data is never invented.** The PR links section and the
awaiting-human-approval bullet's ``gh pr edit`` command are built only from
two trusted sources: an outcome file's ``PUSHED`` line (branch/sha/diffstat)
and portage's ``prs.json`` sidecar (PR number/url) — read directly here as
plain JSON per its documented schema (``trailhead.vcs.github._sidecar_write``:
``{"schema_version": 1, "prs": [...], "external_tracker": null}``) rather
than through portage's VCS-provider seam, so this module carries no runtime
dependency on the portage plugin being installed. An agent's free-text
return is scrubbed like any other untrusted string, but it never supplies a
PR number or url — only ``prs.json`` does.

**Stranded-state recovery is documented, not handled here.** A failed push, a
parked block, a crashed coordinator, a stale lock, a stalled approval, or
this module's own corrupt-``.state.json`` refusal above each have a named,
pinned operator ritual in ``skills/execute/operator-rituals.md`` (alongside
the degraded-trust mode description) — this module raises and reports; it
never resolves those states on its own.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trailhead.paths import ensure_dir, state_dir

from ..sweep.names import validate_shell_safe_name
from ..sweep.report import (
    ReportError,
    _write_0600,
    outcome_path,
    outcomes_dir,
    read_outcome,
    scrub_credentials,
)

__all__ = [
    "ReportError",
    "outcomes_dir",
    "outcome_path",
    "read_outcome",
    "BUCKETS",
    "PUSHED_SUBSTATES",
    "DRAIN_OUTCOME_TOKENS",
    "MONITOR_OUTCOME_TOKENS",
    "DEFAULT_MONITOR_DEADLINE_HOURS",
    "parse_drain_outcome",
    "parse_pushed_argument",
    "parse_monitor_outcome",
    "start",
    "finish",
    "elected_vault",
    "in_flight_count",
    "inflight_cap",
    "mark_in_flight",
    "expire_in_flight",
    "resolve_monitor_outcome",
    "read_prs_sidecar",
    "pr_url_for_branch",
    "approval_command",
    "append_blocked",
    "append_failed",
    "append_skipped",
    "append_crashed",
    "append_dropped",
    "append_pushed_merged",
    "append_pushed_in_flight",
    "append_pushed_awaiting_approval",
    "append_pushed_monitor_timeout",
]

_REPORTS_SUBDIR = "reports"

BUCKETS = ("pushed", "blocked", "failed", "crashed", "skipped", "dropped")

_BUCKET_HEADINGS = {
    "pushed": "Pushed",
    "blocked": "Blocked",
    "failed": "Failed",
    "crashed": "Crashed",
    "skipped": "Skipped",
    "dropped": "Dropped",
}

#: The four substates a ``pushed`` entry travels through as portage's monitor
#: (or this report's own deadline) resolves it. See the module docstring.
PUSHED_SUBSTATES = ("merged", "in-flight", "awaiting-human-approval", "monitor-timeout")

_SUBSTATE_LABELS = {
    "merged": "Merged",
    "in-flight": "In flight",
    "awaiting-human-approval": "Awaiting human approval",
    "monitor-timeout": "Monitor timeout",
}

#: The drain outcome grammar an executor agent's return is held to.
#: ``PUSHED <branch> <sha> <diffstat>`` | ``BLOCKED <reason>`` | ``FAILED
#: <reason>`` | ``SKIPPED <reason>`` — every token's argument mandatory.
DRAIN_OUTCOME_TOKENS = frozenset({"PUSHED", "BLOCKED", "FAILED", "SKIPPED"})
_MAX_OUTCOME_ARG_CHARS = 200

#: Mirrors ``portage.monitor_outcome.MONITOR_OUTCOME_TOKENS`` — reimplemented
#: here rather than imported, the same call the drain queue module already
#: makes for camp's slug normalizer: portage may not be installed (vanilla
#: usage), and this grammar is small and stable enough that mirroring it
#: costs less than a cross-plugin import ranger cannot always resolve.
MONITOR_OUTCOME_TOKENS = frozenset({"MERGED", "READY", "BLOCKED", "STOPPED"})
_MONITOR_TOKENS_REQUIRING_ARGUMENT = frozenset({"READY", "BLOCKED", "STOPPED"})

#: A monitor slot's default lifetime before this report reclaims it as
#: ``monitor-timeout``. Configurable per call (:func:`mark_in_flight`'s
#: ``deadline_hours``) — this is only the fallback.
DEFAULT_MONITOR_DEADLINE_HOURS = 2.0


def parse_drain_outcome(line: str) -> tuple[str | None, str]:
    """Split a drain outcome line into ``(token, argument)``.

    Grammar: ``PUSHED <branch> <sha> <diffstat>`` | ``BLOCKED <reason>`` |
    ``FAILED <reason>`` | ``SKIPPED <reason>``. Returns ``(None, <line>)``
    when the first line is not one of the four tokens, or the token's
    mandatory argument is missing — the caller (``drain record``) treats
    that as a validation failure. Only the first physical line is
    considered, so trailing commentary an agent appends never corrupts the
    result.
    """
    first_line = line.strip().splitlines()[0].strip() if line.strip() else ""
    token, _, argument = first_line.partition(" ")
    argument = argument.strip()
    if token not in DRAIN_OUTCOME_TOKENS or not argument:
        return None, first_line[:_MAX_OUTCOME_ARG_CHARS]
    return token, argument


def parse_pushed_argument(argument: str) -> tuple[str, str, str] | None:
    """Split a ``PUSHED`` outcome's argument into ``(branch, sha, diffstat)``.

    The diffstat is the whole remainder — it carries spaces and commas by
    construction (``3 files changed, 45 insertions(+)``) — so only the first
    two fields are split off. Returns ``None`` when fewer than three fields
    are present: a ``PUSHED`` line the report cannot render is as unparseable
    as an unrecognized token, and ``drain record`` buckets both as failed.
    """
    parts = argument.split(None, 2)
    if len(parts) < 3 or not parts[2].strip():
        return None
    return parts[0], parts[1], parts[2].strip()


def parse_monitor_outcome(line: str) -> tuple[str | None, str]:
    """Split a monitor outcome line into ``(token, argument)``.

    Mirrors ``portage.monitor_outcome.parse_monitor_outcome`` exactly (see
    :data:`MONITOR_OUTCOME_TOKENS` for why it is reimplemented rather than
    imported). ``MERGED`` needs no argument; the other three always carry a
    reason. Public because ``ranger.drain.loop``'s teardown gate classifies
    the same monitor line this module's :func:`resolve_monitor_outcome`
    does — one mirror of portage's grammar inside the drain package, not
    two.
    """
    first_line = line.strip().splitlines()[0].strip() if line.strip() else ""
    token, _, argument = first_line.partition(" ")
    argument = argument.strip()
    if token not in MONITOR_OUTCOME_TOKENS:
        return None, first_line
    if token in _MONITOR_TOKENS_REQUIRING_ARGUMENT and not argument:
        return None, first_line
    return token, argument


def _validate_group(group: str) -> None:
    try:
        validate_shell_safe_name(group, what="group")
    except ValueError as exc:
        raise ReportError(str(exc)) from exc


def _state_path(report_path: Path) -> Path:
    return Path(report_path).with_suffix(".state.json")


def _load_state(report_path: Path) -> dict:
    state_path = _state_path(report_path)
    try:
        text = state_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ReportError(f"no report started at {report_path}: {e}")
    try:
        return json.loads(text)
    except ValueError as e:
        # Never a silent reset — see `ranger.sweep.report._load_state`'s
        # identical refusal for the full rationale, which applies verbatim
        # here: `appended_task_ids` and `in_flight` are this drain's only
        # record of what it has already written and dispatched, and this
        # failure happens before the drain's vault lock is released, so the
        # message names that too.
        raise ReportError(
            f"report state at {state_path} is unreadable JSON ({e}); this drain cannot be "
            "resumed — start a new drain, and keep this report for the lines it already holds; "
            "this failure happens before the drain's vault lock is released, so also clear "
            "that lock (`ranger drain start` reports it as stale, with the exact removal "
            "command, once its holder is gone) before starting the new one"
        )


def _write_state(report_path: Path, state: dict) -> None:
    _write_0600(_state_path(report_path), json.dumps(state, indent=2, sort_keys=True))


def _monitor_timeout_workspaces(state: dict) -> list[str]:
    """Return the workspace slugs held by ``monitor-timeout`` pushed entries.

    These are the one class of preserved workspace that must never appear in
    the report's `camp remove` guidance: an expired deadline means the loop
    lost track of the PR, not that the work is disposable, so a remove
    command next to one destroys the only handle back to it. Derived from the
    pushed entries rather than trusted from the caller's ``still_standing``
    list, so the two can never disagree.
    """
    return [
        entry["workspace"]
        for entry in state["pushed"].values()
        if entry.get("substate") == "monitor-timeout" and entry.get("workspace")
    ]


def _render(state: dict) -> str:
    parts = [
        "# Ranger drain report\n\n",
        f"**Group:** {state['group']}\n",
        f"**Vault:** {state['vault']}\n",
        f"**Queue size:** {state['queue_size']} tasks derived\n",
    ]
    if state.get("degraded"):
        parts.append(
            "\n> **Portage not installed — degraded trust.** This drain ran without portage: "
            "PR pushes could not be monitored, merged, or gated. Every `pushed` line below "
            "reflects only what the executor agent itself reported.\n"
        )
    parts.append("\n")

    for bucket in BUCKETS:
        parts.append(f"## {_BUCKET_HEADINGS[bucket]}\n\n")
        if bucket == "pushed":
            by_substate: dict[str, list[str]] = {s: [] for s in PUSHED_SUBSTATES}
            for entry in state["pushed"].values():
                by_substate.setdefault(entry["substate"], []).append(entry["line"])
            for substate in PUSHED_SUBSTATES:
                lines = by_substate[substate]
                if not lines:
                    continue
                parts.append(f"### {_SUBSTATE_LABELS[substate]}\n\n")
                parts.extend(lines)
                parts.append("\n")
        else:
            entries = state["buckets"][bucket]
            for entry in entries:
                parts.append(entry)
            if entries:
                parts.append("\n")

    preserved = _monitor_timeout_workspaces(state)

    still_standing = [
        ws for ws in (state.get("still_standing") or [])
        if (ws if isinstance(ws, str) else ws.get("slug")) not in preserved
    ]
    if still_standing:
        parts.append("## Still-standing workspaces\n\n")
        for ws in still_standing:
            slug = ws if isinstance(ws, str) else ws.get("slug")
            parts.append(f"- `{slug}` — `camp remove {slug}`\n")
        parts.append("\n")

    if preserved:
        parts.append("## Monitor-timeout workspaces\n\n")
        parts.append(
            "Preserved, **not** for removal — the monitor deadline expired, so the loop lost "
            "track of the PR rather than finishing with it. Check each PR before deciding "
            "anything about its workspace.\n\n"
        )
        for slug in preserved:
            parts.append(f"- `{slug}`\n")
        parts.append("\n")

    if state["finished"]:
        parts.append("---\n\n")
        parts.append(f"Report written to `{state['report_path']}`.\n")
    return "".join(parts)


def _write_report(report_path: Path, state: dict) -> None:
    _write_0600(Path(report_path), _render(state))


def _cleanup_orphaned_temp_files(reports_dir: Path, *, before: float) -> None:
    try:
        candidates = list(reports_dir.glob(".*.tmp"))
    except OSError:
        return
    for candidate in candidates:
        try:
            if candidate.stat().st_mtime < before:
                candidate.unlink()
        except OSError:
            continue


def start(
    group: str,
    vault: str,
    queue_size: int,
    *,
    degraded: bool = False,
    concurrency: int = 2,
    inflight_cap: int = 3,
    monitor_deadline_hours: float = DEFAULT_MONITOR_DEADLINE_HOURS,
    env: dict[str, str] | None = None,
) -> Path:
    """Create the report + state files for a fresh drain, return the report path.

    Mirrors ``ranger.sweep.report.start`` exactly (0600 report+state, 0700
    outcomes dir pre-created here rather than lazily, orphaned-temp-file
    hygiene), plus seeding ``in_flight: {}`` — the durable cap state a later
    loop slice reads and writes across process restarts — and ``degraded``,
    which drives the report's portage-absent banner.
    """
    _validate_group(group)
    reports_dir = ensure_dir(state_dir("ranger", env=env) / _REPORTS_SUBDIR / group, mode=0o700)
    now = datetime.now(timezone.utc)
    _cleanup_orphaned_temp_files(reports_dir, before=now.timestamp())
    timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    report_path = reports_dir / f"{timestamp}.md"
    ensure_dir(outcomes_dir(report_path), mode=0o700)

    state = {
        "group": group,
        "vault": vault,
        "degraded": degraded,
        # The loop's three bounds, persisted so a coordinator that restarts
        # mid-drain reads the same bounds the run started with rather than
        # today's defaults (see `ranger.cli.drain`'s `start` flags).
        "concurrency": concurrency,
        "inflight_cap": inflight_cap,
        "monitor_deadline_hours": monitor_deadline_hours,
        "queue_size": queue_size,
        "report_path": str(report_path),
        "appended_task_ids": [],
        "buckets": {b: [] for b in BUCKETS if b != "pushed"},
        # `pushed` is keyed by task id rather than appended flat, because a
        # task's substate changes over its lifecycle (in-flight -> merged,
        # say) — see the module docstring's Cap accounting section. The
        # latest write for a task id always wins; this is what lets
        # `mark_in_flight` followed later by `resolve_monitor_outcome` or
        # `expire_in_flight` move the same task id from one substate's
        # rendering to another instead of accumulating stale lines.
        "pushed": {},
        "in_flight": {},
        "still_standing": [],
        "finished": False,
    }
    _write_state(report_path, state)
    _write_report(report_path, state)
    return report_path


def elected_vault(report_path: Path) -> str:
    return _load_state(report_path)["vault"]


def finish(report_path: Path, *, still_standing: list[str] | None = None) -> None:
    """Append the footer + still-standing-workspaces section, name the report's own path.

    ``still_standing`` is the loop's own list of camp workspace slugs left
    unresolved at drain end. A ``monitor-timeout`` workspace passed in here is
    not dropped from the report — it is moved into its own
    preserved-not-removable section (:func:`_monitor_timeout_workspaces`), so
    the loop may pass every preserved workspace it knows about without having
    to remember which ones must not carry a `camp remove`.
    """
    state = _load_state(report_path)
    state["finished"] = True
    state["still_standing"] = list(still_standing or [])
    state["report_path"] = str(Path(report_path).resolve())
    _write_state(report_path, state)
    _write_report(report_path, state)


def _append(
    report_path: Path,
    bucket: str,
    task_id: str,
    render,
    untrusted: str = "",
) -> None:
    """Render one flat-bucket line from *render* + *untrusted*, scrubbed, and persist.

    Identical funnel to ``ranger.sweep.report._append`` — see that
    function's docstring for the full rationale on why scrubbing happens
    here, once, on the untrusted argument alone, and why re-appending the
    same task id is a no-op. Never used for ``bucket="pushed"`` — see
    :func:`_set_pushed`.
    """
    state = _load_state(report_path)
    if task_id in state["appended_task_ids"]:
        return
    state["appended_task_ids"].append(task_id)
    state["buckets"][bucket].append(render(scrub_credentials(untrusted)))
    _write_state(report_path, state)
    _write_report(report_path, state)


def _set_pushed(
    report_path: Path, task_id: str, substate: str, render, untrusted: str = "",
    *, workspace: str = "",
) -> None:
    """Set (or replace) *task_id*'s current pushed-substate line, scrubbed, and persist.

    Unlike :func:`_append`'s flat buckets, a pushed task's substate changes
    over its lifecycle — this always overwrites the task's prior entry
    (whatever substate it was in) rather than refusing a second write, which
    is what lets ``in-flight`` be superseded by ``merged`` or
    ``monitor-timeout`` for the same task id.
    """
    state = _load_state(report_path)
    state["pushed"][task_id] = {
        "substate": substate,
        "line": render(scrub_credentials(untrusted)),
        # Carried so `finish` can render the monitor-timeout workspaces as
        # their own preserved-not-removable list rather than folding them
        # into the generic `camp remove` guidance.
        "workspace": workspace,
    }
    _write_state(report_path, state)
    _write_report(report_path, state)


def _clear_pushed(report_path: Path, task_id: str) -> None:
    """Drop *task_id*'s pushed-substate line, if it has one.

    Called when a task leaves the ``pushed`` bucket for a terminal one:
    ``mark_in_flight`` already rendered an ``in-flight`` line, and leaving it
    standing renders the same task under two mutually exclusive buckets with
    nothing telling a reader which is current.
    """
    state = _load_state(report_path)
    if state["pushed"].pop(task_id, None) is None:
        return
    _write_state(report_path, state)
    _write_report(report_path, state)


def append_blocked(report_path: Path, task_id: str, reason: str) -> None:
    _append(report_path, "blocked", task_id, lambda safe: f"- `{task_id}` — {safe}\n", reason)


def append_failed(report_path: Path, task_id: str, reason: str) -> None:
    _append(report_path, "failed", task_id, lambda safe: f"- `{task_id}` — {safe}\n", reason)


def append_skipped(report_path: Path, task_id: str, reason: str) -> None:
    _append(report_path, "skipped", task_id, lambda safe: f"- `{task_id}` — {safe}\n", reason)


def append_crashed(report_path: Path, task_id: str, reason: str) -> None:
    """Report a task whose portage monitor left no readable outcome file.

    See the module docstring's Bucket set section for why this is distinct
    from ``monitor-timeout``: a crash is the monitor process never writing
    anything, a timeout is this report's own deadline elapsing on a monitor
    that may still be running.
    """
    _append(report_path, "crashed", task_id, lambda safe: f"- `{task_id}` — {safe}\n", reason)


def append_dropped(report_path: Path, task_id: str, reason: str) -> None:
    """Report a queued task the drain never dispatched at all this run."""
    _append(report_path, "dropped", task_id, lambda safe: f"- `{task_id}` — {safe}\n", reason)


def _pushed_line(task_id: str, branch: str, sha: str, diffstat: str, *, extra: str = "") -> str:
    return f"- `{task_id}` — `{branch}` @ `{sha}` — {diffstat}{extra}\n"


def _append_pushed(
    report_path: Path, task_id: str, substate: str, branch: str, sha: str, diffstat: str,
    *, extra: str = "",
) -> None:
    _set_pushed(
        report_path, task_id, substate,
        lambda safe_diffstat: _pushed_line(task_id, branch, sha, safe_diffstat, extra=extra),
        diffstat,
    )


def append_pushed_merged(
    report_path: Path, task_id: str, branch: str, sha: str, diffstat: str, *, pr_url: str | None = None,
) -> None:
    extra = f" — {pr_url}" if pr_url else ""
    _append_pushed(report_path, task_id, "merged", branch, sha, diffstat, extra=extra)


def append_pushed_in_flight(
    report_path: Path, task_id: str, branch: str, sha: str, diffstat: str,
    *, pr_url: str | None = None, cap_blocking: bool = True,
) -> None:
    extra = f" — {pr_url}" if pr_url else ""
    extra += " (holding the concurrency cap)" if cap_blocking else ""
    _append_pushed(report_path, task_id, "in-flight", branch, sha, diffstat, extra=extra)


def approval_command(pr_url_or_number: str) -> str:
    """Build the exact, copy-pasteable ``gh pr edit ... --add-label human-approved`` command.

    Never auto-run — see the module docstring's PR-data section: built only
    from ``prs.json``'s PR url/number, never from an agent's free text.
    """
    return f"gh pr edit {pr_url_or_number} --add-label human-approved"


def append_pushed_awaiting_approval(
    report_path: Path, task_id: str, branch: str, sha: str, diffstat: str,
    *, pr_url_or_number: str,
) -> None:
    # No PR reference means `prs.json` never named one for this branch, and a
    # `gh pr edit  --add-label …` with the reference missing is an
    # uncopyable command that still reads as a runnable instruction. Say what
    # is missing instead.
    if pr_url_or_number:
        command = approval_command(pr_url_or_number)
        extra = f" — awaiting human approval\n\nApprove with:\n\n```\n{command}\n```\n"
    else:
        extra = (
            " — awaiting human approval; no PR reference was recorded for this branch, "
            "so no approval command can be given — find the PR by its branch and apply "
            "the `human-approved` label by hand\n"
        )

    def render(safe_diffstat: str) -> str:
        return f"- `{task_id}` — `{branch}` @ `{sha}` — {safe_diffstat}{extra}"

    _set_pushed(report_path, task_id, "awaiting-human-approval", render, diffstat)


def append_pushed_monitor_timeout(
    report_path: Path, task_id: str, branch: str, sha: str, diffstat: str, *, workspace: str,
) -> None:
    extra = (
        f" — monitor deadline expired; ephemeral workspace `{workspace}` preserved, "
        "not removed"
    )
    _set_pushed(
        report_path, task_id, "monitor-timeout",
        lambda safe_diffstat: _pushed_line(task_id, branch, sha, safe_diffstat, extra=extra),
        diffstat,
        workspace=workspace,
    )


def mark_in_flight(
    report_path: Path,
    task_id: str,
    *,
    branch: str,
    sha: str,
    diffstat: str,
    workspace: str,
    cap_blocking: bool = True,
    deadline_hours: float | None = None,
    now: datetime | None = None,
) -> None:
    """Mark *task_id* as occupying a cap slot, durably, with a monitor deadline.

    Called when a task is dispatched to portage's monitor. Also renders the
    ``in-flight`` pushed-substate line immediately, so the report always
    reflects the cap's current occupants without a separate render call.

    **Refused in degraded mode.** A degraded drain (portage absent — see
    ``start``'s ``degraded`` flag) has no monitor outcome file to ever
    resolve this slot against, so a slot opened here would occupy the cap
    forever with no way to close it. Refusing loudly, rather than silently
    no-opping, is what keeps "portage-absent -> in-flight set always empty"
    a property of the substrate itself instead of a discipline every caller
    has to remember to uphold.
    """
    state = _load_state(report_path)
    if state.get("degraded"):
        raise ReportError(
            f"cannot mark {task_id!r} in-flight: this drain is degraded (portage absent), "
            "so its monitor outcome could never resolve the slot — the in-flight cap stays "
            "vacuous in degraded mode"
        )
    # Falls back to the deadline this drain was *started* with, not to the
    # module default — the `--monitor-deadline` an operator passed is only
    # honored if every slot opened afterwards reads it back from the state.
    if deadline_hours is None:
        deadline_hours = state.get("monitor_deadline_hours", DEFAULT_MONITOR_DEADLINE_HOURS)
    now = now or datetime.now(timezone.utc)
    deadline = now + timedelta(hours=deadline_hours)
    state["in_flight"][task_id] = {
        "branch": branch,
        "sha": sha,
        "diffstat": diffstat,
        "workspace": workspace,
        "cap_blocking": cap_blocking,
        "deadline": deadline.isoformat(),
    }
    _write_state(report_path, state)
    append_pushed_in_flight(
        report_path, task_id, branch, sha, diffstat, cap_blocking=cap_blocking,
    )


def in_flight_count(report_path: Path) -> int:
    """Return the number of tasks currently occupying a cap slot.

    Re-derived from the state file alone on every call — the durability
    property a process restart depends on: a fresh ``Report`` object reading
    the same ``.state.json`` sees the same count a crashed process would
    have.
    """
    return len(_load_state(report_path)["in_flight"])


def inflight_cap(report_path: Path) -> int:
    """Return the in-flight cap this drain was *started* with.

    Read back from the state file rather than re-passed per call, for the
    same reason the deadline is (see :func:`mark_in_flight`): a caller free
    to supply its own bound each time is a caller that can quietly stop
    honoring the one `ranger drain start --inflight-cap` set.
    """
    return _load_state(report_path).get("inflight_cap", 3)


def resolve_monitor_outcome(
    report_path: Path,
    task_id: str,
    monitor_outcome_line: str | None,
    *,
    pr_url: str | None = None,
    pr_url_or_number: str | None = None,
    prs_json: Path | str | None = None,
) -> str:
    """Resolve a monitor-terminal outcome for an in-flight task, freeing its cap slot.

    ``monitor_outcome_line`` is the raw text of the monitor's own outcome
    file, or ``None``/empty if that file could not be read at all — the
    latter buckets ``crashed`` (see the module docstring), never
    ``monitor-timeout``, which is reserved for :func:`expire_in_flight`'s own
    deadline check. Returns the bucket the task landed in
    (``"pushed"``/``"failed"``/``"crashed"``).

    ``prs_json`` is portage's sidecar path: when given, the PR url/number for
    this task's own in-flight branch is read from it (never from anything an
    agent wrote) and supplies whichever of ``pr_url`` / ``pr_url_or_number``
    the caller did not pass explicitly.
    """
    state = _load_state(report_path)
    entry = state["in_flight"].pop(task_id, {})
    _write_state(report_path, state)
    branch, sha, diffstat = entry.get("branch", ""), entry.get("sha", ""), entry.get("diffstat", "")

    if prs_json is not None:
        sidecar_url, sidecar_number = pr_url_for_branch(read_prs_sidecar(prs_json), branch)
        pr_url = pr_url or sidecar_url
        pr_url_or_number = pr_url_or_number or sidecar_url or sidecar_number

    if not monitor_outcome_line or not monitor_outcome_line.strip():
        _clear_pushed(report_path, task_id)
        append_crashed(report_path, task_id, "monitor left no readable outcome file")
        return "crashed"

    token, argument = parse_monitor_outcome(monitor_outcome_line)
    if token == "MERGED":
        append_pushed_merged(report_path, task_id, branch, sha, diffstat, pr_url=pr_url)
        return "pushed"
    if token == "READY":
        target = pr_url_or_number or pr_url or ""
        append_pushed_awaiting_approval(
            report_path, task_id, branch, sha, diffstat, pr_url_or_number=target,
        )
        return "pushed"
    # `BLOCKED`, `STOPPED`, or an unparseable monitor line all land in
    # `failed`. A monitor's BLOCKED is a PR it could not get green, not the
    # drain's `blocked` bucket — that one is reserved for an executor agent's
    # own operator-question park, which carries a `## Refine — unresolved`
    # section on the record and a re-entry ritual that answers it. Routing a
    # red PR there sends the operator to a ritual with nothing to answer.
    _clear_pushed(report_path, task_id)
    append_failed(
        report_path,
        task_id,
        argument or (f"monitor reported {token}" if token else "monitor stopped"),
    )
    return "failed"


def _deadline(task_id: str, entry: dict) -> datetime:
    """Parse one in-flight entry's deadline, or refuse by name.

    Inside this module's ``ReportError`` contract like every other
    malformed-state path: a raw ``ValueError`` out of ``fromisoformat``
    escapes the CLI's refusal funnel and reaches an unattended operator as a
    traceback with no named recovery.
    """
    raw = entry.get("deadline")
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError) as exc:
        raise ReportError(
            f"in-flight entry for {task_id!r} carries an unreadable deadline {raw!r} ({exc}); "
            "the cap cannot be reclaimed from this state — see the corrupt-state-file ritual "
            "in skills/execute/operator-rituals.md"
        ) from exc


def expire_in_flight(report_path: Path, *, now: datetime | None = None) -> list[str]:
    """Reclaim every in-flight task whose monitor deadline has passed.

    Each reclaimed task is removed from ``in_flight`` (freeing its cap slot)
    and rendered as the ``monitor-timeout`` pushed substate, with its
    ephemeral workspace named but never scheduled for removal. Returns the
    list of reclaimed task ids.
    """
    now = now or datetime.now(timezone.utc)
    state = _load_state(report_path)
    expired = [
        task_id
        for task_id, entry in state["in_flight"].items()
        if _deadline(task_id, entry) <= now
    ]
    reclaimed: list[dict] = []
    for task_id in expired:
        reclaimed.append(state["in_flight"].pop(task_id))
    _write_state(report_path, state)

    for task_id, entry in zip(expired, reclaimed):
        append_pushed_monitor_timeout(
            report_path, task_id, entry.get("branch", ""), entry.get("sha", ""),
            entry.get("diffstat", ""), workspace=entry.get("workspace", ""),
        )
    return expired


def read_prs_sidecar(sidecar_path: Path) -> list[dict]:
    """Return the ``prs`` list from portage's ``prs.json`` sidecar, or ``[]``.

    Reads the file directly per its documented schema (see the module
    docstring) rather than through portage's VCS-provider seam — a missing
    file, malformed JSON, or a JSON value that is not the documented shape
    all degrade to an empty list rather than raising: an absent or corrupt
    sidecar means the PR-links section has nothing to show, not that the
    report itself should fail.
    """
    try:
        text = Path(sidecar_path).read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    prs = data.get("prs")
    return prs if isinstance(prs, list) else []


def pr_url_for_branch(prs: list[dict], branch: str) -> tuple[str | None, str | None]:
    """Return ``(url, pr_number)`` for *branch* in a ``read_prs_sidecar`` list, or ``(None, None)``."""
    for pr in prs:
        if isinstance(pr, dict) and pr.get("branch") == branch:
            return pr.get("url"), pr.get("pr_number")
    return None, None
