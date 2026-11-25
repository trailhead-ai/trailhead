"""Plain data types shared by every unit of the health report."""

from __future__ import annotations

from collections.abc import Iterable
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


RETIRED_STATUSES = ("dropped", "superseded")
UNFINISHED_TASK_STATUSES = ("open", "ready", "in-progress", "blocked")
LIVE_STATUSES = {
    "decision": ("active",),
    "adr": ("active",),
    "area": ("active",),
    "task": UNFINISHED_TASK_STATUSES,
}


@dataclass(frozen=True)
class ScannedRecord:
    """One record read from disk: sidecar parsed, body decoded."""

    vault: str
    kind: str
    name: str
    updated_at: str | None
    sidecar: dict
    body: str


def status(record: ScannedRecord) -> str | None:
    """The record's stored status, or ``None`` when it has no string status."""
    value = record.sidecar.get("status") if isinstance(record.sidecar, dict) else None
    return value if isinstance(value, str) else None


def is_live(record: ScannedRecord) -> bool:
    """Whether the record's status counts as live for its kind; an unrecognised status is not."""
    return status(record) in LIVE_STATUSES.get(record.kind, ())


def key(record: ScannedRecord) -> tuple[str, str, str]:
    """The record's ``(kind, name, vault)``, for sorting and set membership."""
    return record.kind, record.name, record.vault


@dataclass(frozen=True)
class UnreadableRecord:
    """A record that could not be read. Carries a reason code and no record text."""

    vault: str
    kind: str
    name: str
    reason: str


@dataclass(frozen=True)
class EscapedRecord:
    """A record whose files resolve outside the vault root, known by kind and name only.

    ``name`` is ``None`` when the whole kind directory escapes, so every record of
    that kind is unknown. No path is ever held.
    """

    vault: str
    kind: str
    name: str | None


@dataclass
class VaultScan:
    """The result of scanning one vault.

    ``unreadable_reason`` is set when the vault directory itself could not be
    read, or when its records exceed the scan's byte budget (``budget_bytes``,
    ``bytes_read`` counting the bytes read so far). ``skipped_escaping`` counts
    records (or kind directories) whose files resolve outside the vault root;
    ``escaped`` names each by kind and name, never by path.
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
    escaped: list[EscapedRecord] = field(default_factory=list, repr=False)
    bytes_read: int = 0
    budget_bytes: int | None = None

    @property
    def fully_read(self) -> bool:
        """Whether every record of the vault was reached: no directory error, no escaping skip."""
        return self.unreadable_reason is None and self.skipped_escaping == 0

    def readable_for(self, kinds: Iterable[str]) -> bool:
        """Whether every record of *kinds* was reached.

        False when the vault itself was not read, when a record or whole directory of
        one of *kinds* escapes, or when an escape could not be attributed to a kind
        (it may be of any). An escape in another kind does not count against it.
        """
        if self.unreadable_reason is not None or self.skipped_escaping > len(self.escaped):
            return False
        wanted = set(kinds)
        return not any(e.kind in wanted for e in self.escaped)

    def escapes_in(self, kinds: Iterable[str]) -> bool:
        """Whether the vault was read apart from escapes that all name a kind of *kinds*.

        True when :meth:`readable_for` is false only because an attributed escape is of
        one of *kinds*: a check that guards each link against an escaped target may skip
        this vault alone and still judge the others.
        """
        return (self.unreadable_reason is None and self.skipped_escaping <= len(self.escaped)
                and not self.readable_for(kinds))


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
