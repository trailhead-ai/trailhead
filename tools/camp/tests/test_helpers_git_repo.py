"""Contract of the suite's own ``init_git_repo`` helper.

Every camp test that needs a repository gets it from this one helper, so the
properties asserted here are the ones ~100 call sites are entitled to assume:
a repo on ``main`` carrying exactly one commit under a fixed identity, with a
clean worktree, and — only when the caller asks for it — an ``origin`` remote
pointing at the repo itself whose remote-tracking refs already resolve.

``origin`` is the input that varies: with it, ``origin/main`` resolves and the
remote URL is the repo's own path; without it, neither exists. A repo built
for one case must not quietly satisfy the other, which is what keeps the
helper's cheap path honest about what it produced.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ._helpers import _clone_template, init_git_repo


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def test_repo_is_on_main_with_one_commit_and_a_clean_tree(tmp_path):
    repo = tmp_path / "plain"
    init_git_repo(repo)

    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert _git(repo, "status", "--porcelain").stdout == ""
    assert (repo / "README.md").read_text() == "# test\n"


def test_commit_carries_the_fixed_test_identity(tmp_path):
    """Pinned so the suite never depends on the developer's git config."""
    repo = tmp_path / "identity"
    init_git_repo(repo)

    author = _git(repo, "log", "-1", "--format=%an <%ae>").stdout.strip()
    assert author == "Test <test@test.com>"


def test_origin_true_gives_a_self_pointing_remote_whose_refs_resolve(tmp_path):
    repo = tmp_path / "with-origin"
    init_git_repo(repo, origin=True)

    assert _git(repo, "remote", "get-url", "origin").stdout.strip() == str(repo)
    remote_head = _git(repo, "rev-parse", "origin/main")
    assert remote_head.returncode == 0
    assert remote_head.stdout.strip() == _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_origin_false_gives_no_remote_and_no_tracking_refs(tmp_path):
    """The other side of the varied input: no remote asked for, none present."""
    repo = tmp_path / "no-origin"
    init_git_repo(repo, origin=False)

    assert _git(repo, "remote").stdout.strip() == ""
    assert _git(repo, "rev-parse", "origin/main").returncode != 0


def test_two_repos_are_independent_histories_not_shared_state(tmp_path):
    """Each call must yield a repo the caller can commit into on its own.

    Call sites routinely build two repos in one test and then diverge them, so
    a write to one must not be visible in the other, and neither may point at
    anything outside its own directory.
    """
    a, b = tmp_path / "a", tmp_path / "b"
    init_git_repo(a, origin=True)
    init_git_repo(b, origin=True)

    (a / "only-in-a.txt").write_text("a\n")
    assert _git(a, "add", "only-in-a.txt").returncode == 0
    assert _git(a, "commit", "-m", "second", "--no-gpg-sign").returncode == 0

    assert _git(a, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    assert _git(b, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert not (b / "only-in-a.txt").exists()
    assert _git(b, "remote", "get-url", "origin").stdout.strip() == str(b)


def test_clone_survives_a_lock_file_vanishing_mid_copy(tmp_path, monkeypatch):
    """Regression test for a race between `_clone_template` and git's own
    background maintenance.

    Git can create and then remove a `.git/objects/maintenance.lock` file
    while a copy of the template is in flight: the file is present when
    `shutil.copytree` lists the directory but gone by the time it tries to
    open it, which raises `shutil.Error` unless lock files are left out of
    the copy. Simulate the vanish directly — a lock file that raises
    `FileNotFoundError` the moment something tries to copy it — and confirm
    the clone still succeeds with a fully working repo.
    """
    template = tmp_path / "template"
    init_git_repo(template)
    (template / ".git" / "objects" / "maintenance.lock").write_text("")

    real_copy2 = shutil.copy2

    def vanishing_copy2(src, dst, *args, **kwargs):
        if os.fspath(src).endswith(".lock"):
            raise FileNotFoundError(src)
        return real_copy2(src, dst, *args, **kwargs)

    # `_clone_template` passes `copy_function=shutil.copy2` explicitly (rather
    # than relying on `copytree`'s own default) precisely so this patch is
    # observable; see the comment at its call site.
    monkeypatch.setattr(shutil, "copy2", vanishing_copy2)

    dest = tmp_path / "clone"
    _clone_template(template, dest)

    assert not (dest / ".git" / "objects" / "maintenance.lock").exists()
    assert _git(dest, "rev-parse", "HEAD").returncode == 0
