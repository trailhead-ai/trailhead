"""``select_touched_by_path`` answers, per path, what ``select_touched`` answers for that path alone."""
from __future__ import annotations

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.eval_gate import select
from test_portage_eval_gate_plan import (  # noqa: F401
    _branch, _commit, _corpus, _declared_corpus, _git, _mapping, _write, repo,
)

PATHS = ["p/skills/a.md", "p/skills/b.md", "q/skills/a.md", "r/skills/a.md",
         "p/evals/alpha/case.yaml", "p/evals/delta/case.yaml", "nothing/touches/this.md"]


@pytest.fixture
def mixed(repo):  # noqa: F811  (repo is the imported fixture)
    _corpus(repo, "p", {"alpha": {"measures": ["skills/a.md"]},
                        "beta": {"measures": ["skills/b.md"]},
                        "gamma": {"measures": ["skills/a.md"], "merge_only": True},
                        "delta": {"measures": ["skills/b.md"], "runs": 5, "pass_at": 4}})
    _corpus(repo, "q", {"qa": {"measures": ["skills/a.md"]}})
    _commit(repo)
    _branch(repo)
    _write(repo, "p/evals/eval-gate.json", _mapping({
        "alpha": {"measures": ["skills/a.md"]},
        "beta": {"measures": ["skills/b.md"], "merge_only": True},
        "gamma": {"measures": ["skills/a.md"], "merge_only": False}}))
    _git(repo, "rm", "-rq", "p/evals/delta")
    _git(repo, "rm", "-rq", "q")
    _corpus(repo, "r", {"ra": {"measures": ["skills/a.md"]}},
            runner={"model": "haiku", "allow_tools": ["Read"], "max_cost_usd": 1.0})
    _commit(repo)
    return repo


@pytest.mark.parametrize("either", [False, True])
def test_each_path_gets_the_entries_select_touched_gives_it_alone(mixed, either):
    base = _git(mixed, "merge-base", "base", "HEAD")
    many = select.select_touched_by_path(mixed, base, PATHS, merge_only_either=either)
    assert list(many) == PATHS
    for path in PATHS:
        assert many[path] == select.select_touched(mixed, base, [path], merge_only_either=either), path


def test_the_fixture_exercises_each_case_the_contract_names(mixed):
    base = _git(mixed, "merge-base", "base", "HEAD")
    many = select.select_touched_by_path(mixed, base, PATHS, merge_only_either=True)
    flags = {e["case"]: e for es in many.values() for e in es}
    assert flags["beta"]["merge_only"] is True
    assert select.select_touched_by_path(mixed, base, ["p/skills/b.md"])["p/skills/b.md"][0]["merge_only"] is False
    assert flags["delta"]["case_removed"] is True
    assert flags["ra"]["new_runner_grants"] is True
    assert many["nothing/touches/this.md"] == []


@pytest.fixture
def shapes(repo):  # noqa: F811
    cases = {"dirm": {"measures": ["skills/dir"]},
             "nested": {"measures": ["skills/dir/inner"]},
             "filem": {"measures": ["skills/f.md"]},
             "pa": {"measures": ["skills/a"]},
             "pab": {"measures": ["skills/ab"]},
             "slash": {"measures": ["skills/s/"]},
             "dot": {"measures": ["./skills/d"]},
             "multi": {"measures": ["skills/a", "skills/dir"]},
             "dir": {"measures": ["skills/z"]}}
    _corpus(repo, "p", cases, files=("skills/f.md", "skills/dir/x.md", "skills/dir/inner/y.md", "skills/a/x",
                                  "skills/ab/x", "skills/s/x", "skills/d/x", "skills/z"))
    _corpus(repo, "root", {"rc": {"measures": ["skills/a"]}}, files=("skills/a",))
    _commit(repo)
    return repo


SHAPE_PATHS = [
    "p/skills/dir", "p/skills/dir/x.md", "p/skills/dir/inner", "p/skills/dir/inner/y.md",
    "p/skills/dir/innerx", "p/skills/f.md", "p/skills/f.md/under", "p/skills/f.mdx",
    "p/skills/a", "p/skills/a/x", "p/skills/ab", "p/skills/ab/x", "p/skills/abc", "p/skills/aa",
    "p/skills/s", "p/skills/s/x", "p/skills/d", "p/skills/d/x",
    "p/evals/dir", "p/evals/dir/case.yaml", "p/evals/dir/deep/x", "p/evals/dirx/case.yaml",
    "p/evals/dirm/case.yaml", "p/evals", "p", "", "p/skills", "p/skills/dirty",
    "skills/a", "skills/a/x", "root/skills/a", "other/skills/a", "p//skills/dir/x.md", "p/skills/dir/",
]


