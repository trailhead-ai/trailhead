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
