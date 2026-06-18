"""Derived SQLite + FTS5 index for the lore vault (Slice 1, S3).

This module provisions and maintains a **derived** global index of lore records
at ``state_dir("lore")/index.sqlite`` (WAL mode, created on first use). It is the
authoritative index store the KQL ``search`` facade (S2–S4) reads — scalar facets
for ``WHERE`` predicates, ``record_facet`` membership edges for alias queries, and
a populated ``record_fts`` virtual table for full-text ``MATCH`` + BM25 ranking.

**Text-wins / index-derived posture (locked, umbrella decision 7; spec AC-TX2):**
The git-tracked text files (``<kind>/<name>.md`` + ``<kind>/<name>.json``) are the
source of truth. The index is a cached projection — never the write target, always
rebuildable via ``rebuild(vaults, conn)``. On any partial failure the text already
on disk wins; ``lore reindex`` reconstructs the index from the vault tree.

**Realized schema (S3, LOCKED behavior).** S3 brings S2's *minimal* scalar table up
to the spec's locked schema. Because the index is derived and ``rebuild``-able, S3
**drops + recreates** S2's table on schema upgrade rather than ALTERing — at zero
migration cost (``reindex`` repopulates):

- ``records`` — ``id TEXT PRIMARY KEY`` (``"<vault>/<kind>/<name>"``); ``vault/kind/
  name`` (``UNIQUE``); ``title/status`` NOT NULL; ``team/suite/product/repo``; the
  ISO dates; ``layer TEXT NOT NULL`` with a **``CHECK (layer IN ('personal',
  'shared'))``** constraint so a bad layer value fails at ingest rather than silently
  emitting shared content unfenced (council/Security); ``src_mtime REAL NOT NULL`` /
  ``src_size INTEGER NOT NULL`` for drift detection. **There is no ``body`` column** —
  body text lives only in the markdown file and is fed into ``record_fts.body``.
- ``record_facet(id REFERENCES records(id) ON DELETE CASCADE, facet, value)`` +
  ``idx_facet`` — list-valued facets, one row per value (``keywords``,
  ``related-<kind>``, ``related-phases``, ``related-files-or-folders``,
  ``related-urls``). The sidecar's nested ``related`` map flattens to forward
  ``facet='related-<kind>', value=<target name>`` rows; reverse rows are materialized
  in ``reindex`` pass 2 (see I-4).
- ``record_fts`` — a **populated** ``fts5(title, keywords, body,
  tokenize='unicode61 remove_diacritics 2')`` table with **NO ``content=`` option**
  (KU1, validated Slice 0). ``records`` has no ``body``/``keywords`` column, so
  external-content ``'rebuild'`` cannot read them; the indexer fills the table
  directly via ``INSERT INTO record_fts(rowid, title, keywords, body)`` where
  ``rowid`` aliases ``records.rowid``, ``keywords`` is the joined keyword values, and
  ``body`` is read from the record's ``.md`` file. ``'rebuild'`` is never called.
  ``bm25(record_fts, 3.0, 2.0, 1.0)`` maps weights positionally to title/keywords/body.

**MANDATORY — FK enforcement (Slice 0 verdict).** SQLite defaults ``PRAGMA
foreign_keys`` to OFF and does NOT persist it in the file, so ``open_index`` issues
``PRAGMA foreign_keys = ON`` on **every** connection it opens. Without it, the
``record_facet`` FK guard and ``ON DELETE CASCADE`` silently no-op.

**Invariants:**
- I-1: ``records.id`` is the primary key (``"<vault>/<kind>/<name>"``); the shared
  projection is idempotent — re-projecting a key replaces its row, forward facets,
  and FTS row in place.
- I-2: ``rebuild`` drops all rows and repopulates in a single two-pass transaction —
  the recovery path after any drift between disk and index.
- I-3: The shared projection (``_project_record``) is used by **both** the
  ``update_index`` write seam (via ``upsert_row``) and ``rebuild``: it writes the
  scalar columns, the forward ``record_facet`` rows, and the FTS row. Reverse facet
  rows are a ``reindex``-only, two-pass property (I-4).
- I-4: ``reindex`` pass 1 inserts ALL ``records`` rows across ALL vaults + forward
  facets + FTS; pass 2 inserts **reverse** ``record_facet`` edges. The FK
  ``record_facet.id REFERENCES records(id)`` is satisfied because every record row
  exists before any facet row (incl. cross-vault reverse targets ingested in a later
  vault). Reverse edges make ``related``/alias membership symmetric. The incremental
  ``update_index`` path emits **forward** edges only; full reverse symmetry is a
  documented ``reindex``-only gap — safe because the index is derived.
- I-5: ``layer`` is ``'personal'`` for the default/personal vault and ``'shared'``
  otherwise (``rebuild`` treats its first vault — or the explicit ``personal_vault``
  arg — as personal; the incremental ``upsert_row`` write path always targets the
  user's own vault, so it defaults ``layer='personal'``).
- I-6: ``delete_row`` removes the ``records`` row (CASCADE clears its facets) and the
  matching ``record_fts`` row; a missing key is a silent no-op (never raises).
"""
from __future__ import annotations

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
# Schema (realized S3 — locked behavior)
# ---------------------------------------------------------------------------

