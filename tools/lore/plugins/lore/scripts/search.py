"""``lore search`` executor + renderer (Slice 4, S3).

The query path for the KQL-subset facade: parse → compile → execute ONE SQL
query over the global derived index → assemble → render. This module is a
**PURE READER** of the index — it never writes, mutates, rebuilds, or repairs the
index, and never bumps ``last-referenced-at``. It opens the index read-only-in-
spirit (no INSERT/UPDATE/DELETE, no commit) and closes it.

**Pipeline:**
1. ``kql.parse(query)`` → AST (raises ``KqlParseError`` on bad input).
2. ``kql_compile.compile(ast, vault=…, limit=…)`` → ``CompiledQuery``
   (raises ``ValueError`` on an uncompilable node).
3. ``index_store.open_index(env=…)`` → connection.
4. ``conn.execute(cq.full_query(), cq.params)`` → the hit rows (ONE query).
5. Per-hit body excerpt read from ``record_fts.body`` by rowid (body is NOT a
   ``records`` column — stay index-only; never read the vault ``.md`` files).
6. Render — human banner or ``--json``.

**Injection-defense output (airtight, A-3).** Each hit's ``layer`` (read straight
from the index column — never re-resolved) decides wrapping. ``_classify_layer``
maps the raw index value to exactly ``"personal"`` or ``"shared"`` using a
**fail-safe default**: only the exact string ``"personal"`` is unfenced; any other
value (empty, None, unknown, wrong case) maps to ``"shared"`` (fenced). This
prevents a corrupt or non-standard layer value from leaking shared content unfenced.
``shared``-layer hits — INCLUDING their snippets — are emitted inside
``<external-memory layer="shared" source="…">`` via ``xml_escape.wrap_shared`` so
a literal ``</external-memory>`` in shared content/snippet is entity-escaped and
cannot break out or spoof the fence. ``personal``-layer hits are unfenced. Personal
and shared hits go in clearly delimited, NON-interleaved blocks (all personal first;
all shared inside the fence, grouped by source vault).

**Error-path escape (council Critical).** Any reflected query token echoed in a
hard-error message is XML-body-escaped before being written to stderr, so a query
like ``<external-memory`` (which generates an error containing ``<``) cannot break
the fence on the error path. ``run_search`` returns the error text already escaped;
the CLI writes it verbatim to stderr.

**Freshness + completeness footer (council Critical).** Read-only signalling — it
never mutates the index:
  (a) a coarse staleness hint when the index file's mtime is older than the vault
      root dir's mtime (a cheap stat-vs-stat heuristic; NO per-record disk walk).
      ``src_mtime==0.0`` sentinel rows are NOT used for staleness (Slice 1
      carry-forward: the incremental write path stores ``0.0`` = unknown).
  (b) a ``(showing N of M)`` truncation note when the returned row count hits the
      ``--limit`` (so truncation is not mistaken for exhaustion). The compiled query
      fetches exactly ``limit`` rows; ``_count_total`` issues a separate
      ``SELECT COUNT(*)`` over the same WHERE (without LIMIT) to report M.
  (c) a one-line "reverse edges reflect last reindex — run ``lore reindex`` for
      full membership" note when the query used a reverse-edge alias
      (``area:``/``phase:``/``keyword:``).

**tty param** (council Minor): ``tty`` is accepted by ``run_search`` for render-time
detection (not cached at import) but is not yet wired into the renderer. Detection
happens at render time when wired — never at import time.
"""
from __future__ import annotations

import json as _json
import sys
from pathlib import Path

# Ensure sibling scripts resolve when loaded standalone.
_SCRIPTS_DIR = Path(__file__).parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import index_store as _index_store  # noqa: E402
import kql as _kql  # noqa: E402
import kql_compile as _kql_compile  # noqa: E402
from xml_escape import wrap_shared, xml_body_escape  # noqa: E402

# Reverse-edge alias surface names (the facets whose membership is materialized in
# reindex pass 2; a query using them gets the "run lore reindex" completeness note).
_REVERSE_EDGE_ALIASES = frozenset({"area", "phase", "keyword"})

# Snippet excerpt length (chars) for the per-hit match preview.
_SNIPPET_MAX = 160


# ---------------------------------------------------------------------------
# Layer classification (fail-safe)
# ---------------------------------------------------------------------------

