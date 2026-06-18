"""Slice 1 (S3) tests: locked index schema + full ingest projection + two-pass reindex.

Covers every bullet in the plan's Slice 1 test contract (S3), proving the realized
schema (`records` + `record_facet` + `record_fts`) and the shared projection used by
BOTH the `update_index` write seam and `reindex`:

  - A fixture vault of sidecar+body pairs reindexes to the realized schema: correct
    ``records`` columns, one ``record_facet`` row per list value, FTS rows present.
  - Body-only full-text: a bare term appearing ONLY in a record's ``.md`` body is
    matched — proves body is read from the file and fed into ``record_fts.body``.
  - Tokenizer: a body/keyword containing ``phi-scrubber`` is matched by a bare
    ``phi`` FTS query (unicode61 hyphen split), case-insensitively / diacritics-folded.
  - Reverse-edge symmetry, including a cross-vault case where the reverse-edge target
    lives in a second vault ingested second (FK-ordering).
  - BM25 sort direction: a title-hit sorts before a body-only-hit under
    ``ORDER BY bm25(record_fts, 3.0, 2.0, 1.0)``.
  - Layer integrity: a record from a non-default vault has ``layer='shared'``; a
    non-enum layer value is rejected by the CHECK constraint.
  - ``reindex`` idempotent; a since-deleted record's ``records`` AND ``record_facet``
    rows are gone after rebuild (CASCADE / drop+rebuild).
  - The ``update_index`` write path populates the written record's forward facets + FTS
    in the same call.

All assertions run against a tmp ``$XDG_STATE_HOME`` index — never the real vault/index.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


def load_index_store():
    """Load index_store freshly each call."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    name = "index_store"
    if name in sys.modules:
        del sys.modules[name]
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def env(tmp_path):
    """A dict-copy of os.environ with XDG_STATE_HOME pointing at a tmp dir."""
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    e = dict(os.environ)
    e["XDG_STATE_HOME"] = str(fake_state)
    return e


def _sidecar(
    *,
    kind="spec",
    title="My Spec",
    status="active",
    keywords=None,
    related=None,
    related_phases=None,
    related_files=None,
    related_urls=None,
    team=None,
):
    s = {
        "version": "v1",
        "kind": kind,
        "title": title,
        "keywords": keywords if keywords is not None else ["foo"],
        "status": status,
        "team": team,
        "suite": None,
        "product": None,
        "repo": None,
        "created-at": "2026-06-17T10:00:00Z",
        "created-by": "tom@example.com",
        "updated-at": "2026-06-17T10:00:00Z",
        "updated-by": "tom@example.com",
        "last-referenced-at": None,
    }
    if related is not None:
        s["related"] = related
    if related_phases is not None:
        s["related-phases"] = related_phases
    if related_files is not None:
        s["related-files-or-folders"] = related_files
    if related_urls is not None:
        s["related-urls"] = related_urls
    return s


def _write_record(vault_root: Path, kind: str, name: str, sidecar: dict, body: str):
    kind_dir = vault_root / kind
    kind_dir.mkdir(parents=True, exist_ok=True)
    (kind_dir / f"{name}.json").write_text(json.dumps(sidecar))
    (kind_dir / f"{name}.md").write_text(body)


# ---------------------------------------------------------------------------
# FK enforcement (MANDATORY per Slice 0 verdict)
# ---------------------------------------------------------------------------

def test_open_index_enables_foreign_keys(env):
    """Every connection issues PRAGMA foreign_keys = ON (else CASCADE/FK no-op)."""
    mod = load_index_store()
    conn = mod.open_index(env=env)
    try:
        on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        conn.close()
    assert on == 1, "foreign_keys enforcement must be ON for CASCADE/FK guard"


