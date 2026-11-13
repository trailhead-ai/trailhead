"""Stale-tasks check: unfinished tasks past the staleness window or tied to a retired spec.

Pure: takes the scan of every configured vault, the current time and the window,
and returns data. An unfinished task (``open``, ``ready``, ``in-progress`` or
``blocked``) is reported once when its ``updated-at`` is earlier than
``now - window_days``, when it has no readable ``updated-at``, or when a
reference of it that can resolve to a spec resolves only to ``dropped`` or
``superseded`` records, none unreadable. That is the guard ``retired-links``
applies to a task's reference, so a link one check leaves to the other is never
dropped by both and never reported by both. The ``supersedes`` field records
history and is not judged. When any vault was not fully read the check is
skipped, because a spec's status cannot then be trusted.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import references
from .model import (
    UNFINISHED_TASK_STATUSES,
    ScannedRecord,
    VaultScan,
    key,
    parse_timestamp,
    status,
)
from .retired import retired_references


@dataclass(frozen=True)
class RetiredSpec:
    """A dropped or superseded spec a task refers to."""

    vault: str
    name: str
    status: str


@dataclass(frozen=True)
class Finding:
    """One unfinished task that is stale, tied to a retired spec, or undated.

    ``age_days`` is the whole days since the last update, set only when the task
    is stale on age; ``undated`` is true when ``updated-at`` does not parse;
    ``retired_specs`` are the retired specs it refers to, sorted by name and vault.
    """

    vault: str
    kind: str
    name: str
    updated_at: str | None
    status: str
    age_days: int | None
    undated: bool
    retired_specs: tuple[RetiredSpec, ...] = ()


@dataclass(frozen=True)
class StaleResult:
    """Findings, and per selected vault the number of unfinished tasks judged."""

    findings: list[Finding] = field(default_factory=list)
    judged: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class StaleSkipped:
    """The check could not run; ``vaults`` names the vaults at fault."""

    vaults: tuple[str, ...]


def check_stale(
    scans: Sequence[VaultScan],
    selected: Collection[str],
    *,
    now: datetime,
    window_days: int,
) -> StaleResult | StaleSkipped:
    """Judge the unfinished tasks of every selected vault against *now* and the window."""
    at_fault = tuple(s.vault for s in scans if not s.fully_read)
    if at_fault:
        return StaleSkipped(at_fault)

    candidates = references.build_candidates(scans)
    start = now - timedelta(days=window_days)

    result = StaleResult()
    for s in scans:
        if s.vault not in selected:
            continue
        judged = 0
        for record in s.records:
            if record.kind != "task" or status(record) not in UNFINISHED_TASK_STATUSES:
                continue
            judged += 1
            when = parse_timestamp(record.updated_at)
            age = (now - when).days if when is not None and when < start else None
            specs = _retired_specs(record, candidates)
            if when is None or age is not None or specs:
                result.findings.append(
                    Finding(record.vault, record.kind, record.name, record.updated_at,
                            status(record), age, when is None, specs)
                )
        result.judged[s.vault] = judged
    return result


def _retired_specs(
    record: ScannedRecord, candidates: references.CandidateIndex
) -> tuple[RetiredSpec, ...]:
    tied: dict[tuple[str, str, str], ScannedRecord] = {}
    for _, _, found in retired_references(record, candidates):
        tied.update((key(c), c) for c in found if c.kind == "spec")
    return tuple(
        RetiredSpec(c.vault, c.name, status(c))
        for c in sorted(tied.values(), key=lambda c: (c.name, c.vault))
    )
