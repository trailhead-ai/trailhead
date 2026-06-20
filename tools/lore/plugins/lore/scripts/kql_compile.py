"""KQL AST → SQL compiler (Slice 3, S3 — review redesign).

Compiles a backend-agnostic KQL AST (produced by ``kql.py``) into a parameterized
SQL fragment set for execution against the realized index schema (``index_store.py``).

No I/O, no sqlite execution, no imports from kql.py — takes AST node objects and
returns a :class:`CompiledQuery`. The caller (Slice 4) executes the query.

NOTE: This module intentionally omits ``from __future__ import annotations``.  The
``@dataclass`` machinery looks up ``cls.__module__`` in ``sys.modules`` when
annotations are stored as strings (which ``from __future__ import annotations``
forces). The ``load_script`` test harness loads this module via ``importlib.util``
without registering it in ``sys.modules``, so string-annotation mode would crash
(same gotcha as ``kql.py``, Slice 2 contract).

**Full-text is a SQL-composable predicate (review redesign — CRITICAL #1).**

The earlier design treated FTS ``MATCH`` and SQL ``WHERE`` as two independent
channels rejoined with a hard-coded top-level ``AND``. That broke boolean
composition: ``not foo`` produced a leading-``NOT`` MATCH (FTS5 syntax error) and
``foo or kind:spec`` silently dropped the OR (too-narrow result set). The fix:
compile each ``FullText(term)`` / ``Phrase(text)`` node to a SQL predicate

    records.rowid IN (SELECT rowid FROM record_fts WHERE record_fts MATCH ?)

with the sanitized single-term/phrase MATCH string as a BOUND param. ``And``/``Or``/
``Not``/``Group`` then compose these full-text predicates and the scalar/facet
predicates UNIFORMLY as SQL boolean (``(L) AND (R)``, ``(L) OR (R)``, ``NOT (X)``).
Every combination is now correct:
  - ``foo or kind:spec`` → ``(rowid IN (… MATCH ?)) OR (records.kind = ?)``
  - ``not foo``          → ``NOT (rowid IN (… MATCH ?))`` (no FTS syntax error)
  - ``kind:spec not foo`` → ``(records.kind = ?) AND (NOT (rowid IN (… MATCH ?)))``

**FINAL exact SELECT shape Slice 4 executes (LOCKED — no mandatory FTS JOIN):**

  SELECT * FROM records
  WHERE <predicates — full-text predicates live inline in the tree>
  ORDER BY <ranking>
  LIMIT ?

Use ``CompiledQuery.full_query()`` to obtain the assembled SQL; it is positionally
aligned with ``CompiledQuery.params``.

**Ranking (LOCKED — bm25 weights + sign proven in Slice 0, KU3).**

When the query carries ANY full-text term, order by bm25 with the locked weights
``(3.0, 2.0, 1.0)`` for ``(title, keywords, body)``, best-first = ASC (bm25 is
negative). Because full-text is now in WHERE (not a filtering JOIN), the score is
computed with a correlated subquery over the OR-combined POSITIVE (non-negated)
full-text terms:

  ORDER BY (
    SELECT bm25(record_fts, 3.0, 2.0, 1.0)
    FROM record_fts
    WHERE record_fts.rowid = records.rowid AND record_fts MATCH ?
  ) IS NULL, (
    SELECT bm25(record_fts, 3.0, 2.0, 1.0)
    FROM record_fts
    WHERE record_fts.rowid = records.rowid AND record_fts MATCH ?
  ) ASC, updated_at DESC, last_referenced_at DESC

The ``IS NULL`` leading key pushes rows that do not match the ranking expression
(NULL score) AFTER the matched (negative) rows; ``updated_at DESC,
last_referenced_at DESC`` is a stable tiebreak. A PURE-FACET query (no full-text
node at all) orders by ``updated_at DESC, last_referenced_at DESC`` (no bm25).

**Bound-param ORDER (positionally aligned with full_query()):**
  1. WHERE-clause params in AST tree order (each scalar/facet value AND each
     full-text MATCH string).
  2. The ranking-subquery MATCH param (the OR-combined positive-full-text expr) —
     emitted TWICE because the ORDER BY references it in both the ``IS NULL`` key
     and the ``ASC`` key. Only present when the query has full-text.
  3. The ``LIMIT ?`` value, always last.

**Alias resolution (LOCKED):**
  ``area`` → ``related-area``,  ``phase`` → ``related-phases``,  ``keyword`` → ``keywords``
  Real keys (``related-area``, ``related-phases``, ``keywords``) pass through unchanged.

**Indexed-label selectors (LOCKED — Slice 4):**
  ``LabelEq(key, value)`` → ``EXISTS(SELECT 1 FROM record_labels WHERE id=records.id
  AND key=? AND value=?)``;  ``LabelExists(key)`` → the same EXISTS without the
  ``value`` predicate. BOTH ``key`` and ``value`` are BIND params — never
  interpolated (the dot-for-slash key decoding happens in ``kql.py``, so ``key`` is
  already the real stored key). ``annotations`` have NO selector.

**Scalar field → column mapping (LOCKED):**
  ``kind``, ``status``, ``repo``, ``team``, ``product``, ``suite`` (identity).

**Comparison field → column mapping (LOCKED):**
  ``created-at`` → ``created_at``,  ``updated-at`` → ``updated_at``,
  ``last-referenced-at`` → ``last_referenced_at``.

**Scope (LOCKED):**
  ``vault=X`` → ``AND records.vault = ?`` bound;  ``vault=None`` → no vault predicate.

**Security — injection defense (council-flagged, REQUIRED):**

  *Scalar WHERE values* (field/facet/comparison values, vault, LIMIT) are BIND
  PARAMS — never string-interpolated. SQL injection via any value is structurally
  impossible in the compiled SQL. Column names come ONLY from the fixed allowlist
  maps; an unmapped field is a generic ``ValueError`` (no reflected token).

  *Comparison op* is validated against the fixed ``{">=","<=",">","<"}`` set with a
  plain ``if … raise`` (NOT ``assert`` — ``assert`` is stripped under ``python -O``).
  The error message is generic (no reflected ``op`` value → no log injection).

  *FTS MATCH strings* are bound as ``?`` params, but the FTS5 ``MATCH`` mini-language
  interprets the string contents, so each token is run through a STRICT ALLOWLIST
  sanitizer: a token that does not fully match ``^[-A-Za-z0-9._]+$`` (or that is a
  reserved bare operator ``AND``/``OR``/``NOT``) is wrapped as a quoted FTS5 string
  literal (``"…"`` with internal ``"`` stripped) so it is treated as a literal token,
  never as FTS5 syntax (``*``, ``(``, ``)``, ``NEAR(…)``, ``^``, ``col:`` filter,
  prefix ``foo*`` all become inert literals). Phrase text is ALWAYS wrapped in
  ``"…"``. A token that is empty after sanitizing raises a generic compile error
  rather than silently matching nothing.
"""

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Alias + column maps (the ONLY source of SQL column names — no raw fallthrough)
# ---------------------------------------------------------------------------

