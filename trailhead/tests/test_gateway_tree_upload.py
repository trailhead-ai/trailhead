"""``tree_upload`` reads a tree a sandboxed session controls, as the operator.

Nothing it reads may lie outside the top level, and nothing the session wrote into
its repository's git directory or configuration may steer the git it runs.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import pwd
import subprocess
import sys
import tarfile
import tracemalloc
from pathlib import Path

import pytest

from trailhead.gateway import tree_upload

CI = "name: ci\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
EXPECTED = {".github/workflows/ci.yml", "plain.txt"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(root: Path, *init: str) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", *init)
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


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """The operator home the uploader's private scratch folder is made under."""
    folder = tmp_path / "scratch-home"
    folder.mkdir()
    return folder


def opened(repo: Path, home: Path, **kw) -> tree_upload.Tree:
    return tree_upload.top_level(repo, home=home, **kw)


def tar_of(repo: Path, home: Path, **kw) -> tarfile.TarFile:
    out = io.BytesIO()
    with opened(repo, home) as tree:
        tree_upload.write_tar(tree, out, **kw)
    out.seek(0)
    return tarfile.open(fileobj=out)


class Counting:
    """A sink that keeps only the number of bytes written to it."""

    def __init__(self):
        self.written = 0

    def write(self, data) -> int:
        self.written += len(data)
        return len(data)

    def tell(self) -> int:
        return self.written


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
    """An empty HOME, so nothing of the machine's own git setup reaches a fixture's git."""
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
        sub = next((x for x in args if x == "ls-files"), None)
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


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


class TestSymlinkedFoldersAreNotFollowed:
    def test_an_entry_below_a_symlinked_folder_is_not_archived(self, repo, outside, home):
        (repo / "home").symlink_to(outside)
        index_entry(repo, "home/key", "x")
        tar = tar_of(repo, home)
        assert "OPERATOR-SECRET" not in contents(tar)
        assert "home/key" not in tar.getnames()

    def test_a_real_folder_below_the_top_level_is_still_archived(self, repo, home):
        (repo / "sub" / "deep").mkdir(parents=True)
        (repo / "sub" / "deep" / "f.txt").write_text("inside\n")
        tar = tar_of(repo, home)
        assert tar.extractfile("sub/deep/f.txt").read() == b"inside\n"

    def test_a_symlink_leaf_is_still_archived_as_a_link(self, repo, outside, home):
        (repo / "link").symlink_to(outside / "key")
        member = tar_of(repo, home).getmember("link")
        assert member.issym() and member.linkname == str(outside / "key")

    def test_an_executable_keeps_its_mode(self, repo, home):
        (repo / "run.sh").write_text("#!/bin/sh\n")
        (repo / "run.sh").chmod(0o755)
        tar = tar_of(repo, home)
        assert tar.getmember("run.sh").mode == 0o755
        assert tar.getmember("plain.txt").mode == 0o644


class TestWorkflowDiscoveryStaysInTheTree:
    def test_a_real_workflows_folder_is_found(self, repo, home):
        assert tree_upload.workflow_defining(opened(repo, home), "build") == ".github/workflows/ci.yml"

    def test_a_symlinked_workflows_folder_is_not_read(self, tmp_path, hermetic_git_env, outside, home):
        repo = tmp_path / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / ".github").mkdir()
        (outside / "wf").mkdir()
        (outside / "wf" / "ci.yml").write_text(CI)
        (repo / ".github" / "workflows").symlink_to(outside / "wf")
        with pytest.raises(tree_upload.TreeError, match="no workflow"):
            tree_upload.workflow_defining(opened(repo, home), "build")

    def test_a_symlinked_dot_github_is_not_read(self, tmp_path, hermetic_git_env, outside, home):
        repo = tmp_path / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        (outside / "gh" / "workflows").mkdir(parents=True)
        (outside / "gh" / "workflows" / "ci.yml").write_text(CI)
        (repo / ".github").symlink_to(outside / "gh")
        with pytest.raises(tree_upload.TreeError, match="no workflow"):
            tree_upload.workflow_defining(opened(repo, home), "build")

    def test_a_symlinked_workflow_file_is_not_read(self, repo, outside, home):
        (repo / ".github" / "workflows" / "ci.yml").unlink()
        (outside / "ci.yml").write_text(CI)
        (repo / ".github" / "workflows" / "ci.yml").symlink_to(outside / "ci.yml")
        with pytest.raises(tree_upload.TreeError, match="no workflow"):
            tree_upload.workflow_defining(opened(repo, home), "build")


