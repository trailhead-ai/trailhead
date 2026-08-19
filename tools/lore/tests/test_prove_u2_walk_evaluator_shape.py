"""Assumption probe — U2: one walk, two consumers.

Ephemeral. Proves (or disproves) that a ``{"kind/name": sidecar}`` mapping
built by a warning-collecting sidecar walk (the shape ``lore/pipeline/walk.py``
will build) is shape-compatible with the REAL ``evaluate_dependencies`` at
``lore.record.graph``, and pins the exact behavior of ``load_design_sidecars``
and ``evaluate_dependencies`` that the pipeline slice must match/degrade.

Delete this file (and nothing else) when the unknown is resolved and the real
slice tests exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import load_script


def _graph_mod():
    return load_script("lore.record.graph")


def _guards_mod():
    return load_script("lore.record.guards")


def _write_record(vault: Path, kind: str, name: str, sidecar: dict) -> None:
    kind_dir = vault / kind
    kind_dir.mkdir(parents=True, exist_ok=True)
    (kind_dir / f"{name}.json").write_text(
        json.dumps({"kind": kind, **sidecar}), encoding="utf-8"
    )


def _warning_collecting_walk(vault_root: Path, kinds=("spec", "adr")) -> tuple[dict, list[str]]:
    """Emulate the pipeline's own walk: same filter as guards._load_kind_sidecars
    (skip bad JSON / non-dict JSON) but *report* instead of silently dropping.

    This is deliberately NOT calling guards.load_design_sidecars — the whole
    point of U2 is that the pipeline must build this graph itself.
    """
    graph: dict[str, dict] = {}
    warnings: list[str] = []
    for kind in kinds:
        kind_dir = vault_root / kind
        if not kind_dir.is_dir():
            continue
        for sidecar_path in sorted(kind_dir.glob("*.json")):
            try:
                data = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                warnings.append(f"{kind}/{sidecar_path.stem}: unreadable/invalid JSON ({exc})")
                continue
            if not isinstance(data, dict):
                warnings.append(f"{kind}/{sidecar_path.stem}: sidecar is not a JSON object")
                continue
            graph[f"{kind}/{sidecar_path.stem}"] = data
    return graph, warnings


# ---------------------------------------------------------------------------
# 1. Shape-compatibility: real evaluate_dependencies over a walk-built graph
# ---------------------------------------------------------------------------


def test_walk_built_graph_feeds_real_evaluate_dependencies(tmp_path):
    g = _graph_mod()
    vault = tmp_path / "vault"

    _write_record(vault, "spec", "target-ready", {"status": "ready"})
    _write_record(vault, "spec", "target-planned", {"status": "planned"})
    _write_record(vault, "spec", "target-superseded", {"status": "superseded"})
    _write_record(
        vault,
        "spec",
        "consumer",
        {
            "status": "draft",
            "depends-on": [
                "spec/target-ready@planned",  # unmet: short-of-stage
                "spec/target-planned@planned",  # met
                "spec/does-not-exist@planned",  # unmet: missing
                "spec/target-superseded@planned",  # unmet: target-failed
            ],
        },
    )

    graph, warnings = _warning_collecting_walk(vault)
    assert warnings == []
    assert set(graph) == {
        "spec/target-ready",
        "spec/target-planned",
        "spec/target-superseded",
        "spec/consumer",
    }

    entries = graph["spec/consumer"]["depends-on"]
    statuses = g.evaluate_dependencies(graph, entries)

    assert len(statuses) == 4
    met_short, met_true, met_missing, met_failed = statuses

    assert met_short.met is False
    assert met_short.reason_code == "short-of-stage"
    assert met_short.kind == "spec" and met_short.name == "target-ready"

    assert met_true.met is True
    assert met_true.reason_code is None
    assert met_true.kind == "spec" and met_true.name == "target-planned"

    assert met_missing.met is False
    assert met_missing.reason_code == "missing"
    assert met_missing.kind == "spec" and met_missing.name == "does-not-exist"

    assert met_failed.met is False
    assert met_failed.reason_code == "target-failed"
    assert met_failed.kind == "spec" and met_failed.name == "target-superseded"


def test_malformed_sidecar_produces_named_warning_from_the_walk_not_load_design_sidecars(tmp_path):
    """The core of U2: load_design_sidecars silently drops a malformed sidecar
    (no warning surfaces), which defeats Slice 1's requirement. The pipeline's
    own walk must name it. Prove the walk does, and that load_design_sidecars
    (cited, not called) does not.
    """
    vault = tmp_path / "vault"
    _write_record(vault, "spec", "good", {"status": "draft"})
    (vault / "spec").mkdir(parents=True, exist_ok=True)
    (vault / "spec" / "bad.json").write_text("{not json", encoding="utf-8")

    graph, warnings = _warning_collecting_walk(vault)
    assert set(graph) == {"spec/good"}
    assert len(warnings) == 1
    assert "spec/bad" in warnings[0]

    # Contrast: load_design_sidecars gives no signal that anything was dropped.
    guards = _guards_mod()
    silent_graph = guards.load_design_sidecars(str(vault))
    assert set(silent_graph) == {"spec/good"}  # same surviving membership
    # but nothing tells a caller "bad" ever existed — no warnings list at all.


# ---------------------------------------------------------------------------
# 2. Exactly what load_design_sidecars produces (cited + confirmed empirically)
# ---------------------------------------------------------------------------


def test_load_design_sidecars_shape_key_value_kinds_and_malformed_handling(tmp_path):
    """Pins guards.py:108-123 (load_design_sidecars) and guards.py:76-92
    (_load_kind_sidecars, the shared primitive) behavior:

    - key: f"{kind}/{stem}" for kind in DESIGN_KINDS ({"spec", "adr"}) — the
      filename stem, no `.json`, no directory prefix beyond the kind.
    - value: the parsed JSON dict verbatim (whatever fields it has; no
      normalization, no id/name injected — matches store.py's "sidecar carries
      no id/name key" axiom).
    - kinds included: exactly DESIGN_KINDS = {"spec", "adr"}; "task" is
      excluded even when present in the same vault root.
    - malformed handling: invalid JSON -> skipped, no warning, no error.
      Non-dict JSON (e.g. a JSON array) -> skipped, no warning, no error.
      Missing kind directory -> contributes nothing, no error.
    """
    guards = _guards_mod()
    graph_mod = _graph_mod()
    vault = tmp_path / "vault"

    _write_record(vault, "spec", "a", {"status": "draft", "custom-field": "x"})
    _write_record(vault, "adr", "b", {"status": "active"})
    _write_record(vault, "task", "t", {"status": "open"})  # must be excluded
    (vault / "spec" / "bad.json").write_text("{not json", encoding="utf-8")
    (vault / "adr" / "listy.json").write_text("[1, 2, 3]", encoding="utf-8")

    graph = guards.load_design_sidecars(str(vault))

    assert graph_mod.DESIGN_KINDS == frozenset({"spec", "adr"})
    assert set(graph) == {"spec/a", "adr/b"}  # task excluded, malformed excluded
    assert graph["spec/a"] == {"kind": "spec", "status": "draft", "custom-field": "x"}
    assert "id" not in graph["spec/a"] and "name" not in graph["spec/a"]

    # No leftover directory case: missing spec/adr dirs entirely -> {}
    empty_vault = tmp_path / "empty"
    empty_vault.mkdir()
    assert guards.load_design_sidecars(str(empty_vault)) == {}


# ---------------------------------------------------------------------------
# 3. Per-vault confinement: evaluate_dependencies never merges across vaults
# ---------------------------------------------------------------------------


def test_evaluator_invoked_per_vault_never_resolves_cross_vault_target(tmp_path):
    """Nothing in evaluate_dependencies's contract requires (or supports) a
    merged/global graph: it is a pure function over whatever dict you pass it.
    Prove the pipeline's own-vault-only invocation pattern actually confines —
    a same-qualified-id record in a DIFFERENT vault's graph is invisible.
    """
    g = _graph_mod()

    vault_a = tmp_path / "vault_a"
    vault_b = tmp_path / "vault_b"
    # vault_a has a consumer that depends on spec/shared-name@planned.
    _write_record(vault_a, "spec", "consumer", {
        "status": "draft",
        "depends-on": ["spec/shared-name@planned"],
    })
    # spec/shared-name does NOT exist in vault_a...
    # ...but DOES exist, satisfied, in vault_b.
    _write_record(vault_b, "spec", "shared-name", {"status": "planned"})

    graph_a, warnings_a = _warning_collecting_walk(vault_a)
    graph_b, warnings_b = _warning_collecting_walk(vault_b)
    assert warnings_a == [] and warnings_b == []

    # Evaluate strictly per-vault: vault_a's entries against vault_a's own graph.
    statuses = g.evaluate_dependencies(graph_a, graph_a["spec/consumer"]["depends-on"])
    assert len(statuses) == 1
    assert statuses[0].met is False
    assert statuses[0].reason_code == "missing"  # NOT resolved against vault_b

    # Confirm it's not that the target can never be met — it resolves fine
    # when evaluated against its own vault's graph (vault_b would satisfy it
    # if consumer lived there).
    statuses_if_merged = g.evaluate_dependencies(
        {**graph_a, **graph_b}, graph_a["spec/consumer"]["depends-on"]
    )
    assert statuses_if_merged[0].met is True  # proves a merge WOULD forge a status
    # i.e. the security property depends entirely on the CALLER never merging —
    # evaluate_dependencies itself has no per-vault awareness and will happily
    # resolve across a merged graph if handed one. The pipeline must never
    # construct graph_a | graph_b or pass a merged dict.


# ---------------------------------------------------------------------------
# 4. Raise conditions on malformed graph/entry input
# ---------------------------------------------------------------------------


def test_parse_dependency_never_raises_on_malformed_entries():
    g = _graph_mod()
    design_graph: dict = {}
    weird_entries = [
        "no-slash-at-all",
        "",
        "task/foo",  # task-kind rejected, not raised
        "bogus-kind/foo",
        "spec/foo@not-a-real-stage",
        "spec/foo@dropped",  # failure-stage
        123,  # not even a string
        None,
        "spec/",  # empty name half
        "/foo",  # empty kind half
    ]
    statuses = g.evaluate_dependencies(design_graph, weird_entries)
    assert len(statuses) == len(weird_entries)
    for s in statuses:
        assert s.met is False
        assert s.reason_code in ("missing", "short-of-stage", "target-failed")


def test_missing_status_field_degrades_not_raises():
    g = _graph_mod()
    design_graph = {"spec/foo": {"kind": "spec"}}  # no "status" key at all
    statuses = g.evaluate_dependencies(design_graph, ["spec/foo@planned"])
    assert statuses[0].met is False
    assert statuses[0].reason_code == "short-of-stage"


def test_non_dict_graph_value_raises_attributeerror():
    """A sidecar value in the graph that is not a dict (e.g. a walk that failed
    to filter, or JSON-array content) makes evaluate_dependencies RAISE rather
    than degrade — because it calls target.get("status") unconditionally.

    This is the concrete raise condition Slice 4 must catch and degrade to a
    warning: the graph-construction filter (dict-only, matching
    _load_kind_sidecars's `isinstance(data, dict)` gate) is what keeps this
    from being reachable in practice — but if a bad value ever slips through,
    evaluation raises, it does not return an unmet status.
    """
    g = _graph_mod()
    design_graph = {"spec/foo": ["not", "a", "dict"]}
    with pytest.raises(AttributeError):
        g.evaluate_dependencies(design_graph, ["spec/foo@planned"])

    design_graph2 = {"spec/foo": "also not a dict"}
    with pytest.raises(AttributeError):
        g.evaluate_dependencies(design_graph2, ["spec/foo@planned"])


def test_unhashable_status_field_raises_typeerror():
    """A syntactically valid dict sidecar (passes the dict-only walk filter)
    whose ``status`` field is itself malformed (e.g. a list instead of a str)
    still raises: ``status in FAILURE_STATUSES`` requires ``status`` to be
    hashable. This slips past the walk's dict-only gate because the SIDECAR
    is a dict — only one of its FIELDS is wrong-shaped.
    """
    g = _graph_mod()
    design_graph = {"spec/foo": {"kind": "spec", "status": ["draft"]}}
    with pytest.raises(TypeError):
        g.evaluate_dependencies(design_graph, ["spec/foo@planned"])


# ---------------------------------------------------------------------------
# 5. DependencyStatus field names and reason_code vocabulary
# ---------------------------------------------------------------------------


def test_dependency_status_fields_and_reason_code_vocabulary():
    g = _graph_mod()
    assert g.DependencyStatus._fields == ("kind", "name", "stage", "met", "reason", "reason_code")

    assert g.REASON_MISSING == "missing"
    assert g.REASON_SHORT_OF_STAGE == "short-of-stage"
    assert g.REASON_TARGET_FAILED == "target-failed"

    # Confirm these are the ONLY three non-None reason codes ever emitted, by
    # construction of _evaluate_one (met branch returns reason_code=None).
    design_graph = {
        "spec/a": {"status": "planned"},
        "spec/b": {"status": "superseded"},
        "spec/c": {"status": "draft"},
    }
    entries = [
        "spec/a@planned",  # met
        "spec/b@planned",  # target-failed
        "spec/c@planned",  # short-of-stage
        "spec/missing@planned",  # missing
        "not-real",  # missing (unparseable entry)
    ]
    statuses = g.evaluate_dependencies(design_graph, entries)
    codes = {s.reason_code for s in statuses}
    assert codes == {None, "short-of-stage", "target-failed", "missing"}
    for s in statuses:
        if s.met:
            assert s.reason_code is None
        else:
            assert s.reason_code in {"missing", "short-of-stage", "target-failed"}
        assert isinstance(s.reason, str) and s.reason  # always non-empty
