"""Plain data types shared by every unit of the health report."""

from __future__ import annotations

from dataclasses import dataclass, field


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
    """

    vault: str
    root: str
    records: list[ScannedRecord] = field(default_factory=list)
    unreadable: list[UnreadableRecord] = field(default_factory=list)
    unreadable_reason: str | None = None
    skipped_escaping: int = 0

    @property
    def fully_read(self) -> bool:
        """Whether every record of the vault was reached: no directory error, no escaping skip."""
        return self.unreadable_reason is None and self.skipped_escaping == 0


@dataclass(frozen=True)
class IndexCount:
    """The search index's row count for one vault root, or why there is none.

    ``status`` is ``"ok"``, ``"absent"`` or ``"unreadable"``; ``count`` is
    ``None`` unless ``"ok"``. ``immutable_snapshot`` is always ``True``: the
    count is read without touching the index, so rows a live writer has not yet
    checkpointed are not seen and the count can lag the index.
    """

    root: str
    status: str
    count: int | None
    immutable_snapshot: bool = True
