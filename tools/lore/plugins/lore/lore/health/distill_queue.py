"""The distill queue: which finished specs are waiting to be condensed into ADRs.

Pure: takes the scan of every configured vault and returns data. This is the one
place the rule is stated; ``lore distill-queue`` (and, through it, ``/craft:distill``'s
sweep) and ``lore health`` decide through :func:`build_distill_queue`, so they cannot
disagree. Only ``spec`` records are judged; sessions and every other kind never are.

**Candidates.** A spec is a candidate when its status is ``planned``; or ``complete``
(specs that finished before distilling existed were never distilled, and the
exclusion below drops the ones that were); or ``ready`` and it carries the label
``craft/slice-loop``, whatever its value. A spec under the slice loop holds ``ready``
from review until distill completes it, so it never reaches ``planned`` and without
this third arm the pipeline would have no terminus. The label is the marker the loop
writes when it terminates, as ``complete`` or ``stopped``; both are outcomes distill
has something to say about, and a spec mid-loop has the label cleared so it never
matches. Any other value is still a closed-out loop, but is never echoed.

**Exclusion.** A candidate is out of the queue when either holds:

* it carries a ``distilled`` annotation, whatever its value (``adr``, ``zero-adr``,
  ``rejected``), except a ``ready`` spec whose ``craft/slice-loop`` label is
  ``complete``. A spec that stopped early was distilled and annotated but left
  ``ready``; when the loop is re-entered and terminates ``complete``, the slices
  shipped since the annotation have never been distilled, and excluding it on the
  annotation alone would leave a finished spec unable to reach ``complete``.
* it carries a ``related adr`` edge whose ADR is ``active``, ``superseded`` or
  ``dropped`` (an ADR's frozen statuses). A spec with such an edge descends from a
  decision already on record, and sweeping it would restate that decision in a second
  ADR. An edge whose ADR is ``draft``, has a status outside the ADR vocabulary or none, cannot
  be found, or cannot be read does not
  exclude: no decision is on record yet, and excluding it would strand the spec
  outside the queue with no way back. The ADR is looked up in the spec's own vault
  first; only when that vault holds no copy are the other vaults searched, and then
  the edge excludes only when every copy found is decided, so a same-named ADR in
  another vault cannot silently take a spec out of the queue. An unreadable copy
  confirms nothing. Any one excluding edge excludes the spec.

Both exclusions are per-candidate sidecar checks because annotations are never
indexed. An edge that is not a record id, or that names a kind other than ``adr``, is
ignored.

**Deferral.** A candidate that is not excluded is waiting, unless no task links to it:
the sweep cannot cluster a spec without a task tree, so it is deferred to be distilled
by target. A task links to a spec when its sidecar's ``related.spec`` names the spec
verbatim and case-sensitively (no ``spec/`` prefix stripping, no ``@stage``), in any
vault, at any task status. This is what the search index's ``related-spec:``
facet over ``kind:task`` answers, except that a task missing a ``created-at`` or an
``updated-at`` does not link, because the index does not hold it. The index's other skip
conditions, a reserved label key and a value it cannot bind, are not mirrored: such a task
links here, so its spec is waiting, but a ``kind:task related-spec:`` search does not find it. The task-to-spec map is
built once per run.

**Skipped.** When any vault was not read, or holds an escaping ``spec`` or ``task``
record or directory, no spec can be judged, because a task edge or an ADR's status could
sit in the unread part. The same holds when any vault holds an unreadable ``spec`` or
``task`` record (its kind is its directory): an unread spec is missing from the queue, and
an unread task may be the one linking a spec. The result is :class:`DistillSkipped` naming
both sets of vaults and, in ``records``, each unreadable or escaping spec or task as
``(vault, kind, name)`` (``name`` is ``None`` for a whole escaping kind directory). An
unreadable or escaping ADR does not skip: it is judged per edge, with the "could not be
read" note. An unreadable or escaping record of any other kind is never consulted.

Each waiting or deferred spec is a :class:`QueueItem` carrying its reason (the text a
report prints) and, when a ``related adr`` edge was ignored for want of a decision, an
``adr_note`` saying why. Both lists are oldest first by last update, undated first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar

from ..record.guards import FROZEN_STATUSES
from ..record.model import STATUS_VOCAB
from . import references
from .model import ScannedRecord, VaultScan, parse_timestamp, status

LOOP_LABEL = "craft/slice-loop"
DISTILLED_ANNOTATION = "distilled"
LOOP_COMPLETE = "complete"
LOOP_STOPPED = "stopped"
CANDIDATE_STATUSES = ("planned", "complete")
READY = "ready"

ADR_DECIDED = FROZEN_STATUSES["adr"]
ADR_STATUSES = frozenset(STATUS_VOCAB["adr"])

NOTE_DRAFT = "its ADR is still draft"
NOTE_UNRECOGNISED = "its ADR has no recognised status"
NOTE_NOT_FOUND = "its ADR could not be found"
NOTE_UNREADABLE = "its ADR could not be read"

#: A task the search index would hold carries both of these.
INDEXED_TASK_FIELDS = ("created-at", "updated-at")


@dataclass(frozen=True)
class QueueItem:
    """One spec in the queue."""

    kind: ClassVar[str] = "spec"
    vault: str
    name: str
    updated_at: str | None
    status: str
    reason: str
    adr_note: str | None


@dataclass(frozen=True)
class DistillQueue:
    """The specs waiting to be distilled and the deferred ones, each oldest first."""

    waiting: tuple[QueueItem, ...] = ()
    deferred: tuple[QueueItem, ...] = ()


#: An unreadable record of one of these kinds leaves the queue unjudgeable.
JUDGED_KINDS = ("spec", "task")


@dataclass(frozen=True)
class DistillSkipped:
    """The queue could not be built.

    ``vaults`` names the vaults not fully read; ``unreadable_records`` names the other
    vaults that hold an unreadable spec or task record. ``records`` lists every
    unreadable or escaping spec or task, by vault, kind and name (the name is ``None``
    for a kind directory that escapes); it is not part of equality.
    """

    vaults: tuple[str, ...]
    unreadable_records: tuple[str, ...] = ()
    records: tuple[tuple[str, str, str | None], ...] = field(default=(), compare=False)

    @property
    def at_fault(self) -> tuple[str, ...]:
        return self.vaults + self.unreadable_records


def holds_unreadable_judged_record(scan: VaultScan) -> bool:
    """Whether *scan* holds an unreadable record of a kind the queue is judged from."""
    return any(u.kind in JUDGED_KINDS for u in scan.unreadable)


def unjudgeable_records(scan: VaultScan) -> tuple[tuple[str, str, str | None], ...]:
    """Each unreadable or escaping spec or task of *scan*, as ``(vault, kind, name)``."""
    gone = [(u.vault, u.kind, u.name) for u in scan.unreadable if u.kind in JUDGED_KINDS]
    hidden = [(e.vault, e.kind, e.name) for e in scan.escaped if e.kind in JUDGED_KINDS]
    return tuple(sorted(gone + hidden, key=lambda r: (r[1], r[2] or "")))


def _mapping(sidecar: dict, field: str) -> dict:
    value = sidecar.get(field) if isinstance(sidecar, dict) else None
    return value if isinstance(value, dict) else {}


def _is_candidate(record: ScannedRecord) -> bool:
    spec_status = status(record)
    if spec_status in CANDIDATE_STATUSES:
        return True
    return spec_status == READY and LOOP_LABEL in _mapping(record.sidecar, "labels")


def _slice_loop(record: ScannedRecord) -> object:
    return _mapping(record.sidecar, "labels").get(LOOP_LABEL)


def _is_previously_distilled_resume(record: ScannedRecord) -> bool:
    return status(record) == READY and _slice_loop(record) == LOOP_COMPLETE


def _is_annotated(record: ScannedRecord) -> bool:
    return DISTILLED_ANNOTATION in _mapping(record.sidecar, "annotations")


def _reason(record: ScannedRecord) -> str:
    spec_status = status(record)
    if spec_status == "planned":
        return "planned, not yet distilled"
    if spec_status == "complete":
        return "complete, never distilled"
    loop = _slice_loop(record)
    if loop == LOOP_COMPLETE:
        if _is_annotated(record):
            return "slice loop complete, previously distilled"
        return "slice loop complete, not yet distilled"
    if loop == LOOP_STOPPED:
        return "slice loop stopped, not yet distilled"
    return "slice loop closed, not yet distilled"


class _Adrs:
    """Every scan's ADRs, for judging a ``related adr`` edge; build once per run."""

    def __init__(self, scans: Sequence[VaultScan]) -> None:
        self._readable: dict[tuple[str, str], list[str | None]] = {}
        self._unreadable: set[tuple[str, str]] = set()
        self._blind: set[str] = set()
        for scan in scans:
            for record in scan.records:
                if record.kind == "adr":
                    self._readable.setdefault((scan.vault, record.name), []).append(status(record))
            for gone in scan.unreadable:
                if gone.kind == "adr":
                    self._unreadable.add((scan.vault, gone.name))
            for hidden in scan.escaped:
                if hidden.kind == "adr":
                    if hidden.name is None:
                        self._blind.add(scan.vault)
                    else:
                        self._unreadable.add((scan.vault, hidden.name))
        self._vaults = [s.vault for s in scans]

    def _copies(self, vaults: Sequence[str], name: str) -> tuple[list[str | None], bool]:
        found: list[str | None] = []
        unreadable = False
        for v in vaults:
            found.extend(self._readable.get((v, name), ()))
            unreadable = unreadable or (v, name) in self._unreadable or v in self._blind
        return found, unreadable

    def judge(self, own_vault: str, name: str) -> tuple[bool, str]:
        """Whether the edge excludes, and when it does not, the note saying why."""
        found, unreadable = self._copies([own_vault], name)
        if not found and not unreadable:
            found, unreadable = self._copies([v for v in self._vaults if v != own_vault], name)
        if unreadable:
            return False, NOTE_UNREADABLE
        if not found:
            return False, NOTE_NOT_FOUND
        if all(s in ADR_DECIDED for s in found):
            return True, ""
        if any(s not in ADR_STATUSES for s in found):
            return False, NOTE_UNRECOGNISED
        return False, NOTE_DRAFT


