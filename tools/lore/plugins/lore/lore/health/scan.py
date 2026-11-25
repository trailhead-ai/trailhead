"""Read-only scan of every configured vault, and the search index's per-vault row count.

Nothing here writes: no index connection that can create files, no vault write
lock, no journal recovery. Records are enumerated through the confined iterator
the rename sweep uses, so a symlink planted in a vault cannot make the scan read
outside it.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

from ..cli import common
from ..record import model as record_model
from ..record import rename
from ..search import index as index_mod
from .model import EscapedRecord, IndexCount, ScannedRecord, SidecarOnlyRecord, UnreadableRecord, VaultScan

#: Largest sidecar or body, in bytes, the scan will read.
MAX_RECORD_BYTES = 1024 * 1024

#: Default per-vault byte budget for one scan. The largest live vault measured is
#: about 33 MiB raw; this is roughly 7.8 times that.
DEFAULT_MAX_SCAN_BYTES = 256 * 1024 * 1024

REASON_SIDECAR_MALFORMED = "sidecar-malformed"
REASON_SIDECAR_UNREADABLE = "sidecar-unreadable"
REASON_BODY_UNREADABLE = "body-unreadable"
REASON_OVERSIZE = "oversize"
REASON_VAULT_UNREADABLE = "vault-unreadable"
REASON_VAULT_MISSING = "vault-missing"
REASON_OVER_BUDGET = "over-budget"

INDEX_OK = "ok"
INDEX_ABSENT = "absent"
INDEX_UNREADABLE = "unreadable"


class HealthResolutionError(Exception):
    """The vault set could not be resolved; ``configured`` names what is configured."""

    def __init__(self, message: str, configured: tuple[str, ...] = ()):
        super().__init__(message)
        self.configured = configured


def resolve_vaults(only: str | None = None) -> list[tuple[str, Path]]:
    """Return ``[(name, path), ...]`` for every configured vault, or just *only*.

    Refuses rather than degrading: an unreadable config or an unknown *only*
    name raises :class:`HealthResolutionError` carrying the configured names.
    """
    vaults, error = common._resolve_all_vaults()
    if error is not None:
        raise HealthResolutionError(error)
    names = tuple(n for n, _ in vaults)
    if only is None:
        return vaults
    picked = [(n, p) for n, p in vaults if n == only]
    if not picked:
        raise HealthResolutionError(
            f"no vault named {only!r}; configured vaults: {', '.join(names)}", names
        )
    return picked


def new_vault_scan(name: str, root) -> VaultScan:
    return VaultScan(vault=name, root=str(root))


def _read_capped(path: Path) -> tuple[str, int]:
    """Decode *path* as UTF-8, with the bytes read; ``OSError`` if unreadable.

    ``ValueError`` past the cap or on undecodable bytes. Reads at most one byte
    past the cap, so an oversize file is never loaded whole.
    """
    with open(path, "rb") as fh:
        data = fh.read(MAX_RECORD_BYTES + 1)
    if len(data) > MAX_RECORD_BYTES:
        raise _Oversize(REASON_OVERSIZE)
    try:
        return data.decode("utf-8"), len(data)
    except UnicodeDecodeError:
        raise _Undecodable(len(data)) from None


class _Oversize(ValueError):
    """The file is past the cap; it was charged at the capped read size."""


class _Undecodable(ValueError):
    """The file is not UTF-8; ``size`` is the bytes that were read, to be charged."""

    def __init__(self, size: int):
        super().__init__(size)
        self.size = size


class _IndexerProbe:
    """Decides whether the indexer would hold a record, by running its own projection.

    The projection runs against a throwaway in-memory database with the real
    schema, so every condition the indexer skips a record on (a missing
    ``created-at`` or ``updated-at``, a reserved label key, an unbindable value)
    is applied exactly as it applies there, without a second list to keep in step.
    """

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute("PRAGMA foreign_keys=ON")
        for statement in index_mod._DDL_STATEMENTS:
            self._conn.execute(statement)

    def holds(self, kind: str, name: str, sidecar: dict, body: str) -> bool:
        """Whether the indexer would keep the record.

        The indexer's skip guard leaves whatever row the projection had inserted
        before it raised, and a rejection the projection undoes itself leaves none,
        so the verdict is whether the record's row is present afterwards.
        """
        record_id = index_mod._record_id("probe", kind, name)
        try:
            index_mod._project_record(self._conn, "probe", kind, name, sidecar, body, 0, 0.0, 0)
        except Exception:  # noqa: BLE001 - mirrors the indexer's per-record skip guard
            pass
        held = self._conn.execute("SELECT 1 FROM records WHERE id=?", (record_id,)).fetchone() is not None
        index_mod._delete_projection(self._conn, record_id)
        return held

    def close(self) -> None:
        self._conn.close()


def _attribute_escapes(result: VaultScan, root: Path) -> None:
    """Name each escaping record or kind directory on *result* by kind and name.

    Learns identity from directory entry names alone, so nothing outside the
    vault is opened, and applies the confinement rule the confined iterator
    does: a kind directory whose realpath leaves the root is never listed, and a
    record escapes when either its ``.md`` or its ``.json`` does. Only
    flat ``<kind>/<name>.md`` entries count, as in the scan.
    """
    root_real = os.path.realpath(root)
    inside = rename.store._realpath_is_descendant
    with os.scandir(root) as it:
        kind_entries = sorted((e for e in it if e.name in record_model.KINDS), key=lambda e: e.name)
    for entry in kind_entries:
        if not os.path.isdir(entry.path):
            continue
        if not inside(Path(entry.path), root_real):
            result.escaped.append(EscapedRecord(result.vault, entry.name, None))
            continue
        with os.scandir(entry.path) as it:
            names = sorted(e.name[:-3] for e in it if e.name.endswith(".md"))
        for name in names:
            body = Path(entry.path) / f"{name}.md"
            if not inside(body, root_real) or not inside(body.with_suffix(".json"), root_real):
                result.escaped.append(EscapedRecord(result.vault, entry.name, name))


def iter_vault_records(
    result: VaultScan, *, probe_index: bool = True, max_bytes: int = DEFAULT_MAX_SCAN_BYTES
):
    """Yield each readable record of ``result.root`` one at a time.

    Unreadable records, escaping skips and an unreadable vault are accumulated
    on *result*; the generator itself never raises for a bad record or vault.
    A record is considered exactly when the indexer would consider it: a flat
    ``<kind>/<name>`` with both ``.md`` and ``.json`` present. ``result.indexed``
    counts those the indexer would hold, which excludes an unreadable record
    (the indexer skips it) but includes an oversize one whose sidecar the
    indexer accepts (it reads without a cap).
    A vault directory that is missing, or a kind directory that cannot be
    listed, marks the vault unreadable.

    *max_bytes* bounds what is read from the vault: ``result.bytes_read`` is
    charged as each record is read (an oversize file at its capped read size), and
    once it passes the budget no further record is opened and the vault is marked
    :data:`REASON_OVER_BUDGET`. Records already yielded are at most the budget plus
    one record.

    The index probe is about half the scan's run time and feeds only ``indexed``;
    a caller that never reads that count passes ``probe_index=False``, which
    leaves ``indexed`` at 0 and every other field of the scan unchanged.
    """

    def skipped():
        result.skipped_escaping += 1

    def unlistable():
        result.unreadable_reason = REASON_VAULT_UNREADABLE

    result.budget_bytes = max_bytes
    probe = _IndexerProbe() if probe_index else None
    try:
        root = Path(result.root)
        if not root.is_dir():
            result.unreadable_reason = (
                REASON_VAULT_UNREADABLE if root.exists() else REASON_VAULT_MISSING
            )
            return
        for record_id, kind, body_path in rename.iter_records(
            root, skipped, flat_only=True, on_unlistable=unlistable
        ):
            name = record_id.split("/", 1)[1]
            sidecar_path = body_path.with_suffix(".json")
            record = _read_record(result, probe, kind, name, sidecar_path, body_path)
            if result.bytes_read > max_bytes:
                result.unreadable_reason = REASON_OVER_BUDGET
                return
            if record is not None:
                yield record
        if result.skipped_escaping:
            _attribute_escapes(result, root)
    except OSError:
        result.unreadable_reason = REASON_VAULT_UNREADABLE
    finally:
        if probe is not None:
            probe.close()


def _read_record(result, probe, kind, name, sidecar_path, body_path) -> ScannedRecord | None:
    """Read one record, charging ``result.bytes_read``; ``None`` when it is not a readable record.

    An unreadable record lands on ``result.unreadable``; ``result.indexed`` counts it
    when the indexer would hold it. A record unreadable only for an oversize body also
    keeps its parsed sidecar on ``result.sidecar_only``.
    """

    def unreadable(reason: str) -> None:
        result.unreadable.append(UnreadableRecord(result.vault, kind, name, reason))

    try:
        if not sidecar_path.exists():
            return None
        sidecar_text, size = _read_capped(sidecar_path)
        result.bytes_read += size
    except _Oversize:
        result.bytes_read += MAX_RECORD_BYTES
        if probe is not None:
            result.indexed += 1
        unreadable(REASON_OVERSIZE)
        return None
    except _Undecodable as exc:
        result.bytes_read += exc.size
        unreadable(REASON_SIDECAR_MALFORMED)
        return None
    except ValueError:
        unreadable(REASON_SIDECAR_MALFORMED)
        return None
    except OSError:
        unreadable(REASON_SIDECAR_UNREADABLE)
        return None
    try:
        sidecar = json.loads(sidecar_text)
    except (ValueError, RecursionError):
        sidecar = None
    if not isinstance(sidecar, dict):
        unreadable(REASON_SIDECAR_MALFORMED)
        return None
    try:
        body, size = _read_capped(body_path)
        result.bytes_read += size
    except _Oversize:
        result.bytes_read += MAX_RECORD_BYTES
        if probe is not None and probe.holds(kind, name, sidecar, ""):
            result.indexed += 1
        result.sidecar_only.append(SidecarOnlyRecord(result.vault, kind, name, sidecar))
        unreadable(REASON_OVERSIZE)
        return None
    except _Undecodable as exc:
        result.bytes_read += exc.size
        unreadable(REASON_BODY_UNREADABLE)
        return None
    except (ValueError, OSError):
        unreadable(REASON_BODY_UNREADABLE)
        return None
    if probe is not None and probe.holds(kind, name, sidecar, ""):
        result.indexed += 1
    updated = sidecar.get("updated-at")
    return ScannedRecord(
        result.vault, kind, name, updated if isinstance(updated, str) else None, sidecar, body
    )


def scan_vault(
    name: str, root, *, probe_index: bool = True, max_bytes: int = DEFAULT_MAX_SCAN_BYTES
) -> VaultScan:
    """Scan one vault eagerly; the records land on ``result.records``.

    A vault that passes *max_bytes* keeps none of the records it read: it is not
    fully read, and every check skips it.
    """
    result = new_vault_scan(name, root)
    result.records.extend(iter_vault_records(result, probe_index=probe_index, max_bytes=max_bytes))
    if result.unreadable_reason == REASON_OVER_BUDGET:
        result.records.clear()
    return result


_COUNT_SQL = "SELECT vault, COUNT(*) FROM records GROUP BY vault"


def count_index_rows(roots, env: dict[str, str] | None = None) -> dict[str, IndexCount]:
    """Count the index's rows for each vault root string in *roots*.

    Opens the index with SQLite's ``immutable=1``, which creates and modifies
    nothing in any state (``mode=ro`` would still create ``-shm``/``-wal`` files
    and fail in a read-only directory). The price is that rows a live writer has
    not yet checkpointed are invisible, so a count can lag the index. A missing index and an unreadable one come back
    as values.
    """
    roots = [str(r) for r in roots]
    path = index_mod._resolve_index_path(env=env)
    if not path.is_file():
        return {r: IndexCount(r, INDEX_ABSENT, None) for r in roots}
    uri = "file:" + quote(os.path.abspath(path)) + "?immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            counts = dict(conn.execute(_COUNT_SQL).fetchall())
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return {r: IndexCount(r, INDEX_UNREADABLE, None) for r in roots}
    return {r: IndexCount(r, INDEX_OK, counts.get(r, 0)) for r in roots}