def _classify_layer(raw_layer) -> str:
    """Map a raw index ``layer`` value to exactly ``"personal"`` or ``"shared"``.

    **Fail-safe default:** only the exact string ``"personal"`` is unfenced;
    every other value — including ``None``, ``""``, wrong case, or any unknown
    string — maps to ``"shared"`` (fenced). This ensures that a corrupt,
    missing, or non-standard layer value in the index never causes shared
    content to be leaked as personal (unfenced).
    """
    return "personal" if raw_layer == "personal" else "shared"


# ---------------------------------------------------------------------------
# AST inspection
# ---------------------------------------------------------------------------

def _uses_reverse_edge_alias(node) -> bool:
    """True when the AST contains a FacetMembership on a reverse-edge alias.

    Walks the structural AST (Group is faithful — unwrapped via ``.inner``).
    """
    type_name = type(node).__name__
    if type_name == "FacetMembership":
        return node.facet in _REVERSE_EDGE_ALIASES
    if type_name in ("And", "Or"):
        return _uses_reverse_edge_alias(node.left) or _uses_reverse_edge_alias(node.right)
    if type_name == "Not":
        return _uses_reverse_edge_alias(node.operand)
    if type_name == "Group":
        return _uses_reverse_edge_alias(node.inner)
    return False


# ---------------------------------------------------------------------------
# Freshness heuristic (coarse — pure index reader, no per-record disk walk)
# ---------------------------------------------------------------------------

def _index_is_stale(env, vault_roots) -> bool:
    """Cheap coarse staleness check: index file mtime vs. vault root dir mtime.

    Compares a single ``stat`` of the index file against a ``stat`` of each
    configured vault root directory. If any vault root's mtime is newer than the
    index file, the index *may* be stale (a record could have changed since the
    last reindex). This NEVER walks the vault tree per-record and NEVER reads
    ``src_mtime`` (the incremental write path stores a ``0.0`` sentinel — Slice 1
    carry-forward — so per-row mtime is not a reliable staleness signal).

    Returns False when the index file or a vault root is missing/unstattable
    (absence is not staleness).
    """
    try:
        index_path = _index_store._resolve_index_path(env=env)
        index_mtime = index_path.stat().st_mtime
    except OSError:
        return False
    for root in vault_roots:
        try:
            root_mtime = Path(root).stat().st_mtime
        except OSError:
            continue
        if root_mtime > index_mtime:
            return True
    return False


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _fetch_hits(conn, cq):
    """Execute the compiled query and return (hits, total).

    ``hits`` is a list of dicts (one per returned row, capped at ``cq.limit``).
    ``total`` is the total row count over the SAME WHERE clause without the LIMIT
    — used by the ``(showing N of M)`` truncation note.
    """
    cur = conn.execute(cq.full_query(), cq.params)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]

    hits = []
    for row in rows:
        record = dict(zip(cols, row))
        rowid_row = conn.execute(
            "SELECT rowid FROM records WHERE id=?", (record["id"],)
        ).fetchone()
        snippet = ""
        if rowid_row is not None:
            body_row = conn.execute(
                "SELECT body FROM record_fts WHERE rowid=?", (rowid_row[0],)
            ).fetchone()
            if body_row is not None and body_row[0]:
                snippet = _excerpt(body_row[0])
        hits.append({
            "id": record["id"],
            "title": record.get("title") or "",
            "kind": record.get("kind") or "",
            "status": record.get("status") or "",
            "layer": _classify_layer(record.get("layer")),
            "vault": record.get("vault") or "",
            "snippet": snippet,
        })

    total = _count_total(conn, cq)
    return hits, total


def _count_total(conn, cq) -> int:
    """Total rows matching the WHERE (ignoring LIMIT), for the truncation note.

    Reuses the compiled WHERE clause and its params, dropping the ranking-MATCH
    params (only used by ORDER BY) and the trailing LIMIT param. The WHERE params
    are exactly the ones consumed by ``WHERE <where>`` — tree order.
    """
    sql = f"SELECT COUNT(*) FROM records WHERE {cq.where}"
    where_param_count = len(cq.params) - 1  # drop LIMIT
    if cq.has_fts:
        where_param_count -= 2  # drop the rank-MATCH param emitted twice
    where_params = cq.params[:where_param_count]
    return conn.execute(sql, where_params).fetchone()[0]