def _adr_edges(record: ScannedRecord) -> list[str]:
    names = []
    entries = _mapping(record.sidecar, "related").get("adr")
    for entry in entries if isinstance(entries, list) else ():
        parsed = references.resolve(entry, "adr") if isinstance(entry, str) else None
        if parsed is not None and parsed[0] == "adr":
            names.append(parsed[1])
    return names


def _linked_spec_names(scans: Sequence[VaultScan]) -> set[str]:
    """Every spec name some task, in any vault, links to through ``related.spec``."""
    linked: set[str] = set()
    for scan in scans:
        for record in scan.records:
            if record.kind != "task":
                continue
            if any(record.sidecar.get(f) is None for f in INDEXED_TASK_FIELDS):
                continue
            entries = _mapping(record.sidecar, "related").get("spec")
            if isinstance(entries, list):
                linked.update(e for e in entries if isinstance(e, str))
    return linked


_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def _sort_key(item: QueueItem):
    when = parse_timestamp(item.updated_at)
    return (when is not None, when or _EARLIEST, item.vault, item.name)


def build_distill_queue(scans: Sequence[VaultScan]) -> DistillQueue | DistillSkipped:
    """Judge every spec of every scanned vault; see the module docstring for the rule."""
    at_fault = tuple(s.vault for s in scans if not s.readable_for(JUDGED_KINDS))
    holders = tuple(s.vault for s in scans
                    if s.readable_for(JUDGED_KINDS) and holds_unreadable_judged_record(s))
    if at_fault or holders:
        return DistillSkipped(at_fault, holders,
                              tuple(r for s in scans for r in unjudgeable_records(s)))

    adrs = _Adrs(scans)
    linked = _linked_spec_names(scans)
    waiting: list[QueueItem] = []
    deferred: list[QueueItem] = []
    for scan in scans:
        for record in scan.records:
            if record.kind != "spec" or not _is_candidate(record):
                continue
            if _is_annotated(record) and not _is_previously_distilled_resume(record):
                continue
            note = None
            excluded = False
            for adr_name in _adr_edges(record):
                decided, why = adrs.judge(record.vault, adr_name)
                if decided:
                    excluded = True
                    break
                note = note or why
            if excluded:
                continue
            item = QueueItem(record.vault, record.name, record.updated_at, status(record),
                             _reason(record), note)
            (waiting if record.name in linked else deferred).append(item)
    return DistillQueue(tuple(sorted(waiting, key=_sort_key)), tuple(sorted(deferred, key=_sort_key)))