def test_facet_fk_is_enforced(env):
    """A record_facet row for a missing record id raises (FK actually enforced)."""
    mod = load_index_store()
    conn = mod.open_index(env=env)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(
                "INSERT INTO record_facet(id, facet, value) VALUES (?, ?, ?)",
                ("no/such/record", "keywords", "x"),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Realized schema: tables + columns
# ---------------------------------------------------------------------------

def test_open_index_provisions_realized_tables(env):
    """open_index provisions records, record_facet, idx_facet, and record_fts."""
    mod = load_index_store()
    conn = mod.open_index(env=env)
    try:
        objs = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN "
                "('records', 'record_facet', 'idx_facet', 'record_fts')"
            ).fetchall()
        }
    finally:
        conn.close()
    assert {"records", "record_facet", "idx_facet", "record_fts"} <= objs


def test_records_has_locked_columns(env):
    """records carries the locked S3 columns incl. id, layer, src_mtime, src_size."""
    mod = load_index_store()
    conn = mod.open_index(env=env)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(records)").fetchall()}
    finally:
        conn.close()
    expected = {
        "id", "vault", "kind", "name", "title", "status",
        "team", "suite", "product", "repo",
        "created_at", "updated_at", "last_referenced_at",
        "layer", "src_mtime", "src_size",
    }
    assert expected <= cols, f"missing columns: {expected - cols}"


def test_reindex_populates_records_columns(env, tmp_path):
    """A fixture record reindexes into the records row with the right column values."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    _write_record(
        vault, "spec", "alpha",
        _sidecar(kind="spec", title="Alpha Spec", status="active", team="trailhead"),
        "alpha body",
    )
    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault)], conn)
        conn.commit()
        row = conn.execute(
            "SELECT id, vault, kind, name, title, status, team, layer "
            "FROM records"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == f"{vault}/spec/alpha"
    assert row[1] == str(vault)
    assert row[2] == "spec"
    assert row[3] == "alpha"
    assert row[4] == "Alpha Spec"
    assert row[5] == "active"
    assert row[6] == "trailhead"
    assert row[7] == "personal"


def test_reindex_one_facet_row_per_list_value(env, tmp_path):
    """Each list value becomes one record_facet row with the right facet name."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    _write_record(
        vault, "spec", "alpha",
        _sidecar(
            kind="spec",
            title="Alpha",
            keywords=["one", "two", "three"],
            related_phases=["build", "ship"],
            related_files=["src/foo.py"],
            related_urls=["https://example.com"],
        ),
        "body",
    )
    rid = f"{vault}/spec/alpha"
    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault)], conn)
        conn.commit()
        def vals(facet):
            return sorted(
                r[0] for r in conn.execute(
                    "SELECT value FROM record_facet WHERE id=? AND facet=?",
                    (rid, facet),
                ).fetchall()
            )
        keywords = vals("keywords")
        phases = vals("related-phases")
        files = vals("related-files-or-folders")
        urls = vals("related-urls")
    finally:
        conn.close()
    assert keywords == ["one", "three", "two"]
    assert phases == ["build", "ship"]
    assert files == ["src/foo.py"]
    assert urls == ["https://example.com"]


def test_reindex_populates_fts_rows(env, tmp_path):
    """FTS rows are present after reindex (rowid aliases records.rowid)."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    _write_record(vault, "spec", "alpha", _sidecar(title="Alpha"), "body")
    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault)], conn)
        conn.commit()
        fts_count = conn.execute("SELECT COUNT(*) FROM record_fts").fetchone()[0]
        rec_rowid = conn.execute("SELECT rowid FROM records").fetchone()[0]
        fts_rowid = conn.execute("SELECT rowid FROM record_fts").fetchone()[0]
    finally:
        conn.close()
    assert fts_count == 1
    assert fts_rowid == rec_rowid


# ---------------------------------------------------------------------------
# Body-only full-text (proves body read from .md)
# ---------------------------------------------------------------------------

def test_body_only_term_is_full_text_matched(env, tmp_path):
    """A term ONLY in the markdown body (not title/keywords) is FTS-matched."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    _write_record(
        vault, "spec", "alpha",
        _sidecar(title="Generic Title", keywords=["unrelated"]),
        "The xyzzy quux appears only in the body text.",
    )
    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault)], conn)
        conn.commit()
        hits = conn.execute(
            "SELECT records.name FROM record_fts "
            "JOIN records ON records.rowid = record_fts.rowid "
            "WHERE record_fts MATCH 'xyzzy'"
        ).fetchall()
    finally:
        conn.close()
    assert [h[0] for h in hits] == ["alpha"], "body-only term not matched — body dropped"


