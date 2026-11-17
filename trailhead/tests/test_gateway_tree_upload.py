"""``tree_upload`` reads a tree a sandboxed session controls, as the operator.

Nothing it reads may lie outside the top level, and nothing git runs for it may
come from the tree's own configuration.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from trailhead.gateway import tree_upload

CI = "name: ci\njobs:\n  build:\n    runs-on: ubuntu-latest\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(CI)
    (root / "plain.txt").write_text("plain\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")
    return root


def index_entry(repo: Path, path: str, content: str) -> None:
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo, input=content, check=True, capture_output=True, text=True
    ).stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")


def tar_of(repo: Path) -> tarfile.TarFile:
    out = io.BytesIO()
    tree_upload.write_tar(tree_upload.top_level(repo), out)
    out.seek(0)
    return tarfile.open(fileobj=out)


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    folder = tmp_path / "operator-home"
    folder.mkdir()
    (folder / "key").write_text("OPERATOR-SECRET\n")
    return folder


@pytest.fixture
def repo(tmp_path: Path, hermetic_git_env) -> Path:
    return make_repo(tmp_path / "repo")


def contents(tar: tarfile.TarFile) -> str:
    return "".join(tar.extractfile(m).read().decode() for m in tar.getmembers() if m.isreg())


class TestSymlinkedFoldersAreNotFollowed:
    def test_an_entry_below_a_symlinked_folder_is_not_archived(self, repo, outside):
        (repo / "home").symlink_to(outside)
        index_entry(repo, "home/key", "x")
        tar = tar_of(repo)
        assert "OPERATOR-SECRET" not in contents(tar)
        assert "home/key" not in tar.getnames()

    def test_a_real_folder_below_the_top_level_is_still_archived(self, repo):
        (repo / "sub" / "deep").mkdir(parents=True)
        (repo / "sub" / "deep" / "f.txt").write_text("inside\n")
        tar = tar_of(repo)
        assert tar.extractfile("sub/deep/f.txt").read() == b"inside\n"

    def test_a_symlink_leaf_is_still_archived_as_a_link(self, repo, outside):
        (repo / "link").symlink_to(outside / "key")
        member = tar_of(repo).getmember("link")
        assert member.issym() and member.linkname == str(outside / "key")

    def test_an_executable_keeps_its_mode(self, repo):
        (repo / "run.sh").write_text("#!/bin/sh\n")
        (repo / "run.sh").chmod(0o755)
        tar = tar_of(repo)
        assert tar.getmember("run.sh").mode == 0o755
        assert tar.getmember("plain.txt").mode == 0o644


class TestWorkflowDiscoveryStaysInTheTree:
    def test_a_real_workflows_folder_is_found(self, repo):
        assert tree_upload.workflow_defining(tree_upload.top_level(repo), "build") == ".github/workflows/ci.yml"

    def test_a_symlinked_workflows_folder_is_not_read(self, tmp_path, hermetic_git_env, outside):
        repo = tmp_path / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / ".github").mkdir()
        (outside / "wf").mkdir()
        (outside / "wf" / "ci.yml").write_text(CI)
        (repo / ".github" / "workflows").symlink_to(outside / "wf")
        with pytest.raises(tree_upload.TreeError, match="no workflow"):
            tree_upload.workflow_defining(tree_upload.top_level(repo), "build")

    def test_a_symlinked_dot_github_is_not_read(self, tmp_path, hermetic_git_env, outside):
        repo = tmp_path / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        (outside / "gh" / "workflows").mkdir(parents=True)
        (outside / "gh" / "workflows" / "ci.yml").write_text(CI)
        (repo / ".github").symlink_to(outside / "gh")
        with pytest.raises(tree_upload.TreeError, match="no workflow"):
            tree_upload.workflow_defining(tree_upload.top_level(repo), "build")

    def test_a_symlinked_workflow_file_is_not_read(self, repo, outside):
        (repo / ".github" / "workflows" / "ci.yml").unlink()
        (outside / "ci.yml").write_text(CI)
        (repo / ".github" / "workflows" / "ci.yml").symlink_to(outside / "ci.yml")
        with pytest.raises(tree_upload.TreeError, match="no workflow"):
            tree_upload.workflow_defining(tree_upload.top_level(repo), "build")


class TestGitRunsHardened:
    def test_a_repo_configured_fsmonitor_is_not_run(self, repo, tmp_path):
        marker = tmp_path / "fsmonitor-ran"
        script = tmp_path / "fsmonitor.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\nprintf '\\0'\n")
        script.chmod(0o755)
        git(repo, "config", "core.fsmonitor", str(script))
        (repo / "new.txt").write_text("fresh\n")
        tar = tar_of(repo)
        assert tar.extractfile("new.txt").read() == b"fresh\n"
        assert not marker.exists()


class TestTheTopLevelContainsTheFolder:
    def test_a_worktree_redirected_elsewhere_is_refused(self, repo, tmp_path):
        elsewhere = tmp_path / "operator-home"
        elsewhere.mkdir()
        (elsewhere / "key").write_text("OPERATOR-SECRET\n")
        git(repo, "config", "core.worktree", str(elsewhere))
        with pytest.raises(tree_upload.TreeError, match="top level"):
            tree_upload.top_level(repo)

    def test_a_subfolder_of_the_top_level_resolves_to_it(self, repo):
        (repo / "sub").mkdir()
        assert tree_upload.top_level(repo / "sub") == repo.resolve()

    def test_a_folder_reached_through_a_symlink_resolves_to_the_top_level(self, repo, tmp_path):
        alias = tmp_path / "alias"
        alias.symlink_to(repo)
        assert tree_upload.top_level(alias).resolve() == repo.resolve()