_DDL = """\
CREATE TABLE IF NOT EXISTS records (
    id              TEXT PRIMARY KEY,
    vault           TEXT NOT NULL,
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL,
    team            TEXT,
    suite           TEXT,
    product         TEXT,
    repo            TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_referenced_at TEXT,
    layer           TEXT NOT NULL CHECK (layer IN ('personal', 'shared')),
    src_mtime       REAL NOT NULL,
    src_size        INTEGER NOT NULL,
    UNIQUE (vault, kind, name)
);

CREATE TABLE IF NOT EXISTS record_facet (
    id      TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    facet   TEXT NOT NULL,
    value   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facet ON record_facet(facet, value);

CREATE VIRTUAL TABLE IF NOT EXISTS record_fts USING fts5(
    title, keywords, body,
    tokenize='unicode61 remove_diacritics 2'
);
"""

# The forward list-valued facets and the sidecar keys / facet names they project to.
# ``related`` (the nested kind -> [names] map) is handled separately.
_LIST_FACETS: tuple[tuple[str, str], ...] = (
    ("keywords", "keywords"),
    ("related-phases", "related-phases"),
    ("related-files-or-folders", "related-files-or-folders"),
    ("related-urls", "related-urls"),
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def open_index(env: dict[str, str] | None = None) -> sqlite3.Connection:
    """Open (creating on first use) the global lore index in WAL mode.

    The database is created at ``state_dir("lore")/index.sqlite``, honoring a
    ``XDG_STATE_HOME`` override injected via ``env`` for test isolation — no
    ``os.environ`` mutation.

    Issues ``PRAGMA foreign_keys = ON`` (MANDATORY — see module docstring) and
    provisions the realized ``records`` + ``record_facet`` + ``idx_facet`` +
    ``record_fts`` schema. The caller is responsible for ``conn.commit()`` and
    ``conn.close()``.
    """
    index_path = _resolve_index_path(env=env)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(index_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL)
    conn.commit()
    return conn


def _record_id(vault: str, kind: str, name: str) -> str:
    """The canonical ``records.id`` for a key: ``"<vault>/<kind>/<name>"``."""
    return f"{vault}/{kind}/{name}"


def _delete_projection(conn: sqlite3.Connection, record_id: str) -> None:
    """Drop a record's row, its forward facets (via CASCADE), and its FTS row.

    The ``record_fts`` table is not FK-linked, so its row is removed explicitly by
    the ``records.rowid`` that aliases ``record_fts.rowid`` — before the ``records``
    row is deleted (after which the rowid is gone).
    """
    row = conn.execute("SELECT rowid FROM records WHERE id=?", (record_id,)).fetchone()
    if row is not None:
        conn.execute("DELETE FROM record_fts WHERE rowid=?", (row[0],))
    conn.execute("DELETE FROM records WHERE id=?", (record_id,))


def _project_record(
    conn: sqlite3.Connection,
    vault: str,
    kind: str,
    name: str,
    sidecar: dict[str, Any],
    body: str,
    layer: str,
    src_mtime: float,
    src_size: int,
) -> str:
    """Project one record into ``records`` + forward ``record_facet`` rows + FTS.

    The **shared projection** used by both ``upsert_row`` (the write seam) and
    ``rebuild`` pass 1. Idempotent (I-1): any existing projection for this id is
    dropped first, so re-projecting replaces the row, its forward facets, and its FTS
    row in place. Returns the ``records.id``.

    Reverse facet edges are NOT emitted here — they are a ``reindex``-only pass-2
    property (I-4).
    """
    record_id = _record_id(vault, kind, name)
    _delete_projection(conn, record_id)

    title = sidecar.get("title") or ""
    status = sidecar.get("status") or ""

    cur = conn.execute(
        """\
        INSERT INTO records (
            id, vault, kind, name, title, status,
            team, suite, product, repo,
            created_at, updated_at, last_referenced_at,
            layer, src_mtime, src_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id, vault, kind, name, title, status,
            sidecar.get("team"), sidecar.get("suite"),
            sidecar.get("product"), sidecar.get("repo"),
            sidecar.get("created-at"), sidecar.get("updated-at"),
            sidecar.get("last-referenced-at"),
            layer, src_mtime, src_size,
        ),
    )
    rowid = cur.lastrowid

    # Forward list-valued facets (keywords / related-phases / files / urls).
    keywords: list[str] = []
    for sidecar_key, facet in _LIST_FACETS:
        values = sidecar.get(sidecar_key)
        if not isinstance(values, list):
            continue
        for value in values:
            conn.execute(
                "INSERT INTO record_facet(id, facet, value) VALUES (?, ?, ?)",
                (record_id, facet, value),
            )
            if facet == "keywords":
                keywords.append(value)

    # Forward ``related`` edges: the nested ``kind -> [names]`` map flattens to
    # ``facet='related-<kind>', value=<target name>`` rows on the source record.
    related = sidecar.get("related")
    if isinstance(related, dict):
        for rel_kind, names in related.items():
            if not isinstance(names, list):
                continue
            for target_name in names:
                conn.execute(
                    "INSERT INTO record_facet(id, facet, value) VALUES (?, ?, ?)",
                    (record_id, f"related-{rel_kind}", target_name),
                )

    # FTS row — rowid aliases records.rowid; body read from the .md (caller-supplied).
    conn.execute(
        "INSERT INTO record_fts(rowid, title, keywords, body) VALUES (?, ?, ?, ?)",
        (rowid, title, " ".join(keywords), body),
    )
    return record_id


def upsert_row(
    conn: sqlite3.Connection,
    vault: str,
    kind: str,
    name: str,
    sidecar: dict[str, Any],
    body: str,
    *,
    layer: str = "personal",
) -> None:
    """Project the record keyed ``(vault, kind, name)`` (the write seam, I-3).

    Thin wrapper over the shared projection: writes the scalar columns, the forward
    ``record_facet`` rows, and the FTS row in one call. Reverse edges are a
    ``reindex``-only property (I-4). ``layer`` defaults to ``'personal'`` because the
    incremental write path always targets the user's own (default) vault.

    ``src_mtime``/``src_size`` are derived from the in-memory ``body`` (the write
    just produced it; the on-disk file is fresh by construction). The caller must
    call ``conn.commit()`` to persist.
    """
    body_bytes = body.encode("utf-8")
    _project_record(
        conn, vault, kind, name, sidecar, body, layer,
        src_mtime=0.0, src_size=len(body_bytes),
    )


def delete_row(
    conn: sqlite3.Connection,
    vault: str,
    kind: str,
    name: str,
) -> None:
    """Remove the projection for ``(vault, kind, name)`` (I-6).

    Deletes the ``records`` row (CASCADE clears its facets) and the matching FTS row.
    Silent no-op when the key does not exist. The caller must ``conn.commit()``.
    """
    _delete_projection(conn, _record_id(vault, kind, name))


def rebuild(
    vaults: list[str],
    conn: sqlite3.Connection,
    *,
    personal_vault: str | None = None,
) -> int:
    """Drop and two-pass repopulate the index from the vault directory trees (I-2/I-4).

    Scans each vault root for ``<kind>/<name>.json`` + ``<kind>/<name>.md`` pairs.
    Only **complete pairs** (both files present) are indexed.

    Pass 1 inserts ALL ``records`` rows across ALL vaults + forward ``record_facet``
    rows + FTS rows, and builds a ``(kind, name) -> id`` lookup. Pass 2 materializes
    the **reverse** edges: for every forward ``related-<kind>`` edge whose target name
    resolves to a known record, a symmetric reverse row is emitted on the target. The
    FK is satisfied because every record row exists before any facet row, even when a
    reverse-edge target lives in a later-ingested vault.

    The first vault (or the explicit ``personal_vault``) is the personal layer; all
    others are ``'shared'`` (I-5).

    Args:
        vaults:         Vault root paths (as strings) to scan; first is personal
                        unless ``personal_vault`` is given.
        conn:           Open SQLite connection (FK ON, realized schema present).
        personal_vault: Override which vault is the ``'personal'`` layer.

    Returns:
        The number of record rows inserted.
    """
    conn.execute("DELETE FROM record_fts")
    conn.execute("DELETE FROM record_facet")
    conn.execute("DELETE FROM records")

    if personal_vault is None and vaults:
        personal_vault = vaults[0]

    # (kind, name) -> record id, for reverse-edge target resolution in pass 2.
    name_index: dict[tuple[str, str], str] = {}
    # Forward related edges, deferred to pass 2 so all records exist first:
    #   (source_id, source_name, rel_kind, target_name)
    forward_related: list[tuple[str, str, str, str]] = []

    count = 0
    for vault_str in vaults:
        vault_root = Path(vault_str)
        if not vault_root.is_dir():
            continue
        layer = "personal" if vault_str == personal_vault else "shared"
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
                    import json as _json
                    sidecar = _json.loads(json_path.read_text())
                    body = md_path.read_text()
                    stat = md_path.stat()
                except Exception:
                    continue

                record_id = _project_record(
                    conn, vault_str, kind, name, sidecar, body, layer,
                    src_mtime=stat.st_mtime, src_size=stat.st_size,
                )
                name_index[(kind, name)] = record_id

                related = sidecar.get("related")
                if isinstance(related, dict):
                    for rel_kind, names in related.items():
                        if not isinstance(names, list):
                            continue
                        for target_name in names:
                            forward_related.append(
                                (record_id, name, rel_kind, target_name)
                            )
                count += 1

    # Pass 2 — reverse edges. For each forward ``related-<kind>`` edge whose target
    # resolves to a real record, emit a symmetric reverse row on the target so the
    # source is findable from the target's side (membership is symmetric, I-4).
    for source_id, source_name, rel_kind, target_name in forward_related:
        target_id = name_index.get((rel_kind, target_name))
        if target_id is None:
            continue  # dangling link — forward edge stands, no reverse target
        if target_id == source_id:
            continue  # self-link — forward row already covers it
        conn.execute(
            "INSERT INTO record_facet(id, facet, value) VALUES (?, ?, ?)",
            (target_id, f"related-{rel_kind}", source_name),
        )

    return count
