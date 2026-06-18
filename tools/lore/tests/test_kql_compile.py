"""Slice 3 (S3) tests: KQL AST → SQL compiler.

Covers every bullet in the plan's Slice 3 test contract:

  - Each LOCKED compile-table row produces the expected SQL fragment + params.
  - MATCH encoding: bare term → 'foo', phrase → '"penny worker"', multi-term →
    'penny AND worker'.
  - Ranking: a full-text query emits bm25(...) ORDER BY; pure-facet emits recency ORDER BY.
  - --vault adds the vault = ? param; no --vault adds no vault predicate.
  - Injection safety: a value like ``penny' OR '1'='1`` is a bind param, not
    interpolated; executing the compiled query against a fixture index returns no
    spurious rows.
  - FTS MATCH sanitizer: bare term ``foo'bar`` and a phrase containing stray
    quotes/backslashes are sanitized so the MATCH string is well-formed, raises no
    FTS syntax error, and matches no spurious rows (verified by executing against
    the fixture index).
  - --limit default 20 present; explicit --limit N overrides.

SELECT shape for Slice 4 (LOCKED):
  When the query has full-text terms, Slice 4 MUST execute a JOIN form so that
  bm25(record_fts, ...) is reachable in ORDER BY:

    SELECT records.* FROM record_fts
    JOIN records ON records.rowid = record_fts.rowid
    WHERE record_fts MATCH ?
      AND <other predicates on records.*>
    ORDER BY bm25(record_fts, 3.0, 2.0, 1.0)
    LIMIT ?

  For pure-facet queries (no MATCH), Slice 4 executes:

    SELECT * FROM records
    WHERE <predicates>
    ORDER BY updated_at DESC, last_referenced_at DESC
    LIMIT ?

  CompiledQuery.has_fts tells Slice 4 which form to use.
  CompiledQuery.full_query(fts_join=True/False) returns the assembled SQL.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


def load_script(name: str):
    """Load a module from plugins/lore/scripts/ by stem, freshly each call."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if name in sys.modules:
        del sys.modules[name]
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def kql():
    return load_script("kql")


@pytest.fixture()
def compiler():
    return load_script("kql_compile")


