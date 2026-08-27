"""`lore search` latency benchmark.

Pins the latency target: **p95 < 100 ms** for a representative
query, measured at ~current vault size (~2,149 records, the "1×" corpus).

The benchmark builds SYNTHETIC fixture indexes in ``tmp_path`` via
``index_store.open_index(env=...)`` + ``rebuild(...)`` over a generated tmp vault —
it NEVER touches the real vault or the real index (Axiom 6). Latency is measured
over the hot query path (parse → compile → execute the single SQL query), the same
path ``search.run_search`` drives, excluding subprocess / rendering overhead.

Several runs are timed and the p95 is taken. The assertion carries a documented
slack margin so a hard fail signals a real regression, not host noise — but the
actual measured p95 is always printed so the number is visible in the test output.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

CONFTEST_DIR = Path(__file__).parent
sys.path.insert(0, str(CONFTEST_DIR))
from conftest import load_script  # noqa: E402

# Pinned target.
TARGET_P95_MS = 100.0

# Per-corpus hard ceiling.
#
# At 1× (the real current vault size) the representative mixed query lands ~10ms on
# a dev box — comfortably under the pinned 100ms target, so the 1× assert IS the
# pinned target itself (~10× headroom over the measured value absorbs host noise
# while still catching a real regression at the pinned size — the locked latency SLO).
CEILING_1X_MS = TARGET_P95_MS  # 100ms — the pinned SLO is asserted at the real vault size

CORPUS_1X = 2149  # ~current vault size

# A representative mixed query: a facet predicate + a distinctive full-text term
# (exercises the kind predicate, the FTS MATCH inline-IN, and the bm25 ranking
# JOIN). The term ``scrubber`` is seeded into a minority (~1 in 11)
# of bodies — representative of a real keyword search that hits a small slice of the
# corpus, not a stopword-frequency term that matches most rows.
REPRESENTATIVE_QUERY = "kind:lesson and scrubber"

# A bare, no-facet, high-match-count query: with no facet to narrow candidates
# before the bm25 ORDER BY runs, this is the shape that stresses per-row ranking
# cost the hardest (REPRESENTATIVE_QUERY narrows to ~61 candidate rows before
# ranking, which does not exercise that cost). "it" is a filler word present in
# ~89% of generated bodies (~1,912 of 2,149) — the same shape as a bare `test`
# search on the real vault (2,084 of 3,508, ~59%).
BARE_QUERY = "it"
# This machine runs many concurrent camp/agent sessions, so absolute wall-clock
# ms is too noisy a signal for this large-match-count case on its own (a shared
# host under load can inflate EVERY query's absolute time by 5-10x). The
# reliable signal for a per-candidate-row ranking regression is the SPEEDUP
# RATIO between the current compiler's single-JOIN query and an alternate
# correlated-scalar-subquery ranking shape, measured back-to-back against the
# SAME index in the SAME narrow time window — ambient host noise affects both
# sides roughly equally, so the ratio survives noise that would make either
# side's raw ms flaky alone. The ratio is the sole gate; see
# ``test_search_latency_bare_high_match_query_speedup`` for why no absolute-ms
# assertion accompanies it.
# Measured ~322x-378x across repeated runs once bm25 is genuinely computed once
# via a non-flattenable JOIN (see kql_compile's `LIMIT -1` rationale). A prior
# floor of 1.5x encoded the ~2x a FLATTENED JOIN produces (SQLite folds a plain
# subquery join back into a per-row, rowid-constrained MATCH — same asymptotic
# cost as the old correlated-subquery baseline, just wearing a join) — that
# floor could not tell "computed once" apart from "computed once per row instead
# of twice per row". 20x sits with real headroom below every measured run
# (~16-19x margin) while sitting two orders of magnitude above what either the
# flattened-JOIN or correlated-subquery shape can produce.
BARE_MIN_SPEEDUP = 20.0

_KINDS = ["spec", "lesson", "decision", "deferred", "dead-end", "plan", "session"]
_AREAS = ["penny", "phi-scrubber", "worker", "ingest", "routing", "auth", "vault"]
# Common filler words present in most bodies (the bulk text an agent skims past).
_FILLER = (
    "the system reads a record from the vault and projects it into the index "
    "so a later query can find it again without rescanning the whole tree"
).split()
# Distinctive terms seeded sparsely so a representative query matches a small slice.
_RARE = ["scrubber", "benchmark", "latency", "corpus", "facet"]


def _build_corpus(vault: Path, n: int) -> None:
    """Generate ``n`` sidecar+body record pairs spread across kinds.

    Each body is mostly common filler with ONE distinctive term mixed in (rotating
    through ``_RARE``), so the representative ``scrubber`` query matches ~1/len(_RARE)
    of the corpus — a realistic keyword-search match fraction, not a near-universal
    stopword.
    """
    import json

    for kind in _KINDS:
        (vault / kind).mkdir(parents=True, exist_ok=True)
    for i in range(n):
        kind = _KINDS[i % len(_KINDS)]
        area = _AREAS[i % len(_AREAS)]
        rare = _RARE[i % len(_RARE)]
        name = f"rec-{i:05d}"
        offset = i % len(_FILLER)
        words = _FILLER[offset:] + _FILLER[:offset]
        body = " ".join(words[:14]) + f" {rare}."
        sidecar = {
            "title": f"Record {i} about {area}",
            "status": "active",
            "created-at": "2026-01-01",
            "updated-at": "2026-02-01",
            "keywords": [area, rare],
            "related": {"area": [area]},
        }
        (vault / kind / f"{name}.json").write_text(json.dumps(sidecar))
        (vault / kind / f"{name}.md").write_text(body)


def _build_index(state_dir: Path, vault: Path):
    index_store = load_script("lore.search.index")
    env = {"XDG_STATE_HOME": str(state_dir)}
    conn = index_store.open_index(env=env)
    try:
        count = index_store.rebuild([str(vault)], conn)
        conn.commit()
    finally:
        conn.close()
    return count


def _percentile(samples_ms: list[float], pct: float) -> float:
    ordered = sorted(samples_ms)
    if not ordered:
        return 0.0
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


def _old_style_ranked_query(match_expr: str, limit: int) -> tuple[str, list]:
    """An alternate ranking shape: two correlated scalar subqueries in ORDER BY,
    each re-running the FTS MATCH once per candidate row, instead of the single
    non-flattenable JOIN the production compiler uses. Used only as the
    speedup-ratio baseline in ``_measure_speedup_ratio``; ``kql_compile.compile``
    never emits this shape.

    ``match_expr`` must be the compiler's own SANITIZED MATCH expression (e.g.
    ``cq.params[0]``), never the raw query term — for a bare single-token query
    like ``BARE_QUERY`` they happen to be identical, but a phrase or a multi-term
    query sanitizes to a different MATCH string, and binding the raw term here
    would silently compare two different match sets on the two sides, invalidating
    the speedup ratio.
    """
    bm25_subquery = (
        "(SELECT bm25(record_fts, 3.0, 2.0, 1.0) FROM record_fts "
        "WHERE record_fts.rowid = records.rowid AND record_fts MATCH ?)"
    )
    sql = (
        "SELECT * FROM records\n"
        "WHERE records.rowid IN (SELECT rowid FROM record_fts WHERE record_fts MATCH ?)\n"
        f"ORDER BY {bm25_subquery} IS NULL, {bm25_subquery} ASC, "
        "updated_at DESC, last_referenced_at DESC\n"
        "LIMIT ?"
    )
    return sql, [match_expr, match_expr, match_expr, limit]


def test_old_style_ranked_query_pinned_to_locked_fts_predicate_and_weights():
    """``_old_style_ranked_query`` hardcodes a copy of the deleted correlated-
    subquery SQL as the speedup baseline. Nothing else ties it to the real
    ``kql_compile`` constants it is meant to mirror, so a change to the FTS
    predicate shape or the locked bm25 weights could drift silently and leave
    the speedup ratio comparing two SQL shapes that no longer share a WHERE
    clause or a weighting — pin both here so that drift fails loudly instead."""
    kql_compile = load_script("lore.search.kql_compile")
    cq_probe = kql_compile.compile(load_script("lore.search.kql").parse("zephyr"))
    old_sql, _ = _old_style_ranked_query(cq_probe.params[0], 20)

    assert kql_compile._FTS_PREDICATE in old_sql, (
        "the baseline's WHERE clause no longer matches kql_compile._FTS_PREDICATE"
    )

    cq = kql_compile.compile(load_script("lore.search.kql").parse("zephyr"))
    weights_sql = cq.join_clause.split("bm25(record_fts, ", 1)[1].split(")", 1)[0]
    assert f"bm25(record_fts, {weights_sql})" in old_sql, (
        "the baseline's bm25 weights no longer match the locked production weights"
    )


def _measure_speedup_ratio(state_dir: Path, *, query: str, runs: int = 20) -> tuple[float, float]:
    """Interleave the current compiler's query and the alternate correlated-
    subquery ranking shape (``_old_style_ranked_query``) against the SAME
    fresh-connection-per-run pattern, so both sides see the same ambient host
    load in each iteration. Returns (new_p95_ms, old_p95_ms).
    """
    index_store = load_script("lore.search.index")
    kql = load_script("lore.search.kql")
    kql_compile = load_script("lore.search.kql_compile")
    env = {"XDG_STATE_HOME": str(state_dir)}

    ast = kql.parse(query)
    cq = kql_compile.compile(ast, limit=20)
    # Bind the SAME sanitized MATCH expression the production side binds
    # (``cq.params[0]``, the rank-JOIN param), never the raw query string — for a
    # bare single-token query like BARE_QUERY they are identical today, but a
    # phrase or multi-term query sanitizes differently, and comparing two
    # different match sets would invalidate the ratio.
    old_sql, old_params = _old_style_ranked_query(cq.params[0], 20)

    new_samples: list[float] = []
    old_samples: list[float] = []
    for _ in range(runs):
        conn = index_store.open_index(env=env)
        try:
            t0 = time.perf_counter()
            conn.execute(cq.full_query(), cq.params).fetchall()
            new_samples.append((time.perf_counter() - t0) * 1000.0)

            t0 = time.perf_counter()
            conn.execute(old_sql, old_params).fetchall()
            old_samples.append((time.perf_counter() - t0) * 1000.0)
        finally:
            conn.close()
    return _percentile(new_samples, 95.0), _percentile(old_samples, 95.0)


def _measure_query_p95(state_dir: Path, *, runs: int = 30) -> float:
    """Time the hot query path (parse → compile → execute) ``runs`` times; p95 ms.

    Each run opens the index fresh (mirroring a cold ``lore search`` invocation),
    parses + compiles the representative query, executes the single SQL query, and
    drains the rows. Returns the p95 in milliseconds.
    """
    index_store = load_script("lore.search.index")
    kql = load_script("lore.search.kql")
    kql_compile = load_script("lore.search.kql_compile")
    env = {"XDG_STATE_HOME": str(state_dir)}

    samples_ms: list[float] = []
    for _ in range(runs):
        conn = index_store.open_index(env=env)
        try:
            t0 = time.perf_counter()
            ast = kql.parse(REPRESENTATIVE_QUERY)
            cq = kql_compile.compile(ast, limit=20)
            rows = conn.execute(cq.full_query(), cq.params).fetchall()
            _ = len(rows)
            samples_ms.append((time.perf_counter() - t0) * 1000.0)
        finally:
            conn.close()
    return _percentile(samples_ms, 95.0)


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "corpus_size,label,ceiling_ms",
    [
        (CORPUS_1X, "1x", CEILING_1X_MS),
    ],
)
def test_search_latency_p95_under_target(tmp_path, capsys, corpus_size, label, ceiling_ms):
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    vault.mkdir()
    state.mkdir()

    _build_corpus(vault, corpus_size)
    indexed = _build_index(state, vault)

    p95 = _measure_query_p95(state)

    # Always surface the measured number (visible with `pytest -s` / on failure).
    with capsys.disabled():
        verdict = "OK" if p95 < TARGET_P95_MS else "over-target (within ceiling)"
        print(
            f"\n[search-benchmark {label}] indexed={indexed} records "
            f"query={REPRESENTATIVE_QUERY!r} p95={p95:.2f}ms "
            f"(target<{TARGET_P95_MS:.0f}ms, ceiling<{ceiling_ms:.0f}ms) → {verdict}"
        )

    assert indexed >= corpus_size, f"corpus under-built: indexed {indexed} of {corpus_size}"
    assert p95 < ceiling_ms, (
        f"search p95 {p95:.2f}ms exceeded the {ceiling_ms:.0f}ms ceiling at the "
        f"{label} corpus ({indexed} records). The pinned target is "
        f"{TARGET_P95_MS:.0f}ms; the ceiling covers host noise — a breach signals "
        "a real query-path regression."
    )


@pytest.mark.parametrize(
    "corpus_size,label",
    [
        (CORPUS_1X, "1x"),
    ],
)
def test_search_latency_bare_high_match_query_speedup(tmp_path, capsys, corpus_size, label):
    """A bare, no-facet, high-match-count query — the shape REPRESENTATIVE_QUERY's
    facet narrowing does not exercise. This pins the per-candidate-row ranking
    regression class via a SPEEDUP RATIO (current compiler's single-JOIN query
    vs. an alternate correlated-scalar-subquery ranking shape, measured
    back-to-back so ambient host noise cancels out). No absolute-ms assertion
    accompanies it: this machine's shared-host noise inflates a large-match-count
    query's raw wall-clock by 5-10x run to run (see the module-level comment
    above ``BARE_QUERY``), so an absolute ceiling here would gate on noise
    instead of the query-shape regression this test exists to catch. The ratio
    fails when the current compiler's query is not meaningfully faster than the
    alternate shape (as it would be if a regression reintroduced correlated
    subqueries) and passes once bm25 is computed once per row via a single
    JOIN."""
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    vault.mkdir()
    state.mkdir()

    _build_corpus(vault, corpus_size)
    indexed = _build_index(state, vault)

    new_p95, old_p95 = _measure_speedup_ratio(state, query=BARE_QUERY)
    speedup = old_p95 / new_p95 if new_p95 else float("inf")

    with capsys.disabled():
        print(
            f"\n[search-benchmark {label}-bare] indexed={indexed} records "
            f"query={BARE_QUERY!r} new_p95={new_p95:.2f}ms old_p95={old_p95:.2f}ms "
            f"speedup={speedup:.1f}x (min {BARE_MIN_SPEEDUP:.1f}x)"
        )

    assert indexed >= corpus_size, f"corpus under-built: indexed {indexed} of {corpus_size}"
    assert speedup >= BARE_MIN_SPEEDUP, (
        f"the current compiler's query was only {speedup:.1f}x faster than the "
        f"alternate correlated-subquery ranking shape (need >= {BARE_MIN_SPEEDUP:.1f}x) "
        f"at the {label} corpus ({indexed} records, new_p95={new_p95:.2f}ms, "
        f"old_p95={old_p95:.2f}ms) — the bm25 ranking may no longer be computed "
        "once per row via a single JOIN."
    )