# ---------------------------------------------------------------------------
# Tokenizer behavior
# ---------------------------------------------------------------------------

def test_unicode61_hyphen_split_case_insensitive(env, tmp_path):
    """A keyword 'phi-scrubber' is matched by a bare 'phi' / 'PHI' FTS query."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    _write_record(
        vault, "spec", "tool",
        _sidecar(title="Tool", keywords=["phi-scrubber"]),
        "description",
    )
    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault)], conn)
        conn.commit()
        lower = conn.execute(
            "SELECT COUNT(*) FROM record_fts WHERE record_fts MATCH 'phi'"
        ).fetchone()[0]
        upper = conn.execute(
            "SELECT COUNT(*) FROM record_fts WHERE record_fts MATCH 'PHI'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert lower == 1, "bare 'phi' did not match 'phi-scrubber' (hyphen split broken)"
    assert upper == 1, "case-insensitive 'PHI' did not match 'phi-scrubber'"


def test_diacritics_folded(env, tmp_path):
    """A body term with diacritics is matched by its folded form."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    _write_record(vault, "spec", "cafe", _sidecar(title="Cafe"), "le café is open")
    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault)], conn)
        conn.commit()
        hits = conn.execute(
            "SELECT COUNT(*) FROM record_fts WHERE record_fts MATCH 'cafe'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert hits == 1, "diacritics not folded: 'cafe' did not match 'café'"


# ---------------------------------------------------------------------------
# Reverse-edge symmetry (incl. cross-vault)
# ---------------------------------------------------------------------------

def test_reverse_edge_symmetry_same_vault(env, tmp_path):
    """area:penny finds the linker AND the linked-back record (symmetric)."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    # alpha declares related-area -> penny (forward).
    _write_record(
        vault, "spec", "alpha",
        _sidecar(kind="spec", title="Alpha", related={"area": ["penny"]}),
        "body",
    )
    # penny is a real record; alpha's forward edge makes alpha a member of area:penny.
    _write_record(vault, "area", "penny", _sidecar(kind="area", title="Penny"), "body")
    # penny declares related-area -> marco (forward); reverse makes marco a member of area:penny.
    _write_record(
        vault, "area", "penny2",
        _sidecar(kind="area", title="Penny2", related={"area": ["marco"]}),
        "body",
    )
    _write_record(vault, "area", "marco", _sidecar(kind="area", title="Marco"), "body")

    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault)], conn)
        conn.commit()
        # area:penny membership predicate (forward edge): alpha is a member.
        members = {
            r[0] for r in conn.execute(
                "SELECT records.name FROM records WHERE EXISTS ("
                "SELECT 1 FROM record_facet f WHERE f.id = records.id "
                "AND f.facet='related-area' AND f.value=?)",
                ("penny",),
            ).fetchall()
        }
        # Reverse: querying area:penny2 must find marco (penny2 -> marco forward,
        # so marco carries a reverse edge to penny2).
        marco_members = {
            r[0] for r in conn.execute(
                "SELECT records.name FROM records WHERE EXISTS ("
                "SELECT 1 FROM record_facet f WHERE f.id = records.id "
                "AND f.facet='related-area' AND f.value=?)",
                ("penny2",),
            ).fetchall()
        }
    finally:
        conn.close()
    assert "alpha" in members, "forward edge: alpha should be a member of area:penny"
    assert "marco" in marco_members, (
        "reverse edge: marco should be a member of area:penny2 (penny2 links to marco)"
    )


def test_reverse_edge_cross_vault_target_ingested_second(env, tmp_path):
    """Reverse-edge target lives in a second vault ingested second (FK ordering)."""
    mod = load_index_store()
    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    # Vault A: penny declares related-spec -> marco (marco lives in vault B).
    _write_record(
        vault_a, "area", "penny",
        _sidecar(kind="area", title="Penny", related={"spec": ["marco"]}),
        "body",
    )
    # Vault B: marco (the reverse-edge target), ingested in the second vault.
    _write_record(vault_b, "spec", "marco", _sidecar(kind="spec", title="Marco"), "body")

    conn = mod.open_index(env=env)
    try:
        # personal=vault_a, shared=vault_b; both records inserted before any facet (pass 1/2).
        mod.rebuild([str(vault_a), str(vault_b)], conn)
        conn.commit()
        # Reverse edge: marco carries a related-spec edge back to penny, so
        # spec:penny finds marco.
        rev = {
            r[0] for r in conn.execute(
                "SELECT records.name FROM records WHERE EXISTS ("
                "SELECT 1 FROM record_facet f WHERE f.id = records.id "
                "AND f.facet='related-spec' AND f.value=?)",
                ("penny",),
            ).fetchall()
        }
    finally:
        conn.close()
    assert "marco" in rev, (
        "cross-vault reverse edge missing — marco (vault B) should link back to penny"
    )


# ---------------------------------------------------------------------------
# BM25 sort direction
# ---------------------------------------------------------------------------

def test_bm25_title_hit_sorts_before_body_only_hit(env, tmp_path):
    """Under ORDER BY bm25(record_fts, 3.0, 2.0, 1.0), a title-hit precedes a body-hit."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    _write_record(
        vault, "spec", "titlehit",
        _sidecar(title="quantum computing overview", keywords=["misc"]),
        "nothing relevant here",
    )
    _write_record(
        vault, "spec", "bodyhit",
        _sidecar(title="unrelated heading", keywords=["misc"]),
        "quantum mechanics is discussed in the body",
    )
    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault)], conn)
        conn.commit()
        ordered = [
            r[0] for r in conn.execute(
                "SELECT records.name FROM record_fts "
                "JOIN records ON records.rowid = record_fts.rowid "
                "WHERE record_fts MATCH 'quantum' "
                "ORDER BY bm25(record_fts, 3.0, 2.0, 1.0) ASC"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert ordered == ["titlehit", "bodyhit"], (
        f"title-hit should rank first under bm25 ASC, got {ordered}"
    )


# ---------------------------------------------------------------------------
# Layer integrity
# ---------------------------------------------------------------------------

def test_non_default_vault_is_shared_layer(env, tmp_path):
    """A record from a non-default (second) vault gets layer='shared'."""
    mod = load_index_store()
    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    _write_record(vault_a, "spec", "in-a", _sidecar(title="In A"), "body")
    _write_record(vault_b, "spec", "in-b", _sidecar(title="In B"), "body")
    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault_a), str(vault_b)], conn)
        conn.commit()
        layers = {
            r[0]: r[1]
            for r in conn.execute("SELECT name, layer FROM records").fetchall()
        }
    finally:
        conn.close()
    assert layers["in-a"] == "personal"
    assert layers["in-b"] == "shared"


