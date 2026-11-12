"""Retired-links check: live records that link to dropped or superseded records.

Pure: takes the scan of every configured vault and returns data. A live record
(an active decision, ADR or area; an open, ready, in-progress or blocked task)
is reported for a reference whose every readable candidate is ``dropped`` or
``superseded``. The ``supersedes`` field records history and is not judged, and
a task's reference that can resolve to a spec is not judged. When any vault was
not fully read the check is skipped, because a candidate's status cannot then
be trusted.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from . import references
from .model import ScannedRecord, VaultScan

RETIRED_STATUSES = ("dropped", "superseded")
_LIVE_STATUSES = {
    "decision": ("active",),
    "adr": ("active",),
    "area": ("active",),
    "task": ("open", "ready", "in-progress", "blocked"),
}


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
    carried. Both are empty for a dropped candidate.
    """

    vault: str
    kind: str
    name: str
    status: str
    successors: tuple[Successor, ...] = ()
    nonconforming_successors: int = 0


@dataclass(frozen=True)
class Finding:
    """One reference of a live record whose every candidate is retired.

    ``field`` is ``related <kind>``, ``depends-on``, ``parent`` or ``wikilink``;
    ``target`` is ``<kind>/<name>`` (a bare name for a bare reference).
    """

    vault: str
    kind: str
    name: str
    updated_at: str | None
    field: str
    target: str
    retired: tuple[RetiredCandidate, ...] = ()


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

    result = RetiredResult()
    for s in scans:
        if s.vault not in selected:
            continue
        judged = 0
        for record in s.records:
            if not _is_live(record):
                continue
            judged += 1
            seen: set[tuple[str, str]] = set()
            for ref in references.iter_references(record):
                if ref.field == "supersedes":
                    continue
                parsed = references.resolve(ref.target, ref.default_kind)
                if parsed is None:
                    continue
                kind, name = parsed
                target = f"{kind}/{name}" if kind else name
                if (ref.field, target) in seen:
                    continue
                seen.add((ref.field, target))
                found, unreadable = candidates.lookup(kind, name)
                if unreadable or not found:
                    continue
                if record.kind == "task" and any(c.kind == "spec" for c in found):
                    continue
                if not all(_status(c) in RETIRED_STATUSES for c in found):
                    continue
                retired = tuple(_candidate(c, candidates) for c in sorted(found, key=_key))
                result.findings.append(
                    Finding(
                        record.vault, record.kind, record.name, record.updated_at,
                        ref.field, target, retired,
                    )
                )
        result.judged[s.vault] = judged
    return result


def _status(record: ScannedRecord) -> str | None:
    status = record.sidecar.get("status") if isinstance(record.sidecar, dict) else None
    return status if isinstance(status, str) else None


def _is_live(record: ScannedRecord) -> bool:
    return _status(record) in _LIVE_STATUSES.get(record.kind, ())


def _key(record: ScannedRecord) -> tuple[str, str, str]:
    return record.kind, record.name, record.vault


def _candidate(record: ScannedRecord, candidates: references.CandidateIndex) -> RetiredCandidate:
    status = _status(record)
    assert status is not None
    successors: list[Successor] = []
    nonconforming = 0
    if status == "superseded":
        seen: set[tuple[str, str, str]] = set()
        for s in candidates.superseded_by(record.kind, record.name):
            if _key(s) in seen:
                continue
            seen.add(_key(s))
            if references.resolve(f"{s.kind}/{s.name}", None) is None:
                nonconforming += 1
            else:
                successors.append(Successor(s.vault, s.kind, s.name, _status(s)))
        successors.sort(key=lambda s: (s.kind, s.name, s.vault))
    return RetiredCandidate(
        record.vault, record.kind, record.name, status, tuple(successors), nonconforming
    )
