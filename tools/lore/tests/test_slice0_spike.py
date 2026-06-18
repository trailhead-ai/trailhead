"""Slice 0 assumption-prover spike — EPHEMERAL.

Proves three blocking unknowns (KU1, KU3, KU2) before any Slice 1 code is
written. All assertions run against throwaway :memory: SQLite databases —
never touches the real lore index/vault.

Clean-up: delete this file entirely after the verdicts are recorded.
File: tools/lore/tests/test_slice0_spike.py  (all lines)
"""

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# KU1 — populated FTS5 table (no content=) with explicit rowid INSERT,
#        MATCH, bm25 weights, body-only match, and unicode61 hyphen split.
# ---------------------------------------------------------------------------


class TestKU1PopulatedFTS5:
    """KU1: a realized fts5(title, keywords, body) table (no content=)."""

    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE VIRTUAL TABLE record_fts USING fts5("
            "title, keywords, body, "
            "tokenize='unicode61 remove_diacritics 2'"
            ")"
        )
        return conn

    def test_ku1a_manual_insert_with_explicit_rowid(self, db):
        """(a) accepts INSERT with an explicit rowid."""
        db.execute(
            "INSERT INTO record_fts(rowid, title, keywords, body) VALUES (42, 'Alpha', 'beta', 'gamma')"
        )
        row = db.execute(
            "SELECT rowid, title, keywords, body FROM record_fts WHERE rowid = 42"
        ).fetchone()
        assert row is not None, "row not found by rowid"
        assert row[0] == 42
        assert row[1] == "Alpha"

    def test_ku1b_match_query_works(self, db):
        """(b) MATCH query returns the inserted row."""
        db.execute(
            "INSERT INTO record_fts(rowid, title, keywords, body) VALUES (1, 'Hello world', 'greet', 'nothing')"
        )
        rows = db.execute(
            "SELECT rowid FROM record_fts WHERE record_fts MATCH 'hello'"
        ).fetchall()
        assert len(rows) == 1, f"expected 1 hit, got {rows}"
        assert rows[0][0] == 1

    def test_ku1c_bm25_column_weights_computes(self, db):
        """(c) bm25(record_fts, 3.0, 2.0, 1.0) returns a numeric score."""
        db.execute(
            "INSERT INTO record_fts(rowid, title, keywords, body) VALUES (1, 'solar', 'energy', 'nothing')"
        )
        row = db.execute(
            "SELECT bm25(record_fts, 3.0, 2.0, 1.0) FROM record_fts WHERE record_fts MATCH 'solar'"
        ).fetchone()
        assert row is not None
        score = row[0]
        assert isinstance(score, float), f"expected float score, got {type(score)}: {score}"
        # BM25 in SQLite is negative for relevant rows
        assert score < 0, f"expected negative BM25 score for a match, got {score}"

    def test_ku1d_body_only_term_matched(self, db):
        """(d) a term in body only (not title/keywords) is matched."""
        db.execute(
            "INSERT INTO record_fts(rowid, title, keywords, body)"
            " VALUES (1, 'Generic title', 'some keyword', 'The xyzzy quux appears only in the body text')"
        )
        rows = db.execute(
            "SELECT rowid FROM record_fts WHERE record_fts MATCH 'xyzzy'"
        ).fetchall()
        assert len(rows) == 1, (
            "body-only term 'xyzzy' not found — body full-text is broken"
        )

    def test_ku1e_unicode61_hyphen_split(self, db):
        """(e) unicode61 splits on hyphen: bare 'phi' matches 'phi-scrubber', case-insensitively."""
        db.execute(
            "INSERT INTO record_fts(rowid, title, keywords, body)"
            " VALUES (1, 'Tool', 'phi-scrubber', 'description')"
        )
        # bare lower-case 'phi'
        rows = db.execute(
            "SELECT rowid FROM record_fts WHERE record_fts MATCH 'phi'"
        ).fetchall()
        assert len(rows) == 1, (
            "bare 'phi' did not match 'phi-scrubber' — unicode61 hyphen split broken"
        )

        # upper-case 'PHI' — case-insensitive
        rows_upper = db.execute(
            "SELECT rowid FROM record_fts WHERE record_fts MATCH 'PHI'"
        ).fetchall()
        assert len(rows_upper) == 1, (
            "case-insensitive: 'PHI' did not match 'phi-scrubber'"
        )


# ---------------------------------------------------------------------------
# KU3 — BM25 sign: more-relevant is more-negative; ASC = best-first.
# ---------------------------------------------------------------------------


