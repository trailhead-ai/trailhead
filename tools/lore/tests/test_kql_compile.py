"""KQL AST → SQL compiler tests.

Full-text is a SQL-composable predicate so the WHOLE boolean tree composes
uniformly in SQL. Each FullText/Phrase node compiles to

    records.rowid IN (SELECT rowid FROM record_fts WHERE record_fts MATCH ?)

with the sanitized single-term/phrase MATCH string as a BOUND param. And/Or/Not/
Group compose these full-text predicates and the scalar/facet predicates uniformly
as SQL boolean. Ranking, when any full-text term is present, is a correlated bm25
subquery over the OR-combined POSITIVE (non-negated) full-text terms; pure-facet
queries use recency order.

FINAL SELECT shape the executor runs (no mandatory FTS JOIN):

  SELECT * FROM records
  WHERE <predicates, full-text predicates inline>
  ORDER BY <bm25 subquery sort key | recency>
  LIMIT ?

Bound-param order: WHERE-clause params (tree order, including each MATCH string),
then the ranking-subquery MATCH param (only when full-text present), then LIMIT.
``CompiledQuery.full_query()`` + ``params`` stay positionally aligned.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from conftest import load_script


@pytest.fixture()
def kql():
    return load_script("lore.search.kql")


@pytest.fixture()
def compiler():
    return load_script("lore.search.kql_compile")


@pytest.fixture()
def index_store():
    return load_script("lore.search.index")


# ---------------------------------------------------------------------------
# Fixture index builder
# ---------------------------------------------------------------------------


def _sidecar(
    *,
    kind="spec",
    title="Test Record",
    status="active",
    keywords=None,
    related=None,
    updated_at="2026-06-17T10:00:00Z",
):
    s = {
        "version": "v1",
        "kind": kind,
        "title": title,
        "keywords": keywords if keywords is not None else [],
        "status": status,
        "team": None,
        "suite": None,
        "product": None,
        "repo": None,
        "created-at": "2026-06-17T10:00:00Z",
        "created-by": "tester@example.com",
        "updated-at": updated_at,
        "updated-by": "tester@example.com",
        "last-referenced-at": None,
    }
    if related is not None:
        s["related"] = related
    return s


def _write_record(vault_root: Path, kind: str, name: str, sidecar: dict, body: str):
    kind_dir = vault_root / kind
    kind_dir.mkdir(parents=True, exist_ok=True)
    (kind_dir / f"{name}.json").write_text(json.dumps(sidecar))
    (kind_dir / f"{name}.md").write_text(body)


@pytest.fixture()
def fixture_index(tmp_path, index_store):
    """Provision a small fixture index and return (conn, vault_path, env).

    Records:
      - spec/alpha  — kind=spec, status=active, title="Alpha Spec",
                      keywords=["search"], area=penny,
                      body="unique body word zephyr"
      - plan/beta   — kind=plan, status=draft, title="Beta Plan",
                      body="beta only body content here"
      - area/penny  — kind=area, title="Penny Area"
    """
    vault = tmp_path / "vault"
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    _write_record(
        vault,
        "spec",
        "alpha",
        _sidecar(
            kind="spec",
            title="Alpha Spec",
            status="active",
            keywords=["search"],
            related={"area": ["penny"]},
        ),
        "unique body word zephyr",
    )
    _write_record(
        vault,
        "plan",
        "beta",
        _sidecar(kind="plan", title="Beta Plan", status="draft"),
        "beta only body content here",
    )
    _write_record(
        vault,
        "area",
        "penny",
        _sidecar(kind="area", title="Penny Area", status="active"),
        "penny area body",
    )

    conn = index_store.open_index(env=env)
    index_store.rebuild([str(vault)], conn)
    conn.commit()
    return conn, vault, env


@pytest.fixture()
def ranking_index(tmp_path, index_store):
    """Index with two records both matching the term 'zephyr':

      - spec/title-hit : title="zephyr report", body="filler text"  (TITLE hit)
      - spec/body-hit  : title="Body Only", body="a zephyr in the body"  (BODY hit)

    Under bm25(record_fts, 3.0, 2.0, 1.0) ASC the title hit must sort first.
    """
    vault = tmp_path / "vault"
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    _write_record(
        vault,
        "spec",
        "title-hit",
        _sidecar(kind="spec", title="zephyr report", status="active"),
        "filler text without the magic word",
    )
    _write_record(
        vault,
        "spec",
        "body-hit",
        _sidecar(kind="spec", title="Body Only", status="active"),
        "a zephyr lives in the body content",
    )
    # A non-matching record to confirm NULL-score rows sort last.
    _write_record(
        vault,
        "spec",
        "no-hit",
        _sidecar(kind="spec", title="Nothing", status="active"),
        "irrelevant content entirely",
    )

    conn = index_store.open_index(env=env)
    index_store.rebuild([str(vault)], conn)
    conn.commit()
    return conn, vault, env


def _ids(rows, conn):
    """Map sqlite Row/tuple results to the records.id values."""
    out = []
    for r in rows:
        out.append(r["id"] if isinstance(r, sqlite3.Row) else r[0])
    return out


# ---------------------------------------------------------------------------
# LOCKED compile-table rows
# ---------------------------------------------------------------------------


class TestLockedCompileTable:
    """Each LOCKED compile-table row → expected SQL fragment + params."""

    def test_kind_scalar_field_eq(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"))
        assert "records.kind = ?" in cq.where
        assert "spec" in cq.params

    def test_status_scalar_field_eq(self, kql, compiler):
        cq = compiler.compile(kql.parse("status:active"))
        assert "records.status = ?" in cq.where
        assert "active" in cq.params

    def test_repo_scalar_field_eq(self, kql, compiler):
        cq = compiler.compile(kql.parse('repo:"trailhead-ai/trailhead"'))
        assert "records.repo = ?" in cq.where
        assert "trailhead-ai/trailhead" in cq.params

    def test_team_scalar_field_eq(self, kql, compiler):
        cq = compiler.compile(kql.parse('team:"platform infra"'))
        assert "records.team = ?" in cq.where
        assert "platform infra" in cq.params

    def test_product_scalar_field_eq(self, kql, compiler):
        cq = compiler.compile(kql.parse("product:lore"))
        assert "records.product = ?" in cq.where
        assert "lore" in cq.params

    def test_suite_scalar_field_eq(self, kql, compiler):
        cq = compiler.compile(kql.parse("suite:search"))
        assert "records.suite = ?" in cq.where
        assert "search" in cq.params

    def test_area_alias_resolves_to_facet_exists(self, kql, compiler):
        cq = compiler.compile(kql.parse("area:penny"))
        assert "EXISTS" in cq.where
        assert "record_facet" in cq.where
        assert "related-area" in cq.params
        assert "penny" in cq.params

    def test_phase_alias_resolves_to_facet_exists(self, kql, compiler):
        cq = compiler.compile(kql.parse("phase:build"))
        assert "EXISTS" in cq.where
        assert "related-phases" in cq.params
        assert "build" in cq.params

    def test_keyword_alias_resolves_to_facet_exists(self, kql, compiler):
        cq = compiler.compile(kql.parse("keyword:foo"))
        assert "EXISTS" in cq.where
        assert "keywords" in cq.params
        assert "foo" in cq.params

    def test_related_area_real_key_passthrough(self, kql, compiler):
        cq = compiler.compile(kql.parse("related-area:penny"))
        assert "EXISTS" in cq.where
        assert "related-area" in cq.params

    def test_related_phases_real_key_passthrough(self, kql, compiler):
        cq = compiler.compile(kql.parse("related-phases:build"))
        assert "EXISTS" in cq.where
        assert "related-phases" in cq.params

    def test_keywords_real_key_passthrough(self, kql, compiler):
        cq = compiler.compile(kql.parse("keywords:foo"))
        assert "EXISTS" in cq.where
        assert "keywords" in cq.params

    def test_related_task_kind_field_compiles_no_sql_changes(self, kql, compiler):
        """A kind-derived ``related-<kind>`` field compiles exactly like the
        pre-existing ``related-area``/``related-phases`` real-key fields — the
        facet name is bound as a plain SQL param, so the compiler needed no
        change to support the new per-kind fields."""
        cq = compiler.compile(kql.parse("related-task:my-task"))
        assert "EXISTS" in cq.where
        assert "record_facet" in cq.where
        assert "related-task" in cq.params
        assert "my-task" in cq.params

    def test_compare_gte_created_at(self, kql, compiler):
        cq = compiler.compile(kql.parse('created-at >= "2026-01-01"'))
        assert "records.created_at >= ?" in cq.where
        assert "2026-01-01" in cq.params

    def test_compare_lte_updated_at(self, kql, compiler):
        cq = compiler.compile(kql.parse('updated-at <= "2026-06-01"'))
        assert "records.updated_at <= ?" in cq.where
        assert "2026-06-01" in cq.params

    def test_compare_gt(self, kql, compiler):
        cq = compiler.compile(kql.parse('created-at > "2025-01-01"'))
        assert "records.created_at > ?" in cq.where

    def test_compare_lt_last_referenced_at(self, kql, compiler):
        cq = compiler.compile(kql.parse('last-referenced-at < "2026-01-01"'))
        assert "records.last_referenced_at < ?" in cq.where

    def test_fulltext_bare_term_is_sql_predicate(self, kql, compiler):
        """A bare full-text term compiles to a SQL rowid-IN-subquery predicate."""
        cq = compiler.compile(kql.parse("foo"))
        assert cq.has_fts
        assert (
            "records.rowid IN (SELECT rowid FROM record_fts WHERE record_fts MATCH ?)" in cq.where
        )
        assert "foo" in cq.params

    def test_phrase_is_sql_predicate_quoted_match(self, kql, compiler):
        cq = compiler.compile(kql.parse('"penny worker"'))
        assert cq.has_fts
        assert (
            "records.rowid IN (SELECT rowid FROM record_fts WHERE record_fts MATCH ?)" in cq.where
        )
        assert '"penny worker"' in cq.params

    def test_and_boolean_composed(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec and status:active"))
        assert "records.kind = ?" in cq.where
        assert "records.status = ?" in cq.where
        assert " AND " in cq.where
        assert "spec" in cq.params
        assert "active" in cq.params

    def test_or_boolean_composed(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec or kind:plan"))
        assert " OR " in cq.where
        assert "spec" in cq.params
        assert "plan" in cq.params

    def test_not_boolean_composed(self, kql, compiler):
        cq = compiler.compile(kql.parse("-status:dropped"))
        assert "NOT " in cq.where or "NOT(" in cq.where
        assert "dropped" in cq.params

    def test_group_unwrapped(self, kql, compiler):
        cq = compiler.compile(kql.parse("(kind:spec or kind:plan) and status:active"))
        assert "records.kind = ?" in cq.where
        assert "records.status = ?" in cq.where


# ---------------------------------------------------------------------------
# CRITICAL #1 — boolean composition across the FTS/SQL boundary
# ---------------------------------------------------------------------------


class TestBooleanComposition:
    """Full-text composes uniformly with scalar/facet predicates in SQL."""

    def test_or_of_fulltext_and_facet_preserves_or(self, kql, compiler, fixture_index):
        """`zephyr or kind:plan`: a row matching ONLY the full-text term AND a row
        matching ONLY kind:plan are BOTH returned (OR preserved, not AND-collapsed)."""
        conn, vault, env = fixture_index
        ast = kql.parse("zephyr or kind:plan")
        cq = compiler.compile(ast)
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        ids = _ids(rows, conn)
        # alpha matches ONLY 'zephyr' (it is kind=spec, not plan)
        assert any(i.endswith("/spec/alpha") for i in ids), ids
        # beta matches ONLY kind:plan (no 'zephyr' in it)
        assert any(i.endswith("/plan/beta") for i in ids), ids

    def test_or_returns_more_than_and_would(self, kql, compiler, fixture_index):
        """OR yields a strictly larger set than the broken AND-collapse would."""
        conn, vault, env = fixture_index
        cq_or = compiler.compile(kql.parse("zephyr or kind:plan"))
        cq_and = compiler.compile(kql.parse("zephyr and kind:plan"))
        or_rows = conn.execute(cq_or.full_query(), cq_or.params).fetchall()
        and_rows = conn.execute(cq_and.full_query(), cq_and.params).fetchall()
        assert len(or_rows) > len(and_rows)
        # AND of zephyr+kind:plan matches nothing (alpha is spec, beta has no zephyr)
        assert and_rows == []

    def test_leading_not_fulltext_no_fts_syntax_error(self, kql, compiler, fixture_index):
        """`not zephyr` executes without an FTS5 syntax error (the old leading-NOT bug)."""
        conn, vault, env = fixture_index
        ast = kql.parse("not zephyr")
        cq = compiler.compile(ast)
        try:
            rows = conn.execute(cq.full_query(), cq.params).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"`not zephyr` raised OperationalError: {exc}")
        ids = _ids(rows, conn)
        # The complement: every record EXCEPT alpha (which contains 'zephyr')
        assert not any(i.endswith("/spec/alpha") for i in ids), ids
        assert any(i.endswith("/plan/beta") for i in ids), ids
        assert any(i.endswith("/area/penny") for i in ids), ids

    def test_facet_and_not_fulltext_correct_complement(self, kql, compiler, fixture_index):
        """`kind:area not zephyr` (implicit AND) returns the correct complement set."""
        conn, vault, env = fixture_index
        ast = kql.parse("kind:area not zephyr")
        cq = compiler.compile(ast)
        try:
            rows = conn.execute(cq.full_query(), cq.params).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"`kind:area not zephyr` raised OperationalError: {exc}")
        ids = _ids(rows, conn)
        # Only area/penny is kind=area and it has no 'zephyr'
        assert ids == [i for i in ids if i.endswith("/area/penny")]
        assert any(i.endswith("/area/penny") for i in ids), ids


# ---------------------------------------------------------------------------
# MATCH encoding (single-term predicate strings — locked encoding preserved)
# ---------------------------------------------------------------------------


class TestMatchEncoding:
    """Each full-text node carries its own single-term MATCH string param."""

    def test_bare_term_match_param(self, kql, compiler):
        cq = compiler.compile(kql.parse("foo"))
        assert "foo" in cq.params

    def test_phrase_match_param_double_quoted(self, kql, compiler):
        cq = compiler.compile(kql.parse('"penny worker"'))
        assert '"penny worker"' in cq.params

    def test_multi_term_each_own_predicate(self, kql, compiler):
        """`penny worker` → two separate single-term predicates (penny, worker)."""
        cq = compiler.compile(kql.parse("penny worker"))
        assert "penny" in cq.params
        assert "worker" in cq.params
        # Two rowid-IN predicates joined by AND
        assert cq.where.count("records.rowid IN") == 2

    def test_ranking_match_or_combines_positive_terms(self, kql, compiler):
        """The ranking subquery MATCH param OR-combines positive full-text terms."""
        cq = compiler.compile(kql.parse("penny worker"))
        assert cq.rank_match == "penny OR worker"

    def test_mixed_fts_and_facet_keeps_facet_in_where(self, kql, compiler):
        cq = compiler.compile(kql.parse("penny and kind:spec"))
        assert cq.has_fts
        assert "penny" in cq.params
        assert "records.kind = ?" in cq.where


# ---------------------------------------------------------------------------
# Ranking selection
# ---------------------------------------------------------------------------


class TestRankingSelection:
    """bm25 is computed once, in the ranking JOIN clause; the ORDER BY sorts on
    the pre-computed ``rank.score``. So these assert the locked weights appear in
    the assembled query, not literally inside ``order_by``."""

    def test_fulltext_query_uses_bm25_order(self, kql, compiler):
        cq = compiler.compile(kql.parse("foo"))
        assert "rank.score" in cq.order_by
        assert "bm25(record_fts, 3.0, 2.0, 1.0)" in cq.full_query()

    def test_pure_facet_uses_recency_order(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"))
        assert "updated_at" in cq.order_by
        assert "last_referenced_at" in cq.order_by
        assert "bm25" not in cq.order_by
        assert "bm25" not in cq.full_query()

    def test_phrase_query_uses_bm25_order(self, kql, compiler):
        cq = compiler.compile(kql.parse('"penny worker"'))
        assert "rank.score" in cq.order_by
        assert "bm25(record_fts, 3.0, 2.0, 1.0)" in cq.full_query()

    def test_mixed_fulltext_and_facet_uses_bm25(self, kql, compiler):
        cq = compiler.compile(kql.parse("foo and kind:spec"))
        assert "rank.score" in cq.order_by
        assert "bm25(record_fts, 3.0, 2.0, 1.0)" in cq.full_query()

    def test_bm25_orders_title_hit_before_body_hit(self, kql, compiler, ranking_index):
        """A title-hit row sorts before a body-only-hit row under the bm25 ORDER BY."""
        conn, vault, env = ranking_index
        cq = compiler.compile(kql.parse("zephyr"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        ids = _ids(rows, conn)
        title_idx = next(i for i, x in enumerate(ids) if x.endswith("/spec/title-hit"))
        body_idx = next(i for i, x in enumerate(ids) if x.endswith("/spec/body-hit"))
        assert title_idx < body_idx, ids

    def test_null_score_rows_sort_last(self, kql, compiler, ranking_index):
        """A query that returns matched + unmatched rows sorts unmatched (NULL) last."""
        conn, vault, env = ranking_index
        # `zephyr or kind:spec` — all three rows are kind:spec, but only two match
        # 'zephyr'. The two zephyr-matching rows (negative bm25) must sort before the
        # non-matching no-hit row (NULL score).
        cq = compiler.compile(kql.parse("zephyr or kind:spec"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        ids = _ids(rows, conn)
        no_hit_idx = next(i for i, x in enumerate(ids) if x.endswith("/spec/no-hit"))
        # no-hit must be last (NULL score sorts after matched negative scores)
        assert no_hit_idx == len(ids) - 1, ids

    def test_pure_facet_recency_order_executes(self, kql, compiler, fixture_index):
        conn, vault, env = fixture_index
        cq = compiler.compile(kql.parse("status:active"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# Vault scope
# ---------------------------------------------------------------------------


class TestVaultScope:
    def test_vault_none_adds_no_predicate(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"), vault=None)
        assert "vault" not in cq.where

    def test_vault_name_adds_predicate(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"), vault="my-vault")
        assert "records.vault = ?" in cq.where
        assert "my-vault" in cq.params

    def test_vault_does_not_double_add_on_fulltext(self, kql, compiler):
        cq = compiler.compile(kql.parse("foo"), vault="my-vault")
        assert cq.params.count("my-vault") == 1


# ---------------------------------------------------------------------------
# Limit (bound param)
# ---------------------------------------------------------------------------


class TestLimit:
    def test_default_limit_is_20(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"))
        assert cq.limit == 20

    def test_explicit_limit_overrides(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"), limit=5)
        assert cq.limit == 5

    def test_limit_is_bound_param_not_interpolated(self, kql, compiler):
        """LIMIT is a ? placeholder; the value is the last bind param."""
        cq = compiler.compile(kql.parse("kind:spec"), limit=7)
        sql = cq.full_query()
        assert "LIMIT ?" in sql
        assert "LIMIT 7" not in sql
        assert cq.params[-1] == 7

    def test_limit_coerced_to_int(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"), limit="9")
        assert cq.limit == 9
        assert cq.params[-1] == 9

    def test_limit_appears_in_full_query(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"), limit=7)
        assert "LIMIT" in cq.full_query().upper()


# ---------------------------------------------------------------------------
# Positional param alignment (executed)
# ---------------------------------------------------------------------------


class TestParamAlignment:
    """full_query() placeholders and params stay positionally aligned (executed)."""

    def test_param_count_matches_placeholders_pure_facet(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec and status:active"), vault="v")
        sql = cq.full_query()
        assert sql.count("?") == len(cq.params)

    def test_param_count_matches_placeholders_fulltext(self, kql, compiler):
        cq = compiler.compile(kql.parse("penny worker and kind:spec"), vault="v")
        sql = cq.full_query()
        assert sql.count("?") == len(cq.params)

    def test_assembled_query_executes_with_alignment(self, kql, compiler, fixture_index):
        """A WHERE-params + rank-param + limit query executes (alignment proven)."""
        conn, vault, env = fixture_index
        ast = kql.parse("zephyr and kind:spec")
        cq = compiler.compile(ast, vault=str(vault), limit=10)
        sql = cq.full_query()
        assert sql.count("?") == len(cq.params)
        rows = conn.execute(sql, cq.params).fetchall()
        ids = _ids(rows, conn)
        assert any(i.endswith("/spec/alpha") for i in ids), ids

    def test_rank_match_param_precedes_limit(self, kql, compiler):
        """Param order: WHERE params (incl. MATCH strings), then rank MATCH, then LIMIT."""
        cq = compiler.compile(kql.parse("zephyr"), limit=5)
        # rank_match present and appears in params before the trailing LIMIT
        assert cq.rank_match in cq.params
        assert cq.params[-1] == 5
        # The MATCH string 'zephyr' appears in WHERE position (index 0), rank later.
        assert cq.params[0] == "zephyr"


# ---------------------------------------------------------------------------
# SQL injection safety
# ---------------------------------------------------------------------------


class TestInjectionSafety:
    def test_scalar_value_is_bound_not_interpolated(self, kql, compiler):
        kql_mod = load_script("lore.search.kql")
        inject_ast = kql_mod.FieldEq(field="kind", value="penny' OR '1'='1")
        cq = compiler.compile(inject_ast)
        assert "penny' OR '1'='1" not in cq.where
        assert "penny' OR '1'='1" in cq.params

    def test_injection_value_returns_no_spurious_rows(self, kql, compiler, fixture_index):
        conn, vault, env = fixture_index
        kql_mod = load_script("lore.search.kql")
        inject_ast = kql_mod.FieldEq(field="kind", value="penny' OR '1'='1")
        cq = compiler.compile(inject_ast)
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        assert rows == [], f"injection payload returned {len(rows)} spurious rows"

    def test_facet_injection_in_value_is_bound(self, kql, compiler):
        kql_mod = load_script("lore.search.kql")
        inject_ast = kql_mod.FacetMembership(facet="area", value="foo' OR '1'='1")
        cq = compiler.compile(inject_ast)
        assert "foo' OR '1'='1" not in cq.where
        assert "foo' OR '1'='1" in cq.params


# ---------------------------------------------------------------------------
# SECURITY — Compare.op guard (no assert, generic message)
# ---------------------------------------------------------------------------


class TestCompareOpGuard:
    def test_invalid_op_raises_value_error(self, compiler):
        kql_mod = load_script("lore.search.kql")
        bad = kql_mod.Compare(field="created-at", op="; DROP TABLE records;--", value="x")
        with pytest.raises(ValueError) as exc:
            compiler.compile(bad)
        # Generic message — must NOT reflect the raw op value (no log injection)
        assert "DROP TABLE" not in str(exc.value)

    def test_valid_op_does_not_raise(self, kql, compiler):
        cq = compiler.compile(kql.parse('created-at >= "2026-01-01"'))
        assert "records.created_at >= ?" in cq.where


# ---------------------------------------------------------------------------
# SECURITY — unknown column raises (no node.field fallthrough)
# ---------------------------------------------------------------------------


class TestUnknownColumnGuard:
    def test_unknown_field_eq_column_raises(self, compiler):
        kql_mod = load_script("lore.search.kql")
        node = kql_mod.FieldEq(field="evil_col", value="x")
        with pytest.raises(ValueError) as exc:
            compiler.compile(node)
        assert "evil_col" not in str(exc.value)

    def test_unknown_compare_column_raises(self, compiler):
        kql_mod = load_script("lore.search.kql")
        node = kql_mod.Compare(field="evil_col", op=">=", value="x")
        with pytest.raises(ValueError) as exc:
            compiler.compile(node)
        assert "evil_col" not in str(exc.value)


# ---------------------------------------------------------------------------
# SECURITY — FTS5 strict-allowlist sanitizer (executed)
# ---------------------------------------------------------------------------


class TestFtsSanitizer:
    """Strict allowlist: tokens not matching ^[A-Za-z0-9_]+$ become quoted literals."""

    _INJECTION_TERMS = [
        "*",
        "(foo",
        "foo)",
        "NEAR(foo,bar)",
        "^foo",
        "foo*",
        "body:zephyr",
        "col:nonexistent",
        "(foo OR bar)",
        "auth-service",
        "operational-state",
        "phi-scrubber.v2",
    ]

    @pytest.mark.parametrize("term", _INJECTION_TERMS)
    def test_injection_term_compiles_and_executes_no_error(
        self, kql, compiler, fixture_index, term
    ):
        conn, vault, env = fixture_index
        kql_mod = load_script("lore.search.kql")
        ast = kql_mod.FullText(term=term)
        cq = compiler.compile(ast)
        try:
            rows = conn.execute(cq.full_query(), cq.params).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"FTS term {term!r} raised OperationalError: {exc}")
        # No spurious rows — these are literal tokens that match nothing in the fixture
        assert rows == [], f"{term!r} returned {len(rows)} spurious rows"

    def test_clean_bare_term_stays_unquoted(self, kql, compiler):
        cq = compiler.compile(kql.parse("zephyr"))
        assert "zephyr" in cq.params  # emitted bare, not '"zephyr"'

    def test_reserved_op_term_is_quoted(self, kql, compiler):
        kql_mod = load_script("lore.search.kql")
        cq = compiler.compile(kql_mod.FullText(term="AND"))
        assert '"AND"' in cq.params

    def test_dotted_term_becomes_quoted_literal(self, kql, compiler):
        """Hyphen/dot are re-parsed as column filters by FTS5's query grammar, so
        such tokens must fall through to the quoted-literal path."""
        kql_mod = load_script("lore.search.kql")
        cq = compiler.compile(kql_mod.FullText(term="phi-scrubber.v2"))
        assert '"phi-scrubber.v2"' in cq.params

    def test_hyphenated_term_finds_matching_record(self, tmp_path, kql, compiler, index_store):
        """The quoted-literal fallback must still MATCH — not just avoid erroring.

        A record whose body contains the hyphenated token is the positive
        counterpart to the injection-term sweep above, which only proves no
        `OperationalError` and no spurious rows against terms absent from the
        fixture.
        """
        vault = tmp_path / "vault"
        fake_state = tmp_path / "xdg-state"
        fake_state.mkdir()
        env = dict(os.environ)
        env["XDG_STATE_HOME"] = str(fake_state)
        _write_record(
            vault,
            "spec",
            "gamma",
            _sidecar(kind="spec", title="Gamma Spec", status="active"),
            "the auth-service handles login",
        )
        conn = index_store.open_index(env=env)
        index_store.rebuild([str(vault)], conn)
        conn.commit()

        kql_mod = load_script("lore.search.kql")
        cq = compiler.compile(kql_mod.FullText(term="auth-service"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()

        assert len(rows) == 1
        assert rows[0][0].endswith("spec/gamma")

    def test_single_quote_term_becomes_quoted_literal(self, kql, compiler, fixture_index):
        conn, vault, env = fixture_index
        kql_mod = load_script("lore.search.kql")
        cq = compiler.compile(kql_mod.FullText(term="foo'bar"))
        try:
            rows = conn.execute(cq.full_query(), cq.params).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"single-quote term raised OperationalError: {exc}")
        assert rows == []

    def test_phrase_always_wrapped(self, kql, compiler):
        kql_mod = load_script("lore.search.kql")
        cq = compiler.compile(kql_mod.Phrase(text="penny worker"))
        assert '"penny worker"' in cq.params

    def test_phrase_with_stray_quotes_sanitized(self, kql, compiler, fixture_index):
        conn, vault, env = fixture_index
        kql_mod = load_script("lore.search.kql")
        cq = compiler.compile(kql_mod.Phrase(text='penny" OR "1"="1'))
        try:
            rows = conn.execute(cq.full_query(), cq.params).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"phrase stray-quote raised OperationalError: {exc}")
        assert rows == []


# ---------------------------------------------------------------------------
# SECURITY — empty-after-strip token raises a clean compile error
# ---------------------------------------------------------------------------


class TestEmptyAfterStrip:
    @pytest.mark.parametrize("term", ['"', "\\", '""'])
    def test_empty_after_strip_raises(self, compiler, term):
        kql_mod = load_script("lore.search.kql")
        node = kql_mod.FullText(term=term)
        with pytest.raises(ValueError) as exc:
            compiler.compile(node)
        # Generic — does NOT reflect the raw token
        assert term not in str(exc.value) or term == ""

    def test_empty_phrase_raises(self, compiler):
        kql_mod = load_script("lore.search.kql")
        node = kql_mod.Phrase(text='""')
        with pytest.raises(ValueError):
            compiler.compile(node)


# ---------------------------------------------------------------------------
# Full SELECT query shape
# ---------------------------------------------------------------------------


class TestFullQueryShape:
    def test_full_query_selects_from_records(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"))
        sql = cq.full_query()
        assert sql.lstrip().upper().startswith("SELECT")
        assert "FROM records" in sql
        assert "LIMIT" in sql.upper()

    def test_fulltext_full_query_no_mandatory_join(self, kql, compiler):
        """Full-text query FROM is just `records` — full-text lives in WHERE."""
        cq = compiler.compile(kql.parse("foo"))
        sql = cq.full_query()
        assert "FROM records" in sql
        assert "JOIN record_fts" not in sql
        assert "records.rowid IN (SELECT rowid FROM record_fts WHERE record_fts MATCH ?)" in sql
        assert "bm25(record_fts, 3.0, 2.0, 1.0)" in sql

    def test_fts_full_query_executes_against_fixture(self, kql, compiler, fixture_index):
        conn, vault, env = fixture_index
        cq = compiler.compile(kql.parse("zephyr"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        assert len(rows) >= 1, "expected at least one row matching 'zephyr'"

    def test_pure_facet_full_query_executes_against_fixture(self, kql, compiler, fixture_index):
        conn, vault, env = fixture_index
        cq = compiler.compile(kql.parse("kind:spec"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        assert len(rows) == 1

    def test_area_facet_query_executes_against_fixture(self, kql, compiler, fixture_index):
        conn, vault, env = fixture_index
        cq = compiler.compile(kql.parse("area:penny"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        assert len(rows) >= 1, "expected at least one record with area:penny"

    def test_vault_scope_narrows_results(self, kql, compiler, fixture_index):
        conn, vault, env = fixture_index
        cq_with = compiler.compile(kql.parse("kind:spec"), vault=str(vault))
        rows_with = conn.execute(cq_with.full_query(), cq_with.params).fetchall()
        assert len(rows_with) == 1

        cq_wrong = compiler.compile(kql.parse("kind:spec"), vault="nonexistent-vault")
        rows_wrong = conn.execute(cq_wrong.full_query(), cq_wrong.params).fetchall()
        assert rows_wrong == []

    def test_limit_caps_results(self, kql, compiler, fixture_index):
        conn, vault, env = fixture_index
        cq = compiler.compile(kql.parse("status:active or status:draft"), limit=1)
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Label selectors (LabelEq / LabelExists)
# ---------------------------------------------------------------------------


def _label_sidecar(
    *,
    kind="spec",
    title="L",
    status="active",
    labels=None,
    annotations=None,
    updated_at="2026-06-17T10:00:00Z",
):
    s = _sidecar(kind=kind, title=title, status=status, updated_at=updated_at)
    if labels is not None:
        s["labels"] = labels
    if annotations is not None:
        s["annotations"] = annotations
    return s


@pytest.fixture()
def label_index(tmp_path, index_store):
    """Index with label-bearing records.

    - spec/has-s5      labels={"worktree":"s5"}
    - spec/has-s6      labels={"worktree":"s6"}
    - spec/namespaced  labels={"claude-code/model":"opus"}
    - spec/two-labels  labels={"worktree":"s5","env":"build"}
    - spec/no-labels   annotations={"note":"x"} only (no labels)
    """
    vault = tmp_path / "vault"
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    _write_record(
        vault,
        "spec",
        "has-s5",
        _label_sidecar(title="Has S5", labels={"worktree": "s5"}),
        "body s5",
    )
    _write_record(
        vault,
        "spec",
        "has-s6",
        _label_sidecar(title="Has S6", labels={"worktree": "s6"}),
        "body s6",
    )
    _write_record(
        vault,
        "spec",
        "namespaced",
        _label_sidecar(title="Namespaced", labels={"claude-code/model": "opus"}),
        "body ns",
    )
    _write_record(
        vault,
        "spec",
        "two-labels",
        _label_sidecar(title="Two", labels={"worktree": "s5", "env": "build"}),
        "body two",
    )
    _write_record(
        vault,
        "spec",
        "no-labels",
        _label_sidecar(title="None", annotations={"note": "x"}),
        "body none",
    )

    conn = index_store.open_index(env=env)
    index_store.rebuild([str(vault)], conn)
    conn.commit()
    return conn, vault, env


class TestLabelCompile:
    """LabelEq / LabelExists compile to parameterized EXISTS subqueries."""

    def test_label_eq_compiles_to_exists_with_bind_params(self, kql, compiler):
        cq = compiler.compile(kql.parse("label.worktree:s5"))
        assert "EXISTS" in cq.where
        assert "record_labels" in cq.where
        assert "key = ?" in cq.where
        assert "value = ?" in cq.where
        assert "worktree" in cq.params
        assert "s5" in cq.params

    def test_label_eq_key_and_value_are_not_interpolated(self, kql, compiler):
        cq = compiler.compile(kql.parse("label.worktree:s5"))
        # The literal key/value must NOT appear in the SQL text — only as params.
        assert "worktree" not in cq.where
        assert "s5" not in cq.where

    def test_label_exists_compiles_to_key_only_exists(self, kql, compiler):
        cq = compiler.compile(kql.parse("has:label.worktree"))
        assert "EXISTS" in cq.where
        assert "record_labels" in cq.where
        assert "key = ?" in cq.where
        assert "value = ?" not in cq.where
        assert "worktree" in cq.params

    def test_label_exists_key_not_interpolated(self, kql, compiler):
        cq = compiler.compile(kql.parse("has:label.worktree"))
        assert "worktree" not in cq.where

    def test_namespaced_label_eq_binds_real_key(self, kql, compiler):
        cq = compiler.compile(kql.parse("label.claude-code.model:opus"))
        assert "claude-code/model" in cq.params
        assert "opus" in cq.params

    # -- executed against a fixture index ----------------------------------

    def test_label_eq_returns_exact_records(self, kql, compiler, label_index):
        conn, vault, env = label_index
        cq = compiler.compile(kql.parse("label.worktree:s5"))
        ids = _ids(conn.execute(cq.full_query(), cq.params).fetchall(), conn)
        assert any(i.endswith("/spec/has-s5") for i in ids), ids
        assert any(i.endswith("/spec/two-labels") for i in ids), ids
        assert not any(i.endswith("/spec/has-s6") for i in ids), ids
        assert not any(i.endswith("/spec/no-labels") for i in ids), ids

    def test_label_exists_returns_any_with_key(self, kql, compiler, label_index):
        conn, vault, env = label_index
        cq = compiler.compile(kql.parse("has:label.worktree"))
        ids = _ids(conn.execute(cq.full_query(), cq.params).fetchall(), conn)
        assert any(i.endswith("/spec/has-s5") for i in ids), ids
        assert any(i.endswith("/spec/has-s6") for i in ids), ids
        assert any(i.endswith("/spec/two-labels") for i in ids), ids
        assert not any(i.endswith("/spec/no-labels") for i in ids), ids
        assert not any(i.endswith("/spec/namespaced") for i in ids), ids

    def test_namespaced_label_eq_returns_record(self, kql, compiler, label_index):
        conn, vault, env = label_index
        cq = compiler.compile(kql.parse("label.claude-code.model:opus"))
        ids = _ids(conn.execute(cq.full_query(), cq.params).fetchall(), conn)
        assert [i for i in ids if i.endswith("/spec/namespaced")], ids
        assert len(ids) == 1, ids

    def test_two_label_terms_and_together(self, kql, compiler, label_index):
        conn, vault, env = label_index
        cq = compiler.compile(kql.parse("label.worktree:s5 label.env:build"))
        ids = _ids(conn.execute(cq.full_query(), cq.params).fetchall(), conn)
        # only two-labels has BOTH worktree=s5 and env=build
        assert ids == [i for i in ids if i.endswith("/spec/two-labels")]
        assert any(i.endswith("/spec/two-labels") for i in ids), ids

    def test_nonexistent_label_returns_none(self, kql, compiler, label_index):
        conn, vault, env = label_index
        cq = compiler.compile(kql.parse("label.worktree:nope"))
        ids = _ids(conn.execute(cq.full_query(), cq.params).fetchall(), conn)
        assert ids == [], ids

    def test_nonexistent_label_key_exists_returns_none(self, kql, compiler, label_index):
        conn, vault, env = label_index
        cq = compiler.compile(kql.parse("has:label.bogus"))
        ids = _ids(conn.execute(cq.full_query(), cq.params).fetchall(), conn)
        assert ids == [], ids

    def test_annotations_not_queryable(self, kql, compiler, label_index):
        """annotations are NOT indexed: a label query on an annotation key finds nothing."""
        conn, vault, env = label_index
        cq = compiler.compile(kql.parse("label.note:x"))
        ids = _ids(conn.execute(cq.full_query(), cq.params).fetchall(), conn)
        assert ids == [], ids


class TestLabelInjectionSafety:
    """SQL metachars in a label key/value are bound as params — no SQL executes."""

    def test_label_value_with_sql_metachars_is_bound(self, kql, compiler):
        node = kql.LabelEq(key="worktree", value="x'; DROP TABLE records;--")
        cq = compiler.compile(node)
        assert "DROP TABLE" not in cq.where
        assert "x'; DROP TABLE records;--" in cq.params

    def test_label_key_with_sql_metachars_is_bound(self, kql, compiler):
        node = kql.LabelEq(key="evil'--", value="v")
        cq = compiler.compile(node)
        assert "evil'--" not in cq.where
        assert "evil'--" in cq.params

    def test_label_exists_key_with_metachars_is_bound(self, kql, compiler):
        node = kql.LabelExists(key="evil'; DROP TABLE record_labels;--")
        cq = compiler.compile(node)
        assert "DROP TABLE" not in cq.where
        assert "evil'; DROP TABLE record_labels;--" in cq.params

    def test_sqli_label_executes_with_no_side_effect(self, kql, compiler, label_index):
        conn, vault, env = label_index
        before = conn.execute("SELECT count(*) FROM record_labels").fetchone()[0]
        node = kql.LabelEq(key="worktree", value="s5'; DROP TABLE record_labels;--")
        cq = compiler.compile(node)
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        # The crafted value simply doesn't match; the table is untouched.
        assert rows == []
        after = conn.execute("SELECT count(*) FROM record_labels").fetchone()[0]
        assert after == before


# ---------------------------------------------------------------------------
# CompiledQuery structure
# ---------------------------------------------------------------------------


class TestCompiledQueryStructure:
    def test_compiled_query_has_expected_attrs(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"))
        for attr in ("where", "params", "order_by", "limit", "has_fts", "rank_match"):
            assert hasattr(cq, attr), attr

    def test_params_is_list(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"))
        assert isinstance(cq.params, list)

    def test_has_fts_false_for_pure_facet(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"))
        assert cq.has_fts is False

    def test_has_fts_true_for_fulltext(self, kql, compiler):
        cq = compiler.compile(kql.parse("foo"))
        assert cq.has_fts is True

    def test_rank_match_empty_for_pure_facet(self, kql, compiler):
        cq = compiler.compile(kql.parse("kind:spec"))
        assert not cq.rank_match


# ---------------------------------------------------------------------------
# Single-JOIN bm25 ranking — score computed once per matching row
#
# A single LEFT JOIN computes bm25 once per matching row. A correlated scalar
# subquery in the ORDER BY would re-run the FTS MATCH for every candidate row
# instead (2.37s vs ~10ms on a 3,508-record vault), so the query PLAN is pinned
# alongside the ordering it produces.
# ---------------------------------------------------------------------------


@pytest.fixture()
def ranking_equivalence_index(tmp_path, index_store):
    """Five records exercising bare-term, facet+term, negation, and pure-facet
    ranking shapes, with distinct ``updated_at`` values so the recency tiebreak
    is fully deterministic:

      - spec/r1 — title hit ("zephyr" in title), updated 2026-01-05
      - spec/r2 — body-only hit ("zephyr" in body), updated 2026-01-04
      - spec/r3 — no match, updated 2026-01-03
      - plan/r4 — no match, kind=plan, updated 2026-01-02
      - spec/r5 — title + keyword + body hit (strongest), updated 2026-01-01
    """
    vault = tmp_path / "vault"
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    _write_record(
        vault,
        "spec",
        "r1",
        _sidecar(kind="spec", title="alpha zephyr focus", updated_at="2026-01-05T00:00:00Z"),
        "filler word count filler",
    )
    _write_record(
        vault,
        "spec",
        "r2",
        _sidecar(kind="spec", title="Nothing Related At All", updated_at="2026-01-04T00:00:00Z"),
        "zephyr appears deep in the body text only once",
    )
    _write_record(
        vault,
        "spec",
        "r3",
        _sidecar(kind="spec", title="Gamma No Match", updated_at="2026-01-03T00:00:00Z"),
        "no term here either just filler",
    )
    _write_record(
        vault,
        "plan",
        "r4",
        _sidecar(kind="plan", title="Beta Plan No Match", updated_at="2026-01-02T00:00:00Z"),
        "no term here",
    )
    _write_record(
        vault,
        "spec",
        "r5",
        _sidecar(
            kind="spec",
            title="zephyr keyword focus",
            keywords=["zephyr"],
            updated_at="2026-01-01T00:00:00Z",
        ),
        "another zephyr mention in body too",
    )

    conn = index_store.open_index(env=env)
    index_store.rebuild([str(vault)], conn)
    conn.commit()
    return conn, vault, env


class TestRankingEquivalence:
    """Pins the exact ordered id sequence for four query shapes — bare term,
    facet+term, negation, and pure-facet — so any change to the ranking mechanism
    has to reproduce the ordering byte-for-byte."""

    def test_bare_high_match_term_order_unchanged(self, kql, compiler, ranking_equivalence_index):
        conn, vault, env = ranking_equivalence_index
        cq = compiler.compile(kql.parse("zephyr"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        ids = _ids(rows, conn)
        assert [i.split("/")[-2:] for i in ids] == [
            ["spec", "r5"],
            ["spec", "r1"],
            ["spec", "r2"],
        ], ids

    def test_facet_and_term_mix_order_unchanged(self, kql, compiler, ranking_equivalence_index):
        conn, vault, env = ranking_equivalence_index
        cq = compiler.compile(kql.parse("kind:spec and zephyr"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        ids = _ids(rows, conn)
        assert [i.split("/")[-2:] for i in ids] == [
            ["spec", "r5"],
            ["spec", "r1"],
            ["spec", "r2"],
        ], ids

    def test_negated_term_order_unchanged(self, kql, compiler, ranking_equivalence_index):
        conn, vault, env = ranking_equivalence_index
        cq = compiler.compile(kql.parse("not zephyr"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        ids = _ids(rows, conn)
        assert [i.split("/")[-2:] for i in ids] == [
            ["spec", "r3"],
            ["plan", "r4"],
        ], ids

    def test_pure_facet_no_fts_order_unchanged(self, kql, compiler, ranking_equivalence_index):
        conn, vault, env = ranking_equivalence_index
        cq = compiler.compile(kql.parse("kind:spec"))
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        ids = _ids(rows, conn)
        assert [i.split("/")[-2:] for i in ids] == [
            ["spec", "r1"],
            ["spec", "r2"],
            ["spec", "r3"],
            ["spec", "r5"],
        ], ids


class TestQueryPlanShape:
    """The bm25 ranking must be a single JOIN scan, never a per-row correlated
    scalar subquery."""

    def test_ranked_query_plan_has_no_correlated_scalar_subquery(
        self, kql, compiler, ranking_equivalence_index
    ):
        conn, vault, env = ranking_equivalence_index
        cq = compiler.compile(kql.parse("zephyr"))
        plan_rows = conn.execute(
            f"EXPLAIN QUERY PLAN {cq.full_query()}", cq.params
        ).fetchall()
        detail_col = 3  # EXPLAIN QUERY PLAN: (id, parent, notused, detail)
        details = [row[detail_col] for row in plan_rows]
        assert not any("CORRELATED SCALAR SUBQUERY" in d for d in details), details


class TestParamOrderContract:
    """Single-JOIN param order: rank-JOIN MATCH param (once) precedes WHERE params,
    both precede the trailing LIMIT."""

    def test_rank_param_precedes_where_params(self, kql, compiler):
        cq = compiler.compile(kql.parse("zephyr and kind:spec"), limit=5)
        # Exactly one rank param, at index 0 — no doubled trailing param.
        assert cq.params.count("zephyr") == 2  # WHERE MATCH string + rank param
        assert cq.params[0] == "zephyr"  # the rank-JOIN param, ahead of WHERE
        assert cq.params[1] == "zephyr"  # the WHERE MATCH predicate param
        assert cq.params[2] == "spec"  # the kind:spec WHERE param
        assert cq.params[-1] == 5  # LIMIT trails

    def test_params_positionally_align_with_full_query(self, kql, compiler, fixture_index):
        conn, vault, env = fixture_index
        cq = compiler.compile(kql.parse("zephyr and kind:spec"), vault=str(vault), limit=10)
        sql = cq.full_query()
        assert sql.count("?") == len(cq.params)
        rows = conn.execute(sql, cq.params).fetchall()
        ids = _ids(rows, conn)
        assert any(i.endswith("/spec/alpha") for i in ids), ids

    def test_count_total_extracts_exactly_where_params(self, kql, compiler, fixture_index):
        """``engine._count_total`` must slice out exactly the WHERE params under
        the new (rank-param-first) contract — never the rank param, never LIMIT."""
        conn, vault, env = fixture_index
        engine = load_script("lore.search.engine")
        cq = compiler.compile(kql.parse("zephyr and kind:spec"), limit=10)
        total = engine._count_total(conn, cq)
        assert total == 1


class TestSnippetBatching:
    """``engine._fetch_hits`` must issue ONE snippet query regardless of row
    count, not one per returned row (the N+1 this fix also closes)."""

    def test_fetch_hits_issues_one_snippet_query_for_multiple_rows(
        self, kql, compiler, ranking_equivalence_index
    ):
        conn, vault, env = ranking_equivalence_index
        engine = load_script("lore.search.engine")

        counting = {"n": 0}

        class _CountingConn:
            def execute(self, sql, params=()):
                if "record_fts" in sql and "body" in sql:
                    counting["n"] += 1
                return conn.execute(sql, params)

        cq = compiler.compile(kql.parse("kind:spec"), limit=20)
        hits, total = engine._fetch_hits(_CountingConn(), cq)

        assert len(hits) == 4, hits
        assert counting["n"] == 1, counting["n"]
