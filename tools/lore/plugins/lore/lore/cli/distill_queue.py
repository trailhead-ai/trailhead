"""``lore distill-queue`` — the specs waiting to be distilled, and the deferred ones (read-only).

Usage: ``lore distill-queue`` (no flags but ``--help``). Reads every configured
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
fully read (the line names it and the way on: repair or remove the vault, or distill
one spec by name), the configuration cannot be read, or anything fails unexpectedly.
A caller treats any non-zero exit, and any stdout whose first line does not start
``lore distill-queue — ``, as a refusal and never as an empty queue.

**Read-only.** Nothing is written: no record, lock or index. The scan skips the
per-record index probe, which feeds nothing this command reads.
"""
from __future__ import annotations

import sys

from ..health import distill_queue as queue_mod
from ..health import scan as scan_mod
from .health import _distill_line, _emit, _safe

HEADER = "lore distill-queue — "
DEFERRED_HEADING = "deferred, no linked task (distill each by target):"
_FALLBACK = "repair or remove the vault to sweep again, or distill one spec by name with /craft:distill spec/<name>"

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


def _refusal(skipped: queue_mod.DistillSkipped, scans) -> str:
    by_name = {s.vault: s for s in scans}
    names = ", ".join(_safe(v) for v in skipped.vaults)
    noun = "vault" if len(skipped.vaults) == 1 else "vaults"
    unreadable = all(by_name[v].unreadable_reason is not None for v in skipped.vaults)
    state = "could not be read" if unreadable else "could not be fully read"
    return f"lore: {noun} {names} {state}, so the distill queue is refused rather than built partial; {_FALLBACK}"


def cmd_distill_queue(args) -> int:
    """Print the distill queue; see the module docstring for the exit contract."""
    try:
        scans = [_scan_vault(name, root, probe_index=False) for name, root in scan_mod.resolve_vaults()]
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
    p.set_defaults(func=cmd_distill_queue)
