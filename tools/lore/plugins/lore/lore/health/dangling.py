"""Dangling-reference check: references that point at no record in any vault.

Pure: takes the scan of every configured vault and returns data. A reference
dangles when no record with that kind and name exists in any configured vault;
a bare name dangles only when no record of any kind has it. When any vault was
not fully read the check is skipped, because nothing can then be confirmed
missing.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from . import references
from .model import ScannedRecord, VaultScan

SESSION_KIND = "session"


@dataclass(frozen=True)
class Finding:
    """One reference that dangles, or is not shaped like a record id.

    ``field`` is ``related <kind>``, ``supersedes``, ``depends-on``, ``parent``
    or ``wikilink``. ``target`` is ``<kind>/<name>`` (a bare name for a bare
    reference) and is ``None`` when ``malformed``: the offending text is never
    carried, only ``locations`` -- every malformed spot in the field, in order and
    without repeats: body lines for ``wikilink``, 1-based entry positions for a
    list field, empty for ``parent``.
    """

    vault: str
    kind: str
    name: str
    updated_at: str | None
    field: str
    target: str | None
    malformed: bool = False
    locations: tuple[int, ...] = ()


@dataclass(frozen=True)
class DanglingResult:
    """Findings, and per selected vault the number of records judged."""

    findings: list[Finding] = field(default_factory=list)
    judged: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DanglingSkipped:
    """The check could not run; ``vaults`` names the vaults at fault."""

    vaults: tuple[str, ...]


def check_dangling(
    scans: Sequence[VaultScan], selected: Collection[str]
) -> DanglingResult | DanglingSkipped:
    """Check every selected vault's records against the records of all *scans*."""
    at_fault = tuple(s.vault for s in scans if not s.fully_read)
    if at_fault:
        return DanglingSkipped(at_fault)

    candidates = references.build_candidates(scans)

    result = DanglingResult()
    for s in scans:
        if s.vault not in selected:
            continue
        judged = 0
        for record in s.records:
            if record.kind == SESSION_KIND:
                continue
            judged += 1
            seen: set[tuple[str, str]] = set()
            malformed: dict[str, list[int]] = {}
            for ref in references.iter_references(record):
                parsed = references.resolve(ref.target, ref.default_kind)
                if parsed is None:
                    locations = malformed.setdefault(ref.field, [])
                    if ref.location is not None and ref.location not in locations:
                        locations.append(ref.location)
                    continue
                kind, name = parsed
                target = f"{kind}/{name}" if kind else name
                if (ref.field, target) in seen:
                    continue
                seen.add((ref.field, target))
                found, unreadable = candidates.lookup(kind, name)
                if not found and not unreadable:
                    result.findings.append(_finding(record, ref.field, target))
            for field_label, locations in malformed.items():
                result.findings.append(_finding(record, field_label, None, tuple(locations)))
        result.judged[s.vault] = judged
    return result


def _finding(
    record: ScannedRecord, field_label: str, target: str | None, locations: tuple[int, ...] = ()
) -> Finding:
    return Finding(
        record.vault, record.kind, record.name, record.updated_at, field_label, target,
        malformed=target is None, locations=locations,
    )