class TestTheRepositoryConfigIsNotRead:
    def test_a_repo_configured_fsmonitor_is_not_run(self, repo, tmp_path, home):
        marker = tmp_path / "fsmonitor-ran"
        monitor = script(tmp_path / "fsmonitor.sh", f"touch {marker}\nprintf '\\0'\n")
        git(repo, "config", "core.fsmonitor", str(monitor))
        (repo / "new.txt").write_text("fresh\n")
        tar = tar_of(repo, home)
        assert tar.extractfile("new.txt").read() == b"fresh\n"
        assert not marker.exists()

    def test_a_lazy_fetch_the_repo_config_sets_up_does_not_run_its_command(self, repo, tmp_path, home):
        marker = tmp_path / "upload-pack-ran"
        origin = make_repo(tmp_path / "origin")
        (repo / ".gitignore").write_text("ignored.txt\n")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-q", "-m", "ignore")
        blob = git(repo, "rev-parse", "HEAD:.gitignore")
        git(repo, "update-index", "--skip-worktree", ".gitignore")
        (repo / ".gitignore").unlink()
        (repo / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
        for key, value in (
            ("core.repositoryformatversion", "1"),
            ("extensions.partialClone", "origin"),
            ("remote.origin.promisor", "true"),
            ("remote.origin.url", f"file://{origin}"),
            ("remote.origin.uploadpack", f"sh -c 'touch {marker}; exit 1'"),
        ):
            git(repo, "config", key, value)
        (repo / "untracked.txt").write_text("u\n")
        with contextlib.suppress(tree_upload.TreeError):
            tar_of(repo, home)
        assert not marker.exists()

    def test_an_include_of_an_existing_file_and_of_a_missing_one_upload_alike(self, repo, outside, home):
        (outside / "notes").write_text("operator private notes\n")
        config = repo / ".git" / "config"
        base = config.read_text()
        outcomes = []
        for target in (outside / "notes", outside / "no-such-file"):
            config.write_text(f"{base}[include]\n\tpath = {target}\n")
            try:
                outcomes.append(sorted(tar_of(repo, home).getnames()))
            except tree_upload.TreeError as err:
                outcomes.append(str(err))
        assert outcomes == [sorted(EXPECTED), sorted(EXPECTED)]

    def test_a_core_excludes_file_in_the_repo_config_has_no_effect(self, repo, tmp_path, home):
        excludes = tmp_path / "excludes"
        excludes.write_text("local.txt\n")
        git(repo, "config", "core.excludesFile", str(excludes))
        (repo / "local.txt").write_text("mine\n")
        assert tar_of(repo, home).extractfile("local.txt").read() == b"mine\n"

    def test_core_worktree_in_the_repo_config_has_no_effect(self, repo, outside, home):
        git(repo, "config", "core.worktree", str(outside))
        (repo / "repo-only.txt").write_text("here\n")
        tar = tar_of(repo, home)
        assert set(tar.getnames()) == EXPECTED | {"repo-only.txt"}
        assert "OPERATOR-SECRET" not in contents(tar)


class TestTheIndexIsReadAsData:
    def test_a_tracked_file_the_ignore_rules_match_is_still_uploaded(self, repo, home):
        (repo / ".gitignore").write_text("*.log\n")
        (repo / "kept.log").write_text("tracked\n")
        git(repo, "add", "-f", ".gitignore", "kept.log")
        (repo / "loose.log").write_text("untracked\n")
        assert set(tar_of(repo, home).getnames()) == EXPECTED | {".gitignore", "kept.log"}

    def test_an_index_at_version_4_lists_the_same_files(self, repo, home):
        for rel in ("src/app/one.py", "src/app/two.py", "src/lib/three.py"):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(rel)
        (repo / ".gitignore").write_text("src/\n")
        git(repo, "add", "-f", ".")
        git(repo, "update-index", "--index-version", "4")
        names = set(tar_of(repo, home).getnames())
        assert names == EXPECTED | {".gitignore", "src/app/one.py", "src/app/two.py", "src/lib/three.py"}

    def test_an_intent_to_add_entry_is_read(self, repo, home):
        (repo / ".gitignore").write_text("planned.txt\n")
        (repo / "planned.txt").write_text("soon\n")
        git(repo, "add", ".gitignore")
        git(repo, "add", "-f", "-N", "planned.txt")
        assert "planned.txt" in tar_of(repo, home).getnames()

    def test_a_split_index_is_refused(self, repo, home):
        git(repo, "update-index", "--split-index")
        with pytest.raises(tree_upload.TreeError, match="split index"):
            tar_of(repo, home)

    def test_a_sparse_index_is_refused(self, repo, home):
        (repo / "sub").mkdir()
        (repo / "sub" / "f.txt").write_text("f\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "sub")
        git(repo, "sparse-checkout", "set", "--cone", "--sparse-index", ".github")
        with pytest.raises(tree_upload.TreeError, match="sparse index"):
            tar_of(repo, home)

    def test_a_skip_worktree_gitignore_whose_object_is_missing_is_refused(self, repo, home):
        (repo / ".gitignore").write_text("secret.txt\n")
        git(repo, "add", ".gitignore")
        git(repo, "update-index", "--skip-worktree", ".gitignore")
        (repo / ".gitignore").unlink()
        with pytest.raises(tree_upload.TreeError, match=r"\.gitignore"):
            tar_of(repo, home)

    def test_a_sha256_repository_is_refused(self, tmp_path, hermetic_git_env, home):
        repo = make_repo(tmp_path / "sha256", "--object-format=sha256")
        with pytest.raises(tree_upload.TreeError, match="index is not one this command can read"):
            tar_of(repo, home)

    def test_a_repository_with_no_index_yet_uploads_its_files(self, tmp_path, hermetic_git_env, home):
        repo = tmp_path / "fresh"
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / "first.txt").write_text("1\n")
        assert set(tar_of(repo, home).getnames()) == {"first.txt"}


class TestTheGitDirIsFoundInTheTree:
    def test_a_linked_worktree_with_a_dot_git_file_uploads_its_own_files(self, repo, tmp_path, home):
        worktree = tmp_path / "linked"
        git(repo, "worktree", "add", "-q", str(worktree))
        (worktree / "plain.txt").write_text("edited in the worktree\n")
        (worktree / "new.txt").write_text("new\n")
        (repo / "main-only.txt").write_text("main\n")
        tar = tar_of(worktree, home)
        assert set(tar.getnames()) == EXPECTED | {"new.txt"}
        assert tar.extractfile("plain.txt").read() == b"edited in the worktree\n"

    def test_a_dot_git_file_naming_another_repositorys_git_dir_is_refused(self, repo, tmp_path, home):
        other = tmp_path / "other"
        other.mkdir()
        (other / ".git").write_text(f"gitdir: {repo / '.git'}\n")
        (other / "f.txt").write_text("f\n")
        with pytest.raises(tree_upload.TreeError, match="worktree"):
            opened(other, home)

    def test_a_subfolder_uploads_from_the_top_level(self, repo, home):
        (repo / "sub").mkdir()
        (repo / "sub" / "inner.txt").write_text("i\n")
        assert set(tar_of(repo / "sub", home).getnames()) == EXPECTED | {"sub/inner.txt"}

    def test_a_folder_reached_through_a_symlink_uploads_its_top_level(self, repo, tmp_path, home):
        alias = tmp_path / "alias"
        alias.symlink_to(repo)
        assert set(tar_of(alias, home).getnames()) == EXPECTED

    def test_a_folder_in_no_repository_is_refused(self, tmp_path, hermetic_git_env, home):
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(tree_upload.TreeError, match="not inside a git work tree"):
            opened(plain, home)


class TestTheTopLevelIsResolvedOnce:
    def test_an_ancestor_swapped_after_the_top_level_is_opened_is_not_read_through(self, nested, on_git, home):
        repo, operator_state = nested
        swap = Swap(repo.parent.parent, operator_state)
        top = opened(repo, home, runner=on_git.run)
        swap.to_link()
        on_git("before", "ls-files", swap.to_real)
        on_git("after", "ls-files", swap.to_link)
        out = io.BytesIO()
        tree_upload.write_tar(top, out)
        out.seek(0)
        assert tarfile.open(fileobj=out).extractfile("plain.txt").read() == b"plain\n"

    def test_git_lists_the_folder_that_was_opened_not_whatever_its_path_now_names(self, nested, home):
        repo, operator_state = nested
        (repo / "real-only.txt").write_text("real\n")
        top = opened(repo, home)
        Swap(repo.parent.parent, operator_state).to_link()
        out = io.BytesIO()
        tree_upload.write_tar(top, out)
        out.seek(0)
        assert set(tarfile.open(fileobj=out).getnames()) == EXPECTED | {"real-only.txt"}

    def test_workflow_discovery_reads_the_folder_that_was_opened(self, nested, home):
        repo, operator_state = nested
        decoy_workflows = operator_state / "x" / "repo" / ".github" / "workflows"
        (decoy_workflows / "ci.yml").rename(decoy_workflows / "decoy.yml")
        top = opened(repo, home)
        Swap(repo.parent.parent, operator_state).to_link()
        assert tree_upload.workflow_defining(top, "build") == ".github/workflows/ci.yml"


class TestListedPathsStayInTheTree:
    @pytest.mark.parametrize(
        "path", ["../operator-home/key", "/operator-home/key", "sub//key", "./plain.txt", "sub/../plain.txt"]
    )
    def test_a_listed_path_that_is_not_a_plain_relative_path_refuses_the_upload(self, repo, outside, home, path):
        forge_index_entry(repo, path)
        out = io.BytesIO()
        with pytest.raises(tree_upload.TreeError, match="not a plain path"):
            tree_upload.write_tar(opened(repo, home), out)
        assert b"OPERATOR-SECRET" not in out.getvalue()


class TestGitIsTheOperatorsOwn:
    def test_git_dir_in_the_environment_does_not_redirect_the_listing(self, repo, tmp_path, monkeypatch, home):
        (repo / ".gitignore").write_text("ignored.txt\n")
        (repo / "ignored.txt").write_text("ignored\n")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-q", "-m", "ignore")
        other = tmp_path / "other"
        other.mkdir()
        git(other, "init", "-q")
        index_entry(other, "ignored.txt", "x")
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        assert set(tar_of(repo, home).getnames()) == EXPECTED | {".gitignore"}

    def test_a_git_earlier_on_path_is_not_the_one_run(self, repo, tmp_path, monkeypatch, home):
        fake = tmp_path / "fakebin"
        fake.mkdir()
        script(fake / "git", "exit 1\n")
        monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
        assert set(tar_of(repo, home).getnames()) == EXPECTED

    def test_with_no_git_at_a_fixed_path_the_upload_is_refused_even_with_one_on_path(self, repo, tmp_path, home):
        top = opened(repo, home, git_programs=(str(tmp_path / "no-git-here"),))
        with pytest.raises(tree_upload.TreeError, match="git is not installed"):
            tree_upload.write_tar(top, io.BytesIO())

    def test_a_git_that_outlives_its_timeout_is_refused(self, repo, tmp_path, home):
        slow = script(tmp_path / "slow-git", "exec sleep 30\n")
        top = opened(repo, home, git_programs=(str(slow),), timeout=0.5)
        with pytest.raises(tree_upload.TreeError, match="seconds"):
            tree_upload.write_tar(top, io.BytesIO())

    def test_a_git_listing_over_its_cap_is_stopped_while_it_writes(self, repo, tmp_path, home):
        finished = tmp_path / "wrote-it-all"
        loud = script(tmp_path / "loud-git", f"/usr/bin/head -c 2000000 /dev/zero && touch {finished}\n")
        top = opened(repo, home, git_programs=(str(loud),), max_listing=1_000_000)
        with pytest.raises(tree_upload.TreeError, match="git"):
            tree_upload.write_tar(top, io.BytesIO())
        assert not finished.exists()


class TestTheScratchFolderIsTheOperatorsOwn:
    def test_a_symlink_on_the_way_to_the_scratch_folder_is_refused(self, repo, tmp_path, home):
        writable = tmp_path / "session-writable"
        writable.mkdir()
        (home / ".local").symlink_to(writable)
        with pytest.raises(tree_upload.TreeError, match="symlink"):
            tree_upload.write_tar(opened(repo, home), io.BytesIO())

    def test_a_scratch_folder_others_can_open_is_refused(self, repo, home):
        uploads = home / ".local" / "state" / "trailhead" / "gateway" / "uploads"
        uploads.mkdir(parents=True)
        uploads.chmod(0o755)
        with pytest.raises(tree_upload.TreeError, match="private"):
            tree_upload.write_tar(opened(repo, home), io.BytesIO())

    def test_the_scratch_folder_is_made_under_the_home_it_is_given(self, repo, home):
        tar_of(repo, home)
        uploads = home / ".local" / "state" / "trailhead" / "gateway" / "uploads"
        assert uploads.stat().st_mode & 0o777 == 0o700

    def test_the_default_home_is_the_password_databases_not_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "session-chosen"))
        assert tree_upload.operator_home() == Path(pwd.getpwuid(os.getuid()).pw_dir)