@pytest.fixture()
def index_store():
    return load_script("index_store")


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
        "updated-at": "2026-06-17T10:00:00Z",
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
                      body="beta only body"
      - area/penny  — kind=area, title="Penny Area"
    """
    vault = tmp_path / "vault"
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    _write_record(
        vault, "spec", "alpha",
        _sidecar(
            kind="spec", title="Alpha Spec", status="active",
            keywords=["search"],
            related={"area": ["penny"]},
        ),
        "unique body word zephyr",
    )
    _write_record(
        vault, "plan", "beta",
        _sidecar(kind="plan", title="Beta Plan", status="draft"),
        "beta only body content here",
    )
    _write_record(
        vault, "area", "penny",
        _sidecar(kind="area", title="Penny Area", status="active"),
        "penny area body",
    )

    conn = index_store.open_index(env=env)
    index_store.rebuild([str(vault)], conn)
    conn.commit()
    return conn, vault, env


# ---------------------------------------------------------------------------
# Helper: execute a CompiledQuery against an open connection
# ---------------------------------------------------------------------------

def _exec(conn, cq, *, fts_join=False):
    """Execute a CompiledQuery and return all rows as a list."""
    sql = cq.full_query(fts_join=fts_join)
    return conn.execute(sql, cq.params).fetchall()


# ---------------------------------------------------------------------------
# LOCKED compile-table rows
# ---------------------------------------------------------------------------

class TestLockedCompileTable:
    """Each LOCKED compile-table row → expected SQL fragment + params."""

    def test_kind_scalar_field_eq(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast)
        assert "records.kind = ?" in cq.where
        assert "spec" in cq.params

    def test_status_scalar_field_eq(self, kql, compiler):
        ast = kql.parse("status:active")
        cq = compiler.compile(ast)
        assert "records.status = ?" in cq.where
        assert "active" in cq.params

    def test_repo_scalar_field_eq(self, kql, compiler):
        ast = kql.parse('repo:"trailhead-ai/trailhead"')
        cq = compiler.compile(ast)
        assert "records.repo = ?" in cq.where
        assert "trailhead-ai/trailhead" in cq.params

    def test_team_scalar_field_eq(self, kql, compiler):
        ast = kql.parse('team:"platform infra"')
        cq = compiler.compile(ast)
        assert "records.team = ?" in cq.where
        assert "platform infra" in cq.params

    def test_product_scalar_field_eq(self, kql, compiler):
        ast = kql.parse("product:lore")
        cq = compiler.compile(ast)
        assert "records.product = ?" in cq.where
        assert "lore" in cq.params

    def test_suite_scalar_field_eq(self, kql, compiler):
        ast = kql.parse("suite:search")
        cq = compiler.compile(ast)
        assert "records.suite = ?" in cq.where
        assert "search" in cq.params

    def test_area_alias_resolves_to_facet_exists(self, kql, compiler):
        """area:penny → related-area facet EXISTS subquery."""
        ast = kql.parse("area:penny")
        cq = compiler.compile(ast)
        assert "EXISTS" in cq.where
        assert "record_facet" in cq.where
        assert "related-area" in cq.params
        assert "penny" in cq.params

    def test_phase_alias_resolves_to_facet_exists(self, kql, compiler):
        """phase:build → related-phases facet EXISTS subquery."""
        ast = kql.parse("phase:build")
        cq = compiler.compile(ast)
        assert "EXISTS" in cq.where
        assert "related-phases" in cq.params
        assert "build" in cq.params

    def test_keyword_alias_resolves_to_facet_exists(self, kql, compiler):
        """keyword:foo → keywords facet EXISTS subquery."""
        ast = kql.parse("keyword:foo")
        cq = compiler.compile(ast)
        assert "EXISTS" in cq.where
        assert "keywords" in cq.params
        assert "foo" in cq.params

    def test_related_area_real_key_passthrough(self, kql, compiler):
        """related-area:penny (real key, not alias) still resolves to EXISTS."""
        ast = kql.parse("related-area:penny")
        cq = compiler.compile(ast)
        assert "EXISTS" in cq.where
        assert "related-area" in cq.params

    def test_related_phases_real_key_passthrough(self, kql, compiler):
        ast = kql.parse("related-phases:build")
        cq = compiler.compile(ast)
        assert "EXISTS" in cq.where
        assert "related-phases" in cq.params

    def test_keywords_real_key_passthrough(self, kql, compiler):
        ast = kql.parse("keywords:foo")
        cq = compiler.compile(ast)
        assert "EXISTS" in cq.where
        assert "keywords" in cq.params

    def test_compare_gte_created_at(self, kql, compiler):
        ast = kql.parse('created-at >= "2026-01-01"')
        cq = compiler.compile(ast)
        assert "records.created_at >= ?" in cq.where
        assert "2026-01-01" in cq.params

    def test_compare_lte_updated_at(self, kql, compiler):
        ast = kql.parse('updated-at <= "2026-06-01"')
        cq = compiler.compile(ast)
        assert "records.updated_at <= ?" in cq.where
        assert "2026-06-01" in cq.params

    def test_compare_gt(self, kql, compiler):
        ast = kql.parse('created-at > "2025-01-01"')
        cq = compiler.compile(ast)
        assert "records.created_at > ?" in cq.where

    def test_compare_lt_last_referenced_at(self, kql, compiler):
        ast = kql.parse('last-referenced-at < "2026-01-01"')
        cq = compiler.compile(ast)
        assert "records.last_referenced_at < ?" in cq.where

    def test_fulltext_bare_term(self, kql, compiler):
        """Bare full-text term produces a MATCH expression and has_fts=True."""
        ast = kql.parse("foo")
        cq = compiler.compile(ast)
        assert cq.has_fts
        assert cq.match_expr == "foo"

    def test_phrase_produces_quoted_match(self, kql, compiler):
        """Quoted phrase produces a double-quoted MATCH string."""
        ast = kql.parse('"penny worker"')
        cq = compiler.compile(ast)
        assert cq.has_fts
        assert cq.match_expr == '"penny worker"'

    def test_and_boolean_composed(self, kql, compiler):
        """kind:spec AND status:active → two predicates joined by AND."""
        ast = kql.parse("kind:spec and status:active")
        cq = compiler.compile(ast)
        assert "records.kind = ?" in cq.where
        assert "records.status = ?" in cq.where
        assert " AND " in cq.where
        assert "spec" in cq.params
        assert "active" in cq.params

    def test_or_boolean_composed(self, kql, compiler):
        """kind:spec OR kind:plan → two predicates joined by OR."""
        ast = kql.parse("kind:spec or kind:plan")
        cq = compiler.compile(ast)
        assert " OR " in cq.where
        assert "spec" in cq.params
        assert "plan" in cq.params

    def test_not_boolean_composed(self, kql, compiler):
        """-status:dropped → NOT predicate."""
        ast = kql.parse("-status:dropped")
        cq = compiler.compile(ast)
        assert "NOT " in cq.where or "NOT(" in cq.where
        assert "dropped" in cq.params

    def test_group_unwrapped(self, kql, compiler):
        """Group(inner) is unwrapped via .inner — not treated as transparent."""
        ast = kql.parse("(kind:spec or kind:plan) and status:active")
        cq = compiler.compile(ast)
        # The whole thing compiles without error; the paren group is just SQL parens
        assert "records.kind = ?" in cq.where
        assert "records.status = ?" in cq.where


# ---------------------------------------------------------------------------
# MATCH encoding
# ---------------------------------------------------------------------------

class TestMatchEncoding:
    """Exact MATCH expression strings per the LOCKED spec."""

    def test_bare_term_match(self, kql, compiler):
        ast = kql.parse("foo")
        cq = compiler.compile(ast)
        assert cq.match_expr == "foo"

    def test_phrase_match_double_quoted(self, kql, compiler):
        """Phrase 'penny worker' → match_expr = '"penny worker"' (double quotes preserved)."""
        ast = kql.parse('"penny worker"')
        cq = compiler.compile(ast)
        assert cq.match_expr == '"penny worker"'

    def test_multi_term_and_match(self, kql, compiler):
        """Two bare terms via implicit AND → match_expr = 'penny AND worker'."""
        ast = kql.parse("penny worker")
        cq = compiler.compile(ast)
        assert cq.match_expr == "penny AND worker"

    def test_explicit_and_terms_match(self, kql, compiler):
        ast = kql.parse("penny and worker")
        cq = compiler.compile(ast)
        assert cq.match_expr == "penny AND worker"

    def test_or_terms_match(self, kql, compiler):
        ast = kql.parse("penny or worker")
        cq = compiler.compile(ast)
        assert cq.match_expr == "penny OR worker"

    def test_not_fts_term_match(self, kql, compiler):
        ast = kql.parse("penny not worker")
        cq = compiler.compile(ast)
        # NOT in FTS5 boolean → NOT worker in match expr
        assert "NOT" in cq.match_expr

    def test_mixed_fts_and_facet_match(self, kql, compiler):
        """A query with both FTS term and facet: has_fts=True, match_expr only for FTS part."""
        ast = kql.parse("penny and kind:spec")
        cq = compiler.compile(ast)
        assert cq.has_fts
        assert cq.match_expr == "penny"
        # Facet part lives in WHERE
        assert "records.kind = ?" in cq.where


# ---------------------------------------------------------------------------
# Ranking selection
# ---------------------------------------------------------------------------

class TestRankingSelection:

    def test_fulltext_query_uses_bm25_order(self, kql, compiler):
        ast = kql.parse("foo")
        cq = compiler.compile(ast)
        assert "bm25(record_fts, 3.0, 2.0, 1.0)" in cq.order_by

    def test_pure_facet_uses_recency_order(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast)
        assert "updated_at" in cq.order_by
        assert "last_referenced_at" in cq.order_by
        assert "bm25" not in cq.order_by

    def test_phrase_query_uses_bm25_order(self, kql, compiler):
        ast = kql.parse('"penny worker"')
        cq = compiler.compile(ast)
        assert "bm25(record_fts, 3.0, 2.0, 1.0)" in cq.order_by

    def test_mixed_fulltext_and_facet_uses_bm25(self, kql, compiler):
        """Any full-text term in the query → bm25 ORDER BY."""
        ast = kql.parse("foo and kind:spec")
        cq = compiler.compile(ast)
        assert "bm25(record_fts, 3.0, 2.0, 1.0)" in cq.order_by


# ---------------------------------------------------------------------------
# Vault scope
# ---------------------------------------------------------------------------

class TestVaultScope:

    def test_vault_none_adds_no_predicate(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast, vault=None)
        assert "vault" not in cq.where

    def test_vault_name_adds_predicate(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast, vault="my-vault")
        assert "records.vault = ?" in cq.where
        assert "my-vault" in cq.params

    def test_vault_does_not_double_add_on_fulltext(self, kql, compiler):
        ast = kql.parse("foo")
        cq = compiler.compile(ast, vault="my-vault")
        assert cq.params.count("my-vault") == 1


# ---------------------------------------------------------------------------
# Limit
# ---------------------------------------------------------------------------

class TestLimit:

    def test_default_limit_is_20(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast)
        assert cq.limit == 20

    def test_explicit_limit_overrides(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast, limit=5)
        assert cq.limit == 5

    def test_limit_appears_in_full_query(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast, limit=7)
        sql = cq.full_query()
        assert "LIMIT" in sql.upper()


# ---------------------------------------------------------------------------
# SQL injection safety (council-flagged, REQUIRED)
# ---------------------------------------------------------------------------

class TestInjectionSafety:

    def test_scalar_value_is_bound_not_interpolated(self, kql, compiler):
        """A SQL-injection payload in a field value travels as a bind param."""
        ast = kql.parse("kind:spec")
        # Manually craft a FieldEq with a payload value (the parser would reject it
        # as an unknown value, but the compiler sees it as a plain string).
        kql_mod = load_script("kql")
        inject_ast = kql_mod.FieldEq(field="kind", value="penny' OR '1'='1")
        cq = compiler.compile(inject_ast)
        # The where clause must NOT contain the literal payload string
        assert "penny' OR '1'='1" not in cq.where
        # But it must be in params
        assert "penny' OR '1'='1" in cq.params

    def test_injection_value_returns_no_spurious_rows(self, kql, compiler, fixture_index):
        """Executing the compiled query with the injection payload returns no spurious rows."""
        conn, vault, env = fixture_index
        kql_mod = load_script("kql")
        # Craft a FieldEq with SQL injection payload
        inject_ast = kql_mod.FieldEq(field="kind", value="penny' OR '1'='1")
        cq = compiler.compile(inject_ast)
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        assert rows == [], f"injection payload returned {len(rows)} spurious rows"

    def test_facet_injection_in_value_is_bound(self, kql, compiler):
        """Facet value with injection payload is also a bind param."""
        kql_mod = load_script("kql")
        inject_ast = kql_mod.FacetMembership(facet="area", value="foo' OR '1'='1")
        cq = compiler.compile(inject_ast)
        assert "foo' OR '1'='1" not in cq.where
        assert "foo' OR '1'='1" in cq.params


# ---------------------------------------------------------------------------
# FTS MATCH sanitizer (council-flagged, REQUIRED)
# ---------------------------------------------------------------------------

class TestFtsSanitizer:
    """FTS match tokens with stray quotes/backslashes produce well-formed MATCH strings."""

    def test_bare_term_with_single_quote_is_sanitized(self, kql, compiler):
        """FullText("foo'bar") produces a well-formed match_expr (no FTS syntax error)."""
        kql_mod = load_script("kql")
        # Simulate what the parser would emit if it saw a stray-quote term
        # (the parser strips quotes, but the compiler must sanitize anyway)
        ast = kql_mod.FullText(term="foo'bar")
        cq = compiler.compile(ast)
        # match_expr must not contain a raw single quote that would break FTS
        # (single quotes in bare terms are not valid FTS5 syntax)
        assert cq.has_fts
        assert cq.match_expr  # non-empty

    def test_sanitized_term_raises_no_fts_error_on_execute(self, kql, compiler, fixture_index):
        """Executing a query with a sanitized stray-quote term raises no FTS syntax error."""
        conn, vault, env = fixture_index
        kql_mod = load_script("kql")
        ast = kql_mod.FullText(term="foo'bar")
        cq = compiler.compile(ast)
        # Must not raise sqlite3.OperationalError
        try:
            rows = conn.execute(
                "SELECT records.* FROM record_fts "
                "JOIN records ON records.rowid = record_fts.rowid "
                f"WHERE record_fts MATCH ?",
                (cq.match_expr,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"Sanitized FTS term raised OperationalError: {exc}")
        # No spurious rows (no record has 'foo'bar' in it)
        assert rows == []

    def test_phrase_with_stray_double_quote_is_sanitized(self, kql, compiler):
        """Phrase.text with stray double quote is sanitized so match_expr is well-formed."""
        kql_mod = load_script("kql")
        # Phrase text containing an embedded double quote (injection attempt)
        ast = kql_mod.Phrase(text='penny" OR "1"="1')
        cq = compiler.compile(ast)
        assert cq.has_fts
        # The match_expr must not have unmatched or stray quotes that break FTS
        assert cq.match_expr

    def test_phrase_stray_quote_raises_no_fts_error_on_execute(
        self, kql, compiler, fixture_index
    ):
        """Executing a phrase with stray quotes sanitized raises no FTS syntax error."""
        conn, vault, env = fixture_index
        kql_mod = load_script("kql")
        ast = kql_mod.Phrase(text='penny" OR "1"="1')
        cq = compiler.compile(ast)
        try:
            rows = conn.execute(
                "SELECT records.* FROM record_fts "
                "JOIN records ON records.rowid = record_fts.rowid "
                f"WHERE record_fts MATCH ?",
                (cq.match_expr,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"Phrase with stray quote raised OperationalError: {exc}")
        assert rows == []

    def test_bare_term_with_backslash_is_sanitized(self, kql, compiler, fixture_index):
        """FullText with backslash is sanitized, raises no FTS error on execute."""
        conn, vault, env = fixture_index
        kql_mod = load_script("kql")
        ast = kql_mod.FullText(term="foo\\bar")
        cq = compiler.compile(ast)
        try:
            conn.execute(
                "SELECT records.* FROM record_fts "
                "JOIN records ON records.rowid = record_fts.rowid "
                f"WHERE record_fts MATCH ?",
                (cq.match_expr,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"Backslash term raised OperationalError: {exc}")

    def test_sanitized_term_matches_no_spurious_rows(self, kql, compiler, fixture_index):
        """A sanitized injection payload in a FTS term returns no spurious rows."""
        conn, vault, env = fixture_index
        kql_mod = load_script("kql")
        # Injection attempt: try to break out of the MATCH phrase
        ast = kql_mod.FullText(term='zephyr" OR "')
        cq = compiler.compile(ast)
        try:
            rows = conn.execute(
                "SELECT records.* FROM record_fts "
                "JOIN records ON records.rowid = record_fts.rowid "
                f"WHERE record_fts MATCH ?",
                (cq.match_expr,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"FTS injection raised OperationalError: {exc}")
        # 'zephyr' IS in one record body, but 'zephyr" OR "' (as injected) must not
        # return the alpha record OR any spurious rows via injection
        # (sanitization wraps in quotes so the whole thing is one token, which won't match)
        assert len(rows) == 0, (
            f"injection payload returned {len(rows)} rows — sanitizer breakout"
        )

    def test_fts_literal_operator_in_term_is_neutralized(self, kql, compiler, fixture_index):
        """A bare term containing 'AND' or 'OR' as text is not treated as FTS operator."""
        conn, vault, env = fixture_index
        kql_mod = load_script("kql")
        # The term 'ANDING' should not be split into 'AND' + 'ING' as FTS operators.
        # But more dangerously, a term that IS a bare FTS operator shouldn't break things.
        ast = kql_mod.FullText(term="AND")
        cq = compiler.compile(ast)
        # Must not raise FTS syntax error
        try:
            conn.execute(
                "SELECT records.* FROM record_fts "
                "JOIN records ON records.rowid = record_fts.rowid "
                f"WHERE record_fts MATCH ?",
                (cq.match_expr,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"Bare 'AND' term raised OperationalError: {exc}")


# ---------------------------------------------------------------------------
# Full SELECT query shape (Slice 4 compatibility)
# ---------------------------------------------------------------------------

class TestFullQueryShape:

    def test_pure_facet_full_query_selects_from_records(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast)
        sql = cq.full_query(fts_join=False)
        assert sql.lstrip().upper().startswith("SELECT")
        assert "FROM records" in sql
        assert "LIMIT" in sql.upper()

    def test_fts_full_query_uses_join_form(self, kql, compiler):
        """full_query(fts_join=True) uses the JOIN form for bm25() reachability."""
        ast = kql.parse("foo")
        cq = compiler.compile(ast)
        sql = cq.full_query(fts_join=True)
        assert "JOIN records" in sql
        assert "record_fts MATCH" in sql
        assert "bm25(record_fts, 3.0, 2.0, 1.0)" in sql

    def test_fts_full_query_executes_against_fixture(self, kql, compiler, fixture_index):
        """A full-text query compiled and executed via the JOIN form returns rows."""
        conn, vault, env = fixture_index
        ast = kql.parse("zephyr")
        cq = compiler.compile(ast)
        sql = cq.full_query(fts_join=True)
        # The match_expr is bound in params; sql contains MATCH ? placeholder
        rows = conn.execute(sql, cq.params).fetchall()
        assert len(rows) >= 1, "expected at least one row matching 'zephyr'"

    def test_pure_facet_full_query_executes_against_fixture(
        self, kql, compiler, fixture_index
    ):
        """A pure-facet query compiled and executed returns rows from records."""
        conn, vault, env = fixture_index
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast)
        sql = cq.full_query(fts_join=False)
        rows = conn.execute(sql, cq.params).fetchall()
        # alpha is kind=spec
        assert len(rows) == 1

    def test_area_facet_query_executes_against_fixture(
        self, kql, compiler, fixture_index
    ):
        """area:penny returns the record that declared area:penny (forward edge)."""
        conn, vault, env = fixture_index
        ast = kql.parse("area:penny")
        cq = compiler.compile(ast)
        sql = cq.full_query(fts_join=False)
        rows = conn.execute(sql, cq.params).fetchall()
        assert len(rows) >= 1, "expected at least one record with area:penny"

    def test_vault_scope_narrows_results(self, kql, compiler, fixture_index):
        """vault= narrows results to that vault; wrong vault returns nothing."""
        conn, vault, env = fixture_index
        ast = kql.parse("kind:spec")
        cq_with = compiler.compile(ast, vault=str(vault))
        rows_with = conn.execute(cq_with.full_query(), cq_with.params).fetchall()
        assert len(rows_with) == 1

        cq_wrong = compiler.compile(ast, vault="nonexistent-vault")
        rows_wrong = conn.execute(cq_wrong.full_query(), cq_wrong.params).fetchall()
        assert rows_wrong == []

    def test_limit_caps_results(self, kql, compiler, fixture_index):
        """limit=1 caps results to 1 even when more rows exist."""
        conn, vault, env = fixture_index
        # All records (3 total)
        ast = kql.parse("status:active or status:draft")
        cq = compiler.compile(ast, limit=1)
        rows = conn.execute(cq.full_query(), cq.params).fetchall()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# CompiledQuery structure
# ---------------------------------------------------------------------------

class TestCompiledQueryStructure:

    def test_compiled_query_has_where_params_order_by_limit(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast)
        assert hasattr(cq, "where")
        assert hasattr(cq, "params")
        assert hasattr(cq, "order_by")
        assert hasattr(cq, "limit")
        assert hasattr(cq, "has_fts")
        assert hasattr(cq, "match_expr")

    def test_params_is_list(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast)
        assert isinstance(cq.params, list)

    def test_has_fts_false_for_pure_facet(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast)
        assert cq.has_fts is False

    def test_has_fts_true_for_fulltext(self, kql, compiler):
        ast = kql.parse("foo")
        cq = compiler.compile(ast)
        assert cq.has_fts is True

    def test_match_expr_empty_for_pure_facet(self, kql, compiler):
        ast = kql.parse("kind:spec")
        cq = compiler.compile(ast)
        assert not cq.match_expr  # empty string / None / falsy
