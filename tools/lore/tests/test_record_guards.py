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


# ---------------------------------------------------------------------------
# evaluate_task_guards — depends-on entry form (bare task names only)
# ---------------------------------------------------------------------------


def _task_guards(g, tmp_path, sidecar):
    return g.evaluate_task_guards(
        kind="task",
        name="a",
        sidecar={"kind": "task", "status": "open", **sidecar},
        body="",
        vault_root=str(tmp_path),
        status_set="open",
    )


def test_task_depends_on_with_kind_prefix_is_a_blocking_error(tmp_path):
    """The silent-detached-node shape: a prefixed target never reaches disk."""
    g = _guards()
    errors, notices = _task_guards(g, tmp_path, {"depends-on": ["task/foo"]})
    assert any("task-edge-form" in e for e in errors)
    assert any("task/foo" in e for e in errors)
    assert notices == []


def test_task_depends_on_with_stage_tail_is_a_blocking_error(tmp_path):
    g = _guards()
    errors, notices = _task_guards(g, tmp_path, {"depends-on": ["foo@ready"]})
    assert any("task-edge-form" in e for e in errors)
    assert any("foo@ready" in e for e in errors)
    assert notices == []


def test_task_depends_on_bare_name_is_still_accepted(tmp_path):
    g = _guards()
    errors, notices = _task_guards(g, tmp_path, {"depends-on": ["foo"]})
    assert errors == []


def test_task_parent_with_kind_prefix_is_still_accepted(tmp_path):
    """``parent`` is deliberately untouched by the depends-on form rejection."""
    g = _guards()
    errors, notices = _task_guards(g, tmp_path, {"parent": "task/foo"})
    assert errors == []
    assert notices == []


def test_task_depends_on_traversal_still_reports_confinement_first(tmp_path):
    """A traversal value keeps its confinement rejection, not the form one."""
    g = _guards()
    errors, notices = _task_guards(g, tmp_path, {"depends-on": ["../evil"]})
    assert any("edge-reference" in e for e in errors)
    assert not any("task-edge-form" in e for e in errors)


# ---------------------------------------------------------------------------
# evaluate_design_guards — no-op outside the design kinds
# ---------------------------------------------------------------------------


def _design_guards(g, tmp_path, *, kind="spec", name="a", sidecar=None,
                   status_set=None, deleting=False):
    return g.evaluate_design_guards(
        kind=kind,
        name=name,
        sidecar={"kind": kind, **(sidecar or {})},
        vault_root=str(tmp_path),
        status_set=status_set,
        deleting=deleting,
    )


def test_design_guards_are_a_noop_for_task(tmp_path):
    g = _guards()
    assert _design_guards(
        g, tmp_path, kind="task", sidecar={"depends-on": ["b"]}, status_set="done"
    ) == ([], [])


def test_design_guards_are_a_noop_for_an_unrelated_kind(tmp_path):
    g = _guards()
    assert _design_guards(g, tmp_path, kind="decision", status_set="superseded") == ([], [])


# ---------------------------------------------------------------------------
# evaluate_design_guards — blocking grammar/vocabulary rejections
# ---------------------------------------------------------------------------


def test_design_bare_name_is_a_blocking_error(tmp_path):
    g = _guards()
    errors, notices = _design_guards(g, tmp_path, sidecar={"depends-on": ["other"]})
    assert any("design-edge-form" in e for e in errors)
    assert any("other" in e for e in errors)
    assert notices == []


def test_design_task_target_is_a_blocking_error(tmp_path):
    g = _guards()
    errors, notices = _design_guards(g, tmp_path, sidecar={"depends-on": ["task/foo"]})
    assert any("design-edge-form" in e for e in errors)
    assert any("task/foo" in e for e in errors)


def test_design_unknown_kind_prefix_is_a_blocking_error(tmp_path):
    g = _guards()
    errors, notices = _design_guards(g, tmp_path, sidecar={"depends-on": ["lesson/foo"]})
    assert any("design-edge-form" in e for e in errors)
    assert any("lesson" in e for e in errors)


def test_design_unknown_stage_is_a_blocking_error_listing_valid_stages(tmp_path):
    g = _guards()
    errors, notices = _design_guards(g, tmp_path, sidecar={"depends-on": ["spec/foo@bogus"]})
    assert any("design-edge-stage" in e for e in errors)
    joined = " ".join(errors)
    assert "bogus" in joined
    # the target kind's own stage vocabulary is spelled out, not just the violation
    assert "draft" in joined and "ready" in joined and "complete" in joined