_FACET_ALIAS_MAP = {
    "area": "related-area",
    "phase": "related-phases",
    "keyword": "keywords",
}

_SCALAR_COL_MAP = {
    "kind": "kind",
    "status": "status",
    "repo": "repo",
    "team": "team",
    "product": "product",
    "suite": "suite",
}

_COMPARE_COL_MAP = {
    "created-at": "created_at",
    "updated-at": "updated_at",
    "last-referenced-at": "last_referenced_at",
}

_VALID_OPS = frozenset({">=", "<=", ">", "<"})


# ---------------------------------------------------------------------------
# FTS token sanitizers (strict allowlist)
# ---------------------------------------------------------------------------

# A token that fully matches this is a safe bare FTS5 term (unicode61 splits on
# hyphen/dot/underscore so these are inert as far as the MATCH grammar goes).
_FTS_BARE_SAFE = re.compile(r"^[-A-Za-z0-9._]+$")

# Reserved bare FTS5 operators — must be wrapped even though they pass the regex.
_FTS5_RESERVED_OPS = frozenset({"AND", "OR", "NOT"})


def _clean_for_literal(raw: str) -> str:
    """Strip characters that cannot live inside an FTS5 quoted literal.

    Interior double quotes would terminate the literal; backslashes have no role in
    FTS5 — both are removed so what remains is inert literal text.
    """
    return raw.replace('"', "").replace("\\", "")


def _sanitize_bare_term(raw: str) -> str:
    """Sanitize a bare ``FullText`` term into a single-token MATCH string.

    Strict allowlist: a token that fully matches ``^[-A-Za-z0-9._]+$`` and is not a
    reserved bare operator is emitted as-is (so ``foo`` → ``foo`` per the LOCKED
    MATCH encoding). Anything else is wrapped as a quoted FTS5 string literal so it
    is treated as a literal token, never FTS5 syntax. Raises ``ValueError`` on a
    token that is empty after stripping interior quotes (generic message — no
    reflected raw token).
    """
    if _FTS_BARE_SAFE.match(raw) and raw.upper() not in _FTS5_RESERVED_OPS:
        return raw
    cleaned = _clean_for_literal(raw)
    if not cleaned.strip():
        raise ValueError("empty full-text term after sanitizing")
    return f'"{cleaned}"'


