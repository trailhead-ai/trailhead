"""``ranger drain`` — the four verbs a drain is driven through.

Mirrors `ranger sweep`'s split (see `cli/sweep.py`'s module docstring for the
full rationale): a coordinating loop drives `start` once, `derive` between
tasks, `record` once per dispatched task, and `finish` once — each a
separate process, so a coordinator that dies between two of them leaves
recoverable, on-disk state rather than nothing. The lock and report
substrate is reused verbatim from the sweep — `drain` and `sweep` contend on
the identical `state_dir("ranger")/locks/<vault>.lock` path, so an operator
cannot run a refine sweep and an execute drain against the same vault at
once.

**This slice's scope.** `start`/`derive` are fully wired: preflight, lock,
and queue derivation. `record` validates the drain outcome grammar and the
task id's shell-safety — the sibling `ranger-drain-report-and-outcome-contract`
slice adds the report substrate `record` writes into (bucket rendering, the
outcomes directory, the in-flight cap) and `finish`'s report footer; until
then, `record` only validates and `finish` only releases the lock.

**No ``--vault`` election override**, matching `ranger sweep`: the group and
elected vault are both read from cwd (`ranger.drain.preflight.run_preflight`),
so a flag naming a different vault could only relabel the report while the
drain kept touching cwd's vault.
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

    p_derive = p_drain_sub.add_parser(
        "derive", help="Re-derive and print the elected vault's drain queue classification"
    )
    p_derive.add_argument("--json", action="store_true", help="Emit the queue as a JSON array")

    p_record = p_drain_sub.add_parser(
        "record", help="Validate one task's outcome against the drain outcome grammar"
    )
    p_record.add_argument("--task", required=True, metavar="ID", help="Task record id")
    p_record.add_argument(
        "--outcome", required=True, metavar="LINE",
        help="The dispatched agent's one-line drain outcome",
    )

    p_finish = p_drain_sub.add_parser("finish", help="Release the vault lock")
    p_finish.add_argument("--vault", required=True, metavar="NAME", help="The locked vault")
    p_finish.add_argument(
        "--token", required=True, metavar="TOKEN",
        help="The `lock_token` this drain's `start` returned; proves the lock is this run's",
    )

    p_start.set_defaults(func=cmd_drain)
    p_derive.set_defaults(func=cmd_drain)
    p_record.set_defaults(func=cmd_drain)
    p_finish.set_defaults(func=cmd_drain)


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
    from ..sweep import lock as lock_mod

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

    try:
        entries = drain_queue_mod.derive_drain_queue(vault)
    except drain_queue_mod.QueueDeriveError as exc:
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
                "lock_token": token,
                "queue": entries,
            }
        )
    )
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
    from ..drain import queue as drain_queue_mod

    try:
        _record_id(args.task)
    except ValueError as exc:
        return _fail(str(exc))

    token, argument = drain_queue_mod.parse_drain_outcome(args.outcome)
    if token is None:
        return _fail(
            f"--outcome {args.outcome!r} does not match the drain grammar "
            f"({'|'.join(sorted(drain_queue_mod.DRAIN_OUTCOME_TOKENS))} <argument>)"
        )

    # Persistence into a durable report is the sibling
    # `ranger-drain-report-and-outcome-contract` slice's job — this verb's
    # scope in this slice is the grammar validation above, which is what
    # lets the loop trust a dispatched agent's outcome before that report
    # substrate exists to hold it.
    print(json.dumps({"task": _record_id(args.task), "token": token, "argument": argument}))
    return 0


def _cmd_drain_finish(args) -> int:
    from ..sweep import lock as lock_mod

    try:
        lock_mod.release(args.vault, token=args.token)
    except lock_mod.LockError as exc:
        return _fail(str(exc))
    return 0


_ACTIONS = {
    "start": _cmd_drain_start,
    "derive": _cmd_drain_derive,
    "record": _cmd_drain_record,
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