class TestUploadCeilings:
    def test_a_sparse_file_over_the_byte_ceiling_is_refused_before_it_is_read(self, repo, home):
        with open(repo / "huge.bin", "wb") as fh:
            fh.truncate(512 * 1024 * 1024 + 1)
        sink = Counting()
        with pytest.raises(tree_upload.TreeError, match="bytes"):
            with opened(repo, home) as tree:
                tree_upload.write_tar(tree, sink)
        assert sink.written < 1024 * 1024

    @pytest.mark.parametrize(("extra", "refused"), [(0, False), (1, True)])
    def test_the_byte_ceiling_allows_exactly_its_size(self, repo, home, extra, refused):
        content = len(CI) + len("plain\n")
        (repo / "plain.txt").write_text("plain\n" + "x" * extra)
        with contextlib.ExitStack() as stack:
            if refused:
                stack.enter_context(pytest.raises(tree_upload.TreeError, match="bytes"))
            tar = tar_of(repo, home, max_bytes=content)
            assert set(tar.getnames()) == EXPECTED

    @pytest.mark.parametrize(("files", "refused"), [(1, False), (2, True)])
    def test_the_entry_ceiling_allows_exactly_its_count(self, repo, home, files, refused):
        for n in range(files):
            (repo / f"extra-{n}.txt").write_text("e\n")
        with contextlib.ExitStack() as stack:
            if refused:
                stack.enter_context(pytest.raises(tree_upload.TreeError, match="entries"))
            assert len(tar_of(repo, home, max_entries=3).getnames()) == 3

    def test_a_hard_linked_file_is_refused_and_named(self, repo, outside, home):
        os.link(outside / "key", repo / "alias.txt")
        sink = Counting()
        with pytest.raises(tree_upload.TreeError, match="alias.txt"):
            with opened(repo, home) as tree:
                tree_upload.write_tar(tree, sink)


