"""Pins ``_vault_mid_rebase`` — the single liveness authority for a resolution
session, and ``sync``'s abort verification.

The contract it answers to:

  - True exactly while a rebase is in progress, False once it is over, so a
    caller can tell a stopped resolution from a finished one. Only the state
    directory's disappearance means the rebase is done.
  - Correct in a linked worktree, where ``.git`` is a file and the state
    directory lives under the main repository rather than beside the vault —
    the case a hand-built ``<vault>/.git/rebase-merge`` gets wrong.
  - False, not an exception, for a directory git knows nothing about.

Both rebase backends put their state in their own directory (``rebase-merge``
for the merge backend, ``rebase-apply`` for the apply backend), so the probe
has to consider both; each test drives a real rebase rather than planting a
directory, so what is asserted is the state git itself writes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import _copy_git_tree


def _load_common():
    from lore.cli import common

    return common


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True
    )


def _build_repo_with_a_conflict_ahead(path: Path) -> Path:
    """A repo whose branch `side` cannot replay onto `main` without conflict."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@e.st")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("base\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "base")

    _git(path, "checkout", "-q", "-b", "side")
    (path / "f.txt").write_text("side\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "side edit")

    _git(path, "checkout", "-q", "main")
    (path / "f.txt").write_text("main\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "main edit")
    _git(path, "checkout", "-q", "side")
    return path


#: The repo above is deterministic and every test starts from it, so it is
#: built once and copied. Six subprocesses per test, over eight tests, is more
#: than this file's whole subject is worth. Nothing here embeds an absolute
#: path: git records its own paths relative to the repository, and this repo
#: has no remote — the one thing that would write an absolute URL into
#: `.git/config`.
_TEMPLATE: dict[str, Path] = {}


@pytest.fixture(scope="module")
def _template(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("mid-rebase-template")
    return _build_repo_with_a_conflict_ahead(root / "repo")


@pytest.fixture
def repo(_template, tmp_path) -> Path:
    """A fresh copy of the template, on `side`, ready to rebase onto `main`."""
    path = tmp_path / "v"
    _copy_git_tree(_template, path)
    return path


def _start_conflicted_rebase(path: Path) -> None:
    result = _git(path, "rebase", "main")
    assert result.returncode != 0, "the rebase was meant to stop on a conflict"


def test_repo_fixture_survives_a_lock_file_vanishing_mid_copy(tmp_path, monkeypatch):
    """`repo` copies `_template` with `_copy_git_tree`, which races git's own
    background maintenance: a `.git/objects/maintenance.lock` file can be
    present when `shutil.copytree` lists the source directory but gone by the
    time it opens it. Simulate the vanish directly and confirm the copy still
    produces a working repository rather than raising `shutil.Error`.
    """
    template = tmp_path / "template"
    _build_repo_with_a_conflict_ahead(template)
    (template / ".git" / "objects" / "maintenance.lock").write_text("")

    real_copy2 = shutil.copy2

    def vanishing_copy2(src, dst, *args, **kwargs):
        if os.fspath(src).endswith(".lock"):
            raise FileNotFoundError(src)
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", vanishing_copy2)

    dest = tmp_path / "clone"
    _copy_git_tree(template, dest)

    assert not (dest / ".git" / "objects" / "maintenance.lock").exists()
    assert _git(dest, "rev-parse", "HEAD").returncode == 0


def test_a_vault_with_no_rebase_running_is_not_mid_rebase(repo):
    common = _load_common()
    vault = repo

    assert common._vault_mid_rebase(vault) is False


def test_a_vault_stopped_in_a_rebase_is_mid_rebase(repo):
    common = _load_common()
    vault = repo

    _start_conflicted_rebase(vault)

    assert common._vault_mid_rebase(vault) is True


def test_the_answer_goes_back_to_false_once_the_rebase_is_aborted(repo):
    """Absence of the state directory is the only end-of-rebase signal."""
    common = _load_common()
    vault = repo
    _start_conflicted_rebase(vault)
    assert common._vault_mid_rebase(vault) is True

    _git(vault, "rebase", "--abort")

    assert common._vault_mid_rebase(vault) is False


def test_the_answer_goes_back_to_false_once_the_rebase_is_completed(repo):
    common = _load_common()
    vault = repo
    _start_conflicted_rebase(vault)

    (vault / "f.txt").write_text("resolved\n")
    _git(vault, "add", "-A")
    _git(vault, "-c", "core.editor=true", "rebase", "--continue")

    assert common._vault_mid_rebase(vault) is False


def test_a_linked_worktree_mid_rebase_is_mid_rebase(tmp_path):
    """`.git` is a file here and the state lives under the main repository, so
    a probe that looked beside the vault would answer False through the whole
    rebase."""
    common = _load_common()
    main = _build_repo_with_a_conflict_ahead(tmp_path / "main")
    _git(main, "checkout", "-q", "main")
    linked = tmp_path / "linked"
    added = _git(main, "worktree", "add", "-q", str(linked), "side")
    assert added.returncode == 0, added.stderr
    assert (linked / ".git").is_file()

    _start_conflicted_rebase(linked)

    assert common._vault_mid_rebase(linked) is True
    # The main checkout is a different worktree and is not itself rebasing.
    assert common._vault_mid_rebase(main) is False


def test_a_directory_that_is_not_a_repository_is_not_mid_rebase(tmp_path):
    common = _load_common()
    plain = tmp_path / "plain"
    plain.mkdir()

    assert common._vault_mid_rebase(plain) is False


@pytest.mark.parametrize("backend", ["merge", "apply"])
def test_both_rebase_backends_are_seen(repo, backend):
    """The two backends write different state directories; neither may be missed."""
    common = _load_common()
    vault = repo

    flag = "--merge" if backend == "merge" else "--apply"
    result = _git(vault, "rebase", flag, "main")
    assert result.returncode != 0, "the rebase was meant to stop on a conflict"

    assert common._vault_mid_rebase(vault) is True
