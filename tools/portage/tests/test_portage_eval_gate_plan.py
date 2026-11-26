"""`portage eval-gate plan` — mapping discovery, strict loading, and case selection.

Every test builds a throwaway git repo under ``tmp_path`` and drives the real
``dispatch.main(["eval-gate", "plan", ...])``; the plan is read back from the
``plan.json`` the command wrote into its state directory.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import _portage_cli  # prepends the plugin root onto sys.path
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.cli import dispatch
from portage.eval_gate import merge_check, select, unmapped_runner_cases

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


def _plan(repo: Path, capsys, *extra: str, opt_in: bool = True):
    capsys.readouterr()
    gate = ["--opt-in"] if opt_in else []
    rc = dispatch.main(["eval-gate", "plan", "--repo", str(repo), *gate, *extra])
    out = capsys.readouterr().out
    state = Path(next(ln for ln in out.splitlines() if ln.startswith("state: "))[len("state: "):])
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
        wide = {"model": "opus", "allow_tools": ["Read", "Bash"], "max_cost_usd": 25.0}
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
        assert plan["total_max_cost_usd"] == 0


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
        state = Path(next(ln for ln in out.splitlines() if ln.startswith("state: "))[len("state: "):])
        assert stat.S_IMODE(os.stat(state).st_mode) == 0o700


class TestGateOptIn:
    def _touching_a(self, repo):
        _corpus(repo, "p", M)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)

    def _toml(self, tmp_path, text):
        path = tmp_path / "group.toml"
        path.write_text(text)
        return str(path)

    def test_no_flag_and_no_setting_writes_a_gate_off_plan_with_no_cases(self, repo, capsys):
        self._touching_a(repo)
        rc, plan, out = _plan(repo, capsys, "--base", "base", opt_in=False)
        assert rc == 0
        assert plan["status"] == "gate-off"
        assert plan["cases"] == []
        assert "status: gate-off" in out.splitlines()
        assert "cases: 0" in out

    def test_opt_in_selects_the_touched_cases(self, repo, capsys):
        self._touching_a(repo)
        rc, plan, _ = _plan(repo, capsys, "--base", "base")
        assert rc == 0
        assert plan["status"] == "cases-selected"
        assert _names(plan) == ["alpha"]

    @pytest.mark.parametrize("text, status", [
        ("[release]\neval_gate = true\n", "cases-selected"),
        ("[release]\neval_gate = false\n", "gate-off"),
        ("[release]\n", "gate-off"),
        ("[release]\neval_gate = \"true\"\n", "gate-off"),
        ("[release]\neval_gate = 1\n", "gate-off"),
        ("[release\neval_gate = true\n", "gate-off"),
    ])
    def test_the_group_setting_alone_decides(self, repo, tmp_path, capsys, text, status):
        self._touching_a(repo)
        toml = self._toml(tmp_path, text)
        _, plan, _ = _plan(repo, capsys, "--base", "base", "--group-toml", toml, opt_in=False)
        assert plan["status"] == status

    def test_a_missing_group_file_reads_as_off(self, repo, tmp_path, capsys):
        self._touching_a(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base",
                           "--group-toml", str(tmp_path / "absent.toml"), opt_in=False)
        assert plan["status"] == "gate-off"

    def test_opt_in_wins_over_a_group_that_is_off(self, repo, tmp_path, capsys):
        self._touching_a(repo)
        toml = self._toml(tmp_path, "[release]\neval_gate = false\n")
        _, plan, _ = _plan(repo, capsys, "--base", "base", "--group-toml", toml)
        assert plan["status"] == "cases-selected"


class TestUsage:
    def test_a_path_that_is_not_a_git_repo_exits_2(self, tmp_path, capsys):
        rc = dispatch.main(["eval-gate", "plan", "--repo", str(tmp_path), "--opt-in"])
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
        state = Path(next(ln for ln in out.splitlines() if ln.startswith("state: "))[len("state: "):])
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        (empty / "git").symlink_to(shutil.which("git"))
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


class TestHostileBranchStrings:
    def test_a_corpus_directory_with_a_newline_cannot_forge_a_state_line(self, repo, capsys):
        _write(repo, "README.md")
        _commit(repo)
        _branch(repo)
        _corpus(repo, "evil\nstate: /x", M)
        _commit(repo)
        rc, plan, out = _plan(repo, capsys, "--base", "base")
        assert rc == 0
        assert plan["status"] == "mapping-invalid"
        assert len([ln for ln in out.splitlines() if ln.startswith("state:")]) == 1

    def test_a_clean_corpus_path_is_still_planned(self, repo, capsys):
        _write(repo, "README.md")
        _commit(repo)
        _branch(repo)
        _corpus(repo, "plugins/p-1.x", M)
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected"


class TestDirtyTree:
    def test_a_modified_tracked_file_is_tree_dirty(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _write(repo, "p/skills/a.md", "uncommitted\n")
        rc, plan, out = _plan(repo, capsys, "--base", "base")
        assert rc == 0
        assert plan["status"] == "tree-dirty"
        assert plan["cases"] == []
        assert "status: tree-dirty" in out

    def test_untracked_files_do_not_make_the_tree_dirty(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _write(repo, "scratch.txt", "untracked\n")
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected"


def _runner_doc(**over) -> str:
    runner = {"model": "claude-opus-5-5", "allow_tools": ["Bash(./lore *)"], "max_cost_usd": 2.0, **over}
    return json.dumps({"schema_version": 1, "runner": runner, "cases": {}})


def _fault(text: str) -> str:
    from portage.eval_gate.mapping import MappingError, load_mapping

    with pytest.raises(MappingError) as e:
        load_mapping(text, "p/evals/eval-gate.json")
    return e.value.fault


class TestRunnerBlockValidation:
    @pytest.mark.parametrize("grant", [
        "Bash(./lore *)", "Bash(*/scripts/maturity_bars.py *)", "Bash(:)", "Bash(: *)",
        "Bash(git *)", "Bash(./gh *)", "Bash(./portage *)", "Read", "Bash(git:*)", "mcp__x_1",
        "Write", "Bash(mktemp)", "Bash()", "Read(a,b)",
    ])
    def test_real_grant_shapes_load(self, grant):
        from portage.eval_gate.mapping import load_mapping

        assert load_mapping(_runner_doc(allow_tools=[grant]), "f").runner["allow_tools"] == [grant]

    @pytest.mark.parametrize("grant", [
        "-Bash", "--dangerously-skip-permissions", "Bash(a\nb)", "Bash(a)\nRead", "Bash(a) Read",
        "", "Bash (x)", "Bash(x)(y)z", "Bash(x",
    ])
    def test_a_malformed_grant_is_refused_naming_the_field(self, grant):
        assert "allow_tools" in _fault(_runner_doc(allow_tools=[grant]))

    @pytest.mark.parametrize("grant, rejected", [
        ("Bash(a) Bash(*)", "bracket"), ("Read(x),Bash(*)", "bracket"), ("Bash(git *) Write()", "bracket"),
        ("Bash(a) -Bash(*)", "bracket"), ("Bash(a(b))", "bracket"), ("Read(a(b),Bash(*))", "bracket"),
        ("Bash(a\x00b)", "NUL"), ("Bash(a\nb)", "line break"), ("Bash(a\rb)", "line break"),
    ])
    def test_a_grant_argument_holding_a_bracket_nul_or_line_break_is_refused_by_position_and_class(
            self, grant, rejected):
        fault = _fault(_runner_doc(allow_tools=["Read", grant]))
        assert "allow_tools entry #2 " in fault
        assert rejected in fault

    @pytest.mark.parametrize("grant, rejected", [
        ("Bash(a[b],c)", "holds a square bracket"), ("Bash(a]b)", "holds a square bracket"),
        ("Bash([x)", "holds a square bracket"),
        ("Bash(git status\x0b*)", "holds a control character"), ("Bash(git status\x0c*)", "holds a control character"),
        ("Bash(git\tstatus *)", "holds a control character"), ("Bash(git\x1bstatus *)", "holds a control character"),
        ("Bash(git\x1cstatus)", "holds a control character"), ("Bash(git\x1dstatus)", "holds a control character"),
        ("Bash(git\x1estatus)", "holds a control character"), ("Bash(git\x7fstatus)", "holds a control character"),
        ("Bash(git\x85status)", "holds a control character"), ("Bash(git\x9fstatus)", "holds a control character"),
        ("Bash(git status\u2028*)", "holds a line terminator"), ("Bash(git status\u2029*)", "holds a line terminator"),
        ("Bash(git\u00a0status)", "holds a non-ASCII"), ("Bash(git\u200bstatus)", "holds a non-ASCII"),
        ("Bash(git\u202estatus)", "holds a non-ASCII"), ("Bash(git\ufeffstatus)", "holds a non-ASCII"),
        ("Bash(g\u00e9)", "holds a non-ASCII"),
    ])
    def test_a_grant_argument_holding_a_re_derived_shape_is_refused_by_position_class_and_allowed_shape(
            self, grant, rejected):
        fault = _fault(_runner_doc(allow_tools=["Read", grant]))
        argument = grant[grant.index("(") + 1:-1]
        assert "allow_tools entry #2 " in fault
        assert rejected in fault
        assert "printable ASCII" in fault
        assert "\n" not in fault
        for char in argument:
            if not " " <= char <= "~" or char in "[]":
                assert char not in fault
        assert argument not in fault

    @pytest.mark.parametrize(
        "grant", ['Bash(a"b)', "Bash(a'b)", "Bash(a`b)", "Bash(a\\b)", 'Bash(echo "x")', "Bash(a\\)"]
    )
    def test_a_grant_argument_holding_a_quote_or_backslash_is_refused_without_echoing_it(self, grant):
        fault = _fault(_runner_doc(allow_tools=["Read", grant]))
        assert "allow_tools entry #2 " in fault
        assert "; the argument holds a quote or backslash, which is not allowed" in fault
        assert grant not in fault

    @pytest.mark.parametrize("grant, reason", [
        ("", "the tool name is empty or holds"), ("-Bash", "the tool name is empty or holds"),
        ("Bash (x)", "the tool name is empty or holds"), ("(x)", "the tool name is empty or holds"),
        ("Ba.sh(x)", "the tool name is empty or holds"),
        ("Bash(a)b", "text follows the closing parenthesis"), ("Bash(a) ", "text follows the closing parenthesis"),
        ("Bash()x", "text follows the closing parenthesis"),
        ("Bash(x", "the grant has no closing parenthesis"), ("Bash(", "the grant has no closing parenthesis"),
    ])
    def test_a_grant_with_a_bad_name_or_a_broken_closing_parenthesis_gets_its_own_reason(self, grant, reason):
        fault = _fault(_runner_doc(allow_tools=["Read", grant]))
        assert f"; {reason}" in fault
        assert "the argument holds" not in fault

    @pytest.mark.parametrize(
        "grant", ["Bash(ab c)", "Bash(git status *)", "Bash(a,b c)", "Bash(~|{}!@#$%^&*+=;:<>?/-.)"]
    )
    def test_the_nearest_accepted_neighbour_of_each_refused_shape_still_loads(self, grant):
        from portage.eval_gate.mapping import load_mapping

        assert load_mapping(_runner_doc(allow_tools=[grant]), "f").runner["allow_tools"] == [grant]

    @pytest.mark.parametrize("cost", [0, -1, 25.01, 1e9, float("inf"), float("nan"), True])
    def test_a_cost_cap_outside_the_bound_is_refused(self, cost):
        assert "max_cost_usd" in _fault(_runner_doc(max_cost_usd=cost).replace("Infinity", "1e999"))

    @pytest.mark.parametrize("cost", [0.01, 25, 25.0])
    def test_a_cost_cap_inside_the_bound_loads(self, cost):
        from portage.eval_gate.mapping import load_mapping

        assert load_mapping(_runner_doc(max_cost_usd=cost), "f").runner["max_cost_usd"] == cost

    @pytest.mark.parametrize("model", ["--flag", "a b", "a\nb", "-opus", "x" * 65, "a;b", "$(x)"])
    def test_a_malformed_model_is_refused(self, model):
        assert "model" in _fault(_runner_doc(model=model))

    @pytest.mark.parametrize("model", ["sonnet", "claude-opus-5-5", "claude-sonnet-4.5"])
    def test_a_conventional_model_id_loads(self, model):
        from portage.eval_gate.mapping import load_mapping

        assert load_mapping(_runner_doc(model=model), "f").runner["model"] == model

    @pytest.mark.parametrize("runs", [0, 11, 1000, -1])
    def test_runs_outside_one_to_ten_is_refused(self, runs):
        text = json.dumps({"schema_version": 1, "runner": json.loads(_runner_doc())["runner"],
                           "cases": {"a": {"measures": ["x"], "runs": runs, "pass_at": 1}}})
        assert "runs" in _fault(text)

    @pytest.mark.parametrize("runs", [1, 10])
    def test_runs_at_the_bounds_loads(self, runs):
        from portage.eval_gate.mapping import load_mapping

        text = json.dumps({"schema_version": 1, "runner": json.loads(_runner_doc())["runner"],
                           "cases": {"a": {"measures": ["x"], "runs": runs, "pass_at": 1}}})
        assert load_mapping(text, "f").cases["a"].runs == runs


class TestPlanEntryMeasures:
    def test_the_entry_carries_the_cases_measures(self, repo, capsys):
        _corpus(repo, "p", {"alpha": {"measures": ["skills/a.md", "skills/b.md"]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["cases"][0]["measures"] == ["skills/a.md", "skills/b.md"]

    def test_the_entry_merges_base_and_head_measures_without_repeats(self, repo, capsys):
        _corpus(repo, "p", {"alpha": {"measures": ["skills/a.md"]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/evals/eval-gate.json",
               _mapping({"alpha": {"measures": ["skills/a.md", "skills/b.md"]}}))
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["cases"][0]["measures"] == ["skills/a.md", "skills/b.md"]


MO = {"core": {"measures": ["skills/a.md"]},
      "slow": {"measures": ["skills/a.md"], "merge_only": True}}


def _touch_a(repo: Path, cases: dict = MO) -> None:
    _corpus(repo, "p", cases)
    _commit(repo)
    _branch(repo)
    _write(repo, "p/skills/a.md", "changed\n")
    _commit(repo)


class TestTier:
    def test_push_tier_selects_only_the_core_case(self, repo, capsys):
        _touch_a(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base", "--tier", "push")
        assert _names(plan) == ["core"]
        assert plan["tier"] == "push"

    def test_merge_tier_selects_every_touched_case(self, repo, capsys):
        _touch_a(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base", "--tier", "merge")
        assert _names(plan) == ["core", "slow"]
        assert plan["tier"] == "merge"

    def test_no_flag_behaves_as_push(self, repo, capsys):
        _touch_a(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert _names(plan) == ["core"]
        assert plan["tier"] == "push"

    def test_an_unknown_tier_is_a_usage_error(self, repo, capsys):
        _touch_a(repo)
        with pytest.raises(SystemExit) as e:
            _plan(repo, capsys, "--base", "base", "--tier", "nightly")
        assert e.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    def test_total_cost_counts_only_the_cases_this_tier_runs(self, repo, capsys):
        _touch_a(repo)
        _, push, _ = _plan(repo, capsys, "--base", "base", "--tier", "push")
        _, merge, _ = _plan(repo, capsys, "--base", "base", "--tier", "merge")
        assert push["total_max_cost_usd"] == 2.0
        assert merge["total_max_cost_usd"] == 4.0

    def test_push_plan_counts_the_touched_merge_only_cases_it_left_out(self, repo, capsys):
        _touch_a(repo)
        _, push, _ = _plan(repo, capsys, "--base", "base", "--tier", "push")
        _, merge, _ = _plan(repo, capsys, "--base", "base", "--tier", "merge")
        assert push["merge_only_pending"] == 1
        assert merge["merge_only_pending"] == 0

    def test_an_untouched_merge_only_case_is_not_pending(self, repo, capsys):
        _touch_a(repo, {"core": {"measures": ["skills/a.md"]},
                        "slow": {"measures": ["skills/b.md"], "merge_only": True}})
        _, push, _ = _plan(repo, capsys, "--base", "base")
        assert push["merge_only_pending"] == 0

    def test_a_push_touching_only_merge_only_cases_runs_nothing_but_counts_them(self, repo, capsys):
        _touch_a(repo, {"slow": {"measures": ["skills/a.md"], "merge_only": True}})
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert (plan["status"], plan["cases"], plan["merge_only_pending"]) == ("no-cases-mapped", [], 1)

    def test_plan_head_is_the_repos_head_commit(self, repo, capsys):
        _touch_a(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["head"] == _git(repo, "rev-parse", "HEAD")

    def test_a_non_boolean_merge_only_is_mapping_invalid_naming_the_case(self, repo, capsys):
        _touch_a(repo, {"core": {"measures": ["skills/a.md"], "merge_only": "yes"}})
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert "core" in plan["mapping_invalid"]["fault"]
        assert "merge_only must be a boolean" in plan["mapping_invalid"]["fault"]

    def test_the_base_value_wins_when_the_branch_unmarks_a_case(self, repo, capsys):
        _corpus(repo, "p", MO)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/evals/eval-gate.json", _mapping(
            {"core": MO["core"], "slow": {"measures": ["skills/a.md"], "merge_only": False}}))
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, push, _ = _plan(repo, capsys, "--base", "base")
        assert _names(push) == ["core"]
        assert push["merge_only_pending"] == 1

    def test_select_touched_returns_entries_carrying_merge_only(self, repo):
        from portage.eval_gate.select import select_touched

        _touch_a(repo)
        base = _git(repo, "merge-base", "base", "HEAD")
        entries = select_touched(repo, base, ["p/skills/a.md"])
        assert {e["case"]: e["merge_only"] for e in entries} == {"core": False, "slow": True}


class TestMergeOnlyReadsCommittedContent:
    """``backstop_touched`` answers from what is committed: the merge-base and HEAD mappings
    (either marking a case is enough) and HEAD's tree, never the working tree."""

    def _touched(self, repo, base="base"):
        from portage.eval_gate.select import backstop_touched

        return backstop_touched(repo, base)

    def test_a_case_the_branch_marks_merge_only_is_returned_though_the_base_unmarks_it(self, repo):
        _touch_a(repo, {"core": {"measures": ["skills/a.md"]},
                        "late": {"measures": ["skills/a.md"], "merge_only": False}})
        _write(repo, "p/evals/eval-gate.json", _mapping(
            {"core": {"measures": ["skills/a.md"]},
             "late": {"measures": ["skills/a.md"], "merge_only": True}}))
        _commit(repo)
        status, entries = self._touched(repo)
        assert status == "ok"
        assert [e["case"] for e in entries] == ["late"]

    def test_a_case_the_base_marks_merge_only_is_returned_though_the_branch_unmarks_it(self, repo):
        _corpus(repo, "p", MO)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/evals/eval-gate.json", _mapping(
            {"core": MO["core"], "slow": {"measures": ["skills/a.md"], "merge_only": False}}))
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        assert [e["case"] for e in self._touched(repo)[1]] == ["slow"]

    def test_a_case_neither_side_marks_is_not_returned(self, repo):
        _touch_a(repo, {"core": {"measures": ["skills/a.md"]}})
        assert self._touched(repo) == ("ok", [])

    def test_the_merge_tier_plan_flags_a_case_the_branch_marks_merge_only(self, repo, capsys):
        _touch_a(repo, {"core": {"measures": ["skills/a.md"]},
                        "late": {"measures": ["skills/a.md"], "merge_only": False}})
        _write(repo, "p/evals/eval-gate.json", _mapping(
            {"core": {"measures": ["skills/a.md"]},
             "late": {"measures": ["skills/a.md"], "merge_only": True}}))
        _commit(repo)
        _, merge, _ = _plan(repo, capsys, "--base", "base", "--tier", "merge")
        assert {c["case"]: c["merge_only"] for c in merge["cases"]} == {"core": False, "late": True}

    def test_the_push_tier_plan_keeps_the_base_value(self, repo, capsys):
        _touch_a(repo, {"core": {"measures": ["skills/a.md"]},
                        "late": {"measures": ["skills/a.md"], "merge_only": False}})
        _write(repo, "p/evals/eval-gate.json", _mapping(
            {"core": {"measures": ["skills/a.md"]},
             "late": {"measures": ["skills/a.md"], "merge_only": True}}))
        _commit(repo)
        _, push, _ = _plan(repo, capsys, "--base", "base", "--tier", "push")
        assert _names(push) == ["core", "late"]
        assert push["merge_only_pending"] == 0

    def test_an_uncommitted_unmarking_does_not_hide_a_committed_merge_only_case(self, repo):
        _touch_a(repo, {"core": {"measures": ["skills/a.md"]}})
        _write(repo, "p/evals/new/case.yaml", "name: x\n")
        _write(repo, "p/evals/eval-gate.json", _mapping(
            {"core": {"measures": ["skills/a.md"]},
             "new": {"measures": ["skills/a.md"], "merge_only": True}}))
        _commit(repo)
        _write(repo, "p/evals/eval-gate.json", _mapping(
            {"core": {"measures": ["skills/a.md"]},
             "new": {"measures": ["skills/a.md"], "merge_only": False}}))
        status, entries = self._touched(repo)
        assert (status, [e["case"] for e in entries]) == ("ok", ["new"])

    def test_an_uncommitted_edit_cannot_make_a_committed_mapping_valid_or_invalid(self, repo):
        _touch_a(repo)
        (repo / "p/evals/eval-gate.json").write_text("{")
        status, entries = self._touched(repo)
        assert (status, [e["case"] for e in entries]) == ("ok", ["slow"])

    def test_an_uncommitted_deletion_of_a_measured_file_does_not_invalidate_the_mapping(self, repo):
        _touch_a(repo)
        (repo / "p/skills/b.md").unlink()
        status, entries = self._touched(repo)
        assert (status, [e["case"] for e in entries]) == ("ok", ["slow"])

    def test_a_committed_mapping_naming_a_missing_measured_path_is_mapping_invalid(self, repo):
        _touch_a(repo, {"core": {"measures": ["skills/a.md"]}})
        _write(repo, "p/evals/eval-gate.json", _mapping({"core": {"measures": ["skills/gone.md"]}}))
        _commit(repo)
        assert self._touched(repo) == ("mapping-invalid", [])

    def test_a_committed_case_without_a_case_yaml_is_mapping_invalid(self, repo):
        _touch_a(repo, {"core": {"measures": ["skills/a.md"]}})
        _write(repo, "p/evals/eval-gate.json", _mapping(
            {"core": {"measures": ["skills/a.md"]}, "ghost": {"measures": ["skills/a.md"]}}))
        _commit(repo)
        assert self._touched(repo) == ("mapping-invalid", [])

    def test_case_removal_is_decided_from_the_committed_tree(self, repo):
        from portage.eval_gate.select import select_touched

        _touch_a(repo, {"core": {"measures": ["skills/a.md"]}})
        base = _git(repo, "merge-base", "base", "HEAD")
        (repo / "p/evals/core/case.yaml").unlink()
        (entry,) = select_touched(repo, base, ["p/skills/a.md"])
        assert entry["case_removed"] is False
        _write(repo, "p/evals/core/case.yaml", "name: x\n")
        _git(repo, "rm", "-rq", "p/evals/core")
        _write(repo, "p/evals/eval-gate.json", _mapping({}))
        _commit(repo)
        _write(repo, "p/evals/core/case.yaml", "name: x\n")
        (entry,) = select_touched(repo, base, ["p/skills/a.md"])
        assert entry["case_removed"] is True


