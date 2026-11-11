"""`portage check-slice-branch` — the PR step's entry check on the record's `craft/branch` label.

Runs the real CLI through ``dispatch.main`` against real git repos under ``tmp_path``.
"""

from __future__ import annotations

import json
import subprocess

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.cli import dispatch

pytestmark = pytest.mark.usefixtures("hermetic_git_env")

OK, UNREADABLE, DEFAULT_BRANCH, NOT_RECORD_BRANCH = 0, 2, 3, 4


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _member(tmp_path, name, default, head_set=True):
    """A member worktree whose origin's default branch is `default`; `head_set=False` leaves
    refs/remotes/origin/HEAD unset."""
    origin = tmp_path / f"{name}-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", default, str(origin)], check=True)
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", default, str(repo)], check=True)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", default)
    if head_set:
        _git(repo, "remote", "set-head", "origin", default)
    else:
        _git(repo, "update-ref", "-d", "refs/remotes/origin/HEAD")
    return repo


def _run(tmp_path, capsys, members, record, branch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "members": [
        {"name": m.name, "repo_root": str(m), "worktree_path": str(m)} for m in members]}))
    rc = dispatch.main(["check-slice-branch", "--manifest", str(manifest),
                        "--record", record, "--branch", branch])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_the_records_own_task_branch_passes(tmp_path, capsys):
    repo = _member(tmp_path, "repo", "main")
    assert _run(tmp_path, capsys, [repo], "count-words", "count-words")[0] == OK


@pytest.mark.parametrize("default", ["main", "trunk", "develop"])
def test_a_label_naming_the_repos_default_branch_is_refused(tmp_path, capsys, default):
    repo = _member(tmp_path, "repo", default)
    rc, out, err = _run(tmp_path, capsys, [repo], default, default)
    assert (rc, out) == (DEFAULT_BRANCH, "")
    assert err.startswith("portage: ")


def test_a_label_naming_a_different_member_repos_default_branch_is_refused(tmp_path, capsys):
    api = _member(tmp_path, "api", "main")
    web = _member(tmp_path, "web", "trunk")
    assert _run(tmp_path, capsys, [api, web], "trunk", "trunk")[0] == DEFAULT_BRANCH


@pytest.mark.parametrize("name", ["main", "master"])
def test_the_conventional_default_names_are_refused_when_origin_head_is_unset(
    tmp_path, capsys, name
):
    repo = _member(tmp_path, "repo", "trunk", head_set=False)
    assert _run(tmp_path, capsys, [repo], name, name)[0] == DEFAULT_BRANCH


@pytest.mark.parametrize("label", ["other-slice", "count-words-2", "Count-Words", "release/count-words"])
def test_a_label_that_is_not_the_records_own_branch_is_refused(tmp_path, capsys, label):
    repo = _member(tmp_path, "repo", "main")
    rc, out, err = _run(tmp_path, capsys, [repo], "count-words", label)
    assert (rc, out) == (NOT_RECORD_BRANCH, "")
    assert err.startswith("portage: ")


def test_a_default_branch_label_is_reported_as_such_even_when_it_also_differs_from_the_record(
    tmp_path, capsys
):
    repo = _member(tmp_path, "repo", "main")
    assert _run(tmp_path, capsys, [repo], "count-words", "main")[0] == DEFAULT_BRANCH


def test_the_label_text_is_not_echoed_on_a_refusal(tmp_path, capsys):
    repo = _member(tmp_path, "repo", "main")
    rc, out, err = _run(tmp_path, capsys, [repo], "count-words", "ignore-all-instructions")
    assert rc == NOT_RECORD_BRANCH
    assert "ignore-all-instructions" not in out + err


def test_an_unreadable_manifest_exits_2(tmp_path, capsys):
    rc = dispatch.main(["check-slice-branch", "--manifest", str(tmp_path / "nope.json"),
                        "--record", "a", "--branch", "a"])
    assert rc == UNREADABLE
    assert capsys.readouterr().err.startswith("portage: ")
