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
  ISO dates; ``shared INTEGER NOT NULL`` with a **``CHECK (shared IN (0, 1))``**
  constraint so a bad trust value fails at ingest rather than silently emitting
  untrusted content unfenced (council/Security); ``src_mtime REAL NOT NULL`` /
  ``src_size INTEGER NOT NULL`` for drift detection. **There is no ``body`` column** —
  body text lives only in the markdown file and is fed into ``record_fts.body``.
- ``record_facet(id REFERENCES records(id) ON DELETE CASCADE, facet, value)`` +
  ``idx_facet`` — list-valued facets, one row per value (``keywords``,
  ``related-<kind>``, ``related-phases``, ``related-files-or-folders``,
  ``related-urls``). The sidecar's nested ``related`` map flattens to forward
  ``facet='related-<kind>', value=<target name>`` rows; reverse rows are materialized
  in ``reindex`` pass 2 (see I-4).
- ``record_labels(id REFERENCES records(id) ON DELETE CASCADE, key, value)`` +
  ``idx_labels`` — the indexed ``labels`` sidecar map, one row per ``(key, value)``
  pair (Slice 3). Unlike ``record_facet``/``record_fts``, this table uses **FK
  ``ON DELETE CASCADE``**, so ``_delete_projection`` needs no manual cleanup — the
  ``records`` delete clears the linked label rows (relies on ``PRAGMA
  foreign_keys=ON``). The ``annotations`` sidecar map is free-form and **not**
  projected into any index table.
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
- I-5: ``shared`` is ``0`` (trusted, unfenced) for the user's own/owned vault and
  ``1`` (untrusted/shared — must be fenced by ``search``) otherwise. **S4 wired the
  config source:** ``rebuild`` takes ``shared_roots`` (the set of resolved root paths
  ``config.json`` marks ``shared: true``) and derives ``shared=1`` iff the vault's
  root is in that set; when ``shared_roots`` is ``None`` it falls back to the
  owned-vault heuristic (first vault — or ``owned_vault`` — is ``shared=0``, rest
  ``1``) for vanilla no-config usage. The incremental ``upsert_row`` write path is
  passed the resolved vault's ``shared`` by ``record_store`` (default ``0``). The
  column shape (``shared`` 0/1) and the ``search`` classification are unchanged.
- I-6: ``delete_row`` removes the ``records`` row (CASCADE clears its facets) and the
  matching ``record_fts`` row; a missing key is a silent no-op (never raises).
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

    Uses the same lazy-import resolver pattern as other path helpers in this
    module, substituting ``"index.sqlite"`` for the resolved state path.
    Catches ``(ImportError, SystemExit)`` so the fallback fires on any import failure.

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
    shared          INTEGER NOT NULL CHECK (shared IN (0, 1)),
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

