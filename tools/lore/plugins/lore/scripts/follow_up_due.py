"""Follow-up-due selection helper for the check-in ritual.

Selects follow-up notes from a vault's ``follow-ups/`` directory that are due
for polling, based on status, source, check cadence, and last-checked date.
Returns a :class:`FollowUpDueResult` with three sorted lists:

- ``due``            — notes to poll this run (active + stale or bootstrap)
- ``manual``         — active notes with ``source: manual`` (need human check)
- ``skipped_legacy`` — notes with an off-vocab status (e.g. "snoozed"); logged
                       but never polled

Selection predicate:

  poll iff status == "active"
       AND source != "manual"
       AND (last-checked is empty  →  bootstrap-poll,
            OR last-checked is older than its check cadence)

Cadence rules:
  - "daily"  → stale if last-checked < today (strictly before)
  - "weekly" → stale if last-checked is more than 7 days before today
  - unknown  → treated as daily (conservative)

Canonical status vocab for follow-up notes: ``active | resolved | dropped``
(from ``status_validator.CANONICAL["follow-ups"]``). A note carrying any other
status (e.g. a legacy "snoozed") is skipped and placed in
``skipped_legacy``; the helper never crashes on an unexpected value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import frontmatter
from vault import iter_note_paths

# Canonical lore follow-up statuses that are "closed" — always skip.
_CLOSED_STATUSES = frozenset({"resolved", "dropped"})

# The one active status that drives polling.
_ACTIVE_STATUS = "active"

# Weekly cadence threshold: stale if more than 7 days have elapsed.
_WEEKLY_THRESHOLD_DAYS = 7


@dataclass
class FollowUpDueResult:
    """Outcome of a follow-up-due scan.

    Attributes:
        due:            Paths of follow-up notes due for polling this run.
        manual:         Paths of active ``source: manual`` notes (needs human
                        check — not polled by the skill).
        skipped_legacy: Paths of notes with an off-vocab status (e.g. "snoozed")
                        that were flagged and skipped.
    """

    due: list[Path] = field(default_factory=list)
    manual: list[Path] = field(default_factory=list)
    skipped_legacy: list[Path] = field(default_factory=list)


def _is_stale(last_checked: str, check: str, today: date) -> bool:
    """Return True if a last-checked date string is stale for the given cadence.

    An empty or missing last-checked value is always considered stale
    (bootstrap poll).
    """
    if not last_checked:
        return True

    try:
        # last-checked stores an ISO date prefix (YYYY-MM-DD)
        checked_date = date.fromisoformat(str(last_checked)[:10])
    except ValueError:
        # Unparseable date → treat as stale (conservative)
        return True

    cadence = (check or "").strip().lower()
    if cadence == "weekly":
        return (today - checked_date) > timedelta(days=_WEEKLY_THRESHOLD_DAYS)
    # "daily" and any unknown cadence: stale if checked before today
    return checked_date < today


def follow_up_notes_due(vault: Path, *, today: date) -> FollowUpDueResult:
    """Scan ``<vault>/follow-ups/`` and return notes due for polling.

    Args:
        vault: Root of the lore vault. The function reads
            ``<vault>/follow-ups/*.md``.
        today: The reference date for cadence calculations (injectable for tests).

    Returns:
        A :class:`FollowUpDueResult` with three sorted lists.
    """
    follow_up_dir = Path(vault) / "follow-ups"
    result = FollowUpDueResult()

    if not follow_up_dir.is_dir():
        return result

    for note in sorted(iter_note_paths(follow_up_dir, recursive=True)):
        fm = frontmatter.parse_frontmatter(note)
        status = fm.get("status", "")
        source = fm.get("source", "")
        check = fm.get("check", "daily")
        last_checked = fm.get("last-checked", "")

        if status in _CLOSED_STATUSES:
            # Resolved / dropped: silently skip (not a legacy surprise)
            continue

        if status != _ACTIVE_STATUS:
            # Off-vocab status (e.g. "snoozed"): flag and skip, never crash
            result.skipped_legacy.append(note)
            continue

        # status == "active" from here on
        if source == "manual":
            result.manual.append(note)
            continue

        if _is_stale(last_checked, check, today):
            result.due.append(note)
        # else: active but fresh — skip this run

    return result
