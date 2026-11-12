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
from ..record import rename
from ..search import index as index_mod
from .model import IndexCount, ScannedRecord, UnreadableRecord, VaultScan

#: Largest sidecar or body, in bytes, the scan will read.
MAX_RECORD_BYTES = 1024 * 1024

REASON_SIDECAR_MALFORMED = "sidecar-malformed"
REASON_SIDECAR_UNREADABLE = "sidecar-unreadable"
REASON_BODY_UNREADABLE = "body-unreadable"
REASON_OVERSIZE = "oversize"
REASON_VAULT_UNREADABLE = "vault-unreadable"
REASON_VAULT_MISSING = "vault-missing"

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


def _read_capped(path: Path) -> str:
    """Decode *path* as UTF-8; ``ValueError`` past the cap, ``OSError`` if unreadable.

    Reads at most one byte past the cap, so an oversize file is never loaded whole.
    """
    with open(path, "rb") as fh:
        data = fh.read(MAX_RECORD_BYTES + 1)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError(REASON_OVERSIZE)
    return data.decode("utf-8")


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
        record_id = index_mod._record_id("probe", kind, name)
        try:
            index_mod._project_record(self._conn, "probe", kind, name, sidecar, body, 0, 0.0, 0)
        except Exception:  # noqa: BLE001 - mirrors the indexer's per-record skip guard
            index_mod._delete_projection(self._conn, record_id)
            return False
        index_mod._delete_projection(self._conn, record_id)
        return True

    def close(self) -> None:
        self._conn.close()


def iter_vault_records(result: VaultScan):
    """Yield each readable record of ``result.root`` one at a time.

    Unreadable records, escaping skips and an unreadable vault are accumulated
    on *result*; the generator itself never raises for a bad record or vault.
    A record is considered exactly when the indexer would consider it: a flat
    ``<kind>/<name>`` with both ``.md`` and ``.json`` present. ``result.indexed``
    counts those the indexer would hold, which excludes an unreadable record
    (the indexer skips it) but includes an oversize one (it reads without a cap).
    A vault directory that is missing, or a kind directory that cannot be
    listed, marks the vault unreadable.
    """

    def skipped():
        result.skipped_escaping += 1

    def unlistable():
        result.unreadable_reason = REASON_VAULT_UNREADABLE

    probe = _IndexerProbe()
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
            try:
                if not sidecar_path.exists():
                    continue
                sidecar_text = _read_capped(sidecar_path)
            except ValueError as exc:
                reason = (
                    REASON_OVERSIZE if str(exc) == REASON_OVERSIZE else REASON_SIDECAR_MALFORMED
                )
                if reason == REASON_OVERSIZE:
                    result.indexed += 1
                result.unreadable.append(UnreadableRecord(result.vault, kind, name, reason))
                continue
            except OSError:
                result.unreadable.append(
                    UnreadableRecord(result.vault, kind, name, REASON_SIDECAR_UNREADABLE)
                )
                continue
            try:
                sidecar = json.loads(sidecar_text)
            except ValueError:
                sidecar = None
            if not isinstance(sidecar, dict):
                result.unreadable.append(
                    UnreadableRecord(result.vault, kind, name, REASON_SIDECAR_MALFORMED)
                )
                continue
            try:
                body = _read_capped(body_path)
            except ValueError as exc:
                reason = REASON_OVERSIZE if str(exc) == REASON_OVERSIZE else REASON_BODY_UNREADABLE
                if reason == REASON_OVERSIZE:
                    result.indexed += 1
                result.unreadable.append(UnreadableRecord(result.vault, kind, name, reason))
                continue
            except OSError:
                result.unreadable.append(
                    UnreadableRecord(result.vault, kind, name, REASON_BODY_UNREADABLE)
                )
                continue
            if probe.holds(kind, name, sidecar, body):
                result.indexed += 1
            updated = sidecar.get("updated-at")
            yield ScannedRecord(
                result.vault,
                kind,
                name,
                updated if isinstance(updated, str) else None,
                sidecar,
                body,
            )
    except OSError:
        result.unreadable_reason = REASON_VAULT_UNREADABLE
    finally:
        probe.close()


def scan_vault(name: str, root) -> VaultScan:
    """Scan one vault eagerly; the records land on ``result.records``."""
    result = new_vault_scan(name, root)
    result.records.extend(iter_vault_records(result))
    return result


_COUNT_SQL = "SELECT vault, COUNT(*) FROM records GROUP BY vault"


def count_index_rows(roots, env: dict[str, str] | None = None) -> dict[str, IndexCount]:
    """Count the index's rows for each vault root string in *roots*.

    Opens the index with SQLite's ``immutable=1``, which creates and modifies
    nothing in any state (``mode=ro`` would still create ``-shm``/``-wal`` files
    and fail in a read-only directory). The price is that rows a live writer has
    not yet checkpointed are invisible, so a count can lag the index. A missing index and an unreadable one come back as values.
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