def _sanitize_phrase_text(raw: str) -> str:
    """Sanitize a ``Phrase`` text token — ALWAYS a quoted FTS5 phrase literal.

    Interior double quotes are stripped so they cannot break the outer quoting.
    Raises ``ValueError`` if the phrase is empty after stripping (generic message).
    """
    cleaned = _clean_for_literal(raw)
    if not cleaned.strip():
        raise ValueError("empty phrase after sanitizing")
    return f'"{cleaned}"'


# ---------------------------------------------------------------------------
# CompiledQuery
# ---------------------------------------------------------------------------

@dataclass
class CompiledQuery:
    """Output of ``compile(ast)``.

    Attributes:
        where:      The SQL WHERE clause fragment (without the ``WHERE`` keyword).
                    Full-text predicates (``records.rowid IN (SELECT rowid FROM
                    record_fts WHERE record_fts MATCH ?)``) are inline in the tree.
        params:     Ordered bind params, positionally aligned with ``full_query()``:
                    WHERE params (tree order incl. each MATCH string), then the
                    ranking-subquery MATCH param (twice, only when ``has_fts``),
                    then the LIMIT value.
        order_by:   The ORDER BY clause string (without the ``ORDER BY`` keyword).
        limit:      The LIMIT value (int).
        has_fts:    True when the query contains at least one full-text node.
        rank_match: The OR-combined POSITIVE full-text MATCH expression used by the
                    bm25 ranking subquery (empty string for a pure-facet query).
    """

    where: str
    params: list
    order_by: str
    limit: int
    has_fts: bool
    rank_match: str

    def full_query(self) -> str:
        """Assemble the full SELECT statement (FROM records — no mandatory join).

        Returns a SQL string with ``?`` placeholders positionally aligned with
        ``self.params``.
        """
        parts = ["SELECT * FROM records"]
        if self.where:
            parts.append(f"WHERE {self.where}")
        parts.append(f"ORDER BY {self.order_by}")
        parts.append("LIMIT ?")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Internal compiler state
# ---------------------------------------------------------------------------

_FTS_PREDICATE = (
    "records.rowid IN (SELECT rowid FROM record_fts WHERE record_fts MATCH ?)"
)


class _Compiler:
    """Walks the AST and builds a single uniform SQL predicate channel.

    Each ``_compile_*`` returns ``(sql_frag, params)``. Full-text nodes compile to
    the ``_FTS_PREDICATE`` with their sanitized MATCH string as a bound param, so
    the boolean nodes (And/Or/Not/Group) compose them with scalar/facet predicates
    uniformly. Positive (non-negated) full-text MATCH strings are collected in
    ``self.positive_fts`` for the bm25 ranking subquery; ``self._negated`` tracks
    whether the current subtree sits under an odd number of ``Not`` nodes.
    """

    def __init__(self):
        self.positive_fts: list = []
        self._negated = False

    def _compile_node(self, node) -> tuple:
        type_name = type(node).__name__
        handler = {
            "FieldEq": self._compile_field_eq,
            "FacetMembership": self._compile_facet_membership,
            "LabelEq": self._compile_label_eq,
            "LabelExists": self._compile_label_exists,
            "FullText": self._compile_fulltext,
            "Phrase": self._compile_phrase,
            "Compare": self._compile_compare,
            "And": self._compile_and,
            "Or": self._compile_or,
            "Not": self._compile_not,
            "Group": self._compile_group,
        }.get(type_name)
        if handler is None:
            raise ValueError("unknown AST node type")
        return handler(node)

    # -- leaf nodes ----------------------------------------------------------

    def _compile_field_eq(self, node) -> tuple:
        col = _SCALAR_COL_MAP.get(node.field)
        if col is None:
            col = _COMPARE_COL_MAP.get(node.field)
        if col is None:
            # Columns come ONLY from the fixed allowlist maps — no raw fallthrough
            # (structural injection guard). Generic message: no reflected field.
            raise ValueError("unknown field")
        return f"records.{col} = ?", [node.value]

    def _compile_facet_membership(self, node) -> tuple:
        facet = _FACET_ALIAS_MAP.get(node.facet, node.facet)
        # NOTE: ``facet`` is passed as a BIND param (``f.facet = ?``) — it is a VALUE,
        # not a SQL column name, so a facet name need not be allowlisted (unlike the
        # column-name fallthroughs above, which were removed). Do not "fix" this into
        # an allowlist lookup: an unmapped facet name is a safe literal here.
        sql = (
            "EXISTS (SELECT 1 FROM record_facet f "
            "WHERE f.id = records.id AND f.facet = ? AND f.value = ?)"
        )
        return sql, [facet, node.value]

    def _compile_label_eq(self, node) -> tuple:
        # NOTE: ``key`` and ``value`` are BIND params (``key = ? AND value = ?``) —
        # both are VALUES, never SQL column names, so a label key/value containing
        # SQL metachars is structurally inert (mirrors FacetMembership). Do NOT
        # string-interpolate either — this is a hard security requirement.
        sql = (
            "EXISTS (SELECT 1 FROM record_labels "
            "WHERE id = records.id AND key = ? AND value = ?)"
        )
        return sql, [node.key, node.value]

    def _compile_label_exists(self, node) -> tuple:
        # ``key`` is a BIND param (existence-only — no value predicate).
        sql = (
            "EXISTS (SELECT 1 FROM record_labels "
            "WHERE id = records.id AND key = ?)"
        )
        return sql, [node.key]

    def _compile_fulltext(self, node) -> tuple:
        match_str = _sanitize_bare_term(node.term)
        if not self._negated:
            self.positive_fts.append(match_str)
        return _FTS_PREDICATE, [match_str]

    def _compile_phrase(self, node) -> tuple:
        match_str = _sanitize_phrase_text(node.text)
        if not self._negated:
            self.positive_fts.append(match_str)
        return _FTS_PREDICATE, [match_str]

    def _compile_compare(self, node) -> tuple:
        op = node.op
        if op not in _VALID_OPS:
            # Generic message — must NOT reflect the raw op value (log injection).
            # Plain `if`, not `assert` (assert is stripped under `python -O`).
            raise ValueError("invalid comparison operator")
        col = _COMPARE_COL_MAP.get(node.field)
        if col is None:
            col = _SCALAR_COL_MAP.get(node.field)
        if col is None:
            raise ValueError("unknown field")
        return f"records.{col} {op} ?", [node.value]

    # -- boolean nodes -------------------------------------------------------

    def _compile_and(self, node) -> tuple:
        ls, lp = self._compile_node(node.left)
        rs, rp = self._compile_node(node.right)
        return _combine_sql(ls, rs, "AND"), lp + rp

    def _compile_or(self, node) -> tuple:
        ls, lp = self._compile_node(node.left)
        rs, rp = self._compile_node(node.right)
        return _combine_sql(ls, rs, "OR"), lp + rp

    def _compile_not(self, node) -> tuple:
        prev = self._negated
        self._negated = not prev
        inner_sql, inner_params = self._compile_node(node.operand)
        self._negated = prev
        sql_frag = f"NOT ({inner_sql})" if inner_sql else ""
        return sql_frag, inner_params

    def _compile_group(self, node) -> tuple:
        inner_sql, inner_params = self._compile_node(node.inner)
        sql_frag = f"({inner_sql})" if inner_sql else ""
        return sql_frag, inner_params


