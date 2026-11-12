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
from portage.eval_gate import merge_check
from portage.eval_gate.run import run_case

from trailhead.tests._git_support import hermetic_git_env  # noqa: F401  (fixture)
from trailhead.vcs.github import GitHubProvider

from test_portage_eval_gate_run import PASS, Env, _result

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


def make_repo(root: Path, name: str, cases: dict | None, *, origin: bool = True) -> tuple[Path, str]:
    """A repo whose feature branch touches ``skills/a.md``; returns ``(repo, head_sha)``.
    ``cases=None`` writes no ``eval-gate.json`` at all."""
    repo = root / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for f in ("skills/a.md", "skills/b.md"):
        (repo / "p" / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / "p" / f).write_text("x\n")
    if cases is not None:
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
        return make_repo(tmp_path, "core-only", None)

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


UNTOUCHED = {"measures": ["skills/b.md"], "merge_only": True}


class TestPinnedWhenTheCheckDoesNotApply:
    """A merge_only case the change does not touch still pins the merge to the head it was
    judged against: "not touched" is computed from local HEAD, which a remote-only commit
    (a suggestion applied on GitHub) can outrun."""

    @pytest.fixture
    def untouched(self, tmp_path):
        return make_repo(tmp_path, "untouched", {"core": CORE, "later": UNTOUCHED})

    def test_a_remote_head_ahead_of_local_head_is_refused_without_merging(
        self, tmp_path, monkeypatch, capsys, untouched
    ):
        repo, head = untouched
        spy = Spy("f" * 40)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        err = capsys.readouterr().err
        assert rc == 2
        assert spy.merges == []
        assert spy.head_reads == 1
        assert f"portage merge: eval-gate check failed for {repo}: local-head-stale" in err
        assert "f" * 40 in err and head in err
        assert "pull the PR head" in err and "retry" in err

    def test_a_remote_head_equal_to_local_head_merges_pinned_to_it(
        self, tmp_path, monkeypatch, capsys, untouched
    ):
        repo, head = untouched
        spy = Spy(head)
        assert merge(tmp_path, monkeypatch, repo, spy) == 0
        (argv,) = spy.merges
        assert argv[argv.index("--match-head-commit") + 1] == head

    def test_an_unreadable_remote_head_fails_closed(self, tmp_path, monkeypatch, capsys, untouched):
        repo, head = untouched
        spy = Spy(head, head_fails=True)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        err = capsys.readouterr().err
        assert rc == 2
        assert spy.merges == []
        assert f"eval-gate check failed for {repo}: head-unreadable" in err

    def test_a_repo_without_any_mapping_reads_no_head_and_pins_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "no-map", None)
        spy = Spy("f" * 40)
        assert merge(tmp_path, monkeypatch, repo, spy) == 0
        (argv,) = spy.merges
        assert "--match-head-commit" not in argv
        assert spy.head_reads == 0