def test_design_failure_status_stage_is_a_blocking_error_listing_valid_stages(tmp_path):
    g = _guards()
    errors, notices = _design_guards(g, tmp_path, sidecar={"depends-on": ["spec/foo@dropped"]})
    assert any("design-edge-stage" in e for e in errors)
    joined = " ".join(errors)
    assert "dropped" in joined
    assert "draft" in joined and "complete" in joined


def test_design_stage_vocabulary_is_the_target_kinds_not_the_writers(tmp_path):
    """An adr target's stages are listed, even when the record being written is a spec."""
    g = _guards()
    errors, _ = _design_guards(g, tmp_path, sidecar={"depends-on": ["adr/foo@planned"]})
    joined = " ".join(errors)
    assert "design-edge-stage" in joined
    assert "active" in joined
    assert "planned" not in joined.split("valid")[-1]


def test_design_traversal_name_reports_confinement_against_the_name(tmp_path):
    """Ordering: the stage tail is stripped and the kind validated before confinement."""
    g = _guards()
    errors, _ = _design_guards(g, tmp_path, sidecar={"depends-on": ["spec/../evil@ready"]})
    joined = " ".join(errors)
    assert "edge-reference" in joined
    assert "unsafe spec reference" in joined
    assert "'../evil'" in joined
    assert "design-edge-form" not in joined
    assert "design-edge-stage" not in joined


def test_design_grammar_rejection_blocks_before_the_vault_load(tmp_path, monkeypatch):
    g = _guards()
    called: list[str] = []
    monkeypatch.setattr(g, "load_design_sidecars", lambda vr: called.append(vr) or {})
    errors, _ = _design_guards(g, tmp_path, sidecar={"depends-on": ["other"]})
    assert errors
    assert called == []


# ---------------------------------------------------------------------------
# evaluate_design_guards — cycles block, dangling targets do not
# ---------------------------------------------------------------------------


def test_design_dangling_target_is_not_blocked(tmp_path):
    """No existence check — a dependency on an absent record is a valid write."""
    g = _guards()
    assert _design_guards(g, tmp_path, sidecar={"depends-on": ["spec/nowhere"]}) == ([], [])


def test_design_cycle_is_a_blocking_error(tmp_path):
    g = _guards()
    _write_design(tmp_path, "spec", "b", {"status": "draft", "depends-on": ["spec/a"]})
    errors, notices = _design_guards(g, tmp_path, sidecar={"depends-on": ["spec/b"]})
    assert any("design-depends-on-cycle" in e for e in errors)
    assert any("spec/a -> spec/b -> spec/a" in e for e in errors)
    assert notices == []


def test_design_cycle_is_stage_blind(tmp_path):
    g = _guards()
    _write_design(tmp_path, "spec", "b", {"status": "draft", "depends-on": ["spec/a@ready"]})
    errors, _ = _design_guards(g, tmp_path, sidecar={"depends-on": ["spec/b@draft"]})
    assert any("design-depends-on-cycle" in e for e in errors)


def test_design_cycle_spans_kinds(tmp_path):
    g = _guards()
    _write_design(tmp_path, "adr", "b", {"status": "draft", "depends-on": ["spec/a"]})
    errors, _ = _design_guards(g, tmp_path, sidecar={"depends-on": ["adr/b"]})
    assert any("design-depends-on-cycle" in e for e in errors)


def test_design_same_name_different_kind_is_not_a_cycle(tmp_path):
    g = _guards()
    _write_design(tmp_path, "adr", "a", {"status": "draft", "depends-on": ["spec/a"]})
    assert _design_guards(g, tmp_path, sidecar={"depends-on": ["spec/other"]}) == ([], [])


# ---------------------------------------------------------------------------
# evaluate_design_guards — non-blocking dependent notices
# ---------------------------------------------------------------------------


def test_design_superseded_with_dependents_warns_but_does_not_block(tmp_path):
    g = _guards()
    _write_design(tmp_path, "adr", "b", {"status": "draft", "depends-on": ["spec/a@ready"]})
    errors, notices = _design_guards(g, tmp_path, status_set="superseded")
    assert errors == []
    assert any("design-dependents" in n for n in notices)
    assert any("adr/b" in n for n in notices)


def test_design_dropped_with_dependents_warns_but_does_not_block(tmp_path):
    g = _guards()
    _write_design(tmp_path, "spec", "b", {"status": "draft", "depends-on": ["spec/a"]})
    errors, notices = _design_guards(g, tmp_path, status_set="dropped")
    assert errors == []
    assert any("design-dependents" in n for n in notices)