def _combine_sql(left_sql: str, right_sql: str, op: str) -> str:
    """Combine two SQL fragments with a boolean operator, each side parenthesized."""
    if left_sql and right_sql:
        return f"({left_sql}) {op} ({right_sql})"
    return left_sql or right_sql


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile(ast, *, vault=None, limit=20) -> CompiledQuery:
    """Compile a KQL AST to a parameterized SQL fragment set.

    Args:
        ast:   Root AST node produced by ``kql.parse()``.
        vault: When provided, add ``records.vault = ?`` to the WHERE; ``None`` adds
               no vault predicate (all vaults).
        limit: Maximum rows to return (default 20, per spec lock). Coerced to int.

    Returns:
        A :class:`CompiledQuery` whose ``params`` is positionally aligned with
        ``full_query()`` (WHERE params, then the rank-subquery MATCH param ×2 when
        full-text is present, then the LIMIT value).
    """
    limit = int(limit)

    compiler = _Compiler()
    sql_frag, where_params = compiler._compile_node(ast)

    has_fts = bool(compiler.positive_fts)

    where_parts = []
    final_params: list = []

    if sql_frag:
        where_parts.append(sql_frag)
        final_params.extend(where_params)

    if vault is not None:
        where_parts.append("records.vault = ?")
        final_params.append(vault)

    where = " AND ".join(where_parts) if where_parts else "1"

    if has_fts:
        rank_match = " OR ".join(compiler.positive_fts)
        bm25_subquery = (
            "(SELECT bm25(record_fts, 3.0, 2.0, 1.0) FROM record_fts "
            "WHERE record_fts.rowid = records.rowid AND record_fts MATCH ?)"
        )
        # NULL (unmatched) rows sort LAST; matched (negative) rows sort ASC = best
        # first; recency tiebreak makes the order stable. The MATCH param appears
        # twice (the IS NULL key and the ASC key), so it is appended twice.
        order_by = (
            f"{bm25_subquery} IS NULL, {bm25_subquery} ASC, "
            "updated_at DESC, last_referenced_at DESC"
        )
        final_params.append(rank_match)
        final_params.append(rank_match)
    else:
        rank_match = ""
        order_by = "updated_at DESC, last_referenced_at DESC"

    final_params.append(limit)

    return CompiledQuery(
        where=where,
        params=final_params,
        order_by=order_by,
        limit=limit,
        has_fts=has_fts,
        rank_match=rank_match,
    )
