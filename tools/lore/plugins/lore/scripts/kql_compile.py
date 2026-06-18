"""KQL AST → SQL compiler (Slice 3, S3).

Compiles a backend-agnostic KQL AST (produced by ``kql.py``) into a parameterized
SQL fragment set for execution against the realized index schema (``index_store.py``).

No I/O, no sqlite execution, no imports from kql.py — takes AST node objects and
returns a :class:`CompiledQuery`. The caller (Slice 4) executes the query.

NOTE: This module intentionally omits ``from __future__ import annotations``.  The
``@dataclass`` machinery looks up ``cls.__module__`` in ``sys.modules`` when
annotations are stored as strings (which ``from __future__ import annotations``
forces). The ``load_script`` test harness loads this module via
``importlib.util`` without registering it in ``sys.modules``, so string-annotation
mode would crash (same gotcha as ``kql.py``, Slice 2 contract).

**SELECT shape for Slice 4 (LOCKED):**

When the query has full-text terms (``has_fts=True``), Slice 4 MUST execute:

  SELECT records.* FROM record_fts
  JOIN records ON records.rowid = record_fts.rowid
  WHERE record_fts MATCH ?
    AND <other predicates on records.*>
  ORDER BY bm25(record_fts, 3.0, 2.0, 1.0)
  LIMIT ?

The ``MATCH ?`` placeholder receives ``match_expr`` as the first bind param; the
remaining bind params follow in ``params`` order.

For pure-facet queries (``has_fts=False``), Slice 4 executes:

  SELECT * FROM records
  WHERE <predicates>
  ORDER BY updated_at DESC, last_referenced_at DESC
  LIMIT ?

Use ``CompiledQuery.full_query(fts_join=has_fts)`` to obtain the assembled SQL.

**Alias resolution (LOCKED):**
  ``area`` → ``related-area``
  ``phase`` → ``related-phases``
  ``keyword`` → ``keywords``
  Real keys (``related-area``, ``related-phases``, ``keywords``) pass through unchanged.

**Scalar field → column mapping (LOCKED):**
  ``kind`` → ``kind``,  ``status`` → ``status``,  ``repo`` → ``repo``,
  ``team`` → ``team``,  ``product`` → ``product``,  ``suite`` → ``suite``

**Comparison field → column mapping (LOCKED):**
  ``created-at`` → ``created_at``
  ``updated-at`` → ``updated_at``
  ``last-referenced-at`` → ``last_referenced_at``

**Ranking (LOCKED — sign proven in Slice 0, KU3):**
  Full-text query → ``ORDER BY bm25(record_fts, 3.0, 2.0, 1.0)``
  (bm25 returns negative values; no explicit ASC/DESC means ASC = best-first)
  Pure-facet query → ``ORDER BY updated_at DESC, last_referenced_at DESC``

**Scope (LOCKED):**
  ``vault=X`` → ``AND records.vault = ?`` bound
  ``vault=None`` → no vault predicate (all vaults)

**Security — injection defense (council-flagged, REQUIRED):**

  *Scalar WHERE values:* ALL scalar field values, facet values, comparison values, and
  the vault param are passed as BIND PARAMS — never string-interpolated. SQL injection
  via any field value is therefore structurally impossible in the compiled WHERE clause.

  *Comparison op:* the ``op`` on a ``Compare`` node comes from the TYPED AST — a fixed
  enum ``{">=", "<=", ">", "<"}`` set at parse time. It is emitted literally (not
  user-controlled free text at compile time), so literal emission is injection-safe.
  An assertion guards this invariant.

  *FTS MATCH expression:* the MATCH expression string is string-constructed (SQLite
  FTS5 ``MATCH`` takes a single string argument; we bind it as a ``?`` param so the
  full expression is a bind param). However, the *internals* of that string are
  constructed from parser-supplied tokens which may contain stray ``'``, ``"``, ``\\``,
  or literal FTS operators (``AND``/``OR``/``NEAR(...)``), per the Slice 2 contract.

  **FTS token sanitizer rule:**

  Two sanitizer functions are used — one for bare terms (``FullText`` nodes), one for
  phrase text (``Phrase`` nodes):

  ``_sanitize_bare_term(raw)`` — for ``FullText`` tokens:
  1. Strip all backslash characters (``\\`` has no role in FTS5 expressions).
  2. Strip all double-quote characters (``"``).
  3. If the cleaned result is equal (case-insensitively) to a reserved FTS5 operator
     (``AND``, ``OR``, ``NOT``), OR if the original contained any of the stripped
     special characters, wrap in double quotes to form a quoted FTS5 phrase.
     A quoted FTS5 term is treated as a literal single token and cannot be
     interpreted as a boolean operator.
  4. Otherwise, emit the cleaned result as-is (a clean bare term like ``foo`` stays
     ``foo`` per the LOCKED MATCH encoding spec).

  ``_sanitize_phrase_text(raw)`` — for ``Phrase`` tokens:
  1. Strip all backslash characters.
  2. Strip all double-quote characters.
  3. Always wrap in double quotes → ``"<cleaned>"`` so the result is a paired FTS5
     adjacent-phrase expression.

  This means:
  - ``foo`` (bare term, clean) → ``foo``
  - ``foo'bar`` (bare term, has single quote) → ``"foo'bar"``  (quoted)
  - ``foo"bar`` (bare term, has double quote) → ``"foobar"``   (stripped + quoted)
  - ``foo\\bar`` (bare term, has backslash) → ``"foobar"``    (stripped + quoted)
  - ``AND`` (bare FTS operator as a term) → ``"AND"``         (quoted, literal)
  - ``penny worker`` (phrase text) → ``"penny worker"``
  - ``penny" OR "1"="1`` (phrase text, injection) → ``"penny OR 11"`` (quotes stripped)

  Sanitized tokens may match fewer rows when stray chars are stripped, but they
  NEVER raise an FTS syntax error and NEVER return spurious rows via injection.
"""

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Alias + column maps
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