def test_every_path_shape_matches_select_touched_alone(shapes):
    base = _git(shapes, "rev-parse", "HEAD")
    many = select.select_touched_by_path(shapes, base, SHAPE_PATHS)
    assert list(many) == SHAPE_PATHS
    for path in SHAPE_PATHS:
        assert many[path] == select.select_touched(shapes, base, [path]), path


def test_the_shape_corpus_selects_for_some_paths_and_not_others(shapes):
    base = _git(shapes, "rev-parse", "HEAD")
    names = lambda p: [e["case"] for e in select.select_touched_by_path(shapes, base, [p])[p]]  # noqa: E731
    assert names("p/skills/a/x") == ["multi", "pa"]
    assert names("p/skills/ab") == ["pab"]
    assert names("p/skills/abc") == []
    assert names("p/skills/s/x") == ["slash"]
    assert names("p/skills/d") == ["dot"]
    assert names("p/evals/dir/deep/x") == ["dir"]


def test_many_paths_are_answered_without_testing_each_candidate(shapes, monkeypatch):
    base = _git(shapes, "rev-parse", "HEAD")

    def boom(*a, **k):
        raise AssertionError("_touches called per path")

    monkeypatch.setattr(select, "_touches", boom)
    paths = SHAPE_PATHS + [f"p/skills/dir/n{i}.md" for i in range(200)]
    many = select.select_touched_by_path(shapes, base, paths)
    assert [e["case"] for e in many["p/skills/dir/n7.md"]] == ["dirm", "multi"]
    assert [e["case"] for e in many["p/skills/ab/x"]] == ["pab"]
    assert many["p/skills/abc"] == []


def _reference(checkout, base, path, **kw):
    """What the per-candidate ``_touches`` scan answers for ``path`` alone."""
    tree = select._head_tree(checkout)
    either, tier = kw.get("merge_only_either", False), kw.get("tier", "merge")
    candidates = select._candidates(checkout, base, tree, either, tier)[0]
    return [entry for plugin, measures, entry in candidates
            if select._touches(entry["case"], measures, plugin, [path])]


@pytest.mark.parametrize("tier", ["push", "merge"])
def test_every_path_shape_selects_what_the_touches_scan_selects(shapes, tier):
    base = _git(shapes, "rev-parse", "HEAD")
    many = select.select_touched_by_path(shapes, base, SHAPE_PATHS, tier=tier)
    for path in SHAPE_PATHS:
        assert many[path] == _reference(shapes, base, path, tier=tier), path
        assert select.select_touched(shapes, base, [path], tier=tier) == many[path], path


def test_a_changed_set_selects_the_union_in_candidate_order_without_repeats(shapes):
    base = _git(shapes, "rev-parse", "HEAD")
    got = select.select_touched(shapes, base, ["p/skills/a/x", "p/skills/ab/x", "p/skills/a", "p/skills/abc"])
    assert [e["case"] for e in got] == ["multi", "pa", "pab"]


def test_selection_does_not_scan_candidates_per_changed_path(shapes, monkeypatch):
    base = _git(shapes, "rev-parse", "HEAD")

    def boom(*a, **k):
        raise AssertionError("_touches called")

    monkeypatch.setattr(select, "_touches", boom)
    changed = SHAPE_PATHS + [f"p/skills/dir/n{i}.md" for i in range(200)]
    got = select.select_touched(shapes, base, changed)
    assert {"dirm", "multi", "pab", "pa", "slash"} <= {e["case"] for e in got}


