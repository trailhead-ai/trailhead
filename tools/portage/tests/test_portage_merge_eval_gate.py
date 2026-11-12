"""`portage merge --eval-state` — the merge-tier eval-gate backstop.

Each test builds a real git repo under ``tmp_path`` (a base commit mapped to ``origin/HEAD`` and a
feature commit that touches the measured file), builds the state with the real
``portage eval-gate plan`` and result files in the shape ``run`` writes, and drives
``dispatch.main(["merge", ...])`` against the real ``GitHubProvider`` with a spy runner standing in
for ``gh``. The spy records every ``gh`` call, so each refusal test shows the remote-head read
happened where the check reaches it and that ``gh pr merge`` never ran.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.cli import dispatch
from portage.cli import pr as pr_cli

from trailhead.tests._git_support import hermetic_git_env  # noqa: F401  (fixture)
from trailhead.vcs.github import GitHubProvider

pytestmark = pytest.mark.usefixtures("hermetic_git_env")

RUNNER = {"model": "sonnet", "allow_tools": ["Read"], "max_cost_usd": 2.0}
CORE = {"measures": ["skills/a.md"]}
SLOW = {"measures": ["skills/a.md"], "merge_only": True}
AUTO = "[release]\nauto_merge = true\nmerge_method = \"squash\"\n"
NO_AUTO = "[release]\n"
PR = "42"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def make_repo(root: Path, name: str, cases: dict, *, origin: bool = True) -> tuple[Path, str]:
    """A repo whose feature branch touches ``skills/a.md``; returns ``(repo, head_sha)``."""
    repo = root / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for f in ("skills/a.md", "skills/b.md"):
        (repo / "p" / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / "p" / f).write_text("x\n")
    (repo / "p/evals").mkdir(parents=True, exist_ok=True)
    for case in cases:
        (repo / f"p/evals/{case}").mkdir(parents=True, exist_ok=True)
        (repo / f"p/evals/{case}/case.yaml").write_text("name: x\n")
    (repo / "p/evals/eval-gate.json").write_text(
        json.dumps({"schema_version": 1, "runner": RUNNER, "cases": cases}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    if origin:
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "p/skills/a.md").write_text("changed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return repo, _git(repo, "rev-parse", "HEAD")


def make_state(repo: Path, capsys, *, tier="merge", passing=True) -> Path:
    capsys.readouterr()
    assert dispatch.main(["eval-gate", "plan", "--repo", str(repo), "--tier", tier]) == 0
    out = capsys.readouterr().out
    state = Path(next(ln for ln in out.splitlines() if ln.startswith("state: "))[len("state: "):])
    plan = json.loads((state / "plan.json").read_text())
    (state / "results").mkdir(mode=0o700, exist_ok=True)
    for c in plan["cases"]:
        verdict = "pass" if passing else "fail"
        doc = {"case": c["case"], "verdict": verdict, "reason": None, "runs": [],
               "passed": 3 if passing else 0, "failed": 0 if passing else 3, "errored": 0}
        (state / "results" / f"{c['case']}.json").write_text(json.dumps(doc))
    return state


class Spy:
    """Stands in for ``gh``; ``remote_head`` is what the PR's head reads as."""

    def __init__(self, remote_head: str, *, head_fails: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.remote_head = remote_head
        self.head_fails = head_fails

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        s = " ".join(cmd)
        if "user.email" in s:
            return subprocess.CompletedProcess(cmd, 0, "t@e.x\n", "")
        if "remote" in cmd and "get-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "git@github.com:acme/widgets.git\n", "")
        if cmd[:3] == ["gh", "pr", "view"]:
            payload = {"state": "OPEN", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                       "isDraft": False, "headRefName": "feature", "baseRefName": "main"}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        if cmd[:2] == ["gh", "api"] and "/commits" in s:
            c = [{"sha": self.remote_head, "commit": {"committer": {"date": "2026-07-01T00:00:00Z"}}}]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(c), "")
        if cmd[:2] == ["gh", "api"] and "/pulls/" in s:
            if self.head_fails:
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"head": {"sha": self.remote_head}}), "")
        return subprocess.CompletedProcess(cmd, 0, "merged\n", "")

    @property
    def merges(self) -> list[list[str]]:
        return [c for c in self.calls if c[:3] == ["gh", "pr", "merge"]]

    @property
    def head_reads(self) -> int:
        return sum(1 for c in self.calls if c[:2] == ["gh", "api"] and "/pulls/" in " ".join(c)
                   and "/commits" not in " ".join(c))


