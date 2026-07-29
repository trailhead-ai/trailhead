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

**What identifies a sweep across those processes.** None of these processes
outlives the sweep, so two values must be supplied rather than inferred.
``--holder-pid`` names the long-lived process whose liveness *is* the sweep's
liveness; without it the lock would record a pid that dies immediately and
every running sweep would read as abandoned. ``start`` returns a
``lock_token`` in its JSON, and ``finish`` must present it to release the
lock — the vault name alone identifies the *lock*, not the *run*, so a
mistyped or out-of-order ``finish`` would otherwise release a sweep that is
still running. Both are the sweep's identity papers; the skill carries them
from ``start`` to ``finish``.

**No ``--group`` flag.** The group and the elected vault are both read from
the current directory (``lore vault resolve`` takes no group argument), so a
group override could only relabel the report while the sweep drained cwd's
vault. Running from the wrong directory is a refusal, not something to
override.

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
history earns it — but only where the outcome carries nothing the bucket
would lose. ``ESCALATED``, ``SKIPPED``, ``FAILED``, and an unparseable return
all outrank the queue bucket, because each carries something no other line in
the report holds: the question the ritual just wrote and the command that
answers it, or the reason the task was skipped or failed. Reported as a bare
id under "Blocked — answered", every one of those reads as *handled*.

**Nothing here is fatal to the sweep.** An outcome that doesn't parse buckets
``failed`` and exits 0; a record body whose unresolved section carries no
parseable question renders a fixed placeholder and exits 0; a record that has
left the elected vault since it was queued renders its own fixed note, in the
bucket the derivation put it in, and exits 0. One confused agent return, one
malformed record, or one record deleted mid-sweep must not end a sweep that
still has tasks to drain.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_MAX_FAILURE_CHARS = 200

#: The record-name shape ``--task`` must match. Deliberately narrow — the id
#: is rendered verbatim into a shell command the report tells an operator to
#: paste, so every character outside this set is one the operator's shell
#: could interpret rather than read.
#: ``\Z`` (not ``$``) anchors the END OF STRING: ``$`` also matches just
#: before a trailing newline, which would let an id like ``"task/foo\n"``
#: slip through and carry that newline into the pasted command.
_RECORD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*\Z")

#: Agent return tokens that carry a mandatory argument (target / reason).
_TOKENS_WITH_ARGUMENT = ("ROUTED", "SKIPPED", "FAILED")