def test_design_superseded_without_dependents_is_silent(tmp_path):
    g = _guards()
    _write_design(tmp_path, "spec", "b", {"status": "draft"})
    assert _design_guards(g, tmp_path, status_set="superseded") == ([], [])


def test_design_delete_with_dependents_warns_only(tmp_path):
    g = _guards()
    _write_design(tmp_path, "spec", "b", {"status": "draft", "depends-on": ["spec/a"]})
    errors, notices = _design_guards(g, tmp_path, sidecar={}, deleting=True)
    assert errors == []
    assert any("design-dependents" in n for n in notices)
    assert any("spec/b" in n for n in notices)


def test_design_delete_is_never_blocked_by_a_cycle(tmp_path):
    g = _guards()
    _write_design(tmp_path, "spec", "b", {"status": "draft", "depends-on": ["spec/a"]})
    errors, _ = _design_guards(
        g, tmp_path, sidecar={"depends-on": ["spec/b"]}, deleting=True
    )
    assert errors == []


def test_design_status_only_update_skips_the_vault_wide_load(tmp_path, monkeypatch):
    """No edges and no failure transition means nothing needs the graph."""
    g = _guards()
    calls: list[str] = []
    monkeypatch.setattr(g, "load_design_sidecars", lambda vr: calls.append(vr) or {})
    assert _design_guards(g, tmp_path, status_set="ready") == ([], [])
    assert calls == []


# ---------------------------------------------------------------------------
# guard-tag namespacing — a design cycle is never read as a task cycle
# ---------------------------------------------------------------------------


def test_design_and_task_cycle_tags_are_distinguishable(tmp_path):
    g = _guards()
    _write_task(tmp_path, "b", {"status": "open", "depends-on": ["a"]})
    _write_design(tmp_path, "spec", "b", {"status": "draft", "depends-on": ["spec/a"]})
    task_errors, _ = _task_guards(g, tmp_path, {"depends-on": ["b"]})
    design_errors, _ = _design_guards(g, tmp_path, sidecar={"depends-on": ["spec/b"]})

    def tags(messages):
        return {m.split("[", 1)[1].split("]", 1)[0] for m in messages}

    assert tags(task_errors) == {"depends-on-cycle"}
    assert tags(design_errors) == {"design-depends-on-cycle"}
    assert not tags(task_errors) & tags(design_errors)


# ---------------------------------------------------------------------------
# evaluate_graph_guards — the kind dispatcher
# ---------------------------------------------------------------------------


def _dispatch(g, tmp_path, *, kind, name="a", sidecar=None, body="",
              status_set=None, deleting=False):
    return g.evaluate_graph_guards(
        kind=kind,
        name=name,
        sidecar={"kind": kind, **(sidecar or {})},
        body=body,
        vault_root=str(tmp_path),
        status_set=status_set,
        deleting=deleting,
    )


def test_dispatcher_routes_task_to_the_task_guards(tmp_path):
    g = _guards()
    _write_task(tmp_path, "b", {"status": "open", "depends-on": ["a"]})
    errors, _ = _dispatch(
        g, tmp_path, kind="task", sidecar={"status": "open", "depends-on": ["b"]},
        status_set="open",
    )
    assert any("[depends-on-cycle]" in e for e in errors)


def test_dispatcher_routes_spec_to_the_design_guards(tmp_path):
    g = _guards()
    _write_design(tmp_path, "spec", "b", {"status": "draft", "depends-on": ["spec/a"]})
    errors, _ = _dispatch(g, tmp_path, kind="spec", sidecar={"depends-on": ["spec/b"]})
    assert any("[design-depends-on-cycle]" in e for e in errors)


def test_dispatcher_routes_adr_to_the_design_guards(tmp_path):
    g = _guards()
    errors, _ = _dispatch(g, tmp_path, kind="adr", sidecar={"depends-on": ["bare"]})
    assert any("[design-edge-form]" in e for e in errors)


def test_dispatcher_is_a_noop_for_an_unrelated_kind(tmp_path):
    g = _guards()
    for kind in ("decision", "area"):
        _write_task(tmp_path, "b", {"status": "open", "depends-on": ["a"]})
        assert _dispatch(
            g, tmp_path, kind=kind, sidecar={"depends-on": ["spec/b"]},
            body="", status_set="superseded",
        ) == ([], [])
