"""Contract of the shared git-vault builders in conftest.

The sync, resolve, pull and lock suites each need a vault that is a real git
repository, and between them they build one several dozen times per run. These
are the properties those call sites are entitled to assume, and the ones that
must survive the builders handing out copies of a template rather than running
`git init` every time.

Each test varies the input that decides the property: whether the vault carries
a commit, whether its tree is dirty, and which identity authored it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import make_bare_remote, make_git_vault


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def test_a_committed_vault_has_one_commit_on_a_clean_tree(tmp_path):
    vault = make_git_vault(tmp_path / "v")

    assert _git(vault, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert _git(vault, "status", "--porcelain").stdout == ""


def test_an_uncommitted_vault_has_no_commits_at_all(tmp_path):
    """The never-committed vault — the state a real vault was found in."""
    vault = make_git_vault(tmp_path / "v", commit=False)

    assert _git(vault, "rev-parse", "HEAD").returncode != 0
    assert _git(vault, "status", "--porcelain").stdout != ""


def test_dirt_is_what_makes_the_tree_dirty(tmp_path):
    clean = make_git_vault(tmp_path / "clean", dirty=False)
    dirty = make_git_vault(tmp_path / "dirty", dirty=True)

    assert _git(clean, "status", "--porcelain").stdout == ""
    assert _git(dirty, "status", "--porcelain").stdout != ""


def test_the_commit_carries_the_identity_the_caller_asked_for(tmp_path):
    """Two identities exist because some tests stand two devices apart."""
    one = make_git_vault(tmp_path / "one", identity=("a@e.st", "DeviceA"))
    two = make_git_vault(tmp_path / "two", identity=("t@e.st", "Test"))

    assert _git(one, "log", "-1", "--format=%an <%ae>").stdout.strip() == "DeviceA <a@e.st>"
    assert _git(two, "log", "-1", "--format=%an <%ae>").stdout.strip() == "Test <t@e.st>"


def test_a_vault_gitignores_the_lock_sidecars_a_real_one_has(tmp_path):
    """`config.installer` scaffolds this into every real vault; lore's write
    locks are `*.lock` sidecars inside the vault, so a fixture without it would
    be testing a vault shape no install ever has."""
    vault = make_git_vault(tmp_path / "v")

    assert "*.lock" in (vault / ".gitignore").read_text()


def test_committing_is_configured_not_to_reach_for_a_signing_key(tmp_path):
    """A developer with commit.gpgsign on globally must not stall the suite."""
    vault = make_git_vault(tmp_path / "v")

    (vault / "next.md").write_text("more\n")
    _git(vault, "add", "-A")
    assert _git(vault, "commit", "-m", "second").returncode == 0
    assert _git(vault, "rev-list", "--count", "HEAD").stdout.strip() == "2"


def test_two_vaults_are_independent_repositories(tmp_path):
    """Call sites routinely build two and then diverge them."""
    a = make_git_vault(tmp_path / "a")
    b = make_git_vault(tmp_path / "b")

    (a / "only-a.md").write_text("a\n")
    _git(a, "add", "-A")
    _git(a, "commit", "-m", "second")

    assert _git(a, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    assert _git(b, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert not (b / "only-a.md").exists()


def test_a_bare_remote_is_bare_and_a_vault_can_push_to_it(tmp_path):
    vault = make_git_vault(tmp_path / "v")
    remote = make_bare_remote(tmp_path / "r.git")

    assert _git(remote, "rev-parse", "--is-bare-repository").stdout.strip() == "true"

    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(vault, "remote", "add", "origin", str(remote))
    assert _git(vault, "push", "-u", "origin", branch).returncode == 0
    assert _git(remote, "rev-list", "--count", branch).stdout.strip() == "1"


def test_a_second_bare_remote_does_not_share_the_first_ones_refs(tmp_path):
    vault = make_git_vault(tmp_path / "v")
    first = make_bare_remote(tmp_path / "one.git")
    second = make_bare_remote(tmp_path / "two.git")

    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(vault, "remote", "add", "origin", str(first))
    _git(vault, "push", "-u", "origin", branch)

    assert _git(first, "rev-list", "--count", branch).stdout.strip() == "1"
    assert _git(second, "rev-parse", branch).returncode != 0