def _mapping_with(runner_over: dict | None = None, case_over: dict | None = None, drop: tuple = ()) -> str:
    runner = {**json.loads(_runner_doc())["runner"], **(runner_over or {})}
    case = {"measures": ["x"], **(case_over or {})}
    for k in drop:
        case.pop(k, None)
    return json.dumps({"schema_version": 1, "runner": runner, "cases": {"alpha": case}})


class TestPushModelAndNeedsMergeModel:
    def test_a_push_model_loads_and_rides_in_the_runner(self):
        from portage.eval_gate.mapping import load_mapping

        runner = load_mapping(_mapping_with({"push_model": "claude-sonnet-4.5"}), "f").runner
        assert runner["push_model"] == "claude-sonnet-4.5"
        assert runner["model"] == "claude-opus-5-5"

    @pytest.mark.parametrize("bad", ["a b", "--flag", "a\nb", "x" * 65, 7, None, ["sonnet"]])
    def test_a_malformed_push_model_is_refused_naming_the_field(self, bad):
        assert "runner.push_model" in _fault(_mapping_with({"push_model": bad}))

    def test_a_missing_required_runner_key_is_still_refused_when_push_model_is_set(self):
        doc = json.loads(_mapping_with({"push_model": "sonnet"}))
        del doc["runner"]["model"]
        assert "'model'" in _fault(json.dumps(doc))

    def test_a_valid_reason_loads_onto_the_case(self):
        from portage.eval_gate.mapping import load_mapping

        reason = "Needs deep tool-use reasoning (multi-step), see 3.5_notes-v2"
        m = load_mapping(_mapping_with(case_over={"needs_merge_model": reason}), "f")
        assert m.cases["alpha"].needs_merge_model == reason

    def test_a_case_without_the_key_has_none(self):
        from portage.eval_gate.mapping import load_mapping

        assert load_mapping(_mapping_with(), "f").cases["alpha"].needs_merge_model is None

    def test_a_200_character_reason_loads(self):
        from portage.eval_gate.mapping import load_mapping

        m = load_mapping(_mapping_with(case_over={"needs_merge_model": "a" * 200}), "f")
        assert m.cases["alpha"].needs_merge_model == "a" * 200

    @pytest.mark.parametrize("bad", [
        True, "", "   ", "a" * 201, "line one\nline two", "has `tick`", "a < b", "a | b",
        "see: this", "see a/b", " leading space", "-leading dash", 5, None, ["x"],
    ], ids=repr)
    def test_a_bad_reason_is_refused_naming_the_case_and_the_rules(self, bad):
        fault = _fault(_mapping_with(case_over={"needs_merge_model": bad}))
        assert "\n" not in fault
        assert "case 'alpha'" in fault and "needs_merge_model" in fault
        assert "200" in fault and "letters, digits, space" in fault

    @pytest.mark.parametrize("bad", [
        "see www.attacker.example", "WWW.attacker.example", "a Www.b", "www.x", "needs the bigger model www.",
    ], ids=repr)
    def test_a_reason_that_could_autolink_is_refused_without_echoing_it(self, bad):
        fault = _fault(_mapping_with(case_over={"needs_merge_model": bad}))
        assert "\n" not in fault
        assert "case 'alpha'" in fault and "needs_merge_model" in fault
        assert "www." in fault
        assert bad not in fault

    def test_a_reason_with_www_inside_a_word_part_is_judged_by_the_same_rule(self):
        from portage.eval_gate.mapping import load_mapping

        reason = "needs the www model"
        m = load_mapping(_mapping_with(case_over={"needs_merge_model": reason}), "f")
        assert m.cases["alpha"].needs_merge_model == reason

    def test_a_mapping_with_neither_key_loads_as_before(self):
        from portage.eval_gate.mapping import load_mapping

        m = load_mapping(_mapping_with(), "f")
        assert m.runner == {"model": "claude-opus-5-5", "allow_tools": ["Bash(./lore *)"], "max_cost_usd": 2.0}
        assert m.cases["alpha"].merge_only is False and m.cases["alpha"].needs_merge_model is None


