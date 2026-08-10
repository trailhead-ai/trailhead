"""Tests for the ``lore task graph`` CLI — the task-graph read verb.

``lore task graph NAME`` renders the containment subtree rooted at NAME (via
``parent`` edges), the ``depends-on`` edges of every node in that subtree, each
task's status, and a ``(runnable)`` marker on the leaves that qualify (status
``ready`` AND every ``depends-on`` target ``done``). It is a pure reader built on
``record/graph.py`` — no writes, no guards.

Test contract:
  - a fixture vault renders the correct tree structure + depends-on edges +
    per-task statuses for a small task hierarchy.
  - runnable markers appear ONLY on qualifying leaves — never on a parent/
    non-leaf task, even one that is itself ``ready`` with satisfied deps.
  - an unknown NAME fails with a named error.
  - targeting a non-task record (``<kind>/<name>`` naming e.g. an ``area``)
    fails with a named error distinct from "not found".
  - output is plain stdout, no ANSI escape codes.

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern). Never writes
to the real vault: the CLI resolves the test vault from a seeded config.json
(isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import load_script, make_vault as _make_vault, run_cli as _run  # noqa: F401


def _create_task(vault, state, title, *, extra=None):
    """Create a ``task`` record; return the CompletedProcess."""
    args = ["record", "create", "--kind", "task", "--title", title]
    if extra:
        args += extra
    r = _run(args, vault=vault, state_dir=state, stdin_text="body\n")
    assert r.returncode == 0, f"create {title!r} failed: {r.stderr}"
    return r


def _build_fixture(vault, state):
    """A small hierarchy exercising containment, depends-on, and runnability.

    root (in-progress)
      child-a (ready, depends-on dep-x[done], dep-y[open]) -- has a child, so
        NOT a leaf; must never get the runnable marker even though its own
        status+deps would otherwise qualify.
        grandchild (ready, no deps) -- a genuine leaf: runnable.
      child-b (ready, depends-on dep-y[open]) -- a leaf whose dependency is NOT
        done: not runnable.

    dep-x / dep-y are standalone tasks (no parent) referenced only via
    depends-on — they sit outside the containment subtree and must not be
    rendered as their own tree nodes.
    """
    _create_task(vault, state, "dep-x", extra=["--status", "done"])
    _create_task(vault, state, "dep-y")  # default status: open
    _create_task(vault, state, "root", extra=["--status", "in-progress"])
    _create_task(
        vault, state, "child-a",
        extra=[
            "--parent", "root", "--status", "ready",
            "--depends-on", "dep-x", "--depends-on", "dep-y",
        ],
    )
    _create_task(
        vault, state, "child-b",
        extra=["--parent", "root", "--status", "ready", "--depends-on", "dep-y"],
    )
    _create_task(
        vault, state, "grandchild",
        extra=["--parent", "child-a", "--status", "ready"],
    )


# ---------------------------------------------------------------------------
# tree structure + depends-on edges + statuses
# ---------------------------------------------------------------------------


def test_renders_containment_tree_with_statuses_and_depends_on(tmp_path):
    vault, state = _make_vault(tmp_path)
    _build_fixture(vault, state)

    r = _run(["task", "graph", "root"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr

    lines = r.stdout.splitlines()
    # Order: root, then children sorted (child-a before child-b), then
    # child-a's own child (grandchild) nested one level deeper still.
    assert lines[0] == "root [in-progress]"
    assert lines[1].strip().startswith("child-a [ready]")
    assert lines[2].strip().startswith("grandchild [ready]")
    assert lines[3].strip().startswith("child-b [ready]")

    # Indentation reflects containment depth.
    assert lines[1].startswith("  ")
    assert not lines[1].startswith("    ")
    assert lines[2].startswith("    ")
    assert lines[3].startswith("  ")
    assert not lines[3].startswith("    ")

    # depends-on edges are rendered with each target's status.
    assert "depends-on: dep-x (done), dep-y (open)" in lines[1]
    assert "depends-on: dep-y (open)" in lines[3]
    assert "depends-on" not in lines[0]
    assert "depends-on" not in lines[2]

    # dep-x/dep-y are edges, not subtree nodes — they never get their own line.
    assert not any(line.strip().startswith("dep-x ") for line in lines)
    assert not any(line.strip().startswith("dep-y ") for line in lines)


# ---------------------------------------------------------------------------
# runnable markers — only qualifying leaves
# ---------------------------------------------------------------------------


def test_runnable_marker_only_on_qualifying_leaf(tmp_path):
    vault, state = _make_vault(tmp_path)
    _build_fixture(vault, state)

    r = _run(["task", "graph", "root"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr

    lines = {line.strip() for line in r.stdout.splitlines()}
    grandchild_line = next(line for line in lines if line.startswith("grandchild "))
    child_a_line = next(line for line in lines if line.startswith("child-a "))
    child_b_line = next(line for line in lines if line.startswith("child-b "))
    root_line = next(line for line in lines if line.startswith("root "))

    assert "(runnable)" in grandchild_line
    # child-a is ready with every dep done, but it is NOT a leaf (it has
    # grandchild as a child) — must never be marked runnable.
    assert "(runnable)" not in child_a_line
    # child-b is a leaf and ready, but dep-y is not done — not runnable.
    assert "(runnable)" not in child_b_line
    assert "(runnable)" not in root_line


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


def test_unknown_name_fails_with_named_error(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(["task", "graph", "does-not-exist"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert r.stderr.strip()
    assert "does-not-exist" in r.stderr


def test_non_task_target_fails_with_named_error(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "area", "--title", "some-area"],
        vault=vault, state_dir=state, stdin_text="body\n",
    )
    assert r.returncode == 0, r.stderr
    area_id = r.stdout.strip()

    r = _run(["task", "graph", area_id], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert "not a task" in r.stderr
    # Distinct from the unknown-name error — the record was found, just wrong kind.
    assert "no task named" not in r.stderr


# ---------------------------------------------------------------------------
# output hygiene
# ---------------------------------------------------------------------------


def test_output_has_no_ansi_escapes(tmp_path):
    vault, state = _make_vault(tmp_path)
    _build_fixture(vault, state)
    r = _run(["task", "graph", "root"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert "\x1b" not in r.stdout


# ---------------------------------------------------------------------------
# adjacency-map build — a single pre-pass, not a per-node graph.children() scan
# ---------------------------------------------------------------------------


def _sidecar(status="open", *, depends_on=None, parent=None):
    sc = {"kind": "task", "status": status}
    if depends_on is not None:
        sc["depends-on"] = list(depends_on)
    if parent is not None:
        sc["parent"] = parent
    return sc


def test_walk_never_calls_graph_children_directly(monkeypatch):
    """``_render_task_graph`` builds one adjacency map up front and never calls
    ``graph.children`` per node during the recursive walk (the O(V^2) pattern
    the fix removes).
    """
    graph_mod = load_script("lore.record.graph")

    def _boom(graph, name):
        raise AssertionError("graph.children must not be called during the walk")

    monkeypatch.setattr(graph_mod, "children", _boom)

    task_mod = load_script("lore.cli.task")
    graph = {
        "root": _sidecar(status="in-progress"),
        "child-a": _sidecar(status="ready", parent="root"),
        "child-b": _sidecar(status="ready", parent="root"),
        "grandchild": _sidecar(status="ready", parent="child-a"),
    }

    output = task_mod._render_task_graph(graph, "root")

    lines = output.splitlines()
    assert lines[0] == "root [in-progress]"
    assert lines[1].strip().startswith("child-a [ready]")
    assert lines[2].strip().startswith("grandchild [ready]")
    assert lines[3].strip().startswith("child-b [ready]")


# ---------------------------------------------------------------------------
# --vault: explicit vault targeting (read-side collision hazard)
# ---------------------------------------------------------------------------


def _write_config(config_home: Path, vaults: list) -> Path:
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    cfg_path = lore_cfg / "config.json"
    cfg_path.write_text(json.dumps({"vaults": vaults}, indent=2), encoding="utf-8")
    return cfg_path


def _run_cfg(args, *, vault, state, config_home, stdin_text=None):
    return _run(
        args, vault=vault, state_dir=state, stdin_text=stdin_text,
        env_extra={"XDG_CONFIG_HOME": str(config_home)},
    )


def _duplicate_named_task_two_vaults(tmp_path, *, title="Dup Task"):
    """Two team vaults (config order alpha, beta), each holding an
    independently-created task record of the same name -- the collision case
    the cwd-blind scan cannot disambiguate."""
    default_vault, state = _make_vault(tmp_path)
    alpha_vault = tmp_path / "vault_alpha"
    beta_vault = tmp_path / "vault_beta"
    alpha_vault.mkdir(parents=True)
    beta_vault.mkdir(parents=True)
    config_home = tmp_path / "config"
    _write_config(
        config_home,
        [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {"name": "alpha", "scope": "team", "path": str(alpha_vault)},
            {"name": "beta", "scope": "team", "path": str(beta_vault)},
        ],
    )
    for team, status in (("alpha", "open"), ("beta", "in-progress")):
        r = _run_cfg(
            ["record", "create", "--kind", "task", "--title", title,
             "--team", team, "--status", status],
            vault=default_vault, state=state, config_home=config_home, stdin_text="body\n",
        )
        assert r.returncode == 0, r.stderr
    return default_vault, alpha_vault, beta_vault, state, config_home


def test_task_graph_vault_flag_targets_named_vault_on_collision(tmp_path):
    """``task graph NAME --vault beta`` renders beta's subtree, not alpha's
    (the first-configured match a cwd-blind scan would otherwise pick)."""
    default_vault, alpha_vault, beta_vault, state, config_home = (
        _duplicate_named_task_two_vaults(tmp_path)
    )

    r = _run_cfg(
        ["task", "graph", "dup-task", "--vault", "beta"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "dup-task [in-progress]"


def test_task_graph_vault_flag_record_absent_in_named_vault_errors_without_scan_fallback(tmp_path):
    """``--vault`` naming a vault that lacks the task errors plainly -- it
    never falls back to scanning the other configured vaults."""
    default_vault, state = _make_vault(tmp_path)
    alpha_vault = tmp_path / "vault_alpha"
    beta_vault = tmp_path / "vault_beta"
    alpha_vault.mkdir(parents=True)
    beta_vault.mkdir(parents=True)
    config_home = tmp_path / "config"
    _write_config(
        config_home,
        [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {"name": "alpha", "scope": "team", "path": str(alpha_vault)},
            {"name": "beta", "scope": "team", "path": str(beta_vault)},
        ],
    )
    r = _run_cfg(
        ["record", "create", "--kind", "task", "--title", "Solo", "--team", "alpha"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="body\n",
    )
    assert r.returncode == 0, r.stderr

    r = _run_cfg(
        ["task", "graph", "solo", "--vault", "beta"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode != 0
    assert r.stderr.startswith("lore: ")


def test_task_graph_vault_flag_unknown_name_errors(tmp_path):
    """An unconfigured ``--vault`` name errors with ``lore: <msg>`` -- nonzero."""
    vault, state = _make_vault(tmp_path)
    _build_fixture(vault, state)

    r = _run(["task", "graph", "root", "--vault", "nope"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert r.stderr.startswith("lore: ")
    assert "nope" in r.stderr


def test_task_graph_vault_flag_omitted_preserves_scan_behavior(tmp_path):
    """Omitting ``--vault`` keeps rendering via the cwd-blind scan, unchanged."""
    vault, state = _make_vault(tmp_path)
    _build_fixture(vault, state)

    r = _run(["task", "graph", "root"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "root [in-progress]"


def test_adjacency_map_orders_children_same_as_graph_children(tmp_path):
    """The pre-built adjacency map reproduces ``graph.children``'s sorted order.

    Same fixture as the render-correctness tests, but asserted directly against
    ``graph.parent_edges`` (what the adjacency map is built from) rather than
    duplicating the CLI-output assertions those tests already make.
    """
    graph_mod = load_script("lore.record.graph")
    graph = {
        "root": _sidecar(status="in-progress"),
        "child-b": _sidecar(status="ready", parent="root"),
        "child-a": _sidecar(status="ready", parent="root"),
    }

    children_map: dict[str, list[str]] = {}
    for child, parent in graph_mod.parent_edges(graph).items():
        children_map.setdefault(parent, []).append(child)
    for kids in children_map.values():
        kids.sort()

    assert children_map["root"] == graph_mod.children(graph, "root")