#: The agent's complete return vocabulary. Membership is the whole contract —
#: which report bucket each token lands in is decided by ``_cmd_sweep_record``,
#: because the bucket is not a function of the token alone (a
#: previously-blocked task keeps its own bucket, and two of the buckets are
#: rendered from the record body rather than the return line).
_OUTCOME_TOKENS = frozenset({"PROMOTED", "ESCALATED", "ROUTED", "SKIPPED", "FAILED"})

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
        "--holder-pid", type=int, metavar="PID",
        help=(
            "Pid of the long-lived process that constitutes this sweep — the coordinator "
            "session or scheduler wrapper. Recorded in the lock, and its liveness is what "
            "tells a running sweep from an abandoned one. Defaults to this process's parent, "
            "which is correct only when that parent drives the sweep to completion; any "
            "interposed shell (a `sh -c` per verb, a pipeline) makes the default wrong, so "
            "coordinators should pass their own pid explicitly."
        ),
    )

    p_derive = p_sweep_sub.add_parser(
        "derive", help="Re-derive and print the elected vault's queue classification"
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
    p_finish.add_argument(
        "--token", required=True, metavar="TOKEN",
        help="The `lock_token` this sweep's `start` returned; proves the lock is this run's",
    )

    p_start.set_defaults(func=cmd_sweep)
    p_derive.set_defaults(func=cmd_sweep)
    p_record.set_defaults(func=cmd_sweep)
    p_finish.set_defaults(func=cmd_sweep)


def _fail(message: str) -> int:
    print(f"ranger: {message}", file=sys.stderr)
    return 1


def _record_id(task: str) -> str:
    """Normalize ``--task`` to a full ``task/<name>`` record id, or reject it.

    Report lines embed the id verbatim into a copy-pasteable
    ``lore record update <id>`` command, so a bare name would produce a
    command the operator cannot run — and a name carrying shell
    metacharacters would produce one that runs *more* than the operator
    intends, in the operator's own shell. The name is therefore validated to
    the record-name shape before it is ever written, rather than escaped at
    each of the places it is rendered.

    Raises ``ValueError`` on anything else; the caller turns that into the
    CLI's ``ranger: <message>`` refusal.
    """
    name = task[len("task/"):] if task.startswith("task/") else task
    if not _RECORD_NAME_RE.match(name):
        raise ValueError(
            f"--task {task!r} is not a valid record id — expected `task/<name>` with "
            f"<name> matching {_RECORD_NAME_RE.pattern}"
        )
    return f"task/{name}"


def _resolve_target():
    """Run the group + vault preconditions, returning ``(group, resolution)``."""
    from ..sweep import preflight

    group = preflight.resolve_group(cwd=Path.cwd())
    return group, preflight.resolve_vault(group)


def _cmd_sweep_start(args) -> int:
    from ..sweep import lock as lock_mod
    from ..sweep import preflight
    from ..sweep import queue as queue_mod
    from ..sweep import report as report_mod

    try:
        procedure_path, templates_root = preflight.find_refine_procedure()
        group, resolution = _resolve_target()
    except (preflight.PreflightError, queue_mod.QueueDeriveError) as exc:
        return _fail(str(exc))

    # This process exits as soon as the JSON is printed, so its own pid would
    # mark the lock stale for the whole of the sweep it just started. The
    # holder is the caller's long-lived process instead.
    holder_pid = args.holder_pid if args.holder_pid is not None else os.getppid()

    vault = resolution["vault"]
    try:
        _path, token = lock_mod.acquire(vault, group, holder_pid=holder_pid)
    except lock_mod.LockError as exc:
        return _fail(str(exc))

    # From here the lock is held, so every failure must release it — a sweep
    # that never started must not leave its vault locked against the retry.
    try:
        entries = queue_mod.derive_queue(vault)
        report_path = report_mod.start(group, vault, len(entries))
    except (queue_mod.QueueDeriveError, report_mod.ReportError) as exc:
        lock_mod.release(vault, token=token)
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
                "lock_token": token,
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
        _group, resolution = _resolve_target()
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
    if token not in _OUTCOME_TOKENS or (token in _TOKENS_WITH_ARGUMENT and not argument):
        return None, first_line[:_MAX_FAILURE_CHARS]
    return token, argument


def _append_question_line(report_path: Path, task_id: str, *, status: str, bucket: str) -> None:
    """Append one of the two report lines that carry the task's question.

    Reads the record body here rather than taking it from the caller — see the
    module docstring on containment — and re-derives the near-miss signal from
    it, so a report can say "an answer was attempted but not recognized"
    instead of the ambiguous "never answered" without the coordinator having
    to carry that flag back.

    The read names the vault the sweep elected at ``start``, taken from the
    report's own state rather than re-resolved from cwd: an unvaulted
    ``record show`` is a cwd-blind scan in config order, which would quote a
    colliding task name out of a vault this sweep never touched.
    """
    from ..sweep import queue as queue_mod
    from ..sweep import report as report_mod

    vault = report_mod.elected_vault(report_path)
    try:
        body = queue_mod.read_body(task_id.split("/", 1)[1], vault=vault, runner=None)
    except queue_mod.QueueDeriveError:
        # The record left the elected vault between the derivation that queued
        # it and this read — deleted, renamed, or moved. Nothing here is fatal
        # to the sweep (see the module docstring), and refusing would lose this
        # task's report line as well as every task still behind it.
        report_mod.append_unreadable_record(report_path, bucket, task_id)
        return
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
    try:
        task_id = _record_id(args.task)
    except ValueError as exc:
        return _fail(str(exc))
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

        # Outcome first, queue bucket second. Every outcome that carries
        # information the queue bucket cannot express outranks it:
        #
        # - an unparseable return, and `FAILED <reason>` (which the loop
        #   synthesizes for a dispatch that errored or timed out), carry a
        #   reason the `failed` bucket exists to show the operator;
        # - `SKIPPED <reason>` likewise;
        # - `ESCALATED` means the ritual just wrote a fresh question into the
        #   record, and the escalated line's question text plus its answer
        #   command are the operator's only handle on it;
        # - `ROUTED <target>` likewise carries the routing target, which is
        #   the one datum no other line in the report holds.
        #
        # Reported under the queue bucket instead, each of those renders a bare
        # id under "Blocked — answered" — a line that reads as *handled* while
        # the reason, question, or target is dropped.
        token, argument = parse_outcome(args.outcome)
        if token is None or token == "FAILED":
            report_mod.append_failed(report_path, task_id, argument)
        elif token == "SKIPPED":
            report_mod.append_skipped(report_path, task_id, argument)
        elif token == "ESCALATED":
            _append_question_line(
                report_path, task_id, status="open", bucket="escalated-awaiting-operator"
            )
        elif token == "ROUTED":
            report_mod.append_routed(report_path, task_id, argument)
        # Only then does a previously-blocked task keep its own bucket: for
        # `PROMOTED` the return drives the loop's status write, not a further
        # bucket split.
        elif queue_bucket == "blocked-answered":
            report_mod.append_blocked_answered(report_path, task_id)
        else:
            report_mod.append_promoted(report_path, task_id)
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
        lock_mod.release(args.vault, token=args.token)
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