def merge(tmp_path, monkeypatch, repo: Path, spy: Spy, *extra: str, toml: str = AUTO):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "members": [
        {"name": "api", "repo_root": str(repo), "worktree_path": str(repo)}]}))
    group = tmp_path / "group.toml"
    group.write_text(toml)
    monkeypatch.setattr(pr_cli, "get_provider", lambda *a, **k: GitHubProvider(runner=spy))
    monkeypatch.setenv("PORTAGE_STATE_DIR", str(tmp_path / "pstate"))
    argv = ["merge", "--manifest", str(manifest), "--toml", str(group), *extra, f"{repo}:{PR}:api"]
    return dispatch.main(argv)


def refused(rc, spy, capsys, repo, check):
    err = capsys.readouterr().err
    assert rc == 2
    assert spy.merges == []
    assert f"portage merge: eval-gate check failed for {repo}: {check}" in err
    assert "portage eval-gate plan" in err and "--tier merge" in err
    assert "portage eval-gate run" in err and "portage eval-gate verdict" in err
    return err


@pytest.fixture
def mo(tmp_path):
    """A repo touching a core and a merge_only case."""
    return make_repo(tmp_path, "repo", {"core": CORE, "slow": SLOW})


class TestPinnedMerge:
    def test_a_current_merge_tier_state_merges_pinned_to_its_head(
        self, tmp_path, monkeypatch, capsys, mo
    ):
        repo, head = mo
        state = make_state(repo, capsys)
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy, "--eval-state", f"{repo}={state}")
        assert rc == 0
        assert spy.head_reads == 1
        (argv,) = spy.merges
        assert argv[argv.index("--match-head-commit") + 1] == head

    def test_the_pin_is_the_state_head_not_whatever_gh_is_told(
        self, tmp_path, monkeypatch, capsys, mo
    ):
        repo, head = mo
        state = make_state(repo, capsys)
        spy = Spy(head)
        merge(tmp_path, monkeypatch, repo, spy, "--eval-state", f"{repo}={state}")
        (argv,) = spy.merges
        assert head in argv and "0" * 40 not in argv