# Facet names that are real index keys (pass through without alias lookup)
_REAL_FACET_KEYS = frozenset({"related-area", "related-phases", "keywords"})
# Facet names recognized as aliases (resolved before compile)
_FACET_ALIASES = frozenset(_FACET_ALIAS_MAP.keys())


# ---------------------------------------------------------------------------
# FTS token sanitizers
# ---------------------------------------------------------------------------

_FTS5_RESERVED_OPS = frozenset({"AND", "OR", "NOT"})


def _sanitize_bare_term(raw: str) -> str:
    """Sanitize a bare FullText term token for inclusion in a MATCH expression.

    A clean token (no backslashes, no double quotes, no single quotes) is emitted
    as-is so that ``foo`` compiles to the MATCH string ``foo`` per the LOCKED spec.
    Dirty tokens are stripped of ``\\`` and ``"`` (which break FTS5 syntax or phrase
    boundaries), then wrapped in double quotes so they are treated as FTS5 literal
    phrase terms rather than boolean operators.

    Rules:
      1. Strip all backslash characters (no role in FTS5).
      2. Strip all double-quote characters.
      3. If the original contained any special chars that break bare FTS5 terms
         (``\\``, ``"``, or ``'`` — a bare single quote is a syntax error in FTS5),
         OR if the cleaned result (case-insensitively) equals a reserved FTS5
         operator (``AND`` / ``OR`` / ``NOT``), wrap the cleaned result in double
         quotes.  Single quotes inside a double-quoted FTS5 phrase are inert.
      4. Otherwise, emit the cleaned result bare.

    Examples:
      ``foo``      → ``foo``       (clean; emitted as-is)
      ``foo'bar``  → ``"foo'bar"`` (single quote; quoted so FTS5 treats as literal)
      ``foo"bar``  → ``"foobar"``  (stray double quote stripped, then quoted)
      ``foo\\bar`` → ``"foobar"``  (backslash stripped, then quoted)
      ``AND``      → ``"AND"``     (reserved FTS op; quoted to treat as literal)
    """
    has_special = "\\" in raw or '"' in raw or "'" in raw
    cleaned = raw.replace("\\", "").replace('"', "")
    if has_special or cleaned.upper() in _FTS5_RESERVED_OPS:
        return f'"{cleaned}"'
    return cleaned


def _sanitize_phrase_text(raw: str) -> str:
    """Sanitize a Phrase.text token and wrap it in double quotes.

    A Phrase node always produces a ``"..."`` FTS5 adjacent-phrase expression.
    Stray backslashes and double quotes inside the text are stripped so they cannot
    alter phrase boundaries or break the outer quoting.

    Rules:
      1. Strip all backslash characters.
      2. Strip all double-quote characters.
      3. Wrap the result in double quotes → ``"<cleaned>"``.

    Examples:
      ``penny worker``        → ``"penny worker"``
      ``penny" OR "1"="1``    → ``"penny OR 11"``
      ``foo\\bar``            → ``"foobar"``
    """
    cleaned = raw.replace("\\", "").replace('"', "")
    return f'"{cleaned}"'


# ---------------------------------------------------------------------------
# CompiledQuery
# ---------------------------------------------------------------------------

