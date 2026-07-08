"""Pure-unit tests for ``lore.record.graph`` — the task graph model.

The module is pure (dict-in, dict-out): it builds edge maps from a
``{name: sidecar}`` dict, detects ``depends-on`` cycles and ``parent`` ancestor
loops, and answers containment/dependency queries (``children`` / ``leaves`` /
``runnable`` / ``dependents`` / ``non_terminal_children``). No vault, no I/O.

Also covers the shared guard-error formatting helper: every graph guard message
(blocking error, non-blocking warning, ritual reminder) is emitted through one
helper so agents parse a single stable ``graph-guard [<guard>]: …`` shape off
stderr.
"""

from __future__ import annotations

import re

from conftest import load_script


def _graph():
    return load_script("lore.record.graph")


def _task(status="open", *, depends_on=None, parent=None):
    """Minimal task sidecar with the fields the graph reads."""
    sc = {"kind": "task", "status": status}
    if depends_on is not None:
        sc["depends-on"] = list(depends_on)
    if parent is not None:
        sc["parent"] = parent
    return sc


# ---------------------------------------------------------------------------
# edge maps
# ---------------------------------------------------------------------------


def test_depends_on_edges_maps_each_node_to_its_deps():
    g = _graph()
    graph = {
        "a": _task(depends_on=["b", "c"]),
        "b": _task(),
        "c": _task(),
    }
    assert g.depends_on_edges(graph) == {"a": ["b", "c"], "b": [], "c": []}


def test_parent_edges_only_includes_nodes_with_a_parent():
    g = _graph()
    graph = {"a": _task(parent="b"), "b": _task()}
    assert g.parent_edges(graph) == {"a": "b"}


def test_children_returns_nodes_whose_parent_is_name():
    g = _graph()
    graph = {"p": _task(), "c1": _task(parent="p"), "c2": _task(parent="p"), "x": _task()}
    assert g.children(graph, "p") == ["c1", "c2"]


def test_dependents_returns_nodes_that_depend_on_name():
    g = _graph()
    graph = {"a": _task(), "b": _task(depends_on=["a"]), "c": _task(depends_on=["a", "z"])}
    assert g.dependents(graph, "a") == ["b", "c"]


def test_leaves_are_nodes_with_no_children():
    g = _graph()
    graph = {"p": _task(), "c1": _task(parent="p"), "c2": _task(parent="p")}
    assert g.leaves(graph) == ["c1", "c2"]


# ---------------------------------------------------------------------------
# non_terminal_children — the completion-guard predicate
# ---------------------------------------------------------------------------


def test_non_terminal_children_excludes_done_dropped_superseded():
    g = _graph()
    graph = {
        "p": _task(),
        "open": _task("open", parent="p"),
        "prog": _task("in-progress", parent="p"),
        "done": _task("done", parent="p"),
        "drop": _task("dropped", parent="p"),
        "sup": _task("superseded", parent="p"),
    }
    assert g.non_terminal_children(graph, "p") == ["open", "prog"]


def test_all_terminal_children_yield_empty_non_terminal_set():
    g = _graph()
    graph = {
        "p": _task(),
        "a": _task("done", parent="p"),
        "b": _task("dropped", parent="p"),
        "c": _task("superseded", parent="p"),
    }
    assert g.non_terminal_children(graph, "p") == []


# ---------------------------------------------------------------------------
# runnable — ready + all deps done
# ---------------------------------------------------------------------------


def test_runnable_requires_ready_and_all_deps_done():
    g = _graph()
    graph = {
        "dep": _task("done"),
        "ready_all_done": _task("ready", depends_on=["dep"]),
        "ready_dep_open": _task("ready", depends_on=["open_one"]),
        "open_one": _task("open"),
        "ready_no_deps": _task("ready"),
        "in_prog": _task("in-progress"),
    }
    assert g.runnable(graph) == ["ready_all_done", "ready_no_deps"]


def test_runnable_treats_missing_dep_as_not_done():
    g = _graph()
    graph = {"a": _task("ready", depends_on=["ghost"])}
    assert g.runnable(graph) == []


# ---------------------------------------------------------------------------
# dependency-cycle detection
# ---------------------------------------------------------------------------


def test_find_dependency_cycle_detects_two_node_loop():
    g = _graph()
    graph = {"a": _task(depends_on=["b"]), "b": _task(depends_on=["a"])}
    cycle = g.find_dependency_cycle(graph, start="a")
    assert cycle is not None
    assert cycle[0] == cycle[-1]  # closed loop
    assert set(cycle) == {"a", "b"}


def test_find_dependency_cycle_none_for_acyclic():
    g = _graph()
    graph = {"a": _task(depends_on=["b"]), "b": _task(depends_on=["c"]), "c": _task()}
    assert g.find_dependency_cycle(graph, start="a") is None


def test_find_dependency_cycle_ignores_dangling_deps():
    g = _graph()
    graph = {"a": _task(depends_on=["ghost"])}
    assert g.find_dependency_cycle(graph, start="a") is None


# ---------------------------------------------------------------------------
# ancestor-loop detection
# ---------------------------------------------------------------------------


def test_find_ancestor_loop_detects_self_parent():
    g = _graph()
    graph = {"a": _task(parent="a")}
    loop = g.find_ancestor_loop(graph, "a")
    assert loop is not None
    assert loop[0] == loop[-1] == "a"


def test_find_ancestor_loop_detects_deep_loop():
    g = _graph()
    graph = {
        "a": _task(parent="b"),
        "b": _task(parent="c"),
        "c": _task(parent="a"),
    }
    loop = g.find_ancestor_loop(graph, "a")
    assert loop is not None
    assert loop[0] == loop[-1]
    assert set(loop) == {"a", "b", "c"}


def test_find_ancestor_loop_none_for_finite_chain():
    g = _graph()
    graph = {"a": _task(parent="b"), "b": _task(parent="c"), "c": _task()}
    assert g.find_ancestor_loop(graph, "a") is None


def test_find_ancestor_loop_none_for_dangling_parent():
    g = _graph()
    graph = {"a": _task(parent="ghost")}
    assert g.find_ancestor_loop(graph, "a") is None


# ---------------------------------------------------------------------------
# shared guard-error formatting helper
# ---------------------------------------------------------------------------

_SHAPE = re.compile(r"^graph-guard \[[a-z-]+\]: ")


def test_format_guard_message_shape():
    g = _graph()
    msg = g.format_guard_message("depends-on-cycle", "a would cycle")
    assert _SHAPE.match(msg)
    assert "[depends-on-cycle]" in msg
    assert msg.endswith("a would cycle")


def test_format_guard_message_appends_offenders():
    g = _graph()
    msg = g.format_guard_message("parent-completion", "open children", offenders=["c1", "c2"])
    assert _SHAPE.match(msg)
    assert msg.endswith("c1, c2")


def test_all_guard_tags_share_one_format():
    g = _graph()
    for guard in (
        "depends-on-cycle",
        "parent-loop",
        "parent-completion",
        "dependents",
        "flow-out",
        "edge-reference",
    ):
        assert _SHAPE.match(g.format_guard_message(guard, "message", offenders=["x"]))
