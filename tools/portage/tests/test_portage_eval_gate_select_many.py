"""``select_touched_by_path`` answers, per path, what ``select_touched`` answers for that path alone."""
from __future__ import annotations

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.eval_gate import select
from test_portage_eval_gate_plan import _branch, _commit, _corpus, _git, _mapping, _write, repo  # noqa: F401

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