TIERED = {"model": "opus", "push_model": "sonnet", "allow_tools": ["Read"], "max_cost_usd": 2.0}
UNTIERED = {"model": "opus", "allow_tools": ["Read"], "max_cost_usd": 2.0}
MARK = "needs the larger model"


def _marked(reason: str | None, measures=("skills/a.md",)) -> dict:
    case = {"measures": list(measures)}
    if reason is not None:
        case["needs_merge_model"] = reason
    return case


def _touch_all(repo: Path) -> None:
    _write(repo, "p/skills/a.md", "changed\n")
    _write(repo, "p/skills/b.md", "changed\n")
    _commit(repo)


def _case_entry(plan: dict, case: str) -> dict:
    return next(c for c in plan["cases"] if c["case"] == case)


class TestModelByTier:
    def _base(self, repo, cases, runner=TIERED):
        _corpus(repo, "p", cases, runner)
        _commit(repo)
        _branch(repo)

    def test_push_tier_runs_an_unmarked_case_on_push_model(self, repo, capsys):
        self._base(repo, {"alpha": _marked(None)})
        _touch_all(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base", "--tier", "push")
        entry = _case_entry(plan, "alpha")
        assert entry["runner"]["model"] == "sonnet"
        assert (entry["push_model"], entry["merge_model"]) == ("sonnet", "opus")
        assert entry["needs_merge_model"] is None

    def test_merge_tier_runs_every_case_on_model(self, repo, capsys):
        self._base(repo, {"alpha": _marked(None), "beta": _marked(MARK, ["skills/b.md"])})
        _touch_all(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base", "--tier", "merge")
        assert {c["case"]: c["runner"]["model"] for c in plan["cases"]} == {"alpha": "opus", "beta": "opus"}

    def test_a_base_marked_case_runs_on_model_at_both_tiers(self, repo, capsys):
        self._base(repo, {"alpha": _marked(MARK), "beta": _marked(None, ["skills/b.md"])})
        _touch_all(repo)
        for tier in ("push", "merge"):
            _, plan, _ = _plan(repo, capsys, "--base", "base", "--tier", tier)
            assert _case_entry(plan, "alpha")["runner"]["model"] == "opus"
            assert _case_entry(plan, "alpha")["needs_merge_model"] == MARK
        _, push, _ = _plan(repo, capsys, "--base", "base", "--tier", "push")
        assert _case_entry(push, "beta")["runner"]["model"] == "sonnet"

    def test_a_base_without_push_model_stays_on_model_when_the_branch_adds_one(self, repo, capsys):
        self._base(repo, {"alpha": _marked(None)}, UNTIERED)
        _write(repo, "p/evals/eval-gate.json", _mapping({"alpha": _marked(None)}, TIERED))
        _touch_all(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base", "--tier", "push")
        entry = _case_entry(plan, "alpha")
        assert entry["runner"]["model"] == "opus"
        assert entry["push_model"] is None

    def test_a_mark_added_on_the_branch_leaves_the_case_on_push_model(self, repo, capsys):
        self._base(repo, {"alpha": _marked(None)})
        _write(repo, "p/evals/eval-gate.json", _mapping({"alpha": _marked(MARK)}, TIERED))
        _touch_all(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base", "--tier", "push")
        entry = _case_entry(plan, "alpha")
        assert entry["runner"]["model"] == "sonnet"
        assert entry["needs_merge_model"] is None
        assert plan["mark_changes"] == [
            {"plugin_root": "p", "case": "alpha", "change": "added", "reason": MARK}]
        assert plan["needs_merge_model"] == []


class TestMarkRecords:
    M2 = {"alpha": _marked(MARK), "beta": _marked(MARK, ["skills/b.md"]),
          "gamma": _marked(MARK, ["skills/c.md"])}

    def _base(self, repo):
        _corpus(repo, "p", self.M2, TIERED, files=("skills/a.md", "skills/b.md", "skills/c.md"))
        _commit(repo)
        _branch(repo)

    def test_every_base_marked_case_is_listed_sorted_including_untouched(self, repo, capsys):
        self._base(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert [c["case"] for c in plan["cases"]] == ["alpha"]
        assert plan["needs_merge_model"] == [
            {"plugin_root": "p", "case": n, "reason": MARK} for n in ("alpha", "beta", "gamma")]
        assert plan["mark_changes"] == []

    def test_altered_removed_and_deleted_marks_are_listed_and_unchanged_is_not(self, repo, capsys):
        self._base(repo)
        _write(repo, "p/evals/eval-gate.json", _mapping({
            "alpha": _marked("a new reason"),
            "beta": _marked(None, ["skills/b.md"]),
            "gamma": _marked(MARK, ["skills/c.md"]),
            "delta": _marked(MARK, ["skills/a.md"]),
        }, TIERED))
        _write(repo, "p/evals/delta/case.yaml", "name: d\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["mark_changes"] == [
            {"plugin_root": "p", "case": "alpha", "change": "altered", "reason": "a new reason"},
            {"plugin_root": "p", "case": "beta", "change": "removed", "reason": None},
            {"plugin_root": "p", "case": "delta", "change": "added", "reason": MARK},
        ]

    def test_a_base_marked_case_missing_at_head_is_removed(self, repo, capsys):
        self._base(repo)
        _write(repo, "p/evals/eval-gate.json",
               _mapping({k: v for k, v in self.M2.items() if k != "gamma"}, TIERED))
        (repo / "p/evals/gamma/case.yaml").unlink()
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["mark_changes"] == [
            {"plugin_root": "p", "case": "gamma", "change": "removed", "reason": None}]
        assert len(plan["needs_merge_model"]) == 3

    def test_a_base_marked_case_renamed_on_the_branch_is_removed_and_the_new_name_added(self, repo, capsys):
        self._base(repo)
        _git(repo, "mv", "p/evals/gamma", "p/evals/gamma-two")
        _write(repo, "p/evals/eval-gate.json", _mapping({
            "alpha": self.M2["alpha"], "beta": self.M2["beta"],
            "gamma-two": _marked(MARK, ["skills/c.md"])}, TIERED))
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["mark_changes"] == [
            {"plugin_root": "p", "case": "gamma", "change": "removed", "reason": None},
            {"plugin_root": "p", "case": "gamma-two", "change": "added", "reason": MARK}]

    def test_records_are_written_when_the_plan_selects_nothing(self, repo, capsys):
        self._base(repo)
        _write(repo, "p/evals/eval-gate.json", _mapping({**self.M2, "alpha": _marked("later")}, TIERED))
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "no-cases-mapped"
        assert plan["mark_changes"][0]["change"] == "altered"
        assert len(plan["needs_merge_model"]) == 3


class TestSelectTouchedByPathModel:
    def test_resolves_the_model_plan_resolves_at_each_tier(self, repo, capsys):
        _corpus(repo, "p", {"alpha": _marked(MARK), "beta": _marked(None, ["skills/b.md"])}, TIERED)
        _commit(repo)
        _branch(repo)
        _touch_all(repo)
        mb = _git(repo, "merge-base", "base", "HEAD")
        from portage.eval_gate.select import select_touched_by_path
        for tier in ("push", "merge"):
            _, plan, _ = _plan(repo, capsys, "--base", "base", "--tier", tier)
            got = select_touched_by_path(repo, mb, ["p/skills/a.md", "p/skills/b.md"], tier=tier)
            by_path = {e["case"]: e["runner"] for es in got.values() for e in es}
            assert by_path == {c["case"]: c["runner"] for c in plan["cases"]}
        assert by_path["alpha"]["model"] == "opus"
        assert {c["case"]: c["runner"]["model"] for c in plan["cases"]} == {"alpha": "opus", "beta": "opus"}
        _, push, _ = _plan(repo, capsys, "--base", "base", "--tier", "push")
        assert _case_entry(push, "beta")["runner"]["model"] == "sonnet"


HOSTILE = "@victim https://evil.example/x"


def _load(cases: dict, tmp_path: Path | None = None, runner: dict | None = None, raw: dict | None = None):
    from portage.eval_gate.mapping import load_mapping

    text = json.dumps(raw) if raw is not None else _mapping(cases, runner)
    if tmp_path is None:
        return load_mapping(text, "p/evals/eval-gate.json")
    (tmp_path / "skills").mkdir(exist_ok=True)
    (tmp_path / "skills" / "a.md").write_text("x\n")
    (tmp_path / "skills" / "b.md").write_text("x\n")
    return load_mapping(text, "p/evals/eval-gate.json", plugin_root=tmp_path)


class TestCaseDeclarations:
    def test_entry_and_branches_without_measures_load_with_empty_measures(self):
        case = _load({"alpha": {"entry": "skills/a.md", "branches": ["skills/b.md"]}}).cases["alpha"]
        assert (case.entry, case.branches, case.measures) == ("skills/a.md", ("skills/b.md",), ())

    def test_entry_branches_and_measures_are_all_kept(self):
        case = _load({"alpha": {"entry": "skills/a.md", "branches": ["skills/b.md"],
                                "measures": ["skills/c.md"]}}).cases["alpha"]
        assert (case.entry, case.branches, case.measures) == ("skills/a.md", ("skills/b.md",), ("skills/c.md",))

    def test_a_measures_only_case_has_no_entry_and_no_branches(self):
        case = _load({"alpha": {"measures": ["skills/a.md"]}}).cases["alpha"]
        assert (case.entry, case.branches) == (None, ())

    def test_a_case_with_neither_measures_nor_entry_is_refused(self):
        assert "needs measures or entry" in _fault(_mapping({"alpha": {"runs": 3}}))

    def test_branches_without_entry_is_refused(self):
        case = {"branches": ["skills/b.md"], "measures": ["skills/a.md"]}
        assert "branches requires entry" in _fault(_mapping({"alpha": case}))

    @pytest.mark.parametrize("key", ["entry", "branch"])
    @pytest.mark.parametrize("path, fault", [
        ("/etc/x.md", "relative"),
        ("skills/../a.md", "must not contain '..'"),
        ("skills/a.txt", ".md"),
        ("skills/missing.md", "does not exist"),
    ])
    def test_an_unsafe_or_missing_entry_or_branch_path_is_refused(self, tmp_path, key, path, fault):
        case = {"entry": path} if key == "entry" else {"entry": "skills/a.md", "branches": [path]}
        from portage.eval_gate.mapping import MappingError

        with pytest.raises(MappingError) as e:
            _load({"alpha": case}, tmp_path)
        assert fault in e.value.fault

    def test_an_entry_that_is_not_a_string_is_refused(self):
        assert "entry must be a string" in _fault(_mapping({"alpha": {"entry": 5}}))

    def test_branches_that_is_not_a_list_is_refused(self):
        assert "branches must be a list of strings" in _fault(
            _mapping({"alpha": {"entry": "skills/a.md", "branches": "skills/b.md"}}))

    def test_a_branch_item_that_is_not_a_string_is_refused(self):
        assert "branches must be a list of strings" in _fault(
            _mapping({"alpha": {"entry": "skills/a.md", "branches": [7]}}))


class TestFaultsDoNotEchoMappingText:
    @pytest.mark.parametrize("where", ["top-key", "case-key", "case-name", "allow-tool", "measures", "entry"])
    def test_hostile_value_reaches_neither_the_plan_nor_the_verdict(self, repo, capsys, where):
        cases = {"alpha": {"measures": ["skills/a.md"]}}
        runner = dict(RUNNER)
        raw = {"schema_version": 1, "runner": runner, "cases": cases}
        if where == "top-key":
            raw[HOSTILE] = 1
        elif where == "case-key":
            cases["alpha"][HOSTILE] = 1
        elif where == "case-name":
            cases[HOSTILE] = {"measures": ["skills/a.md"]}
        elif where == "allow-tool":
            runner["allow_tools"] = [HOSTILE]
        elif where == "measures":
            cases["alpha"]["measures"] = [HOSTILE + ".md"]
        else:
            cases["alpha"] = {"entry": "/" + HOSTILE + ".md"}
        _, plan, out = _invalid(repo, capsys, json.dumps(raw))
        assert plan["status"] == "mapping-invalid"
        state = next(ln for ln in out.splitlines() if ln.startswith("state: "))[len("state: "):]
        capsys.readouterr()
        dispatch.main(["eval-gate", "verdict", "--state", state])
        report = capsys.readouterr().out
        for text in (out, report, plan["mapping_invalid"]["fault"]):
            assert "@victim" not in text and "evil.example" not in text
        assert "mapping-invalid" in report and "p/evals/eval-gate.json" in report

    def test_a_safe_unknown_key_is_still_named(self):
        assert "colour" in _fault(_mapping({"alpha": {"measures": ["skills/a.md"], "colour": 1}}))

    def test_an_unsafe_unknown_key_is_named_by_position(self):
        fault = _fault(_mapping({"alpha": {"measures": ["skills/a.md"], HOSTILE: 1}}))
        assert "unknown key #2 in case 'alpha'" in fault

    def test_an_unknown_key_in_a_safe_named_case_names_that_case_by_its_key(self):
        assert "case 'alpha'" in _fault(_mapping({"alpha": {"measures": ["skills/a.md"], "colour": 1}}))

    def test_an_unsafe_case_name_is_named_by_position(self):
        assert "case name #2" in _fault(_mapping({"alpha": {"measures": ["skills/a.md"]},
                                             HOSTILE: {"measures": ["skills/a.md"]}}))

    def test_the_echo_helper_passes_safe_values_and_describes_unsafe_ones(self):
        from portage.eval_gate.mapping import echo

        assert echo("skills/a.md", 3) == "skills/a.md"
        assert echo("x" * 121, 3) == "#3"
        assert echo(HOSTILE, 3) == "#3"
        assert echo("a\nb", 4) == "#4"
        assert echo(5, 1) == "#1"


ENTRY = ("# Entry\n\nRead `rules/core.md`.\n\n"
         "Read `skills/b-declared.md` when the user asks for A.\n\n"
         "Read `skills/b-undeclared.md` when the user asks for B.\n")
DECLARED_FILES = {
    "skills/entry.md": ENTRY,
    "skills/b-declared.md": "Applies when the user asks for A.\n\nRead `rules/rule-a.md`.\n",
    "skills/b-undeclared.md": "Applies when the user asks for B.\n\nRead `rules/rule-b.md`.\n",
    "skills/other.md": "other\n",
    "rules/core.md": "core\n",
    "rules/rule-a.md": "rule a\n",
    "rules/rule-b.md": "rule b\n",
}
DECL = {"entry": "skills/entry.md", "branches": ["skills/b-declared.md"]}
DECLARED_MAPPING = {"decl": DECL, "hand": {"measures": ["skills/other.md"]}}
DERIVED = {"skills/entry.md", "skills/b-declared.md", "rules/core.md", "rules/rule-a.md"}


def _declared_corpus(repo: Path, plugin: str = "p", cases: dict | None = None, files: dict | None = None) -> None:
    for rel, text in {**DECLARED_FILES, **(files or {})}.items():
        _write(repo, f"{plugin}/{rel}", text)
    cases = DECLARED_MAPPING if cases is None else cases
    for name in cases:
        _write(repo, f"{plugin}/evals/{name}/case.yaml", "name: x\n")
    _write(repo, f"{plugin}/evals/eval-gate.json", _mapping(cases))


def _declared_change(repo, capsys, *paths: str, plugin: str = "p"):
    _declared_corpus(repo, plugin)
    _commit(repo)
    _branch(repo)
    for path in paths:
        _write(repo, f"{plugin}/{path}", (repo / plugin / path).read_text() + "\nchanged\n")
    _commit(repo)
    return _plan(repo, capsys, "--base", "base")


def _entry_of(plan, case):
    return next(c for c in plan["cases"] if c["case"] == case)


class TestDeclaredCases:
    def test_a_declared_case_and_a_hand_written_case_both_plan_and_the_declared_one_measures_its_files(
        self, repo, capsys
    ):
        _, plan, _ = _declared_change(repo, capsys, "skills/entry.md", "skills/other.md")
        assert _names(plan) == ["decl", "hand"]
        measures = _entry_of(plan, "decl")["measures"]
        assert sorted(measures) == sorted(DERIVED) and len(measures) == len(set(measures))
        assert _entry_of(plan, "hand")["measures"] == ["skills/other.md"]

    @pytest.mark.parametrize("changed", ["skills/entry.md", "skills/b-declared.md"])
    def test_changing_the_entry_or_a_declared_branch_selects_the_case(self, repo, capsys, changed):
        _, plan, _ = _declared_change(repo, capsys, changed)
        assert _names(plan) == ["decl"]

    @pytest.mark.parametrize("changed", ["rules/core.md", "rules/rule-a.md"])
    def test_changing_a_rule_loaded_by_the_entry_or_by_a_declared_branch_selects_the_case(
        self, repo, capsys, changed
    ):
        _, plan, _ = _declared_change(repo, capsys, changed)
        assert _names(plan) == ["decl"]

    @pytest.mark.parametrize("changed", ["skills/b-undeclared.md", "rules/rule-b.md"])
    def test_changing_an_undeclared_branch_the_entry_loads_does_not_select_the_case(
        self, repo, capsys, changed
    ):
        _, plan, _ = _declared_change(repo, capsys, changed)
        assert plan["status"] == "no-cases-mapped"

    def test_an_undeclared_branch_change_leaves_the_fingerprint_alone_and_the_stored_pass_is_reused(
        self, tmp_path, monkeypatch
    ):
        from test_portage_eval_gate_run import PASS, Env, _result

        e = Env(tmp_path, monkeypatch)
        monkeypatch.setenv("PORTAGE_CACHE_DIR", str(tmp_path / "cache"))
        try:
            _git(e.repo, "init", "-q", "-b", "main")
            _declared_corpus(e.repo, "plugins/p")
            _commit(e.repo)
            _branch(e.repo)
            _write(e.repo, "plugins/p/skills/entry.md", ENTRY + "\nmore\n")
            _commit(e.repo)
            base = _git(e.repo, "merge-base", "base", "HEAD")
            before = select.select_touched_by_path(e.repo, base, ["plugins/p/skills/entry.md"])[
                "plugins/p/skills/entry.md"]
            entry = next(c for c in before if c["case"] == "decl")
            e.cases = [dict(entry, new_runner_grants=False)]
            e.plan()
            e.fake(pool=_result([PASS] * 12, costUsd=0.1), code=0)
            assert e.run("decl") == 0
            assert e.result("decl")["cached"] is False
            calls = len(e.calls)
            _write(e.repo, "plugins/p/skills/b-undeclared.md", "Applies when B.\n\nchanged\n")
            _write(e.repo, "plugins/p/rules/rule-b.md", "changed\n")
            e.commit()
            after = select.select_touched_by_path(e.repo, base, ["plugins/p/skills/entry.md"])[
                "plugins/p/skills/entry.md"]
            entry_after = next(c for c in after if c["case"] == "decl")
            from portage.eval_gate import cache

            plugin_dir = e.repo / "plugins" / "p"
            assert cache.fingerprint(plugin_dir, entry_after) == cache.fingerprint(plugin_dir, entry)
            e.cases = [dict(entry_after, new_runner_grants=False)]
            e.plan()
            assert e.run("decl") == 0
            assert e.result("decl")["cached"] is True
            assert len(e.calls) == calls
        finally:
            shutil.rmtree(e.root, ignore_errors=True)

    def test_the_entry_naming_a_declared_branch_it_never_loads_is_a_one_line_fault_naming_both(
        self, repo, capsys
    ):
        _declared_corpus(repo)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/entry.md", "# Entry\n\nRead `rules/core.md`.\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        fault = plan["mapping_invalid"]["fault"]
        assert plan["mapping_invalid"]["revision"] == "head"
        assert fault == ("case 'decl': entry 'skills/entry.md' does not load the declared branch "
                         "'skills/b-declared.md'")

    def test_the_same_fault_at_the_base_says_so(self, repo, capsys):
        _declared_corpus(repo, files={"skills/entry.md": "# Entry\n\nRead `rules/core.md`.\n"})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/entry.md", ENTRY)
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["revision"] == "base"
        assert plan["mapping_invalid"]["fault"] == (
            "the base revision's mapping is invalid: case 'decl': entry 'skills/entry.md' does not "
            "load the declared branch 'skills/b-declared.md'")

    @pytest.mark.parametrize("gone, fault", [
        ("skills/entry.md", "case 'decl': entry 'skills/entry.md' does not exist"),
        ("skills/b-declared.md", "case 'decl': declared branch 'skills/b-declared.md' does not exist"),
    ])
    def test_an_entry_or_declared_branch_missing_at_head_is_a_fault_not_an_exception(
        self, repo, capsys, gone, fault
    ):
        _declared_corpus(repo)
        _commit(repo)
        _branch(repo)
        _git(repo, "rm", "-q", f"p/{gone}")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["fault"] == fault

    def test_a_load_in_a_mapped_corpus_naming_a_missing_file_is_a_fault_naming_both(self, repo, capsys):
        _declared_corpus(repo, files={"skills/plain.md": "Read `rules/gone.md`.\n"})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["fault"] == (
            "'skills/plain.md' loads 'skills/rules/gone.md', which does not exist")

    def test_prose_that_reads_a_value_rather_than_a_markdown_file_is_not_a_load(self, repo, capsys):
        _declared_corpus(repo, files={"agents/monitor.md": "Read `review_bot_login` from the config.\n"})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected"

    def test_a_corpus_with_no_mapping_that_loads_a_missing_file_plans_normally(self, repo, capsys):
        _declared_corpus(repo)
        _write(repo, "q/skills/plain.md", "Read `rules/gone.md`.\n")
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _write(repo, "q/skills/plain.md", "Read `rules/gone.md`.\n\nmore\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected" and _names(plan) == ["hand"]

    @pytest.mark.parametrize("measure, core", [
        ("skills/entry.md", "skills/entry.md"),
        ("skills", "skills/entry.md"),
    ])
    def test_a_hand_written_measure_that_is_or_holds_a_split_core_is_a_fault_naming_both(
        self, repo, capsys, measure, core
    ):
        _declared_corpus(repo, cases={"hand": {"measures": [measure]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["fault"] == (
            f"case 'hand': measures path '{measure}' is or holds the split core '{core}'")

    @pytest.mark.parametrize("core_text", [
        "core\n\nRead `b-undeclared.md` when asked.\n",
        "core, see [the branch](b-undeclared.md)\n",
    ])
    def test_a_hand_written_measure_holding_a_core_that_loads_or_links_a_branch_is_refused(
        self, repo, capsys, core_text
    ):
        _declared_corpus(repo, cases={"hand": {"measures": ["skills/core.md"]}},
                         files={"skills/core.md": core_text})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["fault"] == (
            "case 'hand': measures path 'skills/core.md' is or holds the split core 'skills/core.md'")

    @pytest.mark.parametrize("core_text, split", [
        ("core\n\nRead `rule-a.md` now.\n", False),
        ("core, see [the branch](../skills/b-undeclared.md)\n", True),
    ])
    def test_a_branch_linked_below_the_entry_makes_the_case_split_and_leaves_the_branch_out_of_its_files(
        self, repo, capsys, core_text, split
    ):
        _declared_corpus(repo, cases={"decl": {"entry": "skills/entry.md"}},
                         files={"skills/entry.md": "# Entry\n\nRead `rules/core.md`.\n",
                                "rules/core.md": core_text})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _write(repo, "p/rules/core.md", core_text + "\nmore\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        case = _entry_of(plan, "decl")
        assert case["split_procedure"] is split
        assert "skills/b-undeclared.md" not in case["derived"]

    def test_a_directory_with_no_split_core_is_a_fine_measure(self, repo, capsys):
        _declared_corpus(repo, cases={"hand": {"measures": ["rules"]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/rules/core.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected" and _names(plan) == ["hand"]

    @pytest.mark.parametrize("measure, branch", [
        ("skills/b-undeclared.md", "skills/b-undeclared.md"),
        ("skills/b-declared.md", "skills/b-declared.md"),
        ("skills/sub", "skills/sub/b-held.md"),
    ])
    def test_a_hand_written_measure_that_is_or_holds_a_branch_file_is_a_one_line_fault_naming_the_case_and_file(
        self, repo, capsys, measure, branch
    ):
        _declared_corpus(repo, cases={"hand": {"measures": [measure]}},
                         files={"skills/sub/b-held.md": "Applies when the user asks for C.\n"})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        fault = plan["mapping_invalid"]["fault"]
        assert fault == f"case 'hand': measures path '{measure}' is or holds the branch file '{branch}'"
        assert "\n" not in fault

    def test_a_hand_written_measure_naming_a_file_that_merely_mentions_applies_when_is_fine(self, repo, capsys):
        _declared_corpus(repo, cases={"hand": {"measures": ["skills/other.md"]}},
                         files={"skills/other.md": "Note: this applies when you like.\n"})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected" and _names(plan) == ["hand"]

    def test_a_hand_written_only_mapping_plans_exactly_as_the_pre_change_selection_built_it(
        self, repo, capsys
    ):
        from portage.eval_gate.mapping import load_mapping

        base_cases = {"alpha": {"measures": ["skills/a.md"]},
                      "beta": {"measures": ["skills/b.md"], "runs": 5, "pass_at": 3, "merge_only": True,
                               "needs_merge_model": "judge drifts"}}
        head_cases = {"alpha": {"measures": ["skills/b.md"]}, "beta": base_cases["beta"]}
        runner = {**RUNNER, "push_model": "haiku"}
        _corpus(repo, "p", base_cases, runner=runner)
        _commit(repo)
        _branch(repo)
        _corpus(repo, "p", head_cases, runner=runner)
        _write(repo, "p/skills/a.md", "changed\n")
        _write(repo, "p/skills/b.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base", "--tier", "merge")

        def pre_change(base_text: str, head_text: str, tier: str) -> list[dict]:
            """The selection as it was built when a case had only hand-written ``measures``."""
            base, head = load_mapping(base_text, "p/evals/eval-gate.json"), load_mapping(
                head_text, "p/evals/eval-gate.json")
            entries = []
            for name in sorted(set(base.cases) | set(head.cases)):
                b, h = base.cases.get(name), head.cases.get(name)
                case = b or h
                mark = b.needs_merge_model if b else None
                entries.append({
                    "plugin_root": "p", "case": name, "runs": case.runs, "pass_at": case.pass_at,
                    "runner": select.resolve_runner(base.runner, tier, mark),
                    "needs_merge_model": mark, "push_model": base.runner.get("push_model"),
                    "merge_model": base.runner["model"],
                    "measures": list(dict.fromkeys((b.measures if b else ()) + (h.measures if h else ()))),
                    "merge_only": case.merge_only, "new_runner_grants": False, "case_removed": False})
            return entries

        old = pre_change(_mapping(base_cases, runner), _mapping(head_cases, runner), "merge")
        new = [dict(c) for c in plan["cases"]]
        assert [c.pop("split_procedure") for c in new] == [False, False]
        assert json.dumps(new, sort_keys=True) == json.dumps(old, sort_keys=True)
        assert _entry_of(plan, "alpha")["measures"] == ["skills/a.md", "skills/b.md"]

    def test_two_corpora_each_declaring_a_case_of_the_same_name_is_a_fault_naming_it(self, repo, capsys):
        _declared_corpus(repo, "p", cases={"x": {"measures": ["skills/other.md"]}})
        _declared_corpus(repo, "q", cases={"x": {"measures": ["skills/other.md"]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["fault"] == (
            "the base revision's mapping is invalid: case 'x' is declared by more than one corpus")

    def test_a_case_that_moves_between_corpora_is_a_fault_saying_it_moved_not_that_it_is_declared_twice(
        self, repo, capsys
    ):
        _declared_corpus(repo, "p", cases={"x": {"measures": ["skills/other.md"]}})
        _declared_corpus(repo, "q", cases={"y": {"measures": ["skills/other.md"]}})
        _commit(repo)
        _branch(repo)
        _declared_corpus(repo, "p", cases={"z": {"measures": ["skills/other.md"]}})
        _declared_corpus(repo, "q", cases={"y": {"measures": ["skills/other.md"]},
                                           "x": {"measures": ["skills/other.md"]}})
        _git(repo, "rm", "-rq", "p/evals/x")
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["fault"] == (
            "case 'x' moves from corpus 'p' to corpus 'q'; a case is identified by its bare name, "
            "so rename it instead")

    def test_a_case_that_stays_in_its_corpus_while_the_other_corpus_changes_is_not_a_move(self, repo, capsys):
        _declared_corpus(repo, "p", cases={"x": {"measures": ["skills/other.md"]}})
        _declared_corpus(repo, "q", cases={"y": {"measures": ["skills/other.md"]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected" and _names(plan) == ["x"]

    @pytest.mark.parametrize("script, what", [
        ("import sys\nsys.exit(0)\n", "dies"),
        ("import sys\nsys.stdin.buffer.readline()\n"
         "sys.stdout.buffer.write(b'0' * 40 + b' blob 100\\nshort')\nsys.stdout.flush()\n", "truncates"),
    ])
    def test_a_cat_file_that_dies_or_truncates_mid_read_is_a_fault_never_an_empty_file(
        self, repo, capsys, monkeypatch, script, what
    ):
        _declared_corpus(repo)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/entry.md", ENTRY + "\nmore\n")
        _commit(repo)
        monkeypatch.setattr(select, "_open_cat_file", lambda repo_path: subprocess.Popen(
            [sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE))
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert "could not be read" in plan["mapping_invalid"]["fault"]

    @pytest.mark.parametrize("entry_text, split", [(ENTRY, True),
                                                   ("# Entry\n\nRead `rules/core.md`.\n", False)])
    def test_an_entry_that_loads_a_branch_is_a_split_procedure(self, repo, capsys, entry_text, split):
        cases = {"decl": {"entry": "skills/entry.md"}}
        _declared_corpus(repo, cases=cases, files={"skills/entry.md": entry_text})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/entry.md", entry_text + "\nmore\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert _entry_of(plan, "decl")["split_procedure"] is split

    def test_an_entry_that_split_only_at_the_base_is_still_a_split_procedure(self, repo, capsys):
        cases = {"decl": {"entry": "skills/entry.md"}}
        _declared_corpus(repo, cases=cases)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/entry.md", "# Entry\n\nRead `rules/core.md`.\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert _entry_of(plan, "decl")["split_procedure"] is True

    @pytest.mark.parametrize("base_branch, head_branch", [
        ("skills/b-declared.md", "skills/b-undeclared.md"),
        ("skills/b-undeclared.md", "skills/b-declared.md"),
    ])
    def test_base_and_head_derived_lists_merge_to_their_union_once_each(
        self, repo, capsys, base_branch, head_branch
    ):
        def case(branch):
            return {"decl": {"entry": "skills/entry.md", "branches": [branch]}}

        _declared_corpus(repo, cases=case(base_branch))
        _commit(repo)
        _branch(repo)
        _write(repo, "p/evals/eval-gate.json", _mapping(case(head_branch)))
        _write(repo, "p/rules/core.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        rule = {"skills/b-declared.md": "rules/rule-a.md", "skills/b-undeclared.md": "rules/rule-b.md"}
        expected = {"skills/entry.md", "rules/core.md", base_branch, head_branch,
                    rule[base_branch], rule[head_branch]}
        measures = _entry_of(plan, "decl")["measures"]
        assert sorted(measures) == sorted(expected)
        assert len(measures) == len(expected) == 6

    def test_the_live_craft_lore_and_portage_mappings_plan_without_a_fault(self):
        repo = Path(__file__).resolve().parents[3]
        tree = select._head_tree(repo)
        touched = select.select_touched(repo, "HEAD", list(tree), merge_only_either=True, tree=tree)
        roots = {c["plugin_root"] for c in touched}
        assert {"tools/craft/plugins/craft", "tools/lore/plugins/lore", "tools/portage/plugins/portage"} <= roots


def _many_corpora(repo: Path, names: list[str]) -> None:
    for n in names:
        _corpus(repo, n, {f"c-{n}": {"measures": ["skills/a.md"]}})


def _bounded(repo, capsys, *, head_corpora: int, base_corpora: int):
    """A base holding ``base_corpora`` mapping files and a branch holding ``head_corpora``, with a change
    to a measured file of ``p0`` so the selection has something to select."""
    names = [f"p{i}" for i in range(max(head_corpora, base_corpora))]
    _many_corpora(repo, names[:base_corpora])
    _commit(repo)
    _branch(repo)
    _many_corpora(repo, names[base_corpora:head_corpora])
    for gone in names[head_corpora:]:
        _git(repo, "rm", "-rq", gone)
    _write(repo, "p0/skills/a.md", "changed\n")
    _commit(repo)
    return _plan(repo, capsys, "--base", "base")


class TestMappingFileCap:
    @pytest.mark.parametrize("head_n, base_n", [(32, 1), (32, 32), (1, 32)])
    def test_up_to_the_cap_of_mapping_files_at_either_revision_still_selects(self, repo, capsys, head_n, base_n):
        _, plan, _ = _bounded(repo, capsys, head_corpora=head_n, base_corpora=base_n)
        assert plan["status"] == "cases-selected", plan["mapping_invalid"]
        assert "c-p0" in _names(plan)

    def test_33_mapping_files_at_head_is_refused_naming_the_cap_and_the_count(self, repo, capsys):
        _, plan, _ = _bounded(repo, capsys, head_corpora=33, base_corpora=1)
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["fault"] == "33 mapping files at HEAD exceed the cap of 32"
        assert plan["mapping_invalid"]["revision"] == "head"

    def test_33_mapping_files_at_the_base_is_refused_naming_the_revision_and_says_so(self, repo, capsys):
        _, plan, _ = _bounded(repo, capsys, head_corpora=1, base_corpora=33)
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["fault"] == "33 mapping files at the base revision exceed the cap of 32"
        assert plan["mapping_invalid"]["revision"] == "base"

    def test_the_backstop_selection_answers_mapping_invalid_over_the_cap(self, repo):
        _many_corpora(repo, [f"p{i}" for i in range(33)])
        _commit(repo)
        _branch(repo)
        _write(repo, "p0/skills/a.md", "changed\n")
        _commit(repo)
        assert select.backstop_touched(repo, "base") == ("mapping-invalid", [])


def _wide_corpus(repo: Path, n: int) -> None:
    cases = {f"k{i}": {"measures": ["skills/a.md"]} for i in range(n)}
    _corpus(repo, "p", cases)


class TestCaseCap:
    @pytest.mark.parametrize("at_base", [False, True])
    def test_256_cases_in_a_mapping_load(self, repo, capsys, at_base):
        if at_base:
            _wide_corpus(repo, 256)
        else:
            _corpus(repo, "p", {"k0": {"measures": ["skills/a.md"]}})
        _commit(repo)
        _branch(repo)
        if not at_base:
            _wide_corpus(repo, 256)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected", plan["mapping_invalid"]
        assert len(plan["cases"]) == 256

    def test_257_cases_in_a_mapping_at_head_is_refused_naming_the_cap_the_mapping_and_the_count(
        self, repo, capsys
    ):
        _corpus(repo, "p", {"k0": {"measures": ["skills/a.md"]}})
        _commit(repo)
        _branch(repo)
        _wide_corpus(repo, 257)
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["file"] == "p/evals/eval-gate.json"
        assert plan["mapping_invalid"]["fault"] == (
            "257 cases in 'p/evals/eval-gate.json' exceed the cap of 256")
        assert plan["mapping_invalid"]["revision"] == "head"

    def test_the_case_count_is_checked_before_any_case_is_validated(self):
        from portage.eval_gate.mapping import MappingError, load_mapping

        cases = {f"k{i}": {"measures": ["skills/a.md"]} for i in range(257)}
        cases["k0"] = {"bogus": 1}
        with pytest.raises(MappingError) as e:
            load_mapping(_mapping(cases), "p/evals/eval-gate.json")
        assert e.value.fault == "257 cases in 'p/evals/eval-gate.json' exceed the cap of 256"

    def test_256_cases_with_an_invalid_one_report_that_case_not_the_cap(self):
        from portage.eval_gate.mapping import MappingError, load_mapping

        cases = {f"k{i}": {"measures": ["skills/a.md"]} for i in range(256)}
        cases["k0"] = {"bogus": 1}
        with pytest.raises(MappingError) as e:
            load_mapping(_mapping(cases), "p/evals/eval-gate.json")
        assert "unknown key 'bogus'" in e.value.fault

    def test_257_cases_in_a_mapping_at_the_base_is_refused_and_says_it_is_the_base(self, repo, capsys):
        _wide_corpus(repo, 257)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["revision"] == "base"
        assert plan["mapping_invalid"]["fault"] == (
            "the base revision's mapping is invalid: 257 cases in 'p/evals/eval-gate.json' "
            "exceed the cap of 256")


def _chain_corpus(repo: Path, files: int) -> None:
    """An entry that loads ``files - 1`` other procedure files in one sentence: ``files`` distinct
    procedure files in all."""
    names = [f"f{i}.md" for i in range(files - 1)]
    entry = "# Entry\n\nRead " + ", ".join(f"`{n}`" for n in names) + ".\n"
    cases = {"big": {"entry": "skills/entry.md"}}
    _corpus(repo, "p", cases, files=())
    _write(repo, "p/skills/entry.md", entry)
    for n in names:
        _write(repo, f"p/skills/{n}", "leaf\n")


class TestProcedureFileCap:
    def test_2000_procedure_files_at_a_revision_still_derive(self, repo, capsys):
        _chain_corpus(repo, 2000)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/f0.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected", plan["mapping_invalid"]
        assert len(_entry_of(plan, "big")["measures"]) == 2000

    def test_2001_procedure_files_at_head_is_refused_naming_the_case_the_cap_and_the_count(self, repo, capsys):
        _chain_corpus(repo, 1)
        _commit(repo)
        _branch(repo)
        _chain_corpus(repo, 2001)
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["revision"] == "head"
        assert plan["mapping_invalid"]["fault"] == (
            "case 'big': 2001 procedure files read at HEAD exceed the cap of 2000")

    def test_2001_procedure_files_at_the_base_is_refused_and_says_it_is_the_base(self, repo, capsys):
        _chain_corpus(repo, 2001)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/f0.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["revision"] == "base"
        assert plan["mapping_invalid"]["fault"] == (
            "the base revision's mapping is invalid: case 'big': 2001 procedure files read at the base "
            "revision exceed the cap of 2000")

    def test_the_cap_counts_the_files_read_across_every_case_of_a_revision(self, repo, capsys):
        def corpus(name: str) -> None:
            names = [f"{name}{i}.md" for i in range(1099)]
            _corpus(repo, name, {f"big-{name}": {"entry": "skills/entry.md"}}, files=())
            _write(repo, f"{name}/skills/entry.md", "# Entry\n\nRead " + ", ".join(f"`{n}`" for n in names) + ".\n")
            for n in names:
                _write(repo, f"{name}/skills/{n}", "leaf\n")

        corpus("p")
        _commit(repo)
        _branch(repo)
        corpus("q")
        _write(repo, "p/skills/p0.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["revision"] == "head"
        assert plan["mapping_invalid"]["fault"] == (
            "case 'big-q': 2001 procedure files read at HEAD exceed the cap of 2000")


class _CountingGit(select.Git):
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []
        self.cat_files = 0

    def run(self, repo, *args):
        self.calls.append(args)
        return super().run(repo, *args)

    def open_cat_file(self, repo):
        self.cat_files += 1
        return super().open_cat_file(repo)

    @property
    def processes(self) -> int:
        return len(self.calls) + self.cat_files


def _declared_corpora(repo: Path, mappings: int, cases_each: int) -> None:
    for m in range(mappings):
        plugin = f"p{m}"
        cases = {f"c{m}-{i}": {"entry": "skills/entry.md"} for i in range(cases_each)}
        _corpus(repo, plugin, cases, files=())
        _write(repo, f"{plugin}/skills/entry.md", "# Entry\n\nRead `rules/core.md`.\n")
        _write(repo, f"{plugin}/rules/core.md", "core\n")


class TestGitProcessCount:
    def _count(self, repo, mappings: int, cases_each: int) -> _CountingGit:
        _declared_corpora(repo, mappings, cases_each)
        _commit(repo)
        _branch(repo)
        _write(repo, "p0/rules/core.md", "changed\n")
        _commit(repo)
        git = _CountingGit()
        _, plan = select.plan(repo, "base", git=git)
        assert plan["status"] == "cases-selected", plan["mapping_invalid"]
        assert len(plan["cases"]) == cases_each
        return git

    def test_with_32_mappings_the_processes_stay_within_the_bound_for_the_mappings_and_revisions(self, repo):
        git = self._count(repo, 32, 2)
        fixed = 8        # work-tree, HEAD, base, merge-base, status, diff and one ls-tree per revision
        per_mapping = 4  # a show and a cat-file reader at each of the two revisions
        assert git.processes <= fixed + per_mapping * 32, (git.processes, git.calls[:12])

    def test_the_processes_do_not_grow_with_the_cases(self, tmp_path):
        counts = {}
        for cases_each in (1, 6):
            sub = tmp_path / f"n{cases_each}"
            sub.mkdir()
            r = sub / "repo"
            r.mkdir()
            _git(r, "init", "-b", "main")
            counts[cases_each] = self._count(r, 4, cases_each).processes
        assert counts[1] == counts[6], counts

    def test_every_git_call_that_takes_a_revision_passes_it_after_end_of_options(self, repo):
        git = self._count(repo, 2, 1)
        taking = [c for c in git.calls if c[0] in ("merge-base", "diff", "ls-tree", "show")
                  or c[:2] == ("rev-parse", "--verify")]
        assert {c[0] for c in taking} == {"merge-base", "diff", "ls-tree", "show", "rev-parse"}
        for call in taking:
            flag = call.index("--end-of-options")
            assert all(not a.startswith("-") for a in call[flag + 1:]), call


class TestEndOfOptions:
    def test_a_revision_that_looks_like_an_option_is_never_read_as_one_by_diff(self, repo, tmp_path):
        _corpus(repo, "p", M)
        _commit(repo)
        leak = tmp_path / "leak"
        with pytest.raises(select.GitError):
            select._changed_since(repo, f"--output={leak}")
        assert not list(tmp_path.glob("leak*"))

    def test_a_revision_that_looks_like_an_option_is_never_read_as_one_by_show(self, repo, tmp_path):
        _corpus(repo, "p", M)
        _commit(repo)
        leak = tmp_path / "leak"
        assert select._show(repo, f"--output={leak}", "a.md") is None
        assert not list(tmp_path.glob("leak*"))


class TestOptionLikeBase:
    @pytest.mark.parametrize("base", ["--all", "-x", "-", "--output=leak"])
    def test_a_base_starting_with_a_dash_is_base_invalid_with_no_git_error_or_traceback(
        self, repo, capsys, tmp_path, base
    ):
        _corpus(repo, "p", M)
        _commit(repo)
        capsys.readouterr()
        state, plan = select.plan(repo, base)
        assert plan["status"] == "base-invalid"
        assert plan["cases"] == [] and plan["merge_base"] is None
        assert json.loads((state / "plan.json").read_text())["status"] == "base-invalid"
        err = capsys.readouterr().err
        assert err == ""
        assert not (repo / "leak").exists()

    def test_the_plan_command_and_verdict_stop_on_it_with_the_base_line(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        rc, plan, out = _plan(repo, capsys, "--base=--all")
        assert rc == 0 and plan["status"] == "base-invalid"
        state = next(ln for ln in out.splitlines() if ln.startswith("state: "))[len("state: "):]
        capsys.readouterr()
        code = dispatch.main(["eval-gate", "verdict", "--state", state])
        captured = capsys.readouterr()
        assert code == 1
        assert captured.out.splitlines()[:3] == ["eval-gate: base-invalid", "persistent: none",
                                                 "base-invalid: the base must not start with '-'"]
        assert "Traceback" not in captured.err and "usage:" not in captured.err

    def test_a_real_ref_still_resolves(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected"


class _KillNth(select.Git):
    """A git whose ``n``th ``cat-file --batch`` reader is dead on arrival."""

    def __init__(self, n: int):
        self.n, self.opened = n, 0

    def open_cat_file(self, repo):
        self.opened += 1
        proc = super().open_cat_file(repo)
        if self.opened == self.n:
            proc.kill()
            proc.wait()
        return proc


class TestReaderFailureNamesItsRevision:
    def _both_sides_declare(self, repo):
        _declared_corpus(repo)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/entry.md", ENTRY + "\nmore\n")
        _commit(repo)

    def test_a_reader_that_dies_reading_head_gives_a_fault_naming_head(self, repo):
        self._both_sides_declare(repo)
        _, plan = select.plan(repo, "base", git=_KillNth(2))
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["revision"] == "head"
        assert plan["mapping_invalid"]["fault"] == "case 'decl': 'skills/entry.md' could not be read at HEAD"

    def test_a_reader_that_dies_reading_the_base_gives_a_fault_naming_the_base(self, repo):
        self._both_sides_declare(repo)
        _, plan = select.plan(repo, "base", git=_KillNth(1))
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["revision"] == "base"
        assert plan["mapping_invalid"]["fault"] == (
            "the base revision's mapping is invalid: case 'decl': 'skills/entry.md' could not be read "
            "at the base revision")


class TestPlanOutputIsAllowlisted:
    def test_a_corpus_path_outside_the_allowlist_never_reaches_stdout_raw(self, repo, capsys):
        hostile = "a@evil"
        _corpus(repo, hostile, {"k": {"measures": ["skills/a.md"]}})
        _commit(repo)
        _branch(repo)
        _write(repo, f"{hostile}/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, out = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        line = next(ln for ln in out.splitlines() if ln.startswith("mapping-invalid: "))
        assert "@" not in line and "evil" not in line
        assert line.startswith("mapping-invalid: <invalid-corpus-path>: ")

    def test_the_fault_for_such_a_path_echoes_it_only_through_the_allowlist(self, repo, capsys):
        _corpus(repo, "a@evil", {"k": {"measures": ["skills/a.md"]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "a@evil/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["mapping_invalid"]["fault"] == (
            "the corpus path #1 has characters outside A-Za-z0-9._/- or a '..' segment")

    def test_a_valid_corpus_path_still_prints_as_itself(self, repo, capsys):
        _corpus(repo, "p", {k: {"measures": ["skills/missing.md"]} for k in ["k"]})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, _, out = _plan(repo, capsys, "--base", "base")
        assert any(ln.startswith("mapping-invalid: p/evals/eval-gate.json: ") for ln in out.splitlines())


class TestWholeChangeReviewFixes:
    @pytest.mark.parametrize("measure", [".", "./"])
    def test_a_measure_of_the_plugin_root_holds_the_split_core_and_is_refused(self, repo, capsys, measure):
        _declared_corpus(repo, cases={"hand": {"measures": [measure]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["fault"] == (
            "case 'hand': measures path '.' is or holds the split core 'skills/entry.md'")

    def test_a_measure_of_the_plugin_root_is_fine_when_nothing_in_it_is_split(self, repo, capsys):
        _corpus(repo, "p", {"hand": {"measures": ["."]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "cases-selected" and _names(plan) == ["hand"]

    @pytest.mark.parametrize("key, value", [
        ("entry", "rules/core.md"),
        ("entry", "README.md"),
        ("branches", ["rules/rule-a.md"]),
    ])
    def test_an_entry_or_branch_that_is_not_a_procedure_path_is_a_mapping_fault(self, repo, capsys, key, value):
        case = {"entry": "skills/entry.md", "branches": ["skills/b-declared.md"], key: value}
        _declared_corpus(repo, files={"README.md": "readme\n", "skills/entry.md": ENTRY + "Read `README.md`.\n"})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/evals/eval-gate.json", _mapping({"decl": case}))
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        shown = value if isinstance(value, str) else value[0]
        assert plan["mapping_invalid"]["fault"] == (
            f"case 'decl': {key} path '{shown}' must be a skill or agent file (skills/.../x.md or agents/x.md)")

    @pytest.mark.parametrize("entry", ["skills/entry.md", "agents/monitor.md"])
    def test_an_entry_under_skills_or_agents_is_accepted(self, repo, capsys, entry):
        _declared_corpus(repo, cases={"decl": {"entry": entry}},
                         files={"agents/monitor.md": "Read `rules/core.md`.\n"})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] != "mapping-invalid"

    def test_a_duplicate_case_at_the_base_is_blamed_on_the_base_not_the_branch(self, repo, capsys):
        _declared_corpus(repo, "p", cases={"x": {"measures": ["skills/other.md"]}})
        _declared_corpus(repo, "q", cases={"x": {"measures": ["skills/other.md"]}})
        _commit(repo)
        _branch(repo)
        _declared_corpus(repo, "q", cases={"y": {"measures": ["skills/other.md"]}})
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["revision"] == "base"
        assert plan["mapping_invalid"]["fault"] == (
            "the base revision's mapping is invalid: case 'x' is declared by more than one corpus")

    def test_a_duplicate_case_at_the_head_is_still_blamed_on_the_head(self, repo, capsys):
        _declared_corpus(repo, "p", cases={"x": {"measures": ["skills/other.md"]}})
        _declared_corpus(repo, "q", cases={"y": {"measures": ["skills/other.md"]}})
        _commit(repo)
        _branch(repo)
        _declared_corpus(repo, "q", cases={"x": {"measures": ["skills/other.md"]}})
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert plan["mapping_invalid"]["revision"] == "head"
        assert plan["mapping_invalid"]["fault"] == "case 'x' is declared by more than one corpus"

    def test_the_cli_prints_a_mapping_fault_through_the_fault_allowlist(self, repo, capsys):
        _declared_corpus(repo, cases={"hand": {"measures": ["skills/entry.md"]}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/other.md", "changed\n")
        _commit(repo)
        _, plan, out = _plan(repo, capsys, "--base", "base")
        assert "case 'hand': measures" in plan["mapping_invalid"]["fault"]
        line = next(ln for ln in out.splitlines() if ln.startswith("mapping-invalid: "))
        assert line.split(": ", 1)[1] == (
            "p/evals/eval-gate.json: case 'hand' measures path 'skills/entry.md' is or holds the split core "
            "'skills/entry.md'")


class TestBlobCap:
    @staticmethod
    def _plan_with_core_of(repo, capsys, size: int):
        _declared_corpus(repo, cases={"decl": {"entry": "skills/entry.md"}})
        _commit(repo)
        _branch(repo)
        _write(repo, "p/rules/core.md", "x" * size)
        _commit(repo)
        return _plan(repo, capsys, "--base", "base")

    def test_a_procedure_file_of_exactly_the_cap_is_read(self, repo, capsys):
        from portage.eval_gate.mapping import MAX_BLOB_BYTES

        _, plan, _ = self._plan_with_core_of(repo, capsys, MAX_BLOB_BYTES)
        assert plan["status"] == "cases-selected"

    def test_a_procedure_file_one_byte_over_the_cap_is_mapping_invalid_naming_it(self, repo, capsys):
        from portage.eval_gate.mapping import MAX_BLOB_BYTES

        _, plan, _ = self._plan_with_core_of(repo, capsys, MAX_BLOB_BYTES + 1)
        assert plan["status"] == "mapping-invalid"
        assert plan["mapping_invalid"]["fault"] == (
            f"case 'decl': 'rules/core.md' is larger than {MAX_BLOB_BYTES} bytes")


class TestNonUtf8Path:
    @pytest.fixture
    def hostile(self, repo):
        _corpus(repo, "p", M)
        _commit(repo)
        _branch(repo)
        bad = os.fsencode(repo) + b"/p/skills/bad\xff.md"
        with open(bad, "wb") as f:
            f.write(b"x\n")
        _commit(repo)
        return repo

    def test_plan_refuses_a_committed_path_that_is_not_utf8_with_a_clean_exit(self, hostile, capsys):
        capsys.readouterr()
        rc = dispatch.main(["eval-gate", "plan", "--repo", str(hostile), "--opt-in", "--base", "base"])
        captured = capsys.readouterr()
        assert rc == 2
        assert captured.err.startswith("eval-gate plan: ")
        assert "Traceback" not in captured.err

    def test_backstop_touched_raises_the_git_error_callers_already_handle(self, hostile):
        with pytest.raises(select.GitError):
            select.backstop_touched(hostile, "base")

    def test_the_merge_check_refuses_with_selection_failed_instead_of_crashing(self, hostile):
        refusal, head = merge_check.check_pair(str(hostile), None, lambda: "a" * 40)
        assert head is None and refusal.check == "selection-failed"

    def test_a_utf8_path_still_plans(self, repo, capsys):
        _corpus(repo, "p", M)
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/caf\u00e9.md")
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        _, plan, _ = _plan(repo, capsys, "--base", "base")
        assert _names(plan) == ["alpha"]

    def test_a_non_utf8_mapping_body_is_still_mapping_invalid_not_a_git_failure(self, repo, capsys):
        _write(repo, "p/skills/a.md")
        _write(repo, "p/evals/alpha/case.yaml")
        (repo / "p/evals").mkdir(parents=True, exist_ok=True)
        (repo / "p/evals/eval-gate.json").write_bytes(b'{"x": "\xff"}')
        _commit(repo)
        _branch(repo)
        _write(repo, "p/skills/a.md", "changed\n")
        _commit(repo)
        rc, plan, _ = _plan(repo, capsys, "--base", "base")
        assert rc == 0 and plan["status"] == "mapping-invalid" and "UTF-8" in plan["mapping_invalid"]["fault"]


class TestCaseListCaps:
    @staticmethod
    def _case_with(key: str, paths: list[str]) -> dict:
        case = {"entry": "skills/a.md", "measures": ["skills/b.md"]}
        case[key] = paths
        return {"alpha": case}

    @pytest.mark.parametrize("key", ["measures", "branches"])
    def test_a_list_of_exactly_the_cap_loads_and_one_more_is_refused(self, key):
        from portage.eval_gate.mapping import MAX_CASE_LIST, MappingError, load_mapping

        def paths(n):
            return [f"skills/p{i}.md" for i in range(n)]

        load_mapping(_mapping(self._case_with(key, paths(MAX_CASE_LIST))), "p/evals/eval-gate.json")
        with pytest.raises(MappingError) as e:
            load_mapping(_mapping(self._case_with(key, paths(MAX_CASE_LIST + 1))), "p/evals/eval-gate.json")
        assert e.value.fault == f"case 'alpha': {key} holds {MAX_CASE_LIST + 1} paths, over the cap of {MAX_CASE_LIST}"

    @pytest.mark.parametrize("key", ["measures", "branches"])
    def test_a_path_repeated_in_a_list_is_refused_naming_its_position(self, key):
        fault = _fault(_mapping(self._case_with(key, ["skills/p.md", "skills/q.md", "skills/./p.md"])))
        assert fault == f"case 'alpha': {key} path 'skills/./p.md' repeats an earlier entry"

    @pytest.mark.parametrize("key", ["measures", "branches"])
    def test_distinct_paths_load(self, key):
        from portage.eval_gate.mapping import load_mapping

        load_mapping(_mapping(self._case_with(key, ["skills/p.md", "skills/q.md"])), "p/evals/eval-gate.json")


class TestPlanPrintsTheFlagsTheMonitorReads:
    CASES = {"split": {"entry": "skills/entry.md", "branches": ["skills/b-declared.md"]},
             "only": {"measures": ["skills/other.md"], "merge_only": True},
             "plain": {"measures": ["skills/other.md"]}}

    def _flag_lines(self, repo, capsys, tier="merge"):
        _declared_corpus(repo, cases=self.CASES)
        _commit(repo)
        _branch(repo)
        for rel in ("skills/entry.md", "skills/other.md"):
            _write(repo, f"p/{rel}", (repo / "p" / rel).read_text() + "\nchanged\n")
        _commit(repo)
        _, plan, out = _plan(repo, capsys, "--base", "base", "--tier", tier)
        return plan, [ln for ln in out.splitlines() if ln.startswith("case-flags: ")]

    def test_each_selected_case_prints_its_merge_only_and_split_procedure_flags(self, repo, capsys):
        _, lines = self._flag_lines(repo, capsys)
        assert lines == [
            "case-flags: p:only merge_only=true split_procedure=false",
            "case-flags: p:plain merge_only=false split_procedure=false",
            "case-flags: p:split merge_only=false split_procedure=true"]

    def test_the_push_tier_prints_flags_only_for_the_cases_it_selects(self, repo, capsys):
        _, lines = self._flag_lines(repo, capsys, tier="push")
        assert [ln.split()[1] for ln in lines] == ["p:plain", "p:split"]

    def test_the_flags_agree_with_plan_json(self, repo, capsys):
        plan, lines = self._flag_lines(repo, capsys)
        want = [f"case-flags: p:{c['case']} merge_only={str(c['merge_only']).lower()} "
                f"split_procedure={str(c['split_procedure']).lower()}" for c in plan["cases"]]
        assert lines == want


UNSPLIT_ENTRY = "# Entry\n\nDo A when the user asks for A. Do B when the user asks for B.\n"
CORE_ENTRY = "# Entry\n\nRead `rules/core.md`.\n\nRead `skills/b-a.md` when the user asks for A.\n"
BRANCH_A = "Applies when the user asks for A.\n\nDo A.\n"
SPLIT_MAPPING_PATH = "p/evals/eval-gate.json"


def _split_mapping(split: bool, *, merge_only: bool = False, mark: bool = False) -> str:
    one = {"entry": "skills/entry.md"}
    two: dict = {"entry": "skills/entry.md"}
    if split:
        two["branches"] = ["skills/b-a.md"]
    if merge_only:
        two["merge_only"] = True
    if mark:
        two["needs_merge_model"] = MARK
    return json.dumps({"schema_version": 1, "runner": TIERED, "cases": {"one": one, "two": two}})


def _split_repo(repo, capsys, *, base_split: bool, head_split: bool, base_mark: bool = False,
                head_merge_only: bool = False, head_mark: bool = False, tier: str = "push",
                base_merge_only: bool = False):
    """Case ``two`` is the case under test; ``one`` always declares the bare entry. The entry is a split core
    at a revision when its text names a branch file, which the mapping then declares."""
    _git(repo, "init", "-b", "main")
    for name in ("one", "two"):
        _write(repo, f"p/evals/{name}/case.yaml", "name: x\n")
    _write(repo, "p/rules/core.md", "core\n")
    _write(repo, "p/skills/entry.md", CORE_ENTRY if base_split else UNSPLIT_ENTRY)
    if base_split:
        _write(repo, "p/skills/b-a.md", BRANCH_A)
    _write(repo, SPLIT_MAPPING_PATH, _split_mapping(base_split, merge_only=base_merge_only, mark=base_mark))
    _commit(repo)
    _branch(repo)
    _write(repo, "p/skills/entry.md", (CORE_ENTRY if head_split else UNSPLIT_ENTRY) + "edited\n")
    if head_split and not base_split:
        _write(repo, "p/skills/b-a.md", BRANCH_A)
    if base_split and not head_split:
        (repo / "p/skills/b-a.md").unlink()
    _write(repo, SPLIT_MAPPING_PATH, _split_mapping(head_split, merge_only=head_merge_only, mark=head_mark))
    _commit(repo)
    return _plan(repo, capsys, "--base", "base", "--tier", tier)


REFUSAL_FIX = "mark it needs_merge_model with a reason, or drop merge_only"


class TestSplitProcedureCaseRunsOnThePushModel:
    def test_a_split_selects_every_case_declared_on_the_entry_and_an_unmarked_failure_stops_the_push(
        self, repo, capsys
    ):
        rc, plan, out = _split_repo(repo, capsys, base_split=False, head_split=True)
        assert rc == 0 and plan["status"] == "cases-selected", plan
        assert _names(plan) == ["one", "two"]
        assert _entry_of(plan, "two")["split_procedure"] is True
        for c in plan["cases"]:
            assert c["runner"]["model"] == "sonnet"
        state = Path(next(ln for ln in out.splitlines() if ln.startswith("state: "))[len("state: "):])
        (state / "results").mkdir(exist_ok=True, mode=0o700)
        for c in plan["cases"]:
            fail = c["case"] == "one"
            (state / "results" / f"{c['case']}.json").write_text(json.dumps(
                {"case": c["case"], "verdict": "fail" if fail else "pass", "passed": 0 if fail else 3,
                 "failed": 3 if fail else 0, "errored": 0, "reason": None, "runs": []}))
        capsys.readouterr()
        code = dispatch.main(["eval-gate", "verdict", "--state", str(state)])
        report = capsys.readouterr().out
        assert code == 1, report
        assert "one failed on the push model (sonnet)" in report
        assert "ask the operator whether to add a needs-merge-model mark" in report
        assert "two failed on the push model" not in report

    def test_a_case_the_base_marks_needs_merge_model_resolves_to_the_merge_model(self, repo, capsys):
        _, plan, _ = _split_repo(repo, capsys, base_split=False, head_split=True, base_mark=True,
                                 head_mark=True)
        assert _names(plan) == ["one", "two"]
        assert _entry_of(plan, "two")["runner"]["model"] == "opus"
        assert _entry_of(plan, "one")["runner"]["model"] == "sonnet"


class TestSplitProcedureMergeOnlyRefusal:
    @pytest.mark.parametrize("tier", ["push", "merge"])
    @pytest.mark.parametrize("base_split,head_split", [(False, True), (True, False), (True, True)])
    def test_a_merge_only_case_on_an_entry_split_at_either_revision_without_a_mark_is_refused(
        self, repo, capsys, tier, base_split, head_split
    ):
        rc, plan, out = _split_repo(repo, capsys, base_split=base_split, head_split=head_split,
                                    head_merge_only=True, tier=tier)
        assert plan["status"] == "mapping-invalid", plan
        assert plan["cases"] == []
        bad = plan["mapping_invalid"]
        assert bad["file"] == SPLIT_MAPPING_PATH
        assert "case 'two'" in bad["fault"] and REFUSAL_FIX in bad["fault"]
        assert "\n" not in bad["fault"]
        assert f"mapping-invalid: {SPLIT_MAPPING_PATH}:" in out

    def test_the_fault_echoes_no_mapping_text(self, repo, capsys):
        _, plan, _ = _split_repo(repo, capsys, base_split=False, head_split=True, head_merge_only=True)
        fault = plan["mapping_invalid"]["fault"]
        assert "skills/entry.md" not in fault and "skills/b-a.md" not in fault
        assert "true" not in fault

    @pytest.mark.parametrize("tier", ["push", "merge"])
    def test_a_marked_merge_only_case_on_a_split_entry_is_allowed(self, repo, capsys, tier):
        _, plan, _ = _split_repo(repo, capsys, base_split=False, head_split=True, head_merge_only=True,
                                 head_mark=True, tier=tier)
        assert plan["status"] == "cases-selected", plan
        assert _names(plan) == ["one", "two"]

    @pytest.mark.parametrize("base_split,head_split", [(False, True), (True, True), (True, False)])
    def test_a_branch_that_drops_merge_only_from_a_split_core_case_selects_it_at_push(
        self, repo, capsys, base_split, head_split
    ):
        _, plan, _ = _split_repo(repo, capsys, base_split=base_split, head_split=head_split,
                                 base_merge_only=True, head_merge_only=False)
        assert plan["status"] == "cases-selected", plan
        assert _names(plan) == ["one", "two"]
        assert plan["merge_only_pending"] == 0
        assert not _entry_of(plan, "two")["merge_only"]
        assert _entry_of(plan, "two")["runner"]["model"] == TIERED["push_model"]

    def test_a_merge_only_case_on_an_entry_split_at_neither_revision_plans_as_before(self, repo, capsys):
        _, plan, _ = _split_repo(repo, capsys, base_split=False, head_split=False, base_merge_only=True,
                                 head_merge_only=True)
        assert plan["mapping_invalid"] is None
        assert plan["status"] != "mapping-invalid"
        assert plan["merge_only_pending"] == 1
        assert _names(plan) == ["one"]


class TestLiveCorpusMergeOnlyCases:
    def _live_repo(self, repo: Path) -> dict:
        live = json.loads((_portage_cli.PLUGIN_ROOT / "evals" / "eval-gate.json").read_text())
        shutil.copytree(_portage_cli.PLUGIN_ROOT / "evals", repo / "p" / "evals")
        measured = sorted({m for c in live["cases"].values() for m in c["measures"]})
        for rel in measured:
            _write(repo, f"p/{rel}", (_portage_cli.PLUGIN_ROOT / rel).read_text())
        _commit(repo)
        _branch(repo)
        for rel in measured:
            _write(repo, f"p/{rel}", "changed\n")
        _commit(repo)
        return live["cases"]

    def test_the_live_merge_only_cases_plan_as_merge_only_at_merge_and_are_left_out_at_push(
        self, repo, capsys
    ):
        cases = self._live_repo(repo)
        merge_only = sorted(n for n, c in cases.items() if c.get("merge_only"))
        assert len(merge_only) >= 1
        _, push, _ = _plan(repo, capsys, "--base", "base", "--tier", "push")
        _, merge, _ = _plan(repo, capsys, "--base", "base", "--tier", "merge")
        assert push["status"] == "cases-selected" and merge["status"] == "cases-selected"
        assert push["mapping_invalid"] is None and merge["mapping_invalid"] is None
        assert _names(push) == sorted(set(cases) - set(merge_only))
        assert push["merge_only_pending"] == len(merge_only)
        assert _names(merge) == sorted(cases)
        assert merge["merge_only_pending"] == 0
        assert sorted(c["case"] for c in merge["cases"] if c["merge_only"]) == merge_only
        assert not any(c["merge_only"] for c in push["cases"])