class TestWorkflowFolderErrors:
    def test_a_workflows_folder_that_cannot_be_listed_is_a_one_line_refusal(self, repo, monkeypatch, home):
        tree = opened(repo, home)

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

    def test_a_workflow_at_the_cap_is_read(self, repo, home):
        self._workflow_of_size(repo, 256 * 1024)
        assert tree_upload.workflow_defining(opened(repo, home), "build") == ".github/workflows/ci.yml"

    def test_a_workflow_over_the_cap_is_not_read(self, repo, home):
        self._workflow_of_size(repo, 256 * 1024 + 1)
        with pytest.raises(tree_upload.TreeError, match="no workflow"):
            tree_upload.workflow_defining(opened(repo, home), "build")


def open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


class TestGitsMemoryIsBounded:
    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is enforced on Linux only")
    @pytest.mark.parametrize(("ignore_mib", "refused"), [(0, False), (4, True)])
    def test_a_git_that_would_outgrow_its_memory_ceiling_is_refused(self, repo, home, ignore_mib, refused):
        (repo / ".gitignore").write_text("a\n" * (ignore_mib * 1024 * 1024 // 2))
        top = opened(repo, home, max_memory=64 * 1024 * 1024, max_gitignore=64 * 1024 * 1024)
        with contextlib.ExitStack() as stack:
            if refused:
                stack.enter_context(pytest.raises(tree_upload.TreeError, match="^git could not list"))
            out = io.BytesIO()
            tree_upload.write_tar(top, out)
            out.seek(0)
            assert set(tarfile.open(fileobj=out).getnames()) == EXPECTED | {".gitignore"}

    @pytest.mark.parametrize(("extra", "refused"), [(0, False), (1, True)])
    def test_ignore_files_over_their_summed_cap_refuse_before_git_runs(self, repo, home, extra, refused):
        (repo / ".gitignore").write_text("a\n" * 100)
        (repo / "sub").mkdir()
        (repo / "sub" / ".gitignore").write_text("b\n" * 50 + "c" * extra)
        top = opened(repo, home, max_gitignore=300)
        with contextlib.ExitStack() as stack:
            if refused:
                stack.enter_context(pytest.raises(tree_upload.TreeError, match="gitignore"))
            out = io.BytesIO()
            tree_upload.write_tar(top, out)
            out.seek(0)
            assert set(tarfile.open(fileobj=out).getnames()) == EXPECTED | {".gitignore", "sub/.gitignore"}

    def test_an_ignore_file_below_a_symlinked_folder_is_not_counted(self, repo, outside, home):
        (outside / "big").mkdir()
        (outside / "big" / ".gitignore").write_text("x" * 1000)
        (repo / "linked").symlink_to(outside / "big")
        out = io.BytesIO()
        tree_upload.write_tar(opened(repo, home, max_gitignore=500), out)
        out.seek(0)
        assert "linked" in tarfile.open(fileobj=out).getnames()

    def test_an_ignore_file_inside_the_git_folder_is_not_counted(self, repo, home):
        (repo / ".git" / ".gitignore").write_text("x" * 1000)
        out = io.BytesIO()
        tree_upload.write_tar(opened(repo, home, max_gitignore=500), out)
        out.seek(0)
        assert set(tarfile.open(fileobj=out).getnames()) == EXPECTED

    def test_folders_nested_deeper_than_the_walk_allows_are_refused(self, repo, home):
        (repo / Path(*["d"] * 600)).mkdir(parents=True)
        with pytest.raises(tree_upload.TreeError, match="nest deeper"):
            tar_of(repo, home)


class TestIndexPathsAreBounded:
    def _index_with_path_of(self, repo: Path, length: int, version: str) -> None:
        folders = "d/" * ((length - 1) // 2)
        path = folders + "x" * (length - len(folders))
        assert len(path) == length
        index_entry(repo, path, "deep\n")
        git(repo, "update-index", "--index-version", version)

    @pytest.mark.parametrize("version", ["2", "4"])
    @pytest.mark.parametrize(("length", "refused"), [(4096, False), (4097, True)])
    def test_an_index_path_over_4096_bytes_is_refused(self, repo, home, length, refused, version):
        self._index_with_path_of(repo, length, version)
        with contextlib.ExitStack() as stack:
            if refused:
                stack.enter_context(pytest.raises(tree_upload.TreeError, match="longer than 4096"))
            assert set(tar_of(repo, home).getnames()) == EXPECTED


class TestAGitdirLineWithANulByte:
    def test_a_nul_in_the_dot_git_file_is_a_one_line_refusal(self, tmp_path, home):
        folder = tmp_path / "w"
        folder.mkdir()
        (folder / ".git").write_bytes(b"gitdir: /tmp\0/x\n")
        before = open_fds()
        with pytest.raises(tree_upload.TreeError, match="not a worktree of this folder"):
            opened(folder, home)
        assert open_fds() == before

    def test_a_nul_in_the_backlink_is_a_one_line_refusal_that_closes_what_it_opened(self, repo, tmp_path, home):
        worktree = tmp_path / "linked"
        git(repo, "worktree", "add", "-q", str(worktree))
        backlink = repo / ".git" / "worktrees" / "linked" / "gitdir"
        backlink.write_bytes(str(worktree / ".git").encode() + b"\0\n")
        before = open_fds()
        with pytest.raises(tree_upload.TreeError, match="not a worktree of this folder"):
            opened(worktree, home)
        assert open_fds() == before


class TestTheListingIsStreamed:
    def test_the_entry_ceiling_applies_as_names_arrive(self, repo, outside, home):
        forge_index_entry(repo, "../operator-home/key")
        for n in range(4):
            (repo / f"untracked-{n}.txt").write_text("u\n")
        with pytest.raises(tree_upload.TreeError, match="entries"):
            tar_of(repo, home, max_entries=3)

    def test_a_conflicted_path_is_archived_once(self, repo, home):
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        git(repo, "checkout", "-q", "-b", "theirs")
        (repo / "plain.txt").write_text("theirs\n")
        git(repo, "commit", "-q", "-am", "theirs")
        git(repo, "checkout", "-q", base)
        (repo / "plain.txt").write_text("ours\n")
        git(repo, "commit", "-q", "-am", "ours")
        subprocess.run(["git", "merge", "-q", "theirs"], cwd=repo, capture_output=True)
        assert git(repo, "ls-files", "--stage", "plain.txt").count("\n") == 2
        assert tar_of(repo, home).getnames().count("plain.txt") == 1

    def test_a_long_listing_is_not_held_in_memory(self, repo, tmp_path, home):
        names = script(tmp_path / "many-names-git", "/usr/bin/seq 1 100000 | /usr/bin/tr '\\n' '\\0'\n")
        top = opened(repo, home, git_programs=(str(names),))
        tracemalloc.start()
        try:
            tree_upload.write_tar(top, Counting())
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 4 * 1024 * 1024