def _remap(repo: Path, cases: dict, msg: str = "remap") -> None:
    (repo / "p/evals/eval-gate.json").write_text(
        json.dumps({"schema_version": 1, "runner": RUNNER, "cases": cases}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


class TestStaleBaseDoesNotHideMergeOnly:
    """The base mapping reads ``merge_only: false`` (a stale ``origin/HEAD``); the branch's committed
    mapping says true. Either side marking the case is enough to require the merge-tier verdict."""

    @pytest.fixture
    def stale(self, tmp_path):
        later = {"measures": ["skills/a.md"]}
        repo, _ = make_repo(tmp_path, "stale", {"core": CORE, "later": later})
        _remap(repo, {"core": CORE, "later": {**later, "merge_only": True}})
        return repo, _git(repo, "rev-parse", "HEAD")

    def test_a_case_flagged_on_the_branch_alone_refuses_a_merge_without_a_state(
        self, tmp_path, monkeypatch, capsys, stale
    ):
        repo, head = stale
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        refused(rc, spy, capsys, repo, "no-state")

    def test_a_current_merge_tier_state_still_merges(self, tmp_path, monkeypatch, capsys, stale):
        repo, head = stale
        state = make_state(repo, capsys)
        plan = json.loads((state / "plan.json").read_text())
        assert {c["case"]: c["merge_only"] for c in plan["cases"]} == {"core": False, "later": True}
        spy = Spy(head)
        assert merge(tmp_path, monkeypatch, repo, spy, "--eval-state", f"{repo}={state}") == 0


class TestPinWhenTheRepoHoldsAnyMapping:
    def test_a_mapping_without_a_merge_only_case_still_refuses_a_remote_head_ahead_of_local(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "plain", {"core": CORE})
        spy = Spy("f" * 40)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        err = capsys.readouterr().err
        assert rc == 2 and spy.merges == [] and spy.head_reads == 1
        assert f"eval-gate check failed for {repo}: local-head-stale" in err

    def test_a_mapping_without_a_merge_only_case_merges_pinned_when_heads_agree(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "plain", {"core": CORE})
        spy = Spy(head)
        assert merge(tmp_path, monkeypatch, repo, spy) == 0
        (argv,) = spy.merges
        assert argv[argv.index("--match-head-commit") + 1] == head

    def test_a_mapping_the_branch_adds_pins_though_the_base_has_none(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, _ = make_repo(tmp_path, "added", None)
        (repo / "p/evals/core").mkdir(parents=True)
        (repo / "p/evals/core/case.yaml").write_text("name: x\n")
        _remap(repo, {"core": CORE})
        spy = Spy("f" * 40)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        assert rc == 2 and spy.merges == []
        assert "local-head-stale" in capsys.readouterr().err

    def test_a_mapping_that_exists_only_at_the_merge_base_still_pins(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "dropped", {"core": CORE})
        _git(repo, "rm", "-rq", "p/evals")
        _git(repo, "commit", "-q", "-m", "drop the corpus")
        spy = Spy("f" * 40)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        assert rc == 2 and spy.merges == []
        assert "local-head-stale" in capsys.readouterr().err


class TestFailClosedEdges:
    def _pair(self, repo, head):
        spy = Spy(head)
        return merge_check.check_pair(str(repo), None, lambda: spy.remote_head)

    def test_a_directory_that_is_not_a_git_repository_is_not_applicable(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert self._pair(plain, "a" * 40) == (None, None)

    def test_a_repo_git_cannot_enter_is_selection_failed_not_not_applicable(self, tmp_path):
        repo, head = make_repo(tmp_path, "locked", {"core": CORE, "slow": SLOW})
        repo.chmod(0)
        try:
            refusal, pin = self._pair(repo, head)
        finally:
            repo.chmod(0o755)
        assert pin is None and refusal.check == "selection-failed"

    def test_an_os_error_from_selection_is_selection_failed(self, tmp_path, monkeypatch):
        repo, head = make_repo(tmp_path, "nogit", {"core": CORE, "slow": SLOW})
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        refusal, pin = self._pair(repo, head)
        assert pin is None and refusal.check == "selection-failed"

    def test_an_os_error_while_pinning_an_untouched_repo_is_selection_failed(self, tmp_path, monkeypatch):
        repo, head = make_repo(tmp_path, "nogit", {"core": CORE})
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        refusal, pin = merge_check._pin_untouched(repo, "ok", lambda: head)
        assert pin is None and refusal.check == "selection-failed"

    def test_a_merge_with_no_git_refuses_cleanly_without_a_traceback(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "nogit", {"core": CORE, "slow": SLOW})
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        spy = Spy(head)
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "members": [
            {"name": "api", "repo_root": str(repo), "worktree_path": str(repo)}]}))
        group = tmp_path / "group.toml"
        group.write_text(AUTO)
        monkeypatch.setattr(pr_cli, "get_provider", lambda *a, **k: GitHubProvider(runner=spy))
        monkeypatch.setenv("PATH", str(empty))
        rc = dispatch.main(["merge", "--manifest", str(manifest), "--toml", str(group),
                            f"{repo}:{PR}:api"])
        err = capsys.readouterr().err
        assert rc == 2 and spy.merges == []
        assert "selection-failed" in err and "Traceback" not in err

    @pytest.mark.parametrize("field,bad", [
        ("plugin_root", ["p"]), ("plugin_root", 7), ("plugin_root", {"a": 1}), ("plugin_root", None),
        ("case", ["core"]), ("case", {"a": 1}), ("case", 3),
    ])
    def test_a_crafted_plan_entry_of_the_wrong_type_is_plan_unusable(
        self, tmp_path, monkeypatch, capsys, mo, field, bad
    ):
        repo, head = mo
        state = make_state(repo, capsys)
        plan = json.loads((state / "plan.json").read_text())
        plan["cases"][0][field] = bad
        (state / "plan.json").write_text(json.dumps(plan))
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy, "--eval-state", f"{repo}={state}")
        err = refused(rc, spy, capsys, repo, "plan-unusable")
        assert "Traceback" not in err


