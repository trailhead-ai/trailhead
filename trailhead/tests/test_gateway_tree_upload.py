"""``tree_upload`` reads a tree a sandboxed session controls, as the operator.

Nothing it reads may lie outside the top level, and nothing git runs for it may
come from the tree's own configuration.
"""

from __future__ import annotations

import hashlib
import io
import os
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


@pytest.fixture(autouse=True)
def operator_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The uploader drops every ``GIT_*`` variable, so the hermetic env's global
    config does not reach its git; an empty home keeps the machine's out instead."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))


def forge_index_entry(repo: Path, path: str) -> None:
    """Make the index list *path* alone, a name ``git update-index`` itself refuses."""
    git(repo, "rm", "-r", "-q", "--cached", ".")
    placeholder = "z" * len(path)
    index_entry(repo, placeholder, "forged\n")
    index = repo / ".git" / "index"
    body = index.read_bytes()[:-20]
    body = body.replace(placeholder.encode(), path.encode(), 1)
    index.write_bytes(body + hashlib.sha1(body).digest())
    assert git(repo, "ls-files", "--cached") == path


class Swap:
    """Turns the folder at *at* into a symlink to *target* and back."""

    def __init__(self, at: Path, target: Path):
        self.at, self.target, self.aside = at, target, at.with_name(at.name + ".real")

    def to_link(self) -> None:
        self.at.rename(self.aside)
        self.at.symlink_to(self.target)

    def to_real(self) -> None:
        self.at.unlink()
        self.aside.rename(self.at)


class GitHooks:
    """A git runner for :func:`tree_upload.top_level` that runs a callback just
    before or just after a given git subcommand."""

    def __init__(self):
        self.hooks: dict[tuple[str, str], object] = {}

    def __call__(self, when: str, sub: str, hook) -> None:
        self.hooks[(when, sub)] = hook

    def run(self, args, *a, **kw):
        sub = next((x for x in args if x in ("rev-parse", "ls-files")), None)
        if (hook := self.hooks.get(("before", sub))) is not None:
            hook()
        done = subprocess.run(args, *a, **kw)
        if (hook := self.hooks.get(("after", sub))) is not None:
            hook()
        return done


@pytest.fixture
def on_git() -> GitHooks:
    return GitHooks()


@pytest.fixture
def nested(tmp_path: Path, hermetic_git_env):
    """A repo at ``ws/m/x/repo`` and, outside it, a repo at the same relative path
    under ``operator-state`` whose ``plain.txt`` is the operator's secret."""
    repo = make_repo(tmp_path / "ws" / "m" / "x" / "repo")
    decoy = make_repo(tmp_path / "operator-state" / "x" / "repo")
    (decoy / "plain.txt").write_text("OPERATOR-SECRET\n")
    return repo, tmp_path / "operator-state"


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
        assert tree_upload.top_level(repo / "sub").path == repo.resolve()

    def test_a_folder_reached_through_a_symlink_resolves_to_the_top_level(self, repo, tmp_path):
        alias = tmp_path / "alias"
        alias.symlink_to(repo)
        assert tree_upload.top_level(alias).path == repo.resolve()