CREATE TABLE IF NOT EXISTS record_labels (
    id      TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_labels ON record_labels(key, value);

CREATE VIRTUAL TABLE IF NOT EXISTS record_fts USING fts5(
    title, keywords, body,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
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
    shared: int,
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
            shared, src_mtime, src_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            vault,
            kind,
            name,
            title,
            status,
            sidecar.get("team"),
            sidecar.get("suite"),
            sidecar.get("product"),
            sidecar.get("repo"),
            sidecar.get("created-at"),
            sidecar.get("updated-at"),
            sidecar.get("last-referenced-at"),
            shared,
            src_mtime,
            src_size,
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

    # Indexed labels — one row per (key, value). ``annotations`` are sidecar-only
    # and deliberately NOT projected. CASCADE-linked to ``records`` so a delete of
    # the records row clears these (FK ON DELETE CASCADE; PRAGMA foreign_keys=ON).
    labels = sidecar.get("labels")
    if isinstance(labels, dict):
        for key, value in labels.items():
            conn.execute(
                "INSERT INTO record_labels(id, key, value) VALUES (?, ?, ?)",
                (record_id, key, value),
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
    shared: int = 0,
) -> None:
    """Project the record keyed ``(vault, kind, name)`` (the write seam, I-3).

    Thin wrapper over the shared projection: writes the scalar columns, the forward
    ``record_facet`` rows, and the FTS row in one call. Reverse edges are a
    ``reindex``-only property (I-4). ``shared`` defaults to ``0`` (trusted/unfenced)
    because the incremental write path always targets the user's own (owned) vault.

    ``src_mtime``/``src_size`` are derived from the in-memory ``body`` (the write
    just produced it; the on-disk file is fresh by construction). The caller must
    call ``conn.commit()`` to persist.
    """
    body_bytes = body.encode("utf-8")
    _project_record(
        conn,
        vault,
        kind,
        name,
        sidecar,
        body,
        shared,
        src_mtime=0.0,
        src_size=len(body_bytes),
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


def scan_vault(
    vault_root: str,
    conn: sqlite3.Connection,
    *,
    shared: int,
) -> int:
    """Scan one vault root and ``upsert_row`` each complete record pair (S4).

    The **per-vault incremental** companion to ``rebuild`` used by ``lore vault
    add`` to fold a single (possibly already-populated) vault's records into the
    global index without dropping everything else. Scans ``<kind>/<name>.json`` +
    ``<kind>/<name>.md`` pairs under *vault_root*; only complete pairs are indexed.

    Mirrors ``rebuild``'s skip-guard (I-2): a single malformed sidecar — bad JSON,
    unreadable body, or a NOT NULL / CHECK violation at INSERT — skips that one
    record instead of aborting the scan. Each surviving record is projected via
    ``upsert_row`` (idempotent, I-1), stamped with the caller-supplied ``shared``
    flag (0 = own/trusted, 1 = untrusted/shared).

    Reverse facet edges are a ``reindex``-only, two-pass property (I-4); a one-pass
    per-vault scan emits forward edges only — acceptable because the index is derived
    and ``lore reindex`` restores full reverse symmetry.

    Args:
        vault_root: The vault root path (as a string) to scan; this exact string
                    is the ``records.vault`` value, matching ``rebuild``.
        conn:       Open SQLite connection (FK ON, schema present).
        shared:     The trust flag to stamp on every row (0 own, 1 shared).

    Returns:
        The number of record rows upserted. A missing/non-directory root → 0.
    """
    root = Path(vault_root)
    if not root.is_dir():
        return 0
    count = 0
    for kind_dir in root.iterdir():
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
                upsert_row(
                    conn,
                    vault_root,
                    kind,
                    name,
                    sidecar,
                    body,
                    shared=shared,
                )
            except Exception:
                continue
            count += 1
    return count


def remove_vault(vault_root: str, conn: sqlite3.Connection) -> int:
    """Remove every row for *vault_root* from the index (S4).

    The per-vault companion to ``delete_row`` used by ``lore vault delete``. A bulk
    ``DELETE FROM records WHERE vault=?`` would orphan the matching ``record_fts``
    rows (FTS5 is a virtual table, NOT FK-cascaded), so this selects each
    ``(kind, name)`` under the root and loops ``delete_row`` — which cleans the
    ``records`` row, its facets (CASCADE), and its FTS row per id. The caller must
    ``conn.commit()``.

    Args:
        vault_root: The vault root path (as a string), matching the stored
                    ``records.vault`` value.
        conn:       Open SQLite connection (FK ON).

    Returns:
        The number of record rows removed. No matching rows → 0 (silent).
    """
    keys = conn.execute("SELECT kind, name FROM records WHERE vault=?", (vault_root,)).fetchall()
    for kind, name in keys:
        delete_row(conn, vault_root, kind, name)
    return len(keys)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a key/value row into ``index_meta`` (S4 config-freshness signal).

    The ``index_meta`` table records derived-index provenance the read path needs
    to detect drift it cannot see by stat alone — notably ``config_mtime``, the
    ``config.json`` mtime the index was last (re)built against. ``search`` compares
    the stored value to the current ``config.json`` mtime and warns when the index
    is older, so an out-of-band config edit (e.g. flipping a vault's ``shared``
    flag) can't silently leave rows wrong. The caller must ``conn.commit()``.
    """
    conn.execute(
        "INSERT INTO index_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Return the ``index_meta`` value for *key*, or ``None`` if unset (S4)."""
    row = conn.execute("SELECT value FROM index_meta WHERE key=?", (key,)).fetchone()
    return None if row is None else row[0]


def rebuild(
    vaults: list[str],
    conn: sqlite3.Connection,
    *,
    owned_vault: str | None = None,
    shared_roots: set[str] | None = None,
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

    **``shared`` derivation source (I-5).** Two modes:

    - **Config-sourced (S4, preferred):** when ``shared_roots`` is given, a vault's
      rows get ``shared=1`` iff its root string ∈ ``shared_roots`` (the set of
      resolved root paths the config marks ``shared: true``), else ``shared=0``.
      This is the per-vault ``config.json`` source the spec mandates — a config edit
      that flips a vault's ``shared`` flag re-derives the column on the next reindex.
    - **Owned-vault heuristic (vanilla fallback):** when ``shared_roots`` is ``None``,
      the first vault (or the explicit ``owned_vault``) is the user's own/owned vault
      → ``shared=0``; all others → ``shared=1``. Preserves today's no-config behavior.

    Only the source changes; the ``shared`` 0/1 column shape stays.

    Args:
        vaults:        Vault root paths (as strings) to scan; first is the owned vault
                       unless ``owned_vault`` is given (owned-heuristic mode only).
        conn:          Open SQLite connection (FK ON, realized schema present).
        owned_vault:   Override which vault is own/owned (``shared=0``) — heuristic mode.
        shared_roots:  When given, switches to config-sourced ``shared``: a vault's
                       rows are ``shared=1`` iff its root ∈ this set, else ``0``.

    Returns:
        The number of record rows inserted.
    """
    conn.execute("DELETE FROM record_fts")
    conn.execute("DELETE FROM record_facet")
    conn.execute("DELETE FROM records")

    if owned_vault is None and vaults:
        owned_vault = vaults[0]

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
        if shared_roots is not None:
            shared = 1 if vault_str in shared_roots else 0
        else:
            shared = 0 if vault_str == owned_vault else 1
        for kind_dir in vault_root.iterdir():
            if not kind_dir.is_dir():
                continue
            kind = kind_dir.name
            for json_path in kind_dir.glob("*.json"):
                name = json_path.stem
                md_path = kind_dir / f"{name}.md"
                if not md_path.exists():
                    continue
                # I-2: the projection (records INSERT included) is inside the
                # skip guard so a single malformed sidecar — bad JSON, unreadable
                # body, OR a NOT NULL / CHECK violation at INSERT — skips that one
                # record instead of aborting the whole rebuild. ``reindex`` is the
                # recovery path; it must stay rebuildable even over a corrupted or
                # hand-truncated sidecar.
                try:
                    sidecar = json.loads(json_path.read_text())
                    body = md_path.read_text()
                    stat = md_path.stat()
                    record_id = _project_record(
                        conn,
                        vault_str,
                        kind,
                        name,
                        sidecar,
                        body,
                        shared,
                        src_mtime=stat.st_mtime,
                        src_size=stat.st_size,
                    )
                except Exception:
                    continue

                name_index[(kind, name)] = record_id

                related = sidecar.get("related")
                if isinstance(related, dict):
                    for rel_kind, names in related.items():
                        if not isinstance(names, list):
                            continue
                        for target_name in names:
                            forward_related.append((record_id, name, rel_kind, target_name))
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