class TestRefusals:
    def _refuse(self, tmp_path, monkeypatch, capsys, repo, spy, *extra, check, toml=AUTO):
        rc = merge(tmp_path, monkeypatch, repo, spy, *extra, toml=toml)
        return refused(rc, spy, capsys, repo, check)

    def test_no_eval_state(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        spy = Spy(head)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, check="no-state")
        assert spy.head_reads == 0

    def test_operator_directed_does_not_waive_a_missing_state(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        spy = Spy(head)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--operator-directed",
                     check="no-state", toml=NO_AUTO)

    def test_a_push_tier_state(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        state = make_state(repo, capsys, tier="push")
        spy = Spy(head)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--eval-state", f"{repo}={state}",
                     check="state-tier-not-merge")

    def test_a_push_landed_after_the_gate(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        state = make_state(repo, capsys)
        spy = Spy("f" * 40)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--eval-state", f"{repo}={state}",
                     check="head-mismatch")
        assert spy.head_reads == 1

    def test_a_state_for_another_repo(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        other, _ = make_repo(tmp_path, "other", {"core": CORE, "slow": SLOW})
        state = make_state(other, capsys)
        spy = Spy(head)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--eval-state", f"{repo}={state}",
                     check="state-for-another-repo")

    def test_a_state_whose_verdict_fails(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        state = make_state(repo, capsys, passing=False)
        spy = Spy(head)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--eval-state", f"{repo}={state}",
                     check="verdict-stops-the-change")
        assert spy.head_reads == 1

    def test_a_merge_only_case_that_never_ran_stops_the_merge(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        state = make_state(repo, capsys)
        (state / "results" / "slow.json").unlink()
        spy = Spy(head)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--eval-state", f"{repo}={state}",
                     check="verdict-stops-the-change")

    def test_a_world_writable_state_directory(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        state = make_state(repo, capsys)
        os.chmod(state, 0o777)
        spy = Spy(head)
        err = self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--eval-state",
                           f"{repo}={state}", check="state-unusable")
        assert "0700" in err

    def test_an_unreadable_remote_head_fails_closed(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        state = make_state(repo, capsys)
        spy = Spy(head, head_fails=True)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--eval-state", f"{repo}={state}",
                     check="head-unreadable")
        assert spy.head_reads == 1

    @pytest.mark.parametrize("bad", ["--admin", "HEAD", "abc", "A" * 40, ""])
    def test_a_malformed_head_in_plan_json_never_reaches_gh(
        self, tmp_path, monkeypatch, capsys, mo, bad
    ):
        repo, head = mo
        state = make_state(repo, capsys)
        plan = json.loads((state / "plan.json").read_text())
        plan["head"] = bad
        (state / "plan.json").write_text(json.dumps(plan))
        spy = Spy(head)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--eval-state", f"{repo}={state}",
                     check="plan-head-malformed")

    def test_a_plan_that_leaves_out_a_touched_merge_only_case(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        state = make_state(repo, capsys)
        plan = json.loads((state / "plan.json").read_text())
        plan["cases"] = [c for c in plan["cases"] if c["case"] != "slow"]
        (state / "plan.json").write_text(json.dumps(plan))
        spy = Spy(head)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--eval-state", f"{repo}={state}",
                     check="plan-misses-merge-only-case")

    def test_a_plan_json_that_is_not_json(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        state = make_state(repo, capsys)
        (state / "plan.json").write_text("{")
        spy = Spy(head)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, "--eval-state", f"{repo}={state}",
                     check="plan-unusable")

    def test_the_check_runs_before_the_auto_merge_refusal(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        spy = Spy(head)
        self._refuse(tmp_path, monkeypatch, capsys, repo, spy, check="no-state", toml=NO_AUTO)


class TestUndecidableApplicability:
    def test_a_dirty_tree_does_not_hide_a_merge_only_case(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        (repo / "p/skills/b.md").write_text("dirty\n")
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        refused(rc, spy, capsys, repo, "no-state")

    def test_a_dirty_tree_in_a_repo_without_merge_only_cases_merges(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "core-only", {"core": CORE})
        (repo / "p/skills/b.md").write_text("dirty\n")
        spy = Spy(head)
        assert merge(tmp_path, monkeypatch, repo, spy) == 0
        assert len(spy.merges) == 1

    def test_an_invalid_mapping_is_refused_not_waved_through(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        (repo / "p/evals/eval-gate.json").write_text("{")
        _git(repo, "commit", "-q", "-am", "break mapping")
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        refused(rc, spy, capsys, repo, "no-state")

    def test_an_unresolvable_base_in_a_repo_with_a_merge_only_case_is_refused(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "nobase", {"core": CORE, "slow": SLOW}, origin=False)
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        refused(rc, spy, capsys, repo, "no-state")

    def test_an_unresolvable_base_in_a_repo_with_no_merge_only_case_merges(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "nobase", {"core": CORE}, origin=False)
        spy = Spy(head)
        assert merge(tmp_path, monkeypatch, repo, spy) == 0

    def test_a_state_planned_without_a_base_is_refused_even_though_its_verdict_passes(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "nobase", {"core": CORE, "slow": SLOW}, origin=False)
        state = make_state(repo, capsys, tier="merge")
        assert json.loads((state / "plan.json").read_text())["status"] == "base-unresolved"
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy, "--eval-state", f"{repo}={state}")
        refused(rc, spy, capsys, repo, "plan-not-selected")


class TestGitCannotAnswer:
    def test_a_repo_git_cannot_select_from_is_refused_not_waved_through(
        self, tmp_path, monkeypatch, capsys
    ):
        repo = tmp_path / "unborn"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        spy = Spy("a" * 40)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        refused(rc, spy, capsys, repo, "selection-failed")


class TestNotApplicable:
    @pytest.fixture
    def core_only(self, tmp_path):
        return make_repo(tmp_path, "core-only", {"core": CORE})

    def test_merges_without_a_state_and_without_a_pin(self, tmp_path, monkeypatch, core_only):
        repo, head = core_only
        spy = Spy(head)
        assert merge(tmp_path, monkeypatch, repo, spy) == 0
        (argv,) = spy.merges
        assert "--match-head-commit" not in argv
        assert spy.head_reads == 0

    def test_a_given_state_is_not_consulted(self, tmp_path, monkeypatch, capsys, core_only):
        repo, head = core_only
        spy = Spy("f" * 40)
        junk = tmp_path / "junk"
        assert merge(tmp_path, monkeypatch, repo, spy, "--eval-state", f"{repo}={junk}") == 0
        (argv,) = spy.merges
        assert "--match-head-commit" not in argv
        assert spy.head_reads == 0

    def test_the_auto_merge_refusal_is_unchanged(self, tmp_path, monkeypatch, capsys, core_only):
        repo, head = core_only
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy, toml=NO_AUTO)
        err = capsys.readouterr().err
        assert rc == 2
        assert spy.merges == []
        assert err.strip().startswith("refusing to merge — auto_merge is unset/false")
        assert "eval-gate" not in err.split("`[release] auto_merge = true`")[0]


class TestUsage:
    @pytest.mark.parametrize("arg", ["nodelimiter", "=state", "repo=", ""])
    def test_a_malformed_eval_state_is_a_usage_error(self, tmp_path, monkeypatch, capsys, mo, arg):
        repo, head = mo
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy, "--eval-state", arg)
        assert rc == 2
        assert spy.merges == []
        err = capsys.readouterr().err
        assert "merge: --eval-state expects <repo_path>=<state_dir>" in err
        assert "eval-gate check failed" not in err