def test_plan_does_not_scan_candidates_per_changed_path(shapes, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("_touches called")

    monkeypatch.setattr(select, "_touches", boom)
    _git(shapes, "branch", "base")
    _git(shapes, "checkout", "-q", "-b", "feature")
    for i in range(30):
        _write(shapes, f"p/skills/dir/n{i}.md", "x\n")
    _write(shapes, "p/skills/ab/x", "changed\n")
    _commit(shapes)
    state, result = select.plan(shapes, "base", tier="merge")
    assert result["status"] == "cases-selected"
    assert [c["case"] for c in result["cases"]] == ["dirm", "multi", "pab"]


class _CountingDict(dict):
    def __init__(self, tally):
        super().__init__()
        self.tally = tally

    def get(self, key, default=None):
        self.tally["lookups"] += 1
        self.tally["chars"] += len(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.tally["lookups"] += 1
        self.tally["chars"] += len(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self.tally["lookups"] += 1
        self.tally["chars"] += len(key)
        return super().__contains__(key)


@pytest.mark.parametrize("path", [
    "p/skills/dir/" + "x" * 100_000,
    "p/skills/dir/" + "/".join("seg" for _ in range(20_000)),
    "p/skills/dir/" + "/" * 50_000,
    "p/skills/dir/" + "/".join(["abcdefgh"] * 12_000),
], ids=["one-long-name", "many-components", "many-empty-components", "long-and-many"])
def test_per_path_work_is_linear_in_the_path_length(shapes, monkeypatch, path):
    tally = {"lookups": 0, "chars": 0}
    real_node = select._Node

    class Counting(real_node):
        def __init__(self):
            super().__init__()
            self.children = _CountingDict(tally)

    monkeypatch.setattr(select, "_Node", Counting)
    base = _git(shapes, "rev-parse", "HEAD")
    many = select.select_touched_by_path(shapes, base, [path])
    assert [e["case"] for e in many[path]] == ["dirm", "multi"]
    assert tally["lookups"] <= path.count("/") + 1
    assert tally["chars"] <= len(path)


class _ScanCountingTree(dict):
    """A tree that counts the entries handed out by iteration and the keyed lookups made."""

    def __init__(self, data, tally):
        super().__init__(data)
        self.tally = tally

    def __iter__(self):
        for k in super().__iter__():
            self.tally["scanned"] += 1
            yield k

    def keys(self):
        return iter(self)

    def items(self):
        for k, v in super().items():
            self.tally["scanned"] += 1
            yield k, v

    def values(self):
        for v in super().values():
            self.tally["scanned"] += 1
            yield v

    def get(self, key, default=None):
        self.tally["gets"] += 1
        return super().get(key, default)


_MAPPING_PATH = "p/evals/eval-gate.json"
_BLOB = "100644"


def _mapping_of(cases):
    import json

    from portage.eval_gate.mapping import load_mapping
    runner = {"model": "sonnet", "allow_tools": ["Read"], "max_cost_usd": 1.0}
    return load_mapping(json.dumps({"schema_version": 1, "runner": runner, "cases": cases}), _MAPPING_PATH)


def _tree_of(*paths, extra=None):
    tree = {p: _BLOB for p in paths}
    tree["p/evals/c/case.yaml"] = _BLOB
    tree.update(extra or {})
    return tree


def _check(measures, tree):
    select._check_against_tree(_MAPPING_PATH, _mapping_of({"c": {"measures": measures}}), tree)


@pytest.mark.parametrize("measures", [1, 64])
def test_tree_checking_scans_the_tree_at_most_once_however_many_measures_a_case_holds(measures):
    tally = {"scanned": 0, "gets": 0}
    entries = [f"p/skills/dir{i}/x.md" for i in range(150)]
    tree = _ScanCountingTree(_tree_of(*entries), tally)
    _check([f"skills/dir{i}" for i in range(measures)], tree)
    assert tally["scanned"] <= len(tree)
    assert tally["gets"] <= measures + 1


def test_a_tree_shared_by_several_mappings_is_scanned_twice_per_selection_not_per_mapping(shapes):
    base = _git(shapes, "rev-parse", "HEAD")
    tally = {"scanned": 0, "gets": 0}
    tree = _ScanCountingTree(select._head_tree(shapes), tally)
    got = select.select_touched(shapes, base, ["p/skills/dir/x.md"], tree=tree)
    assert [e["case"] for e in got] == ["dirm", "multi"]
    assert tally["scanned"] <= 2 * len(tree)


@pytest.mark.parametrize("measures, entries, held", [
    (["skills/f.md"], ["p/skills/f.md"], True),
    (["skills/dir"], ["p/skills/dir/x.md"], True),
    (["skills/dir/inner"], ["p/skills/dir/inner/y.md"], True),
    (["skills/a"], ["p/skills/ab/x"], False),
    (["skills/ab"], ["p/skills/a/x"], False),
    (["skills/a"], ["p/skills/a"], True),
    (["skills/nope"], ["p/skills/dir/x.md"], False),
    (["skills/f.md"], ["p/skills/f.mdx"], False),
    (["skills/dir"], ["p/skills/dirty/x.md"], False),
    (["skills/dir", "skills/gone"], ["p/skills/dir/x.md"], False),
], ids=["file", "dir", "nested", "sibling-prefix-a-vs-ab", "sibling-prefix-ab-vs-a", "file-named-like-dir",
        "missing", "file-prefix", "dir-prefix", "one-of-two-missing"])
def test_a_measure_is_held_exactly_when_a_blob_or_a_path_under_it_is_committed(measures, entries, held):
    tree = _tree_of(*entries)
    if held:
        _check(measures, tree)
        return
    with pytest.raises(select.MappingError) as e:
        _check(measures, tree)
    assert "case 'c': measures path " in e.value.fault
    assert "does not exist under the plugin root" in e.value.fault
    assert e.value.file == _MAPPING_PATH


def test_a_non_blob_entry_named_by_a_measure_is_not_held_but_one_under_it_holds_it():
    tree = _tree_of(extra={"p/skills/link": "120000", "p/skills/sub": "160000", "p/skills/d/x": _BLOB})
    with pytest.raises(select.MappingError, match="'skills/link'"):
        _check(["skills/link"], tree)
    with pytest.raises(select.MappingError, match="'skills/sub'"):
        _check(["skills/sub"], tree)
    _check(["skills/d"], tree)


def test_the_first_missing_measure_in_declaration_order_is_the_one_named():
    tree = _tree_of("p/skills/dir/x.md")
    with pytest.raises(select.MappingError) as e:
        _check(["skills/dir", "skills/two", "skills/one", "skills/two"], tree)
    assert "'skills/two'" in e.value.fault


def test_a_case_without_a_committed_case_yaml_is_a_fault_after_its_measures_pass():
    tree = {"p/skills/f.md": _BLOB}
    with pytest.raises(select.MappingError, match="case 'c' has no case.yaml"):
        _check(["skills/f.md"], tree)


class _CountingList(list):
    def __init__(self, tally):
        super().__init__()
        self.tally = tally

    def __iter__(self):
        for i in super().__iter__():
            self.tally["absorbed"] += 1
            yield i


@pytest.mark.parametrize("candidates, paths", [(1, 300), (256, 1), (256, 300)])
def test_candidate_sets_absorbed_grow_with_candidates_plus_paths_not_their_product(repo, monkeypatch,  # noqa: F811
                                                                                    candidates, paths):
    cases = {f"c{i}": {"measures": ["skills/dir"]} for i in range(candidates)}
    _corpus(repo, "p", cases, files=("skills/dir/x.md",))
    _commit(repo)
    base = _git(repo, "rev-parse", "HEAD")
    tally = {"absorbed": 0}
    real_node = select._Node

    class Counting(real_node):
        def __init__(self):
            super().__init__()
            self.targets = _CountingList(tally)
            self.cases = _CountingList(tally)

    monkeypatch.setattr(select, "_Node", Counting)
    changed = [f"p/skills/dir/n{i}.md" for i in range(paths)]
    got = select.select_touched(repo, base, changed)
    assert [e["case"] for e in got] == sorted(cases)
    assert tally["absorbed"] <= candidates + paths


def test_a_node_reached_by_one_path_as_a_target_and_by_a_deeper_one_as_a_case_dir_adds_both_sets(repo):  # noqa: F811
    _corpus(repo, "p", {"a": {"measures": ["evals/g"]}, "g": {"measures": ["skills/f.md"]}},
            files=("skills/f.md",))
    _commit(repo)
    base = _git(repo, "rev-parse", "HEAD")
    assert [e["case"] for e in select.select_touched(repo, base, ["p/evals/g"])] == ["a"]
    assert [e["case"] for e in select.select_touched(repo, base, ["p/evals/g/x"])] == ["a", "g"]
    got = select.select_touched(repo, base, ["p/evals/g", "p/evals/g/x"])
    assert [e["case"] for e in got] == ["a", "g"]


@pytest.mark.parametrize("changed, selected", [
    ("skills/entry.md", True),
    ("skills/b-declared.md", True),
    ("rules/core.md", True),
    ("rules/rule-a.md", True),
    ("skills/b-undeclared.md", False),
    ("rules/rule-b.md", False),
])
def test_a_declared_case_is_found_by_its_entry_declared_branch_and_loaded_rules_and_not_by_an_undeclared_branch(
    repo, changed, selected  # noqa: F811
):
    _declared_corpus(repo)
    _commit(repo)
    base = _git(repo, "rev-parse", "HEAD")
    by_path = select.select_touched_by_path(repo, base, [f"p/{changed}"])
    assert [e["case"] for e in by_path[f"p/{changed}"]] == (["decl"] if selected else [])
    assert [e["case"] for e in select.select_touched(repo, base, [f"p/{changed}"])] == (
        ["decl"] if selected else [])
