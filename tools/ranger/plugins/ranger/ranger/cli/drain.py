"""``ranger drain`` — the verbs a drain is driven through.

Mirrors `ranger sweep`'s split (see `cli/sweep.py`'s module docstring for the
full rationale): a coordinating loop drives `start` once, `derive` between
tasks, `record` once per dispatched task, and `finish` once — each a
separate process, so a coordinator that dies between two of them leaves
recoverable, on-disk state rather than nothing. The lock substrate is reused
verbatim from the sweep — `drain` and `sweep` contend on the identical
`state_dir("ranger")/locks/<vault>.lock` path, so an operator cannot run a
refine sweep and an execute drain against the same vault at once. The report
substrate is `ranger.drain.report` — a sibling of `ranger.sweep.report`, not
the same module, because a drain's `pushed` bucket carries the in-flight
monitor cap sweep's buckets have no analog for (see that module's docstring).

`start` seeds the report (so a drain's report exists from the same moment
its lock does, like a sweep's); `record` classifies one dispatched task's
outcome line and appends its bucket line — including `PUSHED`, which renders
the branch/sha/diffstat and, given portage's `prs.json` sidecar, the PR link;
`finish` writes the report footer (still-standing workspaces + the report's
own path) before releasing the lock.

**Everything the loop needs is a verb.** Beyond those four, the drain's
durable monitor-cap bookkeeping and its two JSON classifications are
exposed here rather than left to the coordinator's prose, because prose
cannot hold state across a restart and prose that re-derives another tool's
JSON drifts from it silently:

- `drain inflight mark|count|resolve|expire` — open a cap slot when a task
  is handed to portage's monitor, ask whether dispatch must pause, close a
  slot against the monitor's own outcome file, and reclaim slots whose
  deadline passed.
- `drain crashed` / `drain dropped` — the two buckets no outcome file ever
  produces: a monitor that wrote nothing, and a queued task the drain never
  dispatched at all.
- `drain sync-gate` — run `camp sync --json` and classify it
  (`ranger.drain.loop.classify_sync`); exit 1 means a member is off
  origin/main, whatever the top-level status said.
- `drain teardown-check` — whether this task's ephemeral workspace may be
  `camp remove`d (`ranger.drain.loop.teardown_decision`).

Untrusted text never arrives as a command-line string: a monitor's outcome
reaches `inflight resolve` as a file path, exactly as an agent's outcome
reaches `record`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

#: The record-name shape ``--task`` must match, identical to
#: `ranger.cli.sweep`'s `_RECORD_NAME_RE` — the id is destined for the same
#: kind of operator-facing rendering a future report writes, so it is held
#: to the same shell-safe allowlist before it is ever accepted.
_RECORD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


def add_drain_subparser(sub) -> None:
    p_drain = sub.add_parser("drain", help="Run and record an unattended execute drain")
    p_drain_sub = p_drain.add_subparsers(dest="drain_action", required=True)

    p_start = p_drain_sub.add_parser(
        "start",
        help="Check preconditions, lock the vault, derive the drain queue, print its JSON",
    )
    p_start.add_argument(
        "--holder-pid", type=int, metavar="PID",
        help=(
            "Pid of the long-lived process that constitutes this drain — the coordinator "
            "session or scheduler wrapper. Recorded in the lock; see `ranger sweep start`'s "
            "own flag for the full rationale, which applies identically here."
        ),
    )

    # The loop's three bounds. Defaults live here, in the CLI, rather than in
    # the coordinator's prose: prose that carries a number drifts from the
    # number the state file actually holds, and the state file is what a
    # restarted coordinator reads.
    p_start.add_argument(
        "--concurrency", type=int, default=2, metavar="N",
        help="How many executor agents may be in flight at once (default 2)",
    )
    p_start.add_argument(
        "--inflight-cap", type=int, default=3, metavar="N",
        help=(
            "How many pushed-but-unmerged tasks may hold a monitor slot at once before "
            "dispatch pauses (default 3)"
        ),
    )
    p_start.add_argument(
        "--monitor-deadline", type=float, default=2.0, metavar="HOURS",
        help=(
            "How long a monitor slot may stay in flight before it is reclaimed into the "
            "`monitor-timeout` bucket, in hours (default 2)"
        ),
    )

    p_derive = p_drain_sub.add_parser(
        "derive", help="Re-derive and print the elected vault's drain queue classification"
    )
    p_derive.add_argument("--json", action="store_true", help="Emit the queue as a JSON array")

    p_record = p_drain_sub.add_parser(
        "record", help="Validate and append one task's outcome to the drain report"
    )
    p_record.add_argument(
        "--report", metavar="PATH",
        help="Report to append to; when omitted, the outcome is only validated (not persisted)",
    )
    p_record.add_argument("--task", required=True, metavar="ID", help="Task record id")
    p_record.add_argument(
        "--outcome", required=True, metavar="LINE",
        help="The dispatched agent's one-line drain outcome",
    )

    p_record.add_argument(
        "--prs-json", metavar="PATH",
        help=(
            "Portage's `prs.json` sidecar; a `PUSHED` outcome's branch is looked up in it "
            "for the PR link the report renders. Never read from an agent's own text."
        ),
    )

    # The cap substrate, exposed as verbs rather than left to prose: the
    # in-flight set is durable state on disk, and a coordinator that tracked
    # it in its own transcript would lose the whole cap to a restart.
    p_inflight = p_drain_sub.add_parser(
        "inflight", help="Drive the durable in-flight monitor cap (mark|count|resolve|expire)"
    )
    p_inflight_sub = p_inflight.add_subparsers(dest="inflight_action", required=True)

    p_mark = p_inflight_sub.add_parser(
        "mark", help="Open a cap slot for a task just handed to portage's monitor"
    )
    p_mark.add_argument("--report", required=True, metavar="PATH")
    p_mark.add_argument("--task", required=True, metavar="ID")
    p_mark.add_argument("--branch", required=True, metavar="NAME")
    p_mark.add_argument("--sha", required=True, metavar="SHA")
    p_mark.add_argument("--diffstat", required=True, metavar="TEXT")
    p_mark.add_argument(
        "--workspace", required=True, metavar="SLUG",
        help="The task's ephemeral camp workspace, named in the report if the slot times out",
    )
    p_mark.add_argument(
        "--deadline-hours", type=float, default=None, metavar="HOURS",
        help="Override this one slot's deadline; defaults to the drain's own `--monitor-deadline`",
    )

    p_count = p_inflight_sub.add_parser(
        "count", help="Report how many cap slots are occupied, and whether dispatch must pause"
    )
    p_count.add_argument("--report", required=True, metavar="PATH")

    p_resolve = p_inflight_sub.add_parser(
        "resolve", help="Close a cap slot against portage monitor's own outcome file"
    )
    p_resolve.add_argument("--report", required=True, metavar="PATH")
    p_resolve.add_argument("--task", required=True, metavar="ID")
    p_resolve.add_argument(
        "--monitor-outcome-file", required=True, metavar="PATH",
        help=(
            "Portage monitor's outcome file. Missing, unreadable, or empty is the crash "
            "signal — the slot is freed and the task buckets `crashed`."
        ),
    )
    p_resolve.add_argument("--prs-json", metavar="PATH", help="Portage's `prs.json` sidecar")

    p_expire = p_inflight_sub.add_parser(
        "expire", help="Reclaim every cap slot whose monitor deadline has passed"
    )
    p_expire.add_argument("--report", required=True, metavar="PATH")

    p_crashed = p_drain_sub.add_parser(
        "crashed", help="Report a task whose monitor left no readable outcome file"
    )
    p_crashed.add_argument("--report", required=True, metavar="PATH")
    p_crashed.add_argument("--task", required=True, metavar="ID")
    p_crashed.add_argument("--reason", required=True, metavar="TEXT")

    p_dropped = p_drain_sub.add_parser(
        "dropped", help="Report a queued task this drain never dispatched at all"
    )
    p_dropped.add_argument("--report", required=True, metavar="PATH")
    p_dropped.add_argument("--task", required=True, metavar="ID")
    p_dropped.add_argument("--reason", required=True, metavar="TEXT")

    p_sync_gate = p_drain_sub.add_parser(
        "sync-gate",
        help="Run `camp sync --json` and classify it into a go / no-go for the next task",
    )
    p_sync_gate.add_argument("--json", action="store_true", help="Emit the verdict as JSON")

    p_teardown = p_drain_sub.add_parser(
        "teardown-check",
        help="Decide whether this task's ephemeral workspace may be `camp remove`d",
    )
    p_teardown.add_argument(
        "--monitor-outcome-file", metavar="PATH",
        help="Portage monitor's outcome file; missing or empty is the crash signal",
    )
    p_teardown.add_argument(
        "--degraded", action="store_true", help="This drain runs degraded (portage absent)"
    )
    p_teardown.add_argument(
        "--expired", action="store_true", help="This slot's monitor deadline already expired"
    )

    p_finish = p_drain_sub.add_parser("finish", help="Write the report footer, release the vault lock")
    p_finish.add_argument(
        "--report", metavar="PATH",
        help="Report to finish; when omitted, only the lock is released",
    )
    p_finish.add_argument(
        "--still-standing", metavar="SLUG", action="append", default=[],
        help="An unresolved ephemeral camp workspace slug (repeatable)",
    )
    p_finish.add_argument("--vault", required=True, metavar="NAME", help="The locked vault")
    p_finish.add_argument(
        "--token", required=True, metavar="TOKEN",
        help="The `lock_token` this drain's `start` returned; proves the lock is this run's",
    )

    for parser in (
        p_start, p_derive, p_record, p_finish, p_mark, p_count, p_resolve, p_expire,
        p_crashed, p_dropped, p_sync_gate, p_teardown,
    ):
        parser.set_defaults(func=cmd_drain)


def _fail(message: str) -> int:
    print(f"ranger: {message}", file=sys.stderr)
    return 1


def _record_id(task: str) -> str:
    """Normalize ``--task`` to a full ``task/<name>`` record id, or reject it.

    Identical contract to `ranger.cli.sweep`'s own `_record_id` — see that
    function's docstring for why the shape is validated before the id is
    ever rendered anywhere.
    """
    name = task[len("task/"):] if task.startswith("task/") else task
    if not _RECORD_NAME_RE.match(name):
        raise ValueError(
            f"--task {task!r} is not a valid record id — expected `task/<name>` with "
            f"<name> matching {_RECORD_NAME_RE.pattern}"
        )
    return f"task/{name}"


def print_drain_queue(entries: list[dict], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(entries))
        return
    for e in entries:
        print(f"{e['name']} status={e['status']} bucket={e['bucket']} slug={e['slug']}")


def _cmd_drain_start(args) -> int:
    from ..drain import preflight as drain_preflight
    from ..drain import queue as drain_queue_mod
    from ..drain import report as drain_report_mod
    from ..sweep import lock as lock_mod

    # Validated before any precondition runs, so a typo'd bound refuses
    # without touching the filesystem — same posture as every other `start`
    # refusal (see `ranger.drain.preflight`'s module docstring).
    for flag, value in (
        ("--concurrency", args.concurrency),
        ("--inflight-cap", args.inflight_cap),
        ("--monitor-deadline", args.monitor_deadline),
    ):
        if value <= 0:
            return _fail(f"{flag} must be greater than 0 (got {value})")

    try:
        result = drain_preflight.run_preflight(cwd=Path.cwd())
    except (drain_preflight.PreflightError, drain_queue_mod.QueueDeriveError) as exc:
        return _fail(str(exc))

    # Same rationale as `ranger sweep start`: this process exits as soon as
    # the JSON is printed, so its own pid would mark the lock stale for the
    # whole of the drain it just started.
    holder_pid = args.holder_pid if args.holder_pid is not None else os.getppid()

    vault = result["vault"]
    try:
        _path, token = lock_mod.acquire(vault, result["group"], holder_pid=holder_pid)
    except lock_mod.LockError as exc:
        return _fail(str(exc))

    # From here the lock is held, so every failure must release it — a drain
    # that never started must not leave its vault locked against the retry.
    try:
        entries = drain_queue_mod.derive_drain_queue(vault)
        report_path = drain_report_mod.start(
            result["group"], vault, len(entries), degraded=result["degraded"],
            concurrency=args.concurrency,
            inflight_cap=args.inflight_cap,
            monitor_deadline_hours=args.monitor_deadline,
        )
    except (drain_queue_mod.QueueDeriveError, drain_report_mod.ReportError) as exc:
        lock_mod.release(vault, token=token)
        return _fail(str(exc))

    print(
        json.dumps(
            {
                "group": result["group"],
                "vault": vault,
                "vault_path": result["vault_path"],
                "procedure_path": str(result["procedure_path"]),
                "templates_root": str(result["templates_root"]),
                "degraded": result["degraded"],
                "report_path": str(report_path),
                "outcomes_dir": str(drain_report_mod.outcomes_dir(report_path)),
                "concurrency": args.concurrency,
                "inflight_cap": args.inflight_cap,
                "monitor_deadline_hours": args.monitor_deadline,
                "lock_token": token,
                "queue": entries,
            }
        )
    )
    print(f"ranger drain: report at {report_path}", file=sys.stderr)
    return 0


def _cmd_drain_derive(args) -> int:
    from ..drain import preflight as drain_preflight
    from ..drain import queue as drain_queue_mod
    from ..sweep import preflight as sweep_preflight

    try:
        group = sweep_preflight.resolve_group(cwd=Path.cwd())
        resolution = sweep_preflight.resolve_vault(group)
        entries = drain_queue_mod.derive_drain_queue(resolution["vault"])
    except (drain_preflight.PreflightError, drain_queue_mod.QueueDeriveError) as exc:
        return _fail(str(exc))

    print_drain_queue(entries, as_json=args.json)
    return 0


def _cmd_drain_record(args) -> int:
    from ..drain import report as drain_report_mod

    try:
        task_id = _record_id(args.task)
    except ValueError as exc:
        return _fail(str(exc))

    token, argument = drain_report_mod.parse_drain_outcome(args.outcome)
    pushed_fields = (
        drain_report_mod.parse_pushed_argument(argument) if token == "PUSHED" else None
    )

    # An outcome line this verb cannot parse is bucketed `failed`, never
    # refused: the agent document promises exactly that, and a refusal would
    # leave a finished-but-unrecordable run with no line in the report at all
    # — the one outcome an unattended operator cannot recover from, because
    # nothing names the task. The raw line rides along as the reason (scrubbed
    # like any other untrusted text by the report's own append funnel).
    unparseable = token is None or (token == "PUSHED" and pushed_fields is None)
    if unparseable:
        raw = args.outcome.strip().splitlines()[0].strip() if args.outcome.strip() else ""
        reason = (
            f"unparseable outcome line: {raw}" if raw else "no outcome written"
        )
        token, argument = "FAILED", reason

    payload = {"task": task_id, "token": token, "argument": argument}
    if unparseable:
        payload["unparseable"] = True
    if pushed_fields:
        payload["branch"], payload["sha"], payload["diffstat"] = pushed_fields

    if args.report:
        try:
            report_path = Path(args.report)
            if token == "PUSHED":
                branch, sha, diffstat = pushed_fields
                pr_url = None
                if args.prs_json:
                    pr_url, _number = drain_report_mod.pr_url_for_branch(
                        drain_report_mod.read_prs_sidecar(Path(args.prs_json)), branch,
                    )
                # Rendered as `in-flight` without holding a cap slot: the
                # branch is pushed, but nothing has been handed to a monitor
                # yet. `drain inflight mark` is what opens the slot.
                drain_report_mod.append_pushed_in_flight(
                    report_path, task_id, branch, sha, diffstat,
                    pr_url=pr_url, cap_blocking=False,
                )
            elif token == "BLOCKED":
                drain_report_mod.append_blocked(report_path, task_id, argument)
            elif token == "FAILED":
                drain_report_mod.append_failed(report_path, task_id, argument)
            elif token == "SKIPPED":
                drain_report_mod.append_skipped(report_path, task_id, argument)
        except drain_report_mod.ReportError as exc:
            return _fail(str(exc))

    print(json.dumps(payload))
    return 0


def _cmd_drain_inflight(args) -> int:
    from ..drain import report as drain_report_mod

    handler = {
        "mark": _cmd_drain_inflight_mark,
        "count": _cmd_drain_inflight_count,
        "resolve": _cmd_drain_inflight_resolve,
        "expire": _cmd_drain_inflight_expire,
    }.get(getattr(args, "inflight_action", None))
    if handler is None:
        return _fail(
            f"drain inflight: unknown action {getattr(args, 'inflight_action', None)!r}. "
            "Use one of: count, expire, mark, resolve."
        )
    try:
        return handler(args, drain_report_mod)
    except drain_report_mod.ReportError as exc:
        return _fail(str(exc))


def _cmd_drain_inflight_mark(args, report_mod) -> int:
    try:
        task_id = _record_id(args.task)
    except ValueError as exc:
        return _fail(str(exc))
    report_mod.mark_in_flight(
        Path(args.report), task_id,
        branch=args.branch, sha=args.sha, diffstat=args.diffstat,
        workspace=args.workspace, deadline_hours=args.deadline_hours,
    )
    print(json.dumps({"task": task_id, "in_flight": report_mod.in_flight_count(Path(args.report))}))
    return 0


def _cmd_drain_inflight_count(args, report_mod) -> int:
    report_path = Path(args.report)
    count = report_mod.in_flight_count(report_path)
    cap = report_mod.inflight_cap(report_path)
    print(json.dumps({"in_flight": count, "inflight_cap": cap, "at_cap": count >= cap}))
    return 0


def _cmd_drain_inflight_resolve(args, report_mod) -> int:
    try:
        task_id = _record_id(args.task)
    except ValueError as exc:
        return _fail(str(exc))
    try:
        line = Path(args.monitor_outcome_file).read_text(encoding="utf-8")
    except OSError:
        # Not a refusal: an unreadable monitor outcome file *is* the crash
        # signal. Treating it as an error would wedge the cap slot forever.
        line = ""
    bucket = report_mod.resolve_monitor_outcome(
        Path(args.report), task_id, line,
        prs_json=Path(args.prs_json) if args.prs_json else None,
    )
    print(json.dumps({"task": task_id, "bucket": bucket}))
    return 0


def _cmd_drain_inflight_expire(args, report_mod) -> int:
    reclaimed = report_mod.expire_in_flight(Path(args.report))
    print(json.dumps({"reclaimed": reclaimed}))
    return 0


def _cmd_drain_crashed(args) -> int:
    return _append_flat_bucket(args, "crashed")


def _cmd_drain_dropped(args) -> int:
    return _append_flat_bucket(args, "dropped")


def _append_flat_bucket(args, bucket: str) -> int:
    from ..drain import report as drain_report_mod

    try:
        task_id = _record_id(args.task)
    except ValueError as exc:
        return _fail(str(exc))
    append = {
        "crashed": drain_report_mod.append_crashed,
        "dropped": drain_report_mod.append_dropped,
    }[bucket]
    try:
        append(Path(args.report), task_id, args.reason)
    except drain_report_mod.ReportError as exc:
        return _fail(str(exc))
    print(json.dumps({"task": task_id, "bucket": bucket}))
    return 0


def _cmd_drain_sync_gate(args) -> int:
    from ..drain import loop as drain_loop
    from ..drain import queue as drain_queue_mod

    try:
        report = drain_queue_mod.run_camp(["sync", "--json"], runner=None)
    except drain_queue_mod.QueueDeriveError as exc:
        return _fail(str(exc))

    verdict = drain_loop.classify_sync(report if isinstance(report, dict) else {})
    if args.json:
        print(json.dumps({
            "ok": verdict.ok,
            "blocking": [list(pair) for pair in verdict.blocking],
            "reason": verdict.reason,
        }))
    elif verdict.ok:
        print("sync-gate: ok — every member is at origin/main")
    else:
        print(f"sync-gate: blocked — {verdict.reason}")
    # Exit 1, not a refusal: a blocked gate is an answered question, and the
    # loop's own next step is to record the task `SKIPPED` and move on.
    return 0 if verdict.ok else 1


def _cmd_drain_teardown_check(args) -> int:
    from ..drain import loop as drain_loop

    line = None
    if args.monitor_outcome_file:
        try:
            line = Path(args.monitor_outcome_file).read_text(encoding="utf-8")
        except OSError:
            line = None

    decision = drain_loop.teardown_decision(
        line, degraded=args.degraded, expired=args.expired,
    )
    print(json.dumps({
        "teardown": decision.teardown,
        "crashed": decision.crashed,
        "reason": decision.reason,
    }))
    return 0


def _cmd_drain_finish(args) -> int:
    from ..drain import report as drain_report_mod
    from ..sweep import lock as lock_mod

    if args.report:
        try:
            drain_report_mod.finish(Path(args.report), still_standing=args.still_standing)
        except drain_report_mod.ReportError as exc:
            return _fail(str(exc))

    try:
        lock_mod.release(args.vault, token=args.token)
    except lock_mod.LockError as exc:
        return _fail(str(exc))
    return 0


_ACTIONS = {
    "start": _cmd_drain_start,
    "derive": _cmd_drain_derive,
    "record": _cmd_drain_record,
    "inflight": _cmd_drain_inflight,
    "crashed": _cmd_drain_crashed,
    "dropped": _cmd_drain_dropped,
    "sync-gate": _cmd_drain_sync_gate,
    "teardown-check": _cmd_drain_teardown_check,
    "finish": _cmd_drain_finish,
}


def cmd_drain(args) -> int:
    """Dispatch ``ranger drain <action>``."""
    action = getattr(args, "drain_action", None)
    handler = _ACTIONS.get(action)
    if handler is None:
        return _fail(
            f"drain: unknown action {action!r}. Use one of: {', '.join(sorted(_ACTIONS))}."
        )
    return handler(args)
