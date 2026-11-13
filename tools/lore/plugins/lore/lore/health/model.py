"""Plain data types shared by every unit of the health report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def parse_timestamp(value: object) -> datetime | None:
    """*value* as an aware UTC datetime, or ``None``; never raises.

    ``None`` for anything that is not an ISO-8601 string carrying a time and an
    offset: a non-string, prose, a date with no time, a timestamp with no
    offset, or one whose UTC conversion overflows. The health report reads every
    ``updated-at`` through this, so a synced vault's stray value costs one date,
    never a check.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class ScannedRecord:
    """One record read from disk: sidecar parsed, body decoded."""

    vault: str
    kind: str
    name: str
    updated_at: str | None
    sidecar: dict
    body: str


@dataclass(frozen=True)
class UnreadableRecord:
    """A record that could not be read. Carries a reason code and no record text."""

    vault: str
    kind: str
    name: str
    reason: str


@dataclass
class VaultScan:
    """The result of scanning one vault.

    ``unreadable_reason`` is set when the vault directory itself could not be
    read. ``skipped_escaping`` counts records (or kind directories) whose files
    resolve outside the vault root; their paths are never recorded.
    ``indexed`` counts the records the search index would hold: every readable
    record whose projection the indexer accepts, plus every oversize record
    (the indexer reads without a cap).
    """

    vault: str
    root: str
    records: list[ScannedRecord] = field(default_factory=list)
    unreadable: list[UnreadableRecord] = field(default_factory=list)
    unreadable_reason: str | None = None
    skipped_escaping: int = 0
    indexed: int = 0

    @property
    def fully_read(self) -> bool:
        """Whether every record of the vault was reached: no directory error, no escaping skip."""
        return self.unreadable_reason is None and self.skipped_escaping == 0


@dataclass(frozen=True)
class IndexCount:
    """The search index's row count for one vault root, or why there is none.

    ``status`` is ``"ok"``, ``"absent"`` or ``"unreadable"``; ``count`` is
    ``None`` unless ``"ok"``. The count is read without touching the index, so
    rows a live writer has not yet checkpointed are not seen.
    """

    root: str
    status: str
    count: int | None