class TestTheTopLevelIsResolvedOnce:
    def test_an_ancestor_swapped_after_the_top_level_is_opened_is_not_read_through(self, nested, on_git):
        repo, operator_state = nested
        swap = Swap(repo.parent.parent, operator_state)
        top = tree_upload.top_level(repo, runner=on_git.run)
        swap.to_link()
        on_git("before", "ls-files", swap.to_real)
        on_git("after", "ls-files", swap.to_link)
        out = io.BytesIO()
        tree_upload.write_tar(top, out)
        out.seek(0)
        assert tarfile.open(fileobj=out).extractfile("plain.txt").read() == b"plain\n"

    def test_git_lists_the_folder_that_was_opened_not_whatever_its_path_now_names(self, nested):
        repo, operator_state = nested
        (repo / "real-only.txt").write_text("real\n")
        top = tree_upload.top_level(repo)
        Swap(repo.parent.parent, operator_state).to_link()
        out = io.BytesIO()
        tree_upload.write_tar(top, out)
        out.seek(0)
        assert set(tarfile.open(fileobj=out).getnames()) == {".github/workflows/ci.yml", "plain.txt", "real-only.txt"}

    def test_containment_is_judged_on_the_folders_opened_not_on_their_paths(self, nested, on_git, tmp_path):
        repo, _ = nested
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "x" / "repo").mkdir(parents=True)
        git(repo, "config", "core.worktree", str(elsewhere))
        on_git("after", "rev-parse", Swap(repo.parent.parent, elsewhere).to_link)
        with pytest.raises(tree_upload.TreeError, match="top level"):
            tree_upload.top_level(repo, runner=on_git.run)

    def test_an_ancestor_that_is_a_symlink_when_the_top_level_is_opened_is_refused(self, nested, on_git):
        repo, operator_state = nested
        on_git("after", "rev-parse", Swap(repo.parent.parent, operator_state).to_link)
        out = io.BytesIO()
        with pytest.raises(tree_upload.TreeError, match="symlink"):
            tree_upload.write_tar(tree_upload.top_level(repo, runner=on_git.run), out)
        assert b"OPERATOR-SECRET" not in out.getvalue()

    def test_a_symlinked_ancestor_is_refused_even_when_it_leads_back_to_the_same_folder(self, nested, on_git):
        repo, _ = nested
        ancestor = repo.parent.parent
        on_git("after", "rev-parse", Swap(ancestor, ancestor.with_name(ancestor.name + ".real")).to_link)
        with pytest.raises(tree_upload.TreeError, match="symlink"):
            tree_upload.top_level(repo, runner=on_git.run)

    def test_workflow_discovery_reads_the_folder_that_was_opened(self, nested):
        repo, operator_state = nested
        decoy_workflows = operator_state / "x" / "repo" / ".github" / "workflows"
        (decoy_workflows / "ci.yml").rename(decoy_workflows / "decoy.yml")
        top = tree_upload.top_level(repo)
        Swap(repo.parent.parent, operator_state).to_link()
        assert tree_upload.workflow_defining(top, "build") == ".github/workflows/ci.yml"

    def test_the_top_level_itself_swapped_for_a_symlink_is_a_one_line_refusal(self, nested, on_git):
        repo, operator_state = nested
        on_git("after", "rev-parse", Swap(repo, operator_state / "x" / "repo").to_link)
        with pytest.raises(tree_upload.TreeError, match="symlink"):
            tree_upload.write_tar(tree_upload.top_level(repo, runner=on_git.run), io.BytesIO())


class TestListedPathsStayInTheTree:
    @pytest.mark.parametrize(
        "path", ["../operator-home/key", "/operator-home/key", "sub//key", "./plain.txt", "sub/../plain.txt"]
    )
    def test_a_listed_path_that_is_not_a_plain_relative_path_refuses_the_upload(self, repo, outside, path):
        forge_index_entry(repo, path)
        out = io.BytesIO()
        with pytest.raises(tree_upload.TreeError, match="not a plain path"):
            tree_upload.write_tar(tree_upload.top_level(repo), out)
        assert b"OPERATOR-SECRET" not in out.getvalue()


class TestGitIsTheOperatorsOwn:
    def test_git_dir_in_the_environment_does_not_redirect_the_listing(self, repo, tmp_path, monkeypatch):
        (repo / ".gitignore").write_text("ignored.txt\n")
        (repo / "ignored.txt").write_text("ignored\n")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-q", "-m", "ignore")
        other = tmp_path / "other"
        other.mkdir()
        git(other, "init", "-q")
        index_entry(other, "ignored.txt", "x")
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        names = set(tar_of(repo).getnames())
        assert names == {".gitignore", ".github/workflows/ci.yml", "plain.txt"}

    def test_a_git_earlier_on_path_is_not_the_one_run(self, repo, tmp_path, monkeypatch):
        fake = tmp_path / "fakebin"
        fake.mkdir()
        (fake / "git").write_text("#!/bin/sh\nexit 1\n")
        (fake / "git").chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
        assert set(tar_of(repo).getnames()) == {".github/workflows/ci.yml", "plain.txt"}


class TestWorkflowFolderErrors:
    def test_a_workflows_folder_that_cannot_be_listed_is_a_one_line_refusal(self, repo, monkeypatch):
        tree = tree_upload.top_level(repo)

        def denied(fd):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(tree_upload.os, "listdir", denied)
        with pytest.raises(tree_upload.TreeError, match="cannot be listed"):
            tree_upload.workflow_defining(tree, "build")


class TestWorkflowSizeCap:
    def _workflow_of_size(self, repo: Path, size: int) -> None:
        pad = "#" * (size - len(CI) - 1) + "\n"
        (repo / ".github" / "workflows" / "ci.yml").write_text(CI + pad)
        assert (repo / ".github" / "workflows" / "ci.yml").stat().st_size == size

    def test_a_workflow_at_the_cap_is_read(self, repo):
        self._workflow_of_size(repo, 256 * 1024)
        assert tree_upload.workflow_defining(tree_upload.top_level(repo), "build") == ".github/workflows/ci.yml"

    def test_a_workflow_over_the_cap_is_not_read(self, repo):
        self._workflow_of_size(repo, 256 * 1024 + 1)
        with pytest.raises(tree_upload.TreeError, match="no workflow"):
            tree_upload.workflow_defining(tree_upload.top_level(repo), "build")