class TestKU3BM25Sign:
    """KU3: bm25() is negative; ORDER BY bm25(...) ASC puts best hit first."""

    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE VIRTUAL TABLE record_fts USING fts5("
            "title, keywords, body, "
            "tokenize='unicode61 remove_diacritics 2'"
            ")"
        )
        # Row 1: term 'quantum' appears in title (weight 3)
        conn.execute(
            "INSERT INTO record_fts(rowid, title, keywords, body)"
            " VALUES (1, 'quantum computing overview', '', '')"
        )
        # Row 2: same term 'quantum' appears only in body (weight 1)
        conn.execute(
            "INSERT INTO record_fts(rowid, title, keywords, body)"
            " VALUES (2, 'unrelated title', '', 'quantum mechanics is discussed here')"
        )
        return conn

    def test_ku3_bm25_scores_are_negative(self, db):
        """Both BM25 scores for 'quantum' are negative."""
        rows = db.execute(
            "SELECT rowid, bm25(record_fts, 3.0, 2.0, 1.0)"
            " FROM record_fts WHERE record_fts MATCH 'quantum'"
            " ORDER BY bm25(record_fts, 3.0, 2.0, 1.0) ASC"
        ).fetchall()
        assert len(rows) == 2, f"expected 2 hits, got {rows}"
        scores = {r[0]: r[1] for r in rows}
        assert scores[1] < 0, f"title-hit BM25 score should be negative, got {scores[1]}"
        assert scores[2] < 0, f"body-hit BM25 score should be negative, got {scores[2]}"

    def test_ku3_title_hit_sorts_first_asc(self, db):
        """Under ORDER BY bm25(...) ASC, the title-hit row (rowid=1) comes first."""
        rows = db.execute(
            "SELECT rowid, bm25(record_fts, 3.0, 2.0, 1.0)"
            " FROM record_fts WHERE record_fts MATCH 'quantum'"
            " ORDER BY bm25(record_fts, 3.0, 2.0, 1.0) ASC"
        ).fetchall()
        assert len(rows) == 2, f"expected 2 rows, got {rows}"
        first_rowid, first_score = rows[0]
        second_rowid, second_score = rows[1]

        # Record the actual values for the report
        print(f"\nBM25 title-hit score (rowid=1): {first_score if first_rowid == 1 else second_score}")
        print(f"BM25 body-hit score  (rowid=2): {second_score if first_rowid == 1 else first_score}")

        assert first_rowid == 1, (
            f"title-hit (rowid=1) should be first under ASC, but got rowid={first_rowid} first. "
            f"Scores: rowid=1 -> {scores_map(rows, 1):.6f}, rowid=2 -> {scores_map(rows, 2):.6f}"
        )
        # title score more negative than body score
        title_score = next(s for r, s in rows if r == 1)
        body_score = next(s for r, s in rows if r == 2)
        assert title_score < body_score, (
            f"title score {title_score} should be < body score {body_score} (more negative = more relevant)"
        )


def scores_map(rows, rowid):
    return next(s for r, s in rows if r == rowid)


# ---------------------------------------------------------------------------
# KU2 — two-pass projection + FK + CASCADE + reverse-edge symmetry.
# ---------------------------------------------------------------------------