@dataclass
class CompiledQuery:
    """Output of ``compile(ast)``.

    Attributes:
        where:      The SQL WHERE clause fragment (without the ``WHERE`` keyword).
                    May contain ``record_fts MATCH ?`` when ``has_fts`` is True.
        params:     Ordered list of bind params corresponding to ``?`` placeholders
                    in ``where`` (and in ``full_query``).
        order_by:   The ORDER BY clause string (without the ``ORDER BY`` keyword).
        limit:      The LIMIT value (int).
        has_fts:    True when the query contains at least one full-text node
                    (FullText or Phrase).  Slice 4 uses this to choose the JOIN form.
        match_expr: The assembled FTS5 MATCH expression string (empty string when
                    ``has_fts`` is False).  This is bound as a ``?`` param — it is
                    already the first element of ``params`` when ``has_fts`` is True.
    """

    where: str
    params: list
    order_by: str
    limit: int
    has_fts: bool
    match_expr: str

    def full_query(self, *, fts_join: bool = False) -> str:
        """Assemble the full SELECT statement.

        Args:
            fts_join: When True, use the FTS JOIN form (required when ``has_fts``
                      is True so that ``bm25(record_fts, ...)`` is reachable in
                      ORDER BY).  When False, use the plain ``FROM records`` form.

        Returns:
            A SQL string with ``?`` placeholders matching ``self.params``.
        """
        if fts_join:
            base = (
                "SELECT records.* FROM record_fts "
                "JOIN records ON records.rowid = record_fts.rowid"
            )
        else:
            base = "SELECT * FROM records"

        parts = [base]
        if self.where:
            parts.append(f"WHERE {self.where}")
        parts.append(f"ORDER BY {self.order_by}")
        parts.append(f"LIMIT {self.limit}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Internal compiler state
# ---------------------------------------------------------------------------

class _Compiler:
    """Stateful compiler that walks the AST and builds WHERE + FTS components.

    Keeps two separate "channels":
      - ``_sql_parts`` / ``_sql_params``: SQL predicates on scalar/facet columns
      - ``_fts_expr``: the assembled FTS5 MATCH expression (as a string)

    The FTS expression mirrors the boolean structure of the full-text portion of
    the AST.  Scalar predicates never appear in the FTS expression; FTS terms never
    appear in the SQL WHERE (they are collapsed into ``record_fts MATCH ?`` instead).

    Strategy:
      Walk the AST recursively.  Each node returns a tuple ``(sql_frag, fts_frag)``
      where one or both may be empty.  Boolean nodes (And/Or/Not/Group) combine
      sub-results appropriately.
    """

    def _compile_node(self, node) -> tuple[str, list, str]:
        """Compile a node, returning (sql_frag, sql_params, fts_frag).

        sql_frag:   SQL predicate for this subtree (empty string if pure FTS).
        sql_params: Bind params for sql_frag.
        fts_frag:   FTS5 MATCH sub-expression for this subtree (empty if no FTS).
        """
        type_name = type(node).__name__

        if type_name == "FieldEq":
            return self._compile_field_eq(node)
        if type_name == "FacetMembership":
            return self._compile_facet_membership(node)
        if type_name == "FullText":
            return self._compile_fulltext(node)
        if type_name == "Phrase":
            return self._compile_phrase(node)
        if type_name == "Compare":
            return self._compile_compare(node)
        if type_name == "And":
            return self._compile_and(node)
        if type_name == "Or":
            return self._compile_or(node)
        if type_name == "Not":
            return self._compile_not(node)
        if type_name == "Group":
            return self._compile_group(node)

        raise ValueError(f"unknown AST node type: {type_name!r}")

    # -- leaf nodes ----------------------------------------------------------

    def _compile_field_eq(self, node) -> tuple[str, list, str]:
        col = _SCALAR_COL_MAP.get(node.field)
        if col is None:
            # Compare fields used with equality (non-standard but passthrough)
            col = _COMPARE_COL_MAP.get(node.field, node.field)
        return f"records.{col} = ?", [node.value], ""

    def _compile_facet_membership(self, node) -> tuple[str, list, str]:
        # Resolve alias first
        facet = _FACET_ALIAS_MAP.get(node.facet, node.facet)
        sql = (
            "EXISTS (SELECT 1 FROM record_facet f "
            "WHERE f.id = records.id AND f.facet = ? AND f.value = ?)"
        )
        return sql, [facet, node.value], ""

    def _compile_fulltext(self, node) -> tuple[str, list, str]:
        sanitized = _sanitize_bare_term(node.term)
        return "", [], sanitized

    def _compile_phrase(self, node) -> tuple[str, list, str]:
        sanitized = _sanitize_phrase_text(node.text)
        return "", [], sanitized

    def _compile_compare(self, node) -> tuple[str, list, str]:
        op = node.op
        assert op in _VALID_OPS, (
            f"Compare.op must be one of {sorted(_VALID_OPS)} — got {op!r}. "
            "This is a typed AST value set by the parser, never user free-text."
        )
        col = _COMPARE_COL_MAP.get(node.field)
        if col is None:
            col = _SCALAR_COL_MAP.get(node.field, node.field)
        return f"records.{col} {op} ?", [node.value], ""

    # -- boolean nodes -------------------------------------------------------

    def _compile_and(self, node) -> tuple[str, list, str]:
        ls, lp, lf = self._compile_node(node.left)
        rs, rp, rf = self._compile_node(node.right)
        return _combine_sql(ls, rs, "AND"), lp + rp, _combine_fts(lf, rf, "AND")

    def _compile_or(self, node) -> tuple[str, list, str]:
        ls, lp, lf = self._compile_node(node.left)
        rs, rp, rf = self._compile_node(node.right)
        return _combine_sql(ls, rs, "OR"), lp + rp, _combine_fts(lf, rf, "OR")

    def _compile_not(self, node) -> tuple[str, list, str]:
        inner_sql, inner_params, inner_fts = self._compile_node(node.operand)

        sql_frag = f"NOT ({inner_sql})" if inner_sql else ""
        fts_frag = f"NOT {inner_fts}" if inner_fts else ""
        return sql_frag, inner_params, fts_frag

    def _compile_group(self, node) -> tuple[str, list, str]:
        inner_sql, inner_params, inner_fts = self._compile_node(node.inner)
        # Wrap SQL in parens (structural grouping)
        sql_frag = f"({inner_sql})" if inner_sql else ""
        # FTS side: FTS5 already handles sub-expression grouping via its own parens,
        # but since we're building a flat FTS expression, we parenthesize here too.
        fts_frag = f"({inner_fts})" if inner_fts else ""
        return sql_frag, inner_params, fts_frag


# ---------------------------------------------------------------------------
# Helpers for combining SQL/FTS fragments
# ---------------------------------------------------------------------------

def _combine_sql(left_sql: str, right_sql: str, op: str) -> str:
    """Combine two SQL fragments with a boolean operator.

    If one side is empty (pure FTS node), the other side is returned as-is.
    """
    if left_sql and right_sql:
        return f"({left_sql}) {op} ({right_sql})"
    return left_sql or right_sql


def _combine_fts(left_fts: str, right_fts: str, op: str) -> str:
    """Combine two FTS expression fragments with a boolean operator.

    If one side is empty (pure SQL node), the other side is returned as-is.
    """
    if left_fts and right_fts:
        return f"{left_fts} {op} {right_fts}"
    return left_fts or right_fts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile(ast, *, vault: str | None = None, limit: int = 20) -> CompiledQuery:
    """Compile a KQL AST to a parameterized SQL fragment set.

    Args:
        ast:   Root AST node produced by ``kql.parse()``.
        vault: When provided, add ``records.vault = ?`` to the WHERE clause;
               ``None`` adds no vault predicate (all vaults).
        limit: Maximum rows to return (default 20, per spec lock).

    Returns:
        A :class:`CompiledQuery` carrying the WHERE fragment, ordered bind params,
        ORDER BY clause, LIMIT, ``has_fts`` flag, and ``match_expr`` string.
    """
    compiler = _Compiler()
    sql_frag, params, fts_frag = compiler._compile_node(ast)

    has_fts = bool(fts_frag)

    # Build the final WHERE clause.
    # When has_fts, inject the MATCH predicate into the WHERE clause so the FTS
    # filter applies during execution.  The match_expr is bound as a ? param (first
    # in the params list) so it is never string-interpolated into the SQL.
    where_parts = []
    final_params: list = []

    if has_fts:
        where_parts.append("record_fts MATCH ?")
        final_params.append(fts_frag)  # match_expr bound as first param

    if sql_frag:
        where_parts.append(sql_frag)
        final_params.extend(params)
    else:
        # No SQL predicates (pure FTS query) — params is empty
        pass

    if vault is not None:
        where_parts.append("records.vault = ?")
        final_params.append(vault)

    where = " AND ".join(where_parts) if where_parts else "1"

    # ORDER BY
    if has_fts:
        order_by = "bm25(record_fts, 3.0, 2.0, 1.0)"
    else:
        order_by = "updated_at DESC, last_referenced_at DESC"

    return CompiledQuery(
        where=where,
        params=final_params,
        order_by=order_by,
        limit=limit,
        has_fts=has_fts,
        match_expr=fts_frag,
    )
