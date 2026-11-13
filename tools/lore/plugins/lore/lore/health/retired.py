"""Retired-links check: live records that link to dropped or superseded records.

Pure: takes the scan of every configured vault and returns data. A live record
(an active decision, ADR or area; an open, ready, in-progress or blocked task)
is reported for a reference whose every readable candidate is ``dropped`` or
``superseded``. The ``supersedes`` field records history and is not judged, and
neither is a link to a record the linker itself supersedes (with a bare name
matching several retired records, superseding any one of them is enough). A
task's reference that can resolve to a spec is not judged. When any vault was
not fully read the check is skipped, because a candidate's status cannot then
be trusted.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass, field

from . import references
from .model import RETIRED_STATUSES, ScannedRecord, VaultScan, is_live, key, status


@dataclass(frozen=True)
class Successor:
    """A record whose ``supersedes`` names a superseded candidate; ``status`` is
    ``None`` when it has no string status."""

    vault: str
    kind: str
    name: str
    status: str | None


@dataclass(frozen=True)
class RetiredCandidate:
    """A dropped or superseded record a reference resolves to.

    ``successors`` are the record-id-shaped successors, sorted by kind, name and
    vault; ``nonconforming_successors`` counts the rest, whose names are never
    carried. Both are empty for a dropped candidate. ``links_back`` is set on a
    superseded candidate with no successor at all whose own ``related`` entries
    name the linking record.
    """

    vault: str
    kind: str
    name: str
    status: str
    successors: tuple[Successor, ...] = ()
    nonconforming_successors: int = 0
    links_back: bool = False


@dataclass(frozen=True)
class Finding:
    """One reference of a live record whose every candidate is retired.

    ``field`` is ``related <kind>``, ``depends-on``, ``parent`` or ``wikilink``;
    ``target`` is ``<kind>/<name>`` (a bare name for a bare reference).
    ``linker_duplicated`` is set when another vault also holds a record, readable
    or not, with the linker's kind and name.
    """

    vault: str
    kind: str
    name: str
    updated_at: str | None
    field: str
    target: str
    retired: tuple[RetiredCandidate, ...] = ()
    linker_duplicated: bool = False


@dataclass(frozen=True)
class RetiredResult:
    """Findings, and per selected vault the number of linking records judged."""

    findings: list[Finding] = field(default_factory=list)
    judged: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RetiredSkipped:
    """The check could not run; ``vaults`` names the vaults at fault."""

    vaults: tuple[str, ...]


def check_retired(
    scans: Sequence[VaultScan], selected: Collection[str]
) -> RetiredResult | RetiredSkipped:
    """Check the live records of every selected vault against the records of all *scans*."""
    at_fault = tuple(s.vault for s in scans if not s.fully_read)
    if at_fault:
        return RetiredSkipped(at_fault)

    candidates = references.build_candidates(scans)
    holders: dict[tuple[str, str], set[str]] = {}
    for s in scans:
        for r in (*s.records, *s.unreadable):
            holders.setdefault((r.kind, r.name), set()).add(s.vault)

    result = RetiredResult()
    for s in scans:
        if s.vault not in selected:
            continue
        judged = 0
        for record in s.records:
            if not is_live(record):
                continue
            judged += 1
            for ref, target, found in retired_references(record, candidates):
                if record.kind == "task" and any(c.kind == "spec" for c in found):
                    continue
                if any(key(record) in {key(r) for r in candidates.superseded_by(c.kind, c.name)}
                       for c in found if status(c) == "superseded"):
                    continue
                retired = tuple(_candidate(c, candidates, record) for c in sorted(found, key=key))
                result.findings.append(
                    Finding(
                        record.vault, record.kind, record.name, record.updated_at,
                        ref.field, target, retired,
                        len(holders[(record.kind, record.name)]) > 1,
                    )
                )
        result.judged[s.vault] = judged
    return result


def retired_references(
    record: ScannedRecord, candidates: references.CandidateIndex
) -> Iterator[tuple[references.Reference, str, tuple[ScannedRecord, ...]]]:
    """Yield ``(reference, target, candidates)`` for each reference of *record*
    whose candidates are all ``dropped`` or ``superseded``, at least one, none unreadable.

    The ``supersedes`` field is never yielded, nor is a malformed reference. This is
    the one guard both ``retired-links`` and ``stale-tasks`` judge a link through.
    """
    for ref, parsed, target in references.resolved_references(record):
        if ref.field == "supersedes" or parsed is None:
            continue
        found, unreadable = candidates.lookup(*parsed)
        if found and not unreadable and all(status(c) in RETIRED_STATUSES for c in found):
            assert target is not None
            yield ref, target, found


def _candidate(
    record: ScannedRecord, candidates: references.CandidateIndex, linker: ScannedRecord
) -> RetiredCandidate:
    state = status(record)
    assert state is not None
    successors: list[Successor] = []
    nonconforming = 0
    if state == "superseded":
        seen: set[tuple[str, str, str]] = set()
        for s in candidates.superseded_by(record.kind, record.name):
            if key(s) in seen:
                continue
            seen.add(key(s))
            if references.resolve(f"{s.kind}/{s.name}", None) is None:
                nonconforming += 1
            else:
                successors.append(Successor(s.vault, s.kind, s.name, status(s)))
        successors.sort(key=lambda s: (s.kind, s.name, s.vault))
    links_back = (
        state == "superseded" and not successors and not nonconforming
        and _relates_to(record, linker)
    )
    return RetiredCandidate(
        record.vault, record.kind, record.name, state, tuple(successors), nonconforming,
        links_back,
    )


def _relates_to(record: ScannedRecord, other: ScannedRecord) -> bool:
    return any(
        ref.field.startswith("related ") and parsed == (other.kind, other.name)
        for ref, parsed, _ in references.resolved_references(record)
    )
