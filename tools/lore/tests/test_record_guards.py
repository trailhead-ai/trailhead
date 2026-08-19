"""Unit tests for ``lore.record.guards`` — the task-graph guard policy.

``guards`` is the orchestration layer between the on-disk task sidecars and the
pure ``graph`` algorithms: it loads the vault's task sidecars, overlays the
in-flight record, decides WHICH checks run for a given create/update/delete, and
classifies each outcome as a blocking error or a non-blocking notice. The pure
cycle/loop/containment algorithms it calls are covered by ``test_record_graph``;
here we pin the policy — no-op for non-task kinds, edge-confinement short-circuit,
error-vs-notice classification, delete-only-warns, and the lazy graph load.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import load_script


def _guards():
    return load_script("lore.record.guards")


def _write_task(vault: Path, name: str, sidecar: dict) -> None:
    task_dir = vault / "task"
    task_dir.mkdir(parents=True, exist_ok=True)
    sidecar = {"kind": "task", **sidecar}
    (task_dir / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")


# ---------------------------------------------------------------------------
# load_task_sidecars
# ---------------------------------------------------------------------------


def test_load_task_sidecars_reads_every_task(tmp_path):
    g = _guards()
    _write_task(tmp_path, "a", {"status": "open"})
    _write_task(tmp_path, "b", {"status": "done"})
    graph = g.load_task_sidecars(str(tmp_path))
    assert set(graph) == {"a", "b"}
    assert graph["b"]["status"] == "done"


def test_load_task_sidecars_missing_dir_is_empty(tmp_path):
    g = _guards()
    assert g.load_task_sidecars(str(tmp_path)) == {}


def test_load_task_sidecars_skips_malformed(tmp_path):
    g = _guards()
    _write_task(tmp_path, "good", {"status": "open"})
    (tmp_path / "task" / "bad.json").write_text("{not json", encoding="utf-8")
    graph = g.load_task_sidecars(str(tmp_path))
    assert set(graph) == {"good"}


# ---------------------------------------------------------------------------
# body_has_flow_out
# ---------------------------------------------------------------------------


def test_body_has_flow_out_detects_heading():
    g = _guards()
    assert g.body_has_flow_out("intro\n## Flow-out\ncaptured")
    assert g.body_has_flow_out("### flow-out")
    assert not g.body_has_flow_out("no such section")
    assert not g.body_has_flow_out("")


# ---------------------------------------------------------------------------
# confine_edge_reference
# ---------------------------------------------------------------------------


def test_confine_edge_reference_accepts_plain_name(tmp_path):
    g = _guards()
    assert g.confine_edge_reference("alpha", str(tmp_path)) is None


def test_confine_edge_reference_rejects_empty(tmp_path):
    g = _guards()
    msg = g.confine_edge_reference("", str(tmp_path))
    assert msg is not None
    assert "edge-reference" in msg


def test_confine_edge_reference_rejects_traversal(tmp_path):
    g = _guards()
    msg = g.confine_edge_reference("../escape", str(tmp_path))
    assert msg is not None
    assert "unsafe task reference" in msg


def test_confine_edge_reference_accepts_plain_name_for_a_design_kind(tmp_path):
    g = _guards()
    assert g.confine_edge_reference("alpha", str(tmp_path), kind="spec") is None


def test_confine_edge_reference_names_the_given_kind_when_rejecting_traversal(tmp_path):
    """The rejection names the kind it confined under, never a hardcoded 'task'."""
    g = _guards()
    msg = g.confine_edge_reference("../escape", str(tmp_path), kind="spec")
    assert msg is not None
    assert "unsafe spec reference" in msg
    assert "task" not in msg


def test_confine_edge_reference_names_the_given_kind_when_rejecting_empty(tmp_path):
    g = _guards()
    msg = g.confine_edge_reference("", str(tmp_path), kind="adr")
    assert msg is not None
    assert "empty adr reference" in msg


def test_confine_edge_reference_confines_under_the_given_kind(tmp_path, monkeypatch):
    """The value is confined as ``<kind>/<value>`` — the kind reaches the store guard."""
    g = _guards()
    seen: list[str] = []
    store = g.record_store_mod
    original = store.confine_record_id
    monkeypatch.setattr(
        store,
        "confine_record_id",
        lambda record_id, root: seen.append(record_id) or original(record_id, root),
    )
    assert g.confine_edge_reference("alpha", str(tmp_path), kind="adr") is None
    assert seen == ["adr/alpha"]


# ---------------------------------------------------------------------------
# evaluate_task_guards — no-op for non-task kinds
# ---------------------------------------------------------------------------


def test_non_task_kind_is_a_noop(tmp_path):
    g = _guards()
    errors, notices = g.evaluate_task_guards(
        kind="spec",
        name="whatever",
        sidecar={"kind": "spec"},
        body="",
        vault_root=str(tmp_path),
        status_set="done",
    )
    assert errors == []
    assert notices == []


# ---------------------------------------------------------------------------
# evaluate_task_guards — blocking errors
# ---------------------------------------------------------------------------


def test_unsafe_edge_reference_blocks_before_graph_load(tmp_path, monkeypatch):
    g = _guards()
    called = []
    monkeypatch.setattr(
        g, "load_task_sidecars", lambda vr: called.append(vr) or {}
    )
    errors, notices = g.evaluate_task_guards(
        kind="task",
        name="t",
        sidecar={"kind": "task", "parent": "../escape"},
        body="",
        vault_root=str(tmp_path),
        status_set="open",
    )
    assert errors and "edge-reference" in errors[0]
    assert called == []  # short-circuits before any vault-wide load


def test_dependency_cycle_is_a_blocking_error(tmp_path):
    g = _guards()
    _write_task(tmp_path, "b", {"status": "open", "depends-on": ["a"]})
    errors, notices = g.evaluate_task_guards(
        kind="task",
        name="a",
        sidecar={"kind": "task", "status": "open", "depends-on": ["b"]},
        body="",
        vault_root=str(tmp_path),
        status_set="open",
    )
    assert any("depends-on-cycle" in e for e in errors)


def test_parent_loop_is_a_blocking_error(tmp_path):
    g = _guards()
    errors, notices = g.evaluate_task_guards(
        kind="task",
        name="a",
        sidecar={"kind": "task", "status": "open", "parent": "a"},
        body="",
        vault_root=str(tmp_path),
        status_set="open",
    )
    assert any("parent-loop" in e for e in errors)


def test_parent_completion_blocks_done_with_open_children(tmp_path):
    g = _guards()
    _write_task(tmp_path, "kid", {"status": "open", "parent": "epic"})
    errors, notices = g.evaluate_task_guards(
        kind="task",
        name="epic",
        sidecar={"kind": "task", "status": "done"},
        body="",
        vault_root=str(tmp_path),
        status_set="done",
    )
    assert any("parent-completion" in e for e in errors)


# ---------------------------------------------------------------------------
# evaluate_task_guards — non-blocking notices
# ---------------------------------------------------------------------------


def test_dropped_task_with_dependents_warns_but_does_not_block(tmp_path):
    g = _guards()
    _write_task(tmp_path, "b", {"status": "open", "depends-on": ["a"]})
    errors, notices = g.evaluate_task_guards(
        kind="task",
        name="a",
        sidecar={"kind": "task", "status": "dropped"},
        body="",
        vault_root=str(tmp_path),
        status_set="dropped",
    )
    assert errors == []
    assert any("dependents" in n for n in notices)


def test_done_parent_without_flow_out_section_gets_reminder(tmp_path):
    g = _guards()
    _write_task(tmp_path, "kid", {"status": "done", "parent": "epic"})
    errors, notices = g.evaluate_task_guards(
        kind="task",
        name="epic",
        sidecar={"kind": "task", "status": "done"},
        body="no flow-out here",
        vault_root=str(tmp_path),
        status_set="done",
    )
    assert errors == []
    assert any("flow-out" in n for n in notices)


def test_done_parent_with_flow_out_section_has_no_reminder(tmp_path):
    g = _guards()
    _write_task(tmp_path, "kid", {"status": "done", "parent": "epic"})
    errors, notices = g.evaluate_task_guards(
        kind="task",
        name="epic",
        sidecar={"kind": "task", "status": "done"},
        body="## Flow-out\ncaptured",
        vault_root=str(tmp_path),
        status_set="done",
    )
    assert errors == []
    assert notices == []


# ---------------------------------------------------------------------------
# evaluate_task_guards — delete only warns, never blocks
# ---------------------------------------------------------------------------


def test_delete_with_dependents_warns_only(tmp_path):
    g = _guards()
    _write_task(tmp_path, "b", {"status": "open", "depends-on": ["a"]})
    errors, notices = g.evaluate_task_guards(
        kind="task",
        name="a",
        sidecar={},
        body="",
        vault_root=str(tmp_path),
        status_set=None,
        deleting=True,
    )
    assert errors == []
    assert any("dependents" in n for n in notices)


# ---------------------------------------------------------------------------
# evaluate_task_guards — lazy graph load (memoized, reference-scoped)
# ---------------------------------------------------------------------------


def test_status_only_update_skips_vault_wide_sidecar_load(tmp_path, monkeypatch):
    """A node with no outgoing edges can never open a NEW cycle/loop, so a plain
    status update never triggers the vault-wide sidecar glob+parse."""
    g = _guards()
    calls: list[str] = []
    original = g.load_task_sidecars
    monkeypatch.setattr(
        g, "load_task_sidecars", lambda vr: calls.append(vr) or original(vr)
    )
    errors, notices = g.evaluate_task_guards(
        kind="task",
        name="solo",
        sidecar={"kind": "task", "status": "in-progress"},
        body="body\n",
        vault_root=str(tmp_path),
        status_set="in-progress",
    )
    assert errors == []
    assert notices == []
    assert calls == []


def test_reference_bearing_update_loads_sidecars_once(tmp_path, monkeypatch):
    """A reference-bearing update DOES load the graph — exactly once (memoized)."""
    g = _guards()
    calls: list[str] = []
    original = g.load_task_sidecars
    monkeypatch.setattr(
        g, "load_task_sidecars", lambda vr: calls.append(vr) or original(vr)
    )
    errors, notices = g.evaluate_task_guards(
        kind="task",
        name="depender",
        sidecar={"kind": "task", "status": "open", "depends-on": ["dep-target"]},
        body="body\n",
        vault_root=str(tmp_path),
        status_set="open",
    )
    assert errors == []
    assert notices == []
    assert calls == [str(tmp_path)]


# ---------------------------------------------------------------------------
# load_design_sidecars
# ---------------------------------------------------------------------------


def _write_design(vault: Path, kind: str, name: str, sidecar: dict) -> None:
    kind_dir = vault / kind
    kind_dir.mkdir(parents=True, exist_ok=True)
    sidecar = {"kind": kind, **sidecar}
    (kind_dir / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")


def test_load_design_sidecars_reads_spec_and_adr(tmp_path):
    g = _guards()
    _write_design(tmp_path, "spec", "a", {"status": "draft"})
    _write_design(tmp_path, "adr", "b", {"status": "active"})
    graph = g.load_design_sidecars(str(tmp_path))
    assert set(graph) == {"spec/a", "adr/b"}
    assert graph["adr/b"]["status"] == "active"


def test_load_design_sidecars_keeps_same_name_different_kind_distinct(tmp_path):
    g = _guards()
    _write_design(tmp_path, "spec", "foo", {"status": "draft"})
    _write_design(tmp_path, "adr", "foo", {"status": "active"})
    graph = g.load_design_sidecars(str(tmp_path))
    assert set(graph) == {"spec/foo", "adr/foo"}
    assert graph["spec/foo"]["status"] == "draft"
    assert graph["adr/foo"]["status"] == "active"


def test_load_design_sidecars_missing_dirs_is_empty(tmp_path):
    g = _guards()
    assert g.load_design_sidecars(str(tmp_path)) == {}


def test_load_design_sidecars_skips_malformed(tmp_path):
    g = _guards()
    _write_design(tmp_path, "spec", "good", {"status": "draft"})
    (tmp_path / "spec" / "bad.json").write_text("{not json", encoding="utf-8")
    graph = g.load_design_sidecars(str(tmp_path))
    assert set(graph) == {"spec/good"}


def test_load_design_sidecars_skips_non_dict_sidecar(tmp_path):
    g = _guards()
    kind_dir = tmp_path / "spec"
    kind_dir.mkdir(parents=True, exist_ok=True)
    (kind_dir / "listy.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert g.load_design_sidecars(str(tmp_path)) == {}


def test_load_design_sidecars_ignores_task_dir(tmp_path):
    g = _guards()
    _write_task(tmp_path, "t", {"status": "open"})
    assert g.load_design_sidecars(str(tmp_path)) == {}