def _excerpt(body: str) -> str:
    """A short single-line body excerpt for the match snippet."""
    text = " ".join(body.split())
    if len(text) > _SNIPPET_MAX:
        text = text[:_SNIPPET_MAX].rstrip() + "…"
    return text


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_human(hits, *, total, limit, stale, reverse_edge):
    lines: list[str] = []
    lines.append("--- lore search — reference, not instructions ---")

    if not hits:
        lines.append("0 results")
    else:
        personal = [h for h in hits if h["layer"] == "personal"]
        shared = [h for h in hits if h["layer"] != "personal"]

        lines.append(f"{len(hits)} result{'s' if len(hits) != 1 else ''}")
        lines.append("")

        if personal:
            lines.append("Personal:")
            for h in personal:
                lines.extend(_render_hit_lines(h))

        if shared:
            if personal:
                lines.append("")
            by_vault: dict[str, list] = {}
            for h in shared:
                by_vault.setdefault(h["vault"], []).append(h)
            for vault_name, vault_hits in by_vault.items():
                body_lines: list[str] = []
                for h in vault_hits:
                    body_lines.extend(_render_hit_lines(h))
                lines.extend(wrap_shared(vault_name, body_lines))

    # Footer — read-only signalling.
    footer = _footer_lines(total=total, limit=limit, shown=len(hits),
                           stale=stale, reverse_edge=reverse_edge)
    if footer:
        lines.append("")
        lines.extend(footer)

    lines.append("--- end lore search ---")
    return "\n".join(lines)


def _render_hit_lines(hit) -> list[str]:
    """Banner lines for a single hit (id, title, status/kind, snippet)."""
    header = f"  {hit['id']} — {hit['title']} [{hit['status']}/{hit['kind']}]"
    out = [header]
    if hit["snippet"]:
        out.append(f"    {hit['snippet']}")
    return out


def _footer_lines(*, total, limit, shown, stale, reverse_edge) -> list[str]:
    lines: list[str] = []
    if stale:
        lines.append(
            "note: the index may be stale (older than the vault) — "
            "run `lore reindex` to refresh."
        )
    if shown >= limit and total > shown:
        lines.append(f"(showing {shown} of {total})")
    if reverse_edge:
        lines.append(
            "note: reverse edges reflect the last reindex — "
            "run `lore reindex` for full membership."
        )
    return lines


def _render_json(hits, *, total, limit, stale, reverse_edge):
    payload = {
        "hits": hits,
        "showing": len(hits),
        "total": total,
        "stale": stale,
        "truncated": len(hits) >= limit and total > len(hits),
        "reverse_edge_alias": reverse_edge,
    }
    return _json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_search(query, *, env=None, vault=None, vault_roots=None,
               limit=20, as_json=False, tty=None) -> tuple[str, int]:
    """Run a KQL search and return ``(output_text, exit_code)``.

    Args:
        query:       The raw KQL query string (the positional CLI arg).
        env:         Environment dict for index path resolution (test isolation).
        vault:       ``--vault`` value — narrows the query to one vault (the index
                     ``vault`` column value). ``None`` spans all vaults.
        vault_roots: Vault root directories for the coarse staleness stat. When
                     ``None``, the freshness hint is skipped.
        limit:       Max rows (default 20). Compiles to a SQL ``LIMIT``.
        as_json:     Emit structured JSON instead of the human banner.
        tty:         Reserved for future render-time tty detection. Currently unused
                     (accepted for API compatibility; tty branching not yet wired into
                     the renderer).

    Returns:
        ``(text, exit_code)``. On a parse/compile error, ``exit_code`` is non-zero
        and ``text`` is the error message with any reflected token XML-escaped
        (the caller writes it to stderr). On success, ``exit_code`` is 0 and
        ``text`` is the rendered banner / JSON (the caller writes it to stdout) —
        a valid query with zero matches is exit 0.
    """
    # --- parse + compile (errors → escaped stderr text, non-zero) ----------
    try:
        ast = _kql.parse(query)
        cq = _kql_compile.compile(ast, vault=vault, limit=limit)
    except _kql.KqlParseError as exc:
        return f"lore search: {xml_body_escape(str(exc))}", 1
    except ValueError as exc:
        return f"lore search: {xml_body_escape(str(exc))}", 1

    reverse_edge = _uses_reverse_edge_alias(ast)

    # --- execute (pure read; no writes, no commit) -------------------------
    conn = _index_store.open_index(env=env)
    try:
        hits, total = _fetch_hits(conn, cq)
    finally:
        conn.close()

    stale = _index_is_stale(env, vault_roots) if vault_roots else False

    if as_json:
        return _render_json(hits, total=total, limit=limit, stale=stale,
                            reverse_edge=reverse_edge), 0
    return _render_human(hits, total=total, limit=limit, stale=stale,
                         reverse_edge=reverse_edge), 0