class TestKU2TwoPassFKAndCascade:
    """KU2: two-pass reindex satisfies FK even for cross-vault reverse edges."""

    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE records (
                id TEXT PRIMARY KEY,
                vault TEXT NOT NULL,
                title TEXT NOT NULL
            );

            CREATE TABLE record_facet (
                id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
                facet TEXT NOT NULL,
                value TEXT NOT NULL
            );

            CREATE INDEX idx_facet ON record_facet(facet, value);
        """)
        return conn

    def _two_pass_populate(self, db):
        """Simulate a two-pass reindex across two vaults.

        Vault A has record 'vaultA/area/penny' which declares related-area -> [marco].
        Vault B has record 'vaultB/spec/marco'.

        Pass 1: INSERT ALL records from both vaults.
        Pass 2: INSERT ALL facets including the reverse edge on marco pointing back.
        """
        # Pass 1 — all records, both vaults
        db.executemany(
            "INSERT INTO records(id, vault, title) VALUES (?, ?, ?)",
            [
                ("vaultA/area/penny", "vaultA", "Penny Area"),
                ("vaultB/spec/marco", "vaultB", "Marco Spec"),
                ("vaultA/note/alpha", "vaultA", "Alpha Note"),
            ],
        )

        # Pass 2 — forward facet (penny -> related-area -> marco)
        #          reverse facet (marco <- related-area <- penny, so marco is queryable)
        db.executemany(
            "INSERT INTO record_facet(id, facet, value) VALUES (?, ?, ?)",
            [
                # Forward edge: penny declares related-area = marco
                ("vaultA/area/penny", "related-area", "vaultB/spec/marco"),
                # Reverse edge: marco gets a back-pointer so area:marco queries find penny too
                ("vaultB/spec/marco", "related-area-reverse", "vaultA/area/penny"),
                # Unrelated forward edge for alpha
                ("vaultA/note/alpha", "related-area", "vaultA/area/penny"),
            ],
        )

    def test_ku2_1_two_pass_satisfies_fk(self, db):
        """(1) Two-pass populate succeeds: all records first, then facets.

        The critical case: vaultB/spec/marco is ingested in the SAME pass-1 batch
        as vaultA/area/penny, so its FK target exists before pass 2 inserts the
        reverse-edge row referencing it.
        """
        # Should not raise — FK satisfied because pass 1 covered all records
        self._two_pass_populate(db)
        count = db.execute("SELECT COUNT(*) FROM record_facet").fetchone()[0]
        assert count == 3, f"expected 3 facet rows, got {count}"

    def test_ku2_1b_single_pass_would_fail_fk(self, db):
        """Confirm FK is actually enforced: inserting a facet for a missing record raises."""
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            db.execute(
                "INSERT INTO record_facet(id, facet, value) VALUES (?, ?, ?)",
                ("nonexistent/record/id", "related-area", "anything"),
            )

    def test_ku2_2_reverse_edge_symmetry(self, db):
        """(2) area:penny query finds BOTH the linking record AND the linked-back record.

        Forward: penny -> related-area -> marco (penny is a linker).
        Reverse: marco -> related-area-reverse -> penny (penny is also findable via marco).

        But the key symmetry the plan needs: querying 'records that have related-area=penny'
        returns alpha (forward edge) AND that penny itself is a member of area queries.

        More precisely: the plan says 'area:penny' membership query uses
        EXISTS (SELECT 1 FROM record_facet WHERE id=records.id AND facet='related-area' AND value LIKE '%penny%').
        We prove BOTH that alpha (forward) is found AND that the reverse edge on marco
        means marco is findable when querying related-area-reverse for any value pointing
        at penny — symmetric membership.
        """
        self._two_pass_populate(db)

        # Forward query: who has related-area pointing to vaultB/spec/marco?
        # Answer: penny (the linker)
        forward_hits = db.execute(
            "SELECT DISTINCT id FROM record_facet"
            " WHERE facet='related-area' AND value='vaultB/spec/marco'"
        ).fetchall()
        forward_ids = {r[0] for r in forward_hits}
        assert "vaultA/area/penny" in forward_ids, (
            f"penny should be in forward hits for marco, got {forward_ids}"
        )

        # Reverse query: which records reference-back to penny?
        # The reverse facet row on marco points back to penny
        reverse_hits = db.execute(
            "SELECT DISTINCT id FROM record_facet"
            " WHERE facet='related-area-reverse' AND value='vaultA/area/penny'"
        ).fetchall()
        reverse_ids = {r[0] for r in reverse_hits}
        assert "vaultB/spec/marco" in reverse_ids, (
            f"marco should appear in reverse hits for penny, got {reverse_ids}"
        )

        # Membership query for alpha->penny (direct forward edge alpha -> penny)
        alpha_hits = db.execute(
            "SELECT DISTINCT id FROM record_facet"
            " WHERE facet='related-area' AND value='vaultA/area/penny'"
        ).fetchall()
        alpha_ids = {r[0] for r in alpha_hits}
        assert "vaultA/note/alpha" in alpha_ids, (
            f"alpha should have forward facet to penny, got {alpha_ids}"
        )

    def test_ku2_3_on_delete_cascade(self, db):
        """(3) ON DELETE CASCADE removes record_facet rows when records row is deleted."""
        self._two_pass_populate(db)

        # Confirm facets exist for penny before deletion
        before = db.execute(
            "SELECT COUNT(*) FROM record_facet WHERE id='vaultA/area/penny'"
        ).fetchone()[0]
        assert before > 0, "penny should have facet rows before deletion"

        # Delete the records row for penny — CASCADE should drop its facet rows
        db.execute("DELETE FROM records WHERE id='vaultA/area/penny'")

        after = db.execute(
            "SELECT COUNT(*) FROM record_facet WHERE id='vaultA/area/penny'"
        ).fetchone()[0]
        assert after == 0, (
            f"ON DELETE CASCADE should have removed penny's facet rows, but {after} remain"
        )

        # Other records' facets should be untouched
        remaining = db.execute("SELECT COUNT(*) FROM record_facet").fetchone()[0]
        # Started with 3: penny's (1 forward) + marco's reverse (1) + alpha's (1)
        # Deleted penny's 1 facet row; marco's reverse and alpha's remain = 2
        assert remaining == 2, f"expected 2 remaining facet rows after penny deletion, got {remaining}"

    def test_ku2_ordering_note_all_records_before_facets(self, db):
        """Confirm the realized ingest order: ALL records first, THEN ALL facets.

        This is the safe two-pass order regardless of vault ingestion order.
        If we attempt to insert a facet before its target record exists, FK fires.
        """
        # Insert vaultA record first
        db.execute("INSERT INTO records(id, vault, title) VALUES ('vaultA/area/penny', 'vaultA', 'Penny')")

        # Attempt to insert reverse-edge facet referencing vaultB/spec/marco BEFORE
        # marco exists in records — this MUST fail with FK error
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            db.execute(
                "INSERT INTO record_facet(id, facet, value) VALUES ('vaultB/spec/marco', 'related-area-reverse', 'vaultA/area/penny')"
            )

        # Now insert marco's records row
        db.execute("INSERT INTO records(id, vault, title) VALUES ('vaultB/spec/marco', 'vaultB', 'Marco')")

        # Now the same facet insert succeeds (FK target exists)
        db.execute(
            "INSERT INTO record_facet(id, facet, value) VALUES ('vaultB/spec/marco', 'related-area-reverse', 'vaultA/area/penny')"
        )
        count = db.execute("SELECT COUNT(*) FROM record_facet").fetchone()[0]
        assert count == 1