class TestBaseRemedy:
    BASE_LINE = "portage eval-gate plan --repo"

    def test_a_plan_whose_base_did_not_resolve_names_the_base_flag(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "nobase", {"core": CORE, "slow": SLOW}, origin=False)
        state = make_state(repo, capsys, tier="merge")
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy, "--eval-state", f"{repo}={state}")
        err = refused(rc, spy, capsys, repo, "plan-not-selected")
        assert f"portage eval-gate plan --repo {repo} --tier merge --base <ref>" in err

    def test_a_repo_whose_base_did_not_resolve_and_has_no_state_names_the_base_flag(
        self, tmp_path, monkeypatch, capsys
    ):
        repo, head = make_repo(tmp_path, "nobase", {"core": CORE, "slow": SLOW}, origin=False)
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        err = refused(rc, spy, capsys, repo, "no-state")
        assert "--tier merge --base <ref>" in err

    def test_other_refusals_do_not_mention_the_base_flag(self, tmp_path, monkeypatch, capsys, mo):
        repo, head = mo
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy)
        err = refused(rc, spy, capsys, repo, "no-state")
        assert "--base" not in err
        state = make_state(repo, capsys, tier="push")
        rc = merge(tmp_path, monkeypatch, repo, Spy(head), "--eval-state", f"{repo}={state}")
        err = capsys.readouterr().err
        assert rc == 2 and "state-tier-not-merge" in err
        assert "--base" not in err


class TestEndToEndSeam:
    """plan -> run -> verdict -> merge, each the real step, so the shape one writes is the shape the
    next reads: the only stub is the runner binary (the fake ``claude`` the run tests use) and ``gh``."""

    def test_a_state_built_by_the_real_steps_merges_pinned_to_its_head(
        self, tmp_path, monkeypatch, capsys, mo
    ):
        repo, head = mo
        capsys.readouterr()
        assert dispatch.main(["eval-gate", "plan", "--repo", str(repo), "--tier", "merge"]) == 0
        out = capsys.readouterr().out
        state = Path(next(ln for ln in out.splitlines() if ln.startswith("state: "))[len("state: "):])
        planned = [c["case"] for c in json.loads((state / "plan.json").read_text())["cases"]]
        assert sorted(planned) == ["core", "slow"]

        real_path = os.environ["PATH"]
        (tmp_path / "runner").mkdir()
        runner = Env(tmp_path / "runner", monkeypatch)
        runner.fake(pool=_result([PASS] * 12, costUsd=0.1), code=0)
        for case in planned:
            assert run_case(state, case)["verdict"] == "pass"
        monkeypatch.setenv("PATH", real_path)

        assert dispatch.main(["eval-gate", "verdict", "--state", str(state)]) == 0
        capsys.readouterr()
        spy = Spy(head)
        rc = merge(tmp_path, monkeypatch, repo, spy, "--eval-state", f"{repo}={state}")
        assert rc == 0, capsys.readouterr().err
        (argv,) = spy.merges
        assert argv[argv.index("--match-head-commit") + 1] == head


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
