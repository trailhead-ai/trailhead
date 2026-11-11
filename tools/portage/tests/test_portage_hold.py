"""`portage hold` / `portage release` and `portage merge`'s refusal of a held branch.

`merge` runs against the real ``GitHubProvider`` with a spy runner standing in for ``gh``. The
held-branch refusal reads the PR's head branch through that same runner, and must happen before
any merge call.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage import holds
from portage.cli import dispatch
from portage.cli import pr as pr_cli

from trailhead.tests._git_support import hermetic_git_env  # noqa: F401  (fixture)
from trailhead.vcs.github import GitHubProvider

pytestmark = pytest.mark.usefixtures("hermetic_git_env")


class _SpyRunner:
    """Stands in for ``gh``. ``view`` sets how ``gh pr view`` answers: a dict is the JSON payload,
    ``"fail"`` is a nonzero exit, ``"empty"`` is a clean exit with no output."""

    def __init__(self, view=None) -> None:
        self.calls: list[list[str]] = []
        self.cwds: list[str | None] = []
        self.view = view if view is not None else {"headRefName": "feat"}

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        self.cwds.append(kwargs.get("cwd"))
        s = " ".join(cmd)
        if "user.email" in s:
            return subprocess.CompletedProcess(cmd, 0, "t@e.x\n", "")
        if "view" in s and "--json" in s:
            if self.view == "fail":
                return subprocess.CompletedProcess(cmd, 1, "", "no pull request found")
            if self.view == "empty":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            payload = {"state": "OPEN", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                       "isDraft": False, "baseRefName": "main", **self.view}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(cmd, 0, "merged\n", "")

    def merge_attempted(self) -> bool:
        return any("pr" in c and "merge" in c for c in self.calls)

    def only_views(self) -> bool:
        return all(c[:3] == ["gh", "pr", "view"] for c in self.calls)


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAGE_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    for cmd in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True)
    return r


def _merge(tmp_path, monkeypatch, repo, toml, *, operator_directed=False, view=None):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "members": [
        {"name": "api", "repo_root": str(repo), "worktree_path": str(repo)}]}))
    group = tmp_path / "group.toml"
    group.write_text(toml)
    spy = _SpyRunner(view)
    monkeypatch.setattr(pr_cli, "get_provider", lambda *a, **k: GitHubProvider(runner=spy))
    argv = ["merge", "--manifest", str(manifest), "--toml", str(group)]
    if operator_directed:
        argv.append("--operator-directed")
    argv.append(f"{repo}:42:api")
    return dispatch.main(argv), spy


AUTO = "[release]\nauto_merge = true\n"


@pytest.mark.parametrize("steps, view", [
    pytest.param([], None, id="no-hold"),
    pytest.param([["hold", "{repo}", "other"]], None, id="hold-on-another-branch"),
    pytest.param([["hold", "{repo}", "feat"], ["release", "{repo}", "feat"]], None,
                 id="held-then-released"),
    pytest.param([["hold", "{other}", "feat"]], None, id="same-branch-held-in-another-repo"),
    pytest.param([["hold", "{repo}", "feat"]], {"headRefName": "other"},
                 id="checked-out-branch-held-but-not-the-pr-head"),
    pytest.param([["hold", "{repo}", "main"]], {"headRefName": "feat"},
                 id="base-branch-held-but-not-the-pr-head"),
])
def test_merge_proceeds_when_the_pr_head_is_not_held(tmp_path, monkeypatch, repo, steps, view):
    other = tmp_path / "other"
    other.mkdir()
    for step in steps:
        argv = [a.format(repo=repo, other=other) for a in step]
        assert dispatch.main(argv) == 0
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO, view=view)
    assert spy.merge_attempted()
    assert rc == 0


def test_hold_blocks_merge_before_any_merge_call_with_auto_merge(tmp_path, monkeypatch, repo, capsys):
    assert dispatch.main(["hold", str(repo), "feat"]) == 0
    capsys.readouterr()
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    err = capsys.readouterr().err
    assert rc == 2
    assert not spy.merge_attempted()
    assert "held" in err and "portage release" in err and "feat" in err


def test_hold_blocks_merge_even_when_operator_directed(tmp_path, monkeypatch, repo):
    dispatch.main(["hold", str(repo), "feat"])
    rc, spy = _merge(tmp_path, monkeypatch, repo, "[release]\n", operator_directed=True)
    assert rc == 2
    assert not spy.merge_attempted()


def test_holding_twice_is_one_hold(tmp_path, repo):
    assert dispatch.main(["hold", str(repo), "feat"]) == 0
    assert dispatch.main(["hold", str(repo), "feat"]) == 0
    files = [p for p in (tmp_path / "state").rglob("*") if p.is_file()]
    assert len(files) == 1
    dispatch.main(["release", str(repo), "feat"])
    assert not [p for p in (tmp_path / "state").rglob("*") if p.is_file()]


def test_release_of_an_unheld_branch_is_ok(repo):
    assert dispatch.main(["release", str(repo), "feat"]) == 0


@pytest.mark.parametrize("verb", ["hold", "release"])
@pytest.mark.parametrize("branch", ["bad branch", "a;b", "$(x)", "", "a\nb", "é"])
def test_invalid_branch_name_is_refused(repo, capsys, verb, branch):
    rc = dispatch.main([verb, str(repo), branch])
    assert rc == 2
    assert capsys.readouterr().err.startswith("portage: ")


def test_held_head_branch_refuses_when_a_different_branch_is_checked_out(
    tmp_path, monkeypatch, repo, capsys
):
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "other"], check=True)
    dispatch.main(["hold", str(repo), "feat"])
    capsys.readouterr()
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    err = capsys.readouterr().err
    assert rc == 2
    assert spy.only_views()
    assert "held" in err and "feat" in err


def test_the_head_branch_is_read_in_the_pairs_repo(tmp_path, monkeypatch, repo):
    dispatch.main(["hold", str(repo), "feat"])
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    views = [cwd for c, cwd in zip(spy.calls, spy.cwds) if c[:3] == ["gh", "pr", "view"]]
    assert views and all(cwd == str(repo) for cwd in views)


@pytest.mark.parametrize("view", ["fail", "empty"])
def test_an_unreadable_head_branch_with_a_hold_on_the_repo_refuses(
    tmp_path, monkeypatch, repo, capsys, view
):
    dispatch.main(["hold", str(repo), "feat"])
    capsys.readouterr()
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO, view=view)
    err = capsys.readouterr().err
    assert rc == 2
    assert not spy.merge_attempted()
    assert "held" in err and "portage release" in err


@pytest.mark.parametrize("view", ["fail", "empty"])
def test_an_unreadable_head_branch_without_any_hold_on_the_repo_is_not_a_hold_refusal(
    tmp_path, monkeypatch, repo, capsys, view
):
    capsys.readouterr()
    _merge(tmp_path, monkeypatch, repo, AUTO, view=view)
    assert "held" not in capsys.readouterr().err


def test_a_repo_with_no_hold_never_asks_for_the_head_branch(tmp_path, monkeypatch, repo):
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    asked = [c for c in spy.calls if c[:3] == ["gh", "pr", "view"]]
    # the merge itself reads the PR once; the hold check adds no further read
    assert len(asked) == 1
    assert spy.merge_attempted()


def test_a_hold_entry_for_another_repo_does_not_count(tmp_path, repo):
    other = tmp_path / "other"
    other.mkdir()
    holds.hold(str(other), "feat")
    assert holds.has_any_hold(str(repo)) is False


def test_an_unlistable_holds_directory_counts_as_held(tmp_path, repo):
    dispatch.main(["hold", str(repo), "feat"])
    holds_dir = holds._holds_dir()
    assert holds_dir.is_dir()
    holds_dir.chmod(0)
    try:
        assert holds.has_any_hold(str(repo)) is True
    finally:
        holds_dir.chmod(0o700)


def test_a_missing_holds_directory_counts_as_no_hold(repo):
    assert holds.has_any_hold(str(repo)) is False


@pytest.mark.parametrize("verb", ["hold", "release"])
def test_an_absolute_existing_directory_is_accepted(repo, verb):
    assert dispatch.main([verb, str(repo), "feat"]) == 0


@pytest.mark.parametrize("verb", ["hold", "release"])
@pytest.mark.parametrize("kind", ["relative", "bare-name", "missing-absolute", "a-file"])
def test_a_repo_that_is_not_an_absolute_existing_directory_is_refused_and_holds_nothing(
    tmp_path, monkeypatch, capsys, verb, kind
):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "afile").write_text("x")
    monkeypatch.chdir(tmp_path)
    arg = {
        "relative": "./real",
        "bare-name": "real",
        "missing-absolute": str(tmp_path / "nope"),
        "a-file": str(tmp_path / "afile"),
    }[kind]
    rc = dispatch.main([verb, arg, "feat"])
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("portage: ") and "absolute" in err
    assert not [p for p in (tmp_path / "state").rglob("*") if p.is_file()]


def test_a_hold_through_a_relative_path_never_reports_success_for_a_hold_merge_misses(
    tmp_path, monkeypatch, repo
):
    monkeypatch.chdir(repo.parent)
    assert dispatch.main(["hold", "repo", "feat"]) == 2
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    assert spy.merge_attempted()


@pytest.mark.parametrize("entry_body", [
    pytest.param({"repo": "/somewhere/else", "branch": "feat"}, id="repo-field-wrong"),
    pytest.param({"branch": "feat"}, id="repo-field-missing"),
])
def test_a_hold_entry_named_for_the_repo_counts_whatever_its_repo_field_says(repo, entry_body):
    holds.hold(str(repo), "feat")
    (entry,) = holds._holds_dir().iterdir()
    entry.write_text(json.dumps(entry_body))
    assert holds.has_any_hold(str(repo)) is True


def test_a_hold_entry_named_for_another_repo_with_a_wrong_field_does_not_count(tmp_path, repo):
    other = tmp_path / "other"
    other.mkdir()
    holds.hold(str(other), "feat")
    (entry,) = holds._holds_dir().iterdir()
    entry.write_text(json.dumps({"repo": "/somewhere/else", "branch": "feat"}))
    assert holds.has_any_hold(str(repo)) is False


@pytest.mark.parametrize("body", ["{not json", "[]", '"held"', "7", "null"])
def test_a_hold_entry_that_is_not_a_json_object_counts_as_held(repo, body):
    holds._holds_dir().mkdir(parents=True, exist_ok=True)
    (holds._holds_dir() / "odd.json").write_text(body)
    assert holds.has_any_hold(str(repo)) is True


def test_a_stat_error_on_the_hold_entry_counts_as_held(repo):
    holds.hold(str(repo), "feat")
    holds._holds_dir().chmod(0)
    try:
        assert holds.is_held(str(repo), "feat") is True
        assert holds.is_held(str(repo), "other") is True
    finally:
        holds._holds_dir().chmod(0o700)


def test_an_absent_hold_entry_is_not_held(repo):
    holds.hold(str(repo), "feat")
    assert holds.is_held(str(repo), "other") is False


def test_the_shipped_cli_refuses_a_held_head_branch_and_never_runs_gh_pr_merge(  # inert-gate: allow seam smoke
    tmp_path, repo
):
    """The shipped `bin/portage` wired end to end: `hold` then `merge` as separate processes
    sharing a state directory, with a `gh` on PATH that answers the PR's head branch and logs
    every call."""
    # merge reads user.email in the directory it runs from (the repo, below); the hermetic env has
    # no global config to supply it
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.st"], check=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "gh.log"
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {log}\n'
        'case "$*" in\n'
        '  *"pr view"*) echo \'{"state":"OPEN","mergeable":"MERGEABLE",'
        '"mergeStateStatus":"CLEAN","isDraft":false,"headRefName":"feat"}\';;\n'
        "esac\n"
    )
    gh.chmod(0o755)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "members": [
        {"name": "api", "repo_root": str(repo), "worktree_path": str(repo)}]}))
    group = tmp_path / "group.toml"
    group.write_text(AUTO)
    wrapper = Path(__file__).resolve().parents[1] / "plugins" / "portage" / "bin" / "portage"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
           "PORTAGE_STATE_DIR": str(tmp_path / "state")}

    def run(*args):
        return subprocess.run([str(wrapper), *args], env=env, cwd=repo, capture_output=True, text=True)

    assert run("hold", str(repo), "feat").returncode == 0
    merge = run("merge", "--manifest", str(manifest), "--toml", str(group), f"{repo}:42:api")
    assert merge.returncode == 2
    assert "held" in merge.stderr and "portage release" in merge.stderr
    assert "pr merge" not in log.read_text()

    assert run("release", str(repo), "feat").returncode == 0
    merge = run("merge", "--manifest", str(manifest), "--toml", str(group), f"{repo}:42:api")
    assert "pr merge" in log.read_text()
