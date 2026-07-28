"""``ranger sweep`` — the four verbs a refine sweep is driven through.

The coordinating skill owns the loop; this module owns everything mechanical
about it, so the loop's state lives on disk rather than in a transcript:

- ``start``   — run the three preconditions, take the per-vault lock, derive
                the queue, seed the report, and hand the skill one JSON object
                with every path and every task it needs.
- ``derive``  — re-derive and print the classification, without touching the
                lock or the report. The loop calls it between tasks.
- ``record``  — append one task's outcome to the report, in the right bucket.
- ``finish``  — write the report footer and release the lock.

**Why the split.** Each verb is a separate process, which is what makes a
sweep survivable: a coordinator that dies between two of them leaves a
partial-but-valid report on disk and a lock naming the dead holder, and the
next ``start`` reports both. Nothing is held in memory between verbs — the
report's state file and the lock file *are* the sweep's state.

**Why the record bodies are read here.** ``record`` reads the task record to
extract an escalated question, so the question text (and the answer command
built from it) is composed in this process and written straight to the report
— it never transits the dispatched agent's one-line return or the
coordinating session's context. That containment is the whole reason the
report writer, not the coordinator, owns question extraction.

**Report bucketing takes two inputs**, because the seven report buckets carry
more than the agent's four return tokens can express: ``--queue-bucket``
(what derivation said about this task before dispatch) and ``--outcome`` (what
the agent returned). A task derivation never dispatched — churn-guarded or
still waiting on an operator — is reported from its queue bucket alone and
takes no outcome; a dispatched task is reported from its outcome, except that
a previously-``blocked`` task keeps the ``blocked-answered`` bucket its
history earns it. An outcome that doesn't parse is never fatal: it buckets
``failed`` and exits 0, because one confused agent return must not end a
sweep that still has tasks to drain.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_MAX_FAILURE_CHARS = 200

#: Agent return tokens that carry a mandatory argument (target / reason).
_TOKENS_WITH_ARGUMENT = ("ROUTED", "SKIPPED", "FAILED")

#: Agent return token -> report bucket, for a task derivation marked
#: ``dispatchable``.
_OUTCOME_BUCKETS = {
    "PROMOTED": "promoted",
    "ESCALATED": "escalated-awaiting-operator",
    "ROUTED": "routed",
    "SKIPPED": "skipped",
    "FAILED": "failed",
}

#: Queue buckets whose tasks were never dispatched, mapped to the report
#: bucket they are reported in. Both carry the task's question, so the
#: operator can answer from the report.
_NEVER_DISPATCHED = {
    "escalated-awaiting-operator": "escalated-awaiting-operator",
    "blocked-still-waiting": "blocked-still-waiting",
}

_QUEUE_BUCKET_CHOICES = ("dispatchable", "blocked-answered", *_NEVER_DISPATCHED)


def add_sweep_subparser(sub) -> None:
    p_sweep = sub.add_parser("sweep", help="Run and record an unattended refine sweep")
    p_sweep_sub = p_sweep.add_subparsers(dest="sweep_action", required=True)

    p_start = p_sweep_sub.add_parser(
        "start",
        help="Check preconditions, lock the vault, seed the report, print the sweep's JSON",
    )
    p_start.add_argument(
        "--group", metavar="NAME",
        help="Camp group to sweep; defaults to the group the current directory resolves to",
    )

    p_derive = p_sweep_sub.add_parser(
        "derive", help="Re-derive and print the elected vault's queue classification"
    )
    p_derive.add_argument(
        "--group", metavar="NAME",
        help="Camp group to derive for; defaults to the group the current directory resolves to",
    )
    p_derive.add_argument("--json", action="store_true", help="Emit the queue as a JSON array")

    p_record = p_sweep_sub.add_parser("record", help="Append one task's outcome to the report")
    p_record.add_argument("--report", required=True, metavar="PATH", help="Report to append to")
    p_record.add_argument("--task", required=True, metavar="ID", help="Task record id")
    p_record.add_argument(
        "--outcome", metavar="LINE",
        help="The agent's one-line return; required unless the task was never dispatched",
    )
    p_record.add_argument(
        "--queue-bucket", default="dispatchable", choices=_QUEUE_BUCKET_CHOICES,
        help="The task's bucket at derivation time (default: dispatchable)",
    )

    p_finish = p_sweep_sub.add_parser("finish", help="Write the report footer, release the lock")
    p_finish.add_argument("--report", required=True, metavar="PATH", help="Report to finish")
    p_finish.add_argument("--vault", required=True, metavar="NAME", help="The locked vault")

    p_start.set_defaults(func=cmd_sweep)
    p_derive.set_defaults(func=cmd_sweep)
    p_record.set_defaults(func=cmd_sweep)
    p_finish.set_defaults(func=cmd_sweep)


def _fail(message: str) -> int:
    print(f"ranger: {message}", file=sys.stderr)
    return 1


def _record_id(task: str) -> str:
    """Normalize ``--task`` to a full ``task/<name>`` record id.

    Report lines embed the id verbatim into a copy-pasteable
    ``lore record update <id>`` command, so a bare name would produce a
    command the operator cannot run.
    """
    return task if task.startswith("task/") else f"task/{task}"


def _resolve_target(group: str | None):
    """Run the group + vault preconditions, returning ``(group, resolution)``."""
    from ..sweep import preflight

    resolved_group = preflight.resolve_group(cwd=Path.cwd(), group=group)
    return resolved_group, preflight.resolve_vault(resolved_group)


def _cmd_sweep_start(args) -> int:
    from ..sweep import lock as lock_mod
    from ..sweep import preflight
    from ..sweep import queue as queue_mod
    from ..sweep import report as report_mod

    try:
        procedure_path, templates_root = preflight.find_refine_procedure()
        group, resolution = _resolve_target(args.group)
    except (preflight.PreflightError, queue_mod.QueueDeriveError) as exc:
        return _fail(str(exc))

    vault = resolution["vault"]
    try:
        lock_mod.acquire(vault, group)
    except lock_mod.LockError as exc:
        return _fail(str(exc))

    # From here the lock is held, so every failure must release it — a sweep
    # that never started must not leave its vault locked against the retry.
    try:
        entries = queue_mod.derive_queue(vault)
        report_path = report_mod.start(group, vault, len(entries))
    except (queue_mod.QueueDeriveError, report_mod.ReportError) as exc:
        lock_mod.release_recorded(vault)
        return _fail(str(exc))

    print(
        json.dumps(
            {
                "group": group,
                "vault": vault,
                "vault_path": resolution["path"],
                "procedure_path": str(procedure_path),
                "templates_root": str(templates_root),
                "report_path": str(report_path),
                "queue": entries,
            }
        )
    )
    # The breadcrumb an attended operator needs if the sweep dies before it
    # ever reaches `finish` — the JSON goes to the skill, not to a human.
    print(f"ranger sweep: report at {report_path}", file=sys.stderr)
    return 0


def _cmd_sweep_derive(args) -> int:
    from ..sweep import preflight
    from ..sweep import queue as queue_mod
    from .queue import print_queue

    try:
        _group, resolution = _resolve_target(args.group)
        entries = queue_mod.derive_queue(resolution["vault"])
    except (preflight.PreflightError, queue_mod.QueueDeriveError) as exc:
        return _fail(str(exc))

    print_queue(entries, as_json=args.json)
    return 0


def parse_outcome(line: str) -> tuple[str | None, str]:
    """Split an agent's return line into ``(token, argument)``.

    Returns ``(None, <line truncated to one line>)`` when the line is not one
    of the recognized tokens, or when a token that requires an argument was
    given none — the caller buckets that as a failure rather than guessing at
    a half-formed return.
    """
    first_line = line.strip().splitlines()[0].strip() if line.strip() else ""
    token, _, argument = first_line.partition(" ")
    argument = argument.strip()
    if token not in _OUTCOME_BUCKETS or (token in _TOKENS_WITH_ARGUMENT and not argument):
        return None, first_line[:_MAX_FAILURE_CHARS]
    return token, argument


def _append_question_line(report_path: Path, task_id: str, *, status: str, bucket: str) -> None:
    """Append one of the two report lines that carry the task's question.

    Reads the record body here rather than taking it from the caller — see the
    module docstring on containment — and re-derives the near-miss signal from
    it, so a report can say "an answer was attempted but not recognized"
    instead of the ambiguous "never answered" without the coordinator having
    to carry that flag back.
    """
    from ..sweep import queue as queue_mod
    from ..sweep import report as report_mod

    body = queue_mod.read_body(task_id.split("/", 1)[1], runner=None)
    _bucket, near_miss = queue_mod.classify(status, body)
    append = (
        report_mod.append_blocked_still_waiting
        if bucket == "blocked-still-waiting"
        else report_mod.append_escalated
    )
    append(report_path, task_id, body, near_miss=near_miss)


def _cmd_sweep_record(args) -> int:
    from ..sweep import queue as queue_mod
    from ..sweep import report as report_mod

    report_path = Path(args.report)
    task_id = _record_id(args.task)
    queue_bucket = args.queue_bucket

    try:
        if queue_bucket in _NEVER_DISPATCHED:
            _append_question_line(
                report_path,
                task_id,
                status="blocked" if queue_bucket.startswith("blocked") else "open",
                bucket=_NEVER_DISPATCHED[queue_bucket],
            )
            return 0

        if args.outcome is None:
            return _fail(
                f"--outcome is required for a dispatched task (--queue-bucket {queue_bucket})"
            )

        token, argument = parse_outcome(args.outcome)
        if token is None:
            report_mod.append_failed(report_path, task_id, argument)
        # A previously-blocked task keeps its own bucket whatever the ritual
        # returned: the return drives the loop's status write, not a further
        # bucket split.
        elif queue_bucket == "blocked-answered":
            report_mod.append_blocked_answered(report_path, task_id)
        elif token == "PROMOTED":
            report_mod.append_promoted(report_path, task_id)
        elif token == "ESCALATED":
            _append_question_line(
                report_path, task_id, status="open", bucket="escalated-awaiting-operator"
            )
        elif token == "ROUTED":
            report_mod.append_routed(report_path, task_id, argument)
        elif token == "SKIPPED":
            report_mod.append_skipped(report_path, task_id, argument)
        else:
            report_mod.append_failed(report_path, task_id, argument)
    except (report_mod.ReportError, queue_mod.QueueDeriveError) as exc:
        return _fail(str(exc))
    return 0


def _cmd_sweep_finish(args) -> int:
    from ..sweep import lock as lock_mod
    from ..sweep import report as report_mod

    try:
        report_mod.finish(Path(args.report))
    except report_mod.ReportError as exc:
        return _fail(str(exc))

    try:
        lock_mod.release_recorded(args.vault)
    except lock_mod.LockError as exc:
        return _fail(str(exc))
    return 0


_ACTIONS = {
    "start": _cmd_sweep_start,
    "derive": _cmd_sweep_derive,
    "record": _cmd_sweep_record,
    "finish": _cmd_sweep_finish,
}


def cmd_sweep(args) -> int:
    """Dispatch ``ranger sweep <action>``."""
    action = getattr(args, "sweep_action", None)
    handler = _ACTIONS.get(action)
    if handler is None:
        return _fail(
            f"sweep: unknown action {action!r}. Use one of: {', '.join(sorted(_ACTIONS))}."
        )
    return handler(args)