def test_check_constraint_rejects_non_enum_layer(env):
    """A non-enum layer value is rejected by the CHECK constraint."""
    mod = load_index_store()
    conn = mod.open_index(env=env)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(
                "INSERT INTO records (id, vault, kind, name, title, status, "
                "created_at, updated_at, layer, src_mtime, src_size) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("v/spec/x", "v", "spec", "x", "X", "active",
                 "2026-06-17T10:00:00Z", "2026-06-17T10:00:00Z",
                 "untrusted", 0.0, 0),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# reindex idempotency + CASCADE / drop on delete
# ---------------------------------------------------------------------------

def test_reindex_idempotent(env, tmp_path):
    """Running reindex twice leaves the same record + facet counts."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    _write_record(
        vault, "spec", "alpha",
        _sidecar(title="Alpha", keywords=["a", "b"]),
        "body",
    )
    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault)], conn)
        conn.commit()
        recs1 = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        facets1 = conn.execute("SELECT COUNT(*) FROM record_facet").fetchone()[0]
        mod.rebuild([str(vault)], conn)
        conn.commit()
        recs2 = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        facets2 = conn.execute("SELECT COUNT(*) FROM record_facet").fetchone()[0]
    finally:
        conn.close()
    assert recs1 == recs2 == 1
    assert facets1 == facets2 == 2


def test_reindex_drops_deleted_records_and_facets(env, tmp_path):
    """A since-deleted record's records AND record_facet rows are gone after rebuild."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    _write_record(
        vault, "spec", "keep",
        _sidecar(title="Keep", keywords=["k"]),
        "body",
    )
    _write_record(
        vault, "spec", "gone",
        _sidecar(title="Gone", keywords=["g1", "g2"]),
        "body",
    )
    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault)], conn)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
        gone_id = f"{vault}/spec/gone"
        before = conn.execute(
            "SELECT COUNT(*) FROM record_facet WHERE id=?", (gone_id,)
        ).fetchone()[0]
        assert before == 2

        # Delete 'gone' from disk, then rebuild.
        (vault / "spec" / "gone.json").unlink()
        (vault / "spec" / "gone.md").unlink()
        mod.rebuild([str(vault)], conn)
        conn.commit()

        rec_rows = {r[0] for r in conn.execute("SELECT name FROM records").fetchall()}
        facet_rows = conn.execute(
            "SELECT COUNT(*) FROM record_facet WHERE id=?", (gone_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert rec_rows == {"keep"}
    assert facet_rows == 0, "deleted record's facet rows must be gone after rebuild"


# ---------------------------------------------------------------------------
# update_index write path: forward facets + FTS in the same call
# ---------------------------------------------------------------------------

def test_upsert_row_populates_forward_facets_and_fts(env, tmp_path):
    """The write-path upsert populates the record's forward facets + FTS in one call."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    conn = mod.open_index(env=env)
    try:
        sidecar = _sidecar(
            title="Written Spec",
            keywords=["alpha", "beta"],
            related={"area": ["penny"]},
            related_phases=["build"],
        )
        mod.upsert_row(conn, str(vault), "spec", "written", sidecar, "the body has gamma")
        conn.commit()
        rid = f"{vault}/spec/written"
        facets = {
            (r[0], r[1])
            for r in conn.execute(
                "SELECT facet, value FROM record_facet WHERE id=?", (rid,)
            ).fetchall()
        }
        fts_hit = conn.execute(
            "SELECT records.name FROM record_fts "
            "JOIN records ON records.rowid = record_fts.rowid "
            "WHERE record_fts MATCH 'gamma'"
        ).fetchone()
    finally:
        conn.close()
    # Forward facets present (keywords, related-area, related-phases).
    assert ("keywords", "alpha") in facets
    assert ("keywords", "beta") in facets
    assert ("related-area", "penny") in facets
    assert ("related-phases", "build") in facets
    # FTS populated from the body in the same call.
    assert fts_hit is not None and fts_hit[0] == "written"


def test_upsert_row_re_upsert_replaces_facets_and_fts(env, tmp_path):
    """Re-upserting the same key replaces its facets + FTS row (no stale duplicates)."""
    mod = load_index_store()
    vault = tmp_path / "vault"
    conn = mod.open_index(env=env)
    try:
        mod.upsert_row(
            conn, str(vault), "spec", "x",
            _sidecar(title="X", keywords=["old"]), "old body",
        )
        conn.commit()
        mod.upsert_row(
            conn, str(vault), "spec", "x",
            _sidecar(title="X", keywords=["new"]), "new body",
        )
        conn.commit()
        rid = f"{vault}/spec/x"
        kw = sorted(
            r[0] for r in conn.execute(
                "SELECT value FROM record_facet WHERE id=? AND facet='keywords'", (rid,)
            ).fetchall()
        )
        fts_count = conn.execute("SELECT COUNT(*) FROM record_fts").fetchone()[0]
        old_hit = conn.execute(
            "SELECT COUNT(*) FROM record_fts WHERE record_fts MATCH 'old'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert kw == ["new"], "stale keyword facet rows not replaced on re-upsert"
    assert fts_count == 1, "duplicate FTS rows on re-upsert"
    assert old_hit == 0, "stale FTS body content not replaced on re-upsert"
