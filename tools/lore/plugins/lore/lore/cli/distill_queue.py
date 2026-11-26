"""``lore distill-queue`` — the specs waiting to be distilled, and the deferred ones (read-only).

Usage: ``lore distill-queue [--max-scan-mib N]``. Reads every configured
vault and prints what :func:`lore.health.distill_queue.build_distill_queue`
decides; that module states the rule, this one only prints it.

**Output.** The first line of every successful run starts ``lore distill-queue — ``:
``<N> waiting, <M> deferred`` followed by a ``waiting:`` list (oldest first) and a
``deferred, no linked task (distill each by target):`` list, one line per spec as
``  <vault>/spec/<name>  <date>  <status>  <reason>[; <ADR note>]``; with nothing
waiting and nothing deferred it is ``lore distill-queue — nothing waiting``. Every
spec line is composed by the same function ``lore health``'s ``distill-queue`` check
prints its findings with (:func:`lore.cli.health._distill_line`), so a spec reads
identically on both surfaces: a control character in a vault name is escaped and a
spec name that is not a valid record id is replaced by a fixed phrase and never echoed.

**Exit codes.** 0 on success, with or without specs waiting. 1 with one ``lore:``
stderr line and nothing on stdout when the queue cannot be built: a vault was not
fully read, or holds a spec or task record that could not be read (the line names the
vault, each such record, and the way on: repair or remove it, or distill one spec by name),
a vault's records exceed the scan budget (the line names the size and ``--max-scan-mib``),
the configuration cannot be read, or anything fails unexpectedly.
A caller treats any non-zero exit, and any stdout whose first line does not start
``lore distill-queue — ``, as a refusal and never as an empty queue.

**Read-only.** Nothing is written: no record, lock or index. The scan skips the
per-record index probe, which feeds nothing this command reads.
"""
from __future__ import annotations

import sys

from ..health import distill_queue as queue_mod
from ..health import scan as scan_mod
from .health import _distill_line, _emit, _record_id, _safe, add_max_scan_mib, budget_exceeded, scan_budget

HEADER = "lore distill-queue — "
DEFERRED_HEADING = "deferred, no linked task (distill each by target):"
_BY_NAME = "or distill one spec by name with /craft:distill spec/<name>"
_FALLBACK = f"repair or remove the vault to sweep again, {_BY_NAME}"
_FALLBACK_RECORD = f"repair or remove the record to sweep again, {_BY_NAME}"
_FALLBACK_BOTH = f"repair or remove what could not be read to sweep again, {_BY_NAME}"

_scan_vault = scan_mod.scan_vault


def render(queue: queue_mod.DistillQueue) -> list[str]:
    if not queue.waiting and not queue.deferred:
        return [f"{HEADER}nothing waiting"]
    lines = [f"{HEADER}{len(queue.waiting)} waiting, {len(queue.deferred)} deferred"]
    if queue.waiting:
        lines += ["", "waiting:", *(_distill_line(i) for i in queue.waiting)]
    if queue.deferred:
        lines += ["", DEFERRED_HEADING, *(_distill_line(i) for i in queue.deferred)]
    return lines


MAX_NAMED_RECORDS = 10


def _named(skipped: queue_mod.DistillSkipped) -> str:
    named = [_record_id(v, k, n) if n is not None else f"{_safe(v)}/{_safe(k)}/"
             for v, k, n in skipped.records[:MAX_NAMED_RECORDS]]
    more = len(skipped.records) - MAX_NAMED_RECORDS
    if more > 0:
        named.append(f"and {more} more")
    return f" ({', '.join(named)})" if named else ""


def _refusal(skipped: queue_mod.DistillSkipped, scans) -> str:
    by_name = {s.vault: s for s in scans}
    over = tuple(v for v in skipped.vaults if by_name[v].unreadable_reason == scan_mod.REASON_OVER_BUDGET)
    others = tuple(v for v in skipped.vaults if v not in over)
    causes = []
    for v in over:
        causes.append(f"vault {_safe(v)}'s " + budget_exceeded(by_name[v]))
    if others:
        names = ", ".join(_safe(v) for v in others)
        noun = "vault" if len(others) == 1 else "vaults"
        unreadable = all(by_name[v].unreadable_reason is not None for v in others)
        state = "could not be read" if unreadable else "could not be fully read"
        causes.append(f"{noun} {names} {state}")
    if skipped.unreadable_records:
        names = ", ".join(_safe(v) for v in skipped.unreadable_records)
        noun, verb = ("vault", "holds") if len(skipped.unreadable_records) == 1 else ("vaults", "hold")
        causes.append(f"{noun} {names} {verb} a spec or task record that could not be read")
    text = f"{' and '.join(causes)}{_named(skipped)}"
    remedies = []
    if others or skipped.unreadable_records:
        remedies.append(_FALLBACK if not skipped.unreadable_records
                        else _FALLBACK_RECORD if not others else _FALLBACK_BOTH)
    if over:
        remedies.append("pass --max-scan-mib to raise it")
    return f"lore: {text}, so the distill queue is refused rather than built partial; {'; '.join(remedies)}"


def cmd_distill_queue(args) -> int:
    """Print the distill queue; see the module docstring for the exit contract."""
    try:
        budget = scan_budget(args.max_scan_mib)
        scans = [_scan_vault(name, root, probe_index=False, max_bytes=budget)
                 for name, root in scan_mod.resolve_vaults()]
        result = queue_mod.build_distill_queue(scans)
        if isinstance(result, queue_mod.DistillSkipped):
            _emit([_refusal(result, scans)], sys.stderr)
            return 1
        _emit(render(result), sys.stdout)
        return 0
    except scan_mod.HealthResolutionError as exc:
        _emit([f"lore: cannot run the distill queue: {_safe(exc)}"], sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - the contract is one line, never a traceback
        _emit([f"lore: distill queue failed: {type(exc).__name__}: {exc}"], sys.stderr)
        return 1


def add_distill_queue_subparser(sub) -> None:
    """Register the ``distill-queue`` command parser."""
    p = sub.add_parser(
        "distill-queue",
        help="List the specs waiting to be distilled into ADRs, and the deferred ones (read-only)",
        description=(
            "Read every configured vault and print the specs waiting to be distilled, oldest "
            "first, then the deferred specs (waiting, but no task in any vault links to them). "
            "Exit status: 0 whenever the queue is built, with or without specs; 1 with one "
            "'lore:' line when a vault was not fully read, the configuration cannot be read, or "
            "the command fails. Treat any non-zero exit, or output not starting "
            "'lore distill-queue — ', as a refusal, never an empty queue. Read-only."
        ),
    )
    add_max_scan_mib(p)
    p.set_defaults(func=cmd_distill_queue)
