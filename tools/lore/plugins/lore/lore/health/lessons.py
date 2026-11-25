"""Lesson-growth check: executor-dispatch lessons, and how many were added within the window.

Pure: takes the scan of every configured vault, the current time and the window, and
returns data. An executor-dispatch lesson is a ``lesson`` record labelled
``craft/dispatch-lesson=executor`` whose status is neither ``dropped`` nor
``superseded``; per selected vault the check counts them (``judged``) and lists as
findings those whose ``created-at`` parses and falls within ``window_days`` days
before *now*, oldest first. A lesson whose ``created-at`` does not parse is counted
and never listed. Informational: the findings are growth, not defects.

Skips are per vault, because a vault whose lessons were not all read would report a
count that could be low: the vault was not fully read (``NOT_FULLY_READ``), holds a
lesson that could not be read (``UNREADABLE_LESSON``) or holds an escaped lesson
(``ESCAPED_LESSON``). An unreadable or escaped record of another kind does not skip. A lesson unreadable only
because its body is oversize is counted from its sidecar (``VaultScan.sidecar_only``): the
labels, status and ``created-at`` all live there; one whose sidecar is oversize cannot be.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .model import RETIRED_STATUSES, ScannedRecord, SidecarOnlyRecord, VaultScan, parse_timestamp, status

KIND = "lesson"
LABEL_KEY = "craft/dispatch-lesson"
LABEL_VALUE = "executor"

NOT_FULLY_READ = "not-fully-read"
UNREADABLE_LESSON = "unreadable-lesson"
ESCAPED_LESSON = "escaped-lesson"


@dataclass(frozen=True)
class Finding:
    """One executor-dispatch lesson added within the window; ``created_at`` parses.

    ``age_days`` is the whole days since it was added.
    """

    vault: str
    kind: str
    name: str
    created_at: str
    age_days: int

    @property
    def updated_at(self) -> str:
        """The date the report prints and sorts a finding by: when the lesson was added."""
        return self.created_at


@dataclass(frozen=True)
class LessonsResult:
    """Findings, per selected vault the lessons counted (``judged``), and per skipped vault why."""

    findings: list[Finding] = field(default_factory=list)
    judged: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


def _is_dispatch_lesson(record: ScannedRecord | SidecarOnlyRecord) -> bool:
    if record.kind != KIND or status(record) in RETIRED_STATUSES:
        return False
    labels = record.sidecar.get("labels") if isinstance(record.sidecar, dict) else None
    return isinstance(labels, dict) and labels.get(LABEL_KEY) == LABEL_VALUE


def _skip_reason(scan: VaultScan) -> str | None:
    if not scan.readable_for((KIND,)):
        return ESCAPED_LESSON if scan.escapes_in((KIND,)) else NOT_FULLY_READ
    counted = {s.name for s in scan.sidecar_only if s.kind == KIND}
    if any(u.kind == KIND and u.name not in counted for u in scan.unreadable):
        return UNREADABLE_LESSON
    return None


def check_lessons(
    scans: Sequence[VaultScan],
    selected: Collection[str],
    *,
    now: datetime,
    window_days: int,
) -> LessonsResult:
    """Count each selected vault's executor-dispatch lessons and list those added in the window."""
    start = now - timedelta(days=window_days)
    result = LessonsResult()
    dated: list[tuple[datetime, Finding]] = []
    for scan in scans:
        if scan.vault not in selected:
            continue
        reason = _skip_reason(scan)
        if reason is not None:
            result.skipped[scan.vault] = reason
            continue
        judged = 0
        for record in (*scan.records, *scan.sidecar_only):
            if not _is_dispatch_lesson(record):
                continue
            judged += 1
            created = record.sidecar.get("created-at")
            when = parse_timestamp(created)
            if when is not None and start <= when <= now:
                dated.append((when, Finding(record.vault, record.kind, record.name, created, (now - when).days)))
        result.judged[scan.vault] = judged
    dated.sort(key=lambda p: (p[0], p[1].vault, p[1].name))
    result.findings.extend(f for _, f in dated)
    return result
