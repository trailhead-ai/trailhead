"""`portage eval-gate plan` — mapping discovery, strict loading, and case selection.

Every test builds a throwaway git repo under ``tmp_path`` and drives the real
``dispatch.main(["eval-gate", "plan", ...])``; the plan is read back from the
``plan.json`` the command wrote into its state directory.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.cli import dispatch
from portage.eval_gate import unmapped_runner_cases

RUNNER = {"model": "sonnet", "allow_tools": ["Read"], "max_cost_usd": 2.0}


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _mapping(cases: dict, runner: dict | None = None) -> str:
    return json.dumps({"schema_version": 1, "runner": runner or RUNNER, "cases": cases})


def _commit(repo: Path, msg: str = "c") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg)


def _corpus(repo: Path, plugin: str, cases: dict, runner: dict | None = None,
            files: tuple[str, ...] = ("skills/a.md", "skills/b.md")) -> None:
    for f in files:
        _write(repo, f"{plugin}/{f}")
    for name in cases:
        _write(repo, f"{plugin}/evals/{name}/case.yaml", "name: x\n")
    _write(repo, f"{plugin}/evals/eval-gate.json", _mapping(cases, runner))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    return r


def _branch(repo: Path) -> None:
    _git(repo, "branch", "base")
    _git(repo, "checkout", "-b", "feature")


def _plan(repo: Path, capsys, *extra: str):
    capsys.readouterr()
    rc = dispatch.main(["eval-gate", "plan", "--repo", str(repo), *extra])
    out = capsys.readouterr().out
    state = Path(next(l for l in out.splitlines() if l.startswith("state: "))[len("state: "):])
    plan = json.loads((state / "plan.json").read_text())
    return rc, plan, out


def _names(plan) -> list[str]:
    return sorted(c["case"] for c in plan["cases"])


M = {"alpha": {"measures": ["skills/a.md"]}, "beta": {"measures": ["skills/b.md"]}}


class TestSelection:
    def test_touching_a_mapped_file_selects_exactly_its_cases(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        rc, plan, out = _plan(repo, capsys, "--base", "base")
        assert rc == 0
        assert plan["status"] == "cases-selected"
        assert _names(plan) == ["alpha"]
        assert "p:alpha" in out.splitlines()
        assert "cases: 1" in out
        assert "max_cost_usd: 2.0" in out

    def test_touching_an_unmapped_file_selects_none(self, repo, capsys):
        _corpus(repo, "p", M, files=("skills/a.md", "skills/b.md", "skills/c.md"))
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/c.md", "changed\n")
        _commit(repo)
        rc, plan, _ = _plan(repo, capsys, "--base", "base")
        assert rc == 0
        assert plan["status"] == "no-cases-mapped"
        assert plan["cases"] == []

    def test_touching_a_file_in_a_cases_own_directory_selects_it(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/evals/beta/case.yaml", "name: changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert _names(plan) == ["beta"]

    def test_two_corpora_select_independently(self, repo, capsys):
        _corpus(repo, "p", M)
        _corpus(repo, "q", {"gamma": {"measures": ["skills/a.md"]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "q/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert [(c["plugin_root"], c["case"]) for c in plan["cases"]] == [("q", "gamma")]


class TestBase:
    def test_explicit_base_is_honoured_over_the_default(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        _git(repo, "checkout", "-b", "feature")
        _write(repo, "p/skills/a.md", "one\n")
        _commit(repo)
        _git(repo, "branch", "mid")
        _write(repo, "p/skills/b.md", "two\n")
        _commit(repo)
        _, default_plan, _ = _plan(repo, capsys)
        _, explicit_plan, _ = _plan(repo, capsys, "--base", "mid")
        assert _names(default_plan) == ["alpha", "beta"]
        assert _names(explicit_plan) == ["beta"]

    def test_no_origin_head_and_no_base_is_base_unresolved(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        rc, plan, _ = _plan(repo, capsys)
        assert rc == 0
        assert plan["status"] == "base-unresolved"
        assert plan["cases"] == []

    def test_an_unknown_explicit_base_is_base_unresolved(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "no-such-ref")
        assert plan["status"] == "base-unresolved"


def _invalid(repo, capsys, mapping_text: str, *, extra_files=(), prep=None):
    _write(repo, "p/skills/a.md")
    _write(repo, "p/evals/alpha/case.yaml", "n: x\n")
    for f in extra_files:
        _write(repo, f)
    if prep:
        prep()
    _commit(repo)
    _branch(repo)
    _write(repo, "p/evals/eval-gate.json", mapping_text)
    _write(repo, "p/skills/a.md", "changed\n")
    _commit(repo)
    rc, plan, out = _plan(repo, capsys, "--base", "base")
    return rc, plan, out


def _entry(**kw) -> str:
    entry = {"measures": ["skills/a.md"], **kw}
    return _mapping({"alpha": entry})


class TestLoaderRefusals:
    @pytest.mark.parametrize(
        "text, fault",
        [
            ("{not json", "JSON"),
            (json.dumps({"schema_version": 1, "runner": RUNNER, "cases": {}, "extra": 1}), "extra"),
            (_entry(bogus=1), "bogus"),
            (_mapping({"alpha": {"measures": ["/etc/passwd"]}}), "absolute"),
            (_mapping({"alpha": {"measures": ["../x.md"]}}), ".."),
            (_mapping({"alpha": {"measures": ["skills/../skills/a.md"]}}), ".."),
            (_mapping({"alpha": {"measures": ["skills/missing.md"]}}), "missing.md"),
            (_mapping({"Bad_Name": {"measures": ["skills/a.md"]}}), "Bad_Name"),
            (_entry(runs=3, pass_at=0), "pass_at"),
            (_entry(runs=3, pass_at=4), "pass_at"),
            (_entry(runs=5), "pass_at"),
        ],
    )
    def test_each_fault_is_mapping_invalid_naming_file_and_fault(self, repo, capsys, text, fault):
        rc, plan, out = _invalid(repo, capsys, text)
        assert rc == 0
        assert plan["status"] == "mapping-invalid"
        assert plan["cases"] == []
        assert plan["mapping_invalid"]["file"] == "p/evals/eval-gate.json"
        assert fault in plan["mapping_invalid"]["fault"]
        assert "p/evals/eval-gate.json" in out and "Traceback" not in out

    def test_a_case_without_case_yaml_is_mapping_invalid(self, repo, capsys):
        text = _mapping({"alpha": {"measures": ["skills/a.md"]}, "ghost": {"measures": ["skills/a.md"]}})
        _, plan, _ = _invalid(repo, capsys, text)
        assert plan["status"] == "mapping-invalid"
        assert "ghost" in plan["mapping_invalid"]["fault"]


    def test_a_bad_case_name_is_refused_even_with_its_case_yaml_present(self, repo, capsys):
        text = _mapping({"Bad_Name": {"measures": ["skills/a.md"]}})
        _, plan, _ = _invalid(repo, capsys, text, extra_files=("p/evals/Bad_Name/case.yaml",))
        assert plan["status"] == "mapping-invalid"
        assert "must match" in plan["mapping_invalid"]["fault"]


class TestDefaults:
    def _go(self, repo, capsys, **kw):
        _write(repo, "p/skills/a.md")
        _write(repo, "p/evals/alpha/case.yaml")
        _write(repo, "p/evals/eval-gate.json", _entry(**kw))
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        return _plan(repo, capsys, "--base", "base")[1]["cases"][0]

    def test_pass_at_defaults_to_2_when_runs_is_3(self, repo, capsys):
        case = self._go(repo, capsys)
        assert (case["runs"], case["pass_at"]) == (3, 2)

    def test_a_declared_pass_at_overrides_the_default(self, repo, capsys):
        case = self._go(repo, capsys, pass_at=3)
        assert (case["runs"], case["pass_at"]) == (3, 3)

    def test_non_default_runs_with_its_own_pass_at_loads(self, repo, capsys):
        case = self._go(repo, capsys, runs=5, pass_at=4)
        assert (case["runs"], case["pass_at"]) == (5, 4)


class TestDiscovery:
    def test_a_repo_with_no_mapping_file_is_no_cases_mapped(self, repo, capsys):
        _write(repo, "p/skills/a.md")
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "no-cases-mapped"


class TestBasePrecedence:
    def test_a_mapping_entry_deleted_on_the_branch_still_selects_its_case(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/evals/eval-gate.json", _mapping({"beta": M["beta"]}))
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected"
        assert _names(plan) == ["alpha"]

    def test_a_grant_widened_on_the_branch_is_ignored(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _branch(repo)
        wide = {"model": "opus", "allow_tools": ["Read", "Bash"], "max_cost_usd": 99.0}
        _write(repo, "p/evals/eval-gate.json", _mapping(M, wide))
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["cases"][0]["runner"] == RUNNER
        assert plan["new_runner_grants"] is False

    def test_runs_and_pass_at_come_from_the_base_entry(self, repo, capsys):
        _corpus(repo, "p", {"alpha": {"measures": ["skills/a.md"], "runs": 5, "pass_at": 4}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/evals/eval-gate.json",
               _mapping({"alpha": {"measures": ["skills/a.md"], "runs": 1, "pass_at": 1}}))
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert (plan["cases"][0]["runs"], plan["cases"][0]["pass_at"]) == (5, 4)

    def test_a_corpus_absent_at_the_base_uses_its_own_block_and_is_flagged(self, repo, capsys):
        _write(repo, "README.md")
        _commit(repo)
        _branch(repo)
        own = {"model": "haiku", "allow_tools": ["Read", "Grep"], "max_cost_usd": 1.5}
        _corpus(repo, "p", M, runner=own)
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected"
        assert plan["cases"][0]["runner"] == own
        assert plan["cases"][0]["new_runner_grants"] is True
        assert plan["new_runner_grants"] is True


class TestLookupAndStateDir:
    def test_unmapped_runner_cases_reports_only_cases_without_an_entry(self, repo):
        _corpus(repo, "p", {"alpha": M["alpha"]})
        _write(repo, "p/evals/orphan/case.yaml")
        assert unmapped_runner_cases(repo / "p" / "evals") == ["orphan"]

    def test_unmapped_runner_cases_changes_with_the_mapping(self, repo):
        _corpus(repo, "p", M)
        _write(repo, "p/evals/orphan/case.yaml")
        _write(repo, "p/evals/eval-gate.json",
               _mapping({**M, "orphan": {"measures": ["skills/a.md"]}}))
        assert unmapped_runner_cases(repo / "p" / "evals") == []

    def test_the_state_directory_is_created_0700(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _, _, out = _plan(repo, capsys, "--base", "main")
        state = Path(next(l for l in out.splitlines() if l.startswith("state: "))[len("state: "):])
        assert stat.S_IMODE(os.stat(state).st_mode) == 0o700


class TestUsage:
    def test_a_path_that_is_not_a_git_repo_exits_2(self, tmp_path, capsys):
        rc = dispatch.main(["eval-gate", "plan", "--repo", str(tmp_path)])
        assert rc == 2
        assert capsys.readouterr().err.strip()


PROSE = "".join(f"line {i} of the measured prose\n" for i in range(20))


class TestBranchHistoryShapes:
    def _measured(self, repo):
        _write(repo, "p/skills/a.md", PROSE)
        _write(repo, "p/evals/alpha/case.yaml")
        _write(repo, "p/evals/beta/case.yaml")
        _write(repo, "p/evals/eval-gate.json",
               _mapping({"alpha": {"measures": ["skills/a.md"]}, "beta": {"measures": ["skills/b.md"]}}))
        _write(repo, "p/skills/b.md")
        _commit(repo)
        _branch(repo)

    def test_renaming_a_measured_file_and_deleting_its_entry_still_selects_the_case(self, repo, capsys):
        self._measured(repo)
        _git(repo, "mv", "p/skills/a.md", "p/skills/renamed.md")
        _write(repo, "p/skills/renamed.md", PROSE + "one more line\n")
        _write(repo, "p/evals/eval-gate.json", _mapping({"beta": {"measures": ["skills/b.md"]}}))
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected"
        assert _names(plan) == ["alpha"]

    def test_a_case_removed_on_the_branch_stays_in_the_plan_flagged_removed(self, repo, capsys):
        self._measured(repo)
        _git(repo, "rm", "-rq", "p/evals/alpha")
        _write(repo, "p/evals/eval-gate.json", _mapping({"beta": {"measures": ["skills/b.md"]}}))
        _write(repo, "p/skills/a.md", PROSE + "edited\n")
        _commit(repo)
        _, plan, out = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected"
        assert [(c["case"], c["case_removed"]) for c in plan["cases"]] == [("alpha", True)]
        assert plan["total_max_cost_usd"] == 0
        assert "p:alpha" in out.splitlines()

    def test_a_renamed_case_dir_selects_the_old_name_as_removed(self, repo, capsys):
        self._measured(repo)
        _git(repo, "mv", "p/evals/alpha", "p/evals/alpha-two")
        _write(repo, "p/evals/eval-gate.json",
               _mapping({"alpha-two": {"measures": ["skills/a.md"]}, "beta": {"measures": ["skills/b.md"]}}))
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        by_name = {c["case"]: c["case_removed"] for c in plan["cases"]}
        assert by_name == {"alpha": True, "alpha-two": False}

    def test_a_present_case_is_not_flagged_removed(self, repo, capsys):
        self._measured(repo)
        _write(repo, "p/skills/a.md", PROSE + "edited\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert [(c["case"], c["case_removed"]) for c in plan["cases"]] == [("alpha", False)]

    def test_a_removed_case_runs_no_runner_and_the_section_explains_it(self, repo, capsys, tmp_path, monkeypatch):
        self._measured(repo)
        _git(repo, "rm", "-rq", "p/evals/alpha")
        _write(repo, "p/evals/eval-gate.json", _mapping({"beta": {"measures": ["skills/b.md"]}}))
        _write(repo, "p/skills/a.md", PROSE + "edited\n")
        _commit(repo)
        _, plan, out = _plan(repo, capsys, "--base", "base")
        state = Path(next(l for l in out.splitlines() if l.startswith("state: "))[len("state: "):])
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert dispatch.main(["eval-gate", "run", "--state", str(state), "--case", "alpha"]) == 0
        assert "reason: case-removed-on-branch" in capsys.readouterr().out
        assert not (state / "canary.json").exists()
        code = dispatch.main(["eval-gate", "verdict", "--state", str(state)])
        verdict_out = capsys.readouterr().out
        assert code == 0
        assert "persistent: none" in verdict_out.splitlines()
        assert ("`alpha` — `case-removed-on-branch`: the branch removes this case; it was not run "
                "— review the removal") in verdict_out


class TestNonUtf8Mapping:
    BAD = b'{"schema_version": 1, "x": "\xff\xfe"}'

    def test_a_non_utf8_mapping_on_the_branch_is_mapping_invalid_naming_the_file(self, repo, capsys):
        _write(repo, "p/skills/a.md")
        _write(repo, "p/evals/alpha/case.yaml")
        _write(repo, "p/evals/eval-gate.json", _entry())
        _commit(repo)
        _branch(repo)
        (repo / "p/evals/eval-gate.json").write_bytes(self.BAD)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        rc, plan, out = _plan(repo, capsys, "--base", "base")
        assert rc == 0
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["file"] == "p/evals/eval-gate.json"
        assert "UTF-8" in plan["mapping_invalid"]["fault"]
        assert "Traceback" not in out

    def test_a_non_utf8_mapping_at_the_base_is_mapping_invalid_naming_the_file(self, repo, capsys):
        _write(repo, "p/skills/a.md")
        _write(repo, "p/evals/alpha/case.yaml")
        (repo / "p/evals").mkdir(parents=True, exist_ok=True)
        (repo / "p/evals/eval-gate.json").write_bytes(self.BAD)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/evals/eval-gate.json", _entry())
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        rc, plan, _ = _plan(repo, capsys, "--base", "base")
        assert rc == 0
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["file"] == "p/evals/eval-gate.json"
        assert "UTF-8" in plan["mapping_invalid"]["fault"]


class TestBrokenBaseMapping:
    def test_an_invalid_base_mapping_is_reported_as_the_base_revisions(self, repo, capsys):
        _write(repo, "p/skills/a.md")
        _write(repo, "p/evals/alpha/case.yaml")
        _write(repo, "p/evals/eval-gate.json", "{not json")
        _commit(repo)
        _branch(repo)
        _write(repo, "p/evals/eval-gate.json", _entry())
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["file"] == "p/evals/eval-gate.json"
        assert "base revision's mapping is invalid" in plan["mapping_invalid"]["fault"]
        assert plan["mapping_invalid"]["revision"] == "base"
        assert "JSON" in plan["mapping_invalid"]["fault"]

    def test_an_invalid_head_mapping_is_not_blamed_on_the_base(self, repo, capsys):
        rc, plan, _ = _invalid(repo, capsys, "{not json")
        assert "base revision" not in plan["mapping_invalid"]["fault"]
        assert plan["mapping_invalid"]["revision"] == "head"
