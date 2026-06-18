"""Minimal derived SQLite index for the lore vault (Slice 1, S2).

This module provisions and maintains a **derived** global index of lore records
at ``state_dir("lore")/index.sqlite`` (WAL mode, created on first use). It is
the authoritative index store for S2 and the seam that S3 enriches with FTS5 +
BM25 search.

**Text-wins / index-derived posture (locked, umbrella decision 7; spec AC-TX2):**
The git-tracked text files (``<kind>/<name>.md`` + ``<kind>/<name>.json``) are
the source of truth. The index is a cached projection — never the write target,
always rebuildable via ``rebuild(vaults, conn)``. On any partial failure the text
already on disk wins; ``lore reindex`` reconstructs the index from the vault tree.

**Index boundary (S2):** This slice owns a *minimal* index — a single ``records``
table keyed ``(vault, kind, name)`` with the scalar sidecar fields from the S1
record model plus a ``body`` text column and ``scope``/``layer`` forward-compat
columns. **No FTS5/BM25** — that is S3. Because the index is derived and
``rebuild``-able, S3 reshaping the schema costs zero migration.

**Invariants:**
- I-1: The primary key is ``(vault, kind, name)``; upsert is always idempotent.
- I-2: ``rebuild`` drops the table and repopulates in one step — the recovery
  path after any drift between disk and index.
- I-3: List/map sidecar fields (``keywords``, ``related-*``, ``related``) are
  stored as JSON strings; callers must not rely on their parsed form from the index
  (text is authoritative; query by scalar columns only in S2).
- I-4: ``scope`` and ``layer`` are NULL in S2; S3/S4 populate them.
- I-5: ``delete_row`` is always a silent no-op on a missing key (never raises).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Index path resolver
# ---------------------------------------------------------------------------

def _resolve_index_path(env: dict[str, str] | None = None) -> Path:
    """Return the canonical path for the SQLite index.

    Mirrors the promote-token pattern at ``cli/lore:602-612`` exactly, substituting
    ``"index.sqlite"`` for ``"promote-tokens"``.  Catches ``(ImportError, SystemExit)``
    so the fallback fires on any import failure.

    Args:
        env: Optional environment dict (for test isolation via XDG_STATE_HOME).
             When None, ``os.environ`` is used.
    """
    try:
        import _bootstrap
        _bootstrap.ensure_trailhead_importable()
        import trailhead.paths as _paths
        if env is not None:
            return _paths.state_dir("lore", env=env) / "index.sqlite"
        return _paths.state_dir("lore") / "index.sqlite"
    except (ImportError, SystemExit):
        # Fallback: use ~/.local/state/lore/index.sqlite
        base = Path(env.get("XDG_STATE_HOME", "")) if env else None
        if base and base.is_absolute():
            return base / "lore" / "index.sqlite"
        return Path.home() / ".local" / "state" / "lore" / "index.sqlite"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """\
CREATE TABLE IF NOT EXISTS records (
    vault           TEXT NOT NULL,
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    title           TEXT,
    status          TEXT,
    team            TEXT,
    suite           TEXT,
    product         TEXT,
    repo            TEXT,
    created_at      TEXT,
    created_by      TEXT,
    updated_at      TEXT,
    updated_by      TEXT,
    last_referenced_at TEXT,
    keywords        TEXT,
    related         TEXT,
    related_urls    TEXT,
    related_phases  TEXT,
    related_files   TEXT,
    body            TEXT,
    scope           TEXT,
    layer           TEXT,
    PRIMARY KEY (vault, kind, name)
)
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def open_index(env: dict[str, str] | None = None) -> sqlite3.Connection:
    """Open (creating on first use) the global lore index in WAL mode.

    The database is created at ``state_dir("lore")/index.sqlite``, honoring a
    ``XDG_STATE_HOME`` override injected via ``env`` for test isolation — no
    ``os.environ`` mutation.

    Returns a ``sqlite3.Connection`` in WAL mode with the ``records`` table
    provisioned. The caller is responsible for ``conn.commit()`` and
    ``conn.close()``.
    """
    index_path = _resolve_index_path(env=env)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(index_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_DDL)
    conn.commit()
    return conn


def upsert_row(
    conn: sqlite3.Connection,
    vault: str,
    kind: str,
    name: str,
    sidecar: dict[str, Any],
    body: str,
) -> None:
    """Insert or replace the record row keyed by ``(vault, kind, name)``.

    Scalar sidecar fields are stored verbatim; list/map fields are JSON-encoded
    (Invariant I-3). The ``scope`` and ``layer`` columns are set from the sidecar
    when present, defaulting to NULL (Invariant I-4).

    The caller must call ``conn.commit()`` to persist.
    """
    def _get(key: str) -> Any:
        return sidecar.get(key)

    def _json(key: str) -> str | None:
        val = sidecar.get(key)
        return json.dumps(val) if val is not None else None

    conn.execute(
        """\
        INSERT OR REPLACE INTO records (
            vault, kind, name,
            title, status, team, suite, product, repo,
            created_at, created_by, updated_at, updated_by, last_referenced_at,
            keywords, related, related_urls, related_phases, related_files,
            body, scope, layer
        ) VALUES (
            ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?
        )
        """,
        (
            vault, kind, name,
            _get("title"), _get("status"), _get("team"), _get("suite"),
            _get("product"), _get("repo"),
            _get("created-at"), _get("created-by"),
            _get("updated-at"), _get("updated-by"),
            _get("last-referenced-at"),
            _json("keywords"), _json("related"), _json("related-urls"),
            _json("related-phases"), _json("related-files-or-folders"),
            body,
            _get("scope"), _get("layer"),
        ),
    )


def delete_row(
    conn: sqlite3.Connection,
    vault: str,
    kind: str,
    name: str,
) -> None:
    """Remove the row keyed by ``(vault, kind, name)``.

    Silent no-op when the key does not exist (Invariant I-5). The caller must
    call ``conn.commit()`` to persist.
    """
    conn.execute(
        "DELETE FROM records WHERE vault=? AND kind=? AND name=?",
        (vault, kind, name),
    )


def rebuild(vaults: list[str], conn: sqlite3.Connection) -> int:
    """Drop and repopulate the index from the vault directory trees.

    Scans each vault root for ``<kind>/<name>.json`` + ``<kind>/<name>.md``
    pairs. Only **complete pairs** (both files present) are indexed. Each row is
    keyed by the canonical vault root string (Invariant I-2, forward-compat for
    S4 multi-vault).

    Args:
        vaults: List of vault root paths (as strings) to scan.
        conn:   Open SQLite connection (WAL, ``records`` table exists).

    Returns:
        The number of record rows inserted.
    """
    conn.execute("DELETE FROM records")

    count = 0
    for vault_str in vaults:
        vault_root = Path(vault_str)
        if not vault_root.is_dir():
            continue
        for kind_dir in vault_root.iterdir():
            if not kind_dir.is_dir():
                continue
            kind = kind_dir.name
            for json_path in kind_dir.glob("*.json"):
                name = json_path.stem
                md_path = kind_dir / f"{name}.md"
                if not md_path.exists():
                    continue
                try:
                    sidecar = json.loads(json_path.read_text())
                    body = md_path.read_text()
                except Exception:
                    continue
                upsert_row(conn, vault_str, kind, name, sidecar, body)
                count += 1

    return count
