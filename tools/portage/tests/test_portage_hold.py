"""`portage hold` / `portage release` and `portage merge`'s refusal of a held branch.

`merge` runs against the real ``GitHubProvider`` with a spy runner standing in for ``gh``;
the held-branch refusal must happen before the spy sees any call.
"""

from __future__ import annotations

import json
import subprocess

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.cli import dispatch
from portage.cli import pr as pr_cli

from trailhead.vcs.github import GitHubProvider


class _SpyRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        s = " ".join(cmd)
        if "user.email" in s:
            return subprocess.CompletedProcess(cmd, 0, "t@e.x\n", "")
        if "view" in s and "--json" in s:
            payload = {"state": "OPEN", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                       "isDraft": False, "headRefName": "feat"}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(cmd, 0, "merged\n", "")

    def merge_attempted(self) -> bool:
        return any("pr" in c and "merge" in c for c in self.calls)


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAGE_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    for cmd in (["init", "-q", "-b", "feat"], ["-c", "user.email=t@e.x", "-c", "user.name=T",
                "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True)
    return r


def _merge(tmp_path, monkeypatch, repo, toml, *, operator_directed=False):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "members": [
        {"name": "api", "repo_root": str(repo), "worktree_path": str(repo)}]}))
    group = tmp_path / "group.toml"
    group.write_text(toml)
    spy = _SpyRunner()
    monkeypatch.setattr(pr_cli, "get_provider", lambda *a, **k: GitHubProvider(runner=spy))
    argv = ["merge", "--manifest", str(manifest), "--toml", str(group)]
    if operator_directed:
        argv.append("--operator-directed")
    argv.append(f"{repo}:42:api")
    return dispatch.main(argv), spy


AUTO = "[release]\nauto_merge = true\n"


def test_merge_on_an_unheld_branch_proceeds(tmp_path, monkeypatch, repo):
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    assert spy.merge_attempted()
    assert rc == 0


def test_hold_blocks_merge_before_any_gh_call_with_auto_merge(tmp_path, monkeypatch, repo, capsys):
    assert dispatch.main(["hold", str(repo), "feat"]) == 0
    capsys.readouterr()
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    err = capsys.readouterr().err
    assert rc == 2
    assert spy.calls == []
    assert "held" in err and "portage release" in err and "feat" in err


def test_hold_blocks_merge_even_when_operator_directed(tmp_path, monkeypatch, repo):
    dispatch.main(["hold", str(repo), "feat"])
    rc, spy = _merge(tmp_path, monkeypatch, repo, "[release]\n", operator_directed=True)
    assert rc == 2
    assert spy.calls == []


def test_hold_on_another_branch_does_not_block(tmp_path, monkeypatch, repo):
    dispatch.main(["hold", str(repo), "other"])
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    assert spy.merge_attempted()


def test_release_lets_merge_proceed(tmp_path, monkeypatch, repo):
    dispatch.main(["hold", str(repo), "feat"])
    assert dispatch.main(["release", str(repo), "feat"]) == 0
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    assert spy.merge_attempted()


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


def test_hold_is_keyed_by_repo_too(tmp_path, monkeypatch, repo):
    other = tmp_path / "other"
    other.mkdir()
    dispatch.main(["hold", str(other), "feat"])
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    assert spy.merge_attempted()


def _detach(repo):
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach"], check=True)


def test_unreadable_branch_with_a_hold_on_the_repo_refuses_before_any_gh_call(
    tmp_path, monkeypatch, repo, capsys
):
    dispatch.main(["hold", str(repo), "feat"])
    _detach(repo)
    capsys.readouterr()
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    err = capsys.readouterr().err
    assert rc == 2
    assert spy.calls == []
    assert "held" in err and "portage release" in err


def test_unreadable_branch_without_any_hold_on_the_repo_proceeds(tmp_path, monkeypatch, repo):
    _detach(repo)
    rc, spy = _merge(tmp_path, monkeypatch, repo, AUTO)
    assert spy.merge_attempted()
