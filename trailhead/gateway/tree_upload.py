"""The working tree as a tar, and the workflow that defines a job in it.

The tree a session controls can change while it is read, so the git top level is
opened exactly once, one folder at a time from ``/`` without following a symlink,
and that open folder is the only route in: git lists the tree from inside it, and
every file and the workflow discovery are read relative to it. Opening refuses when
any folder on the way is a symlink (git names a top level with none, so one there
means the tree was swapped) or when the folder the command runs in does not lie
below that same open folder.

What is archived is the files git tracks (``--cached``) plus the ones it does not
yet track and does not ignore (``--others --exclude-standard``), as they are on disk
now. A listed path that is not a plain relative path (absolute, empty, ``.`` or
``..`` segments, which only a hand-written index can hold) refuses the whole upload
rather than being skipped, so a tampered index is reported, never half-uploaded. A
path that is gone from disk is skipped, as is anything that is not a regular file or
a symlink (submodules and nested repositories are folders) and any entry below a
folder that is a symlink: no path is followed through a symlink. A symlink is
archived as a link with its target text and no content. Files are written into the
tar one at a time, never read whole into memory. A workflow file over Outpost's
256 KiB limit is never read and so defines no job.

Git is a fixed system binary, run with no ``GIT_*`` variable from the environment
and without the repository's own fsmonitor or hooks.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tarfile
import weakref
from pathlib import Path
from typing import Any, BinaryIO, Callable

WORKFLOWS_DIR = ".github/workflows"
MAX_WORKFLOW_BYTES = 256 * 1024


class TreeError(Exception):
    """A one-line reason the working tree cannot be uploaded."""


_HARDENED = ("--no-optional-locks", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}")
_GIT_PROGRAMS = ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git")
_NOT_A_TREE = "this folder is not inside a git work tree"
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class Tree:
    """The git top level, held open: every read and every git run goes through :attr:`fd`."""

    def __init__(self, fd: int, path: Path, runner: Callable[..., Any] = subprocess.run):
        self.fd, self.path, self.runner = fd, path, runner
        self._close = weakref.finalize(self, os.close, fd)

    def close(self) -> None:
        self._close()

    def __enter__(self) -> Tree:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _git_program() -> str:
    for program in _GIT_PROGRAMS:
        if os.access(program, os.X_OK):
            return program
    found = shutil.which("git")
    if found is None:
        raise TreeError("git is not installed")
    return found


def _git(runner: Callable[..., Any], dir_fd: int, *args: str) -> bytes:
    """Run git inside the open folder *dir_fd* the way an operator-side command must
    against a tree a session wrote: no program the repository's own config or the
    environment names (an fsmonitor, a hook, ``GIT_DIR``) may steer it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        done = runner(
            [_git_program(), *_HARDENED, *args],
            preexec_fn=lambda: os.fchdir(dir_fd),
            env=env,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise TreeError(_NOT_A_TREE) from None
    if done.returncode != 0:
        raise TreeError(_NOT_A_TREE)
    return done.stdout


def _open_from_root(path: Path) -> int:
    """An fd on *path*, reached one folder at a time from ``/`` without following a symlink."""
    if not path.is_absolute() or any(name in (".", "..") for name in path.parts):
        raise TreeError("git named a top level that is not a plain absolute path")
    fd = os.open("/", _DIR)
    for name in path.parts[1:]:
        try:
            nxt = os.open(name, _DIR, dir_fd=fd)
        except OSError:
            os.close(fd)
            raise TreeError("a folder on the way to the git top level is a symlink or cannot be opened") from None
        os.close(fd)
        fd = nxt
    return fd


def _lies_below(cwd_fd: int, top_fd: int) -> bool:
    """Whether the open folder *top_fd* is *cwd_fd* or one of its parents."""
    top = os.fstat(top_fd)
    fd = os.dup(cwd_fd)
    try:
        while True:
            here = os.fstat(fd)
            if (here.st_dev, here.st_ino) == (top.st_dev, top.st_ino):
                return True
            parent = os.open("..", _DIR, dir_fd=fd)
            os.close(fd)
            fd = parent
            up = os.fstat(fd)
            if (up.st_dev, up.st_ino) == (here.st_dev, here.st_ino):
                return False
    finally:
        os.close(fd)


def top_level(cwd: Path, *, runner: Callable[..., Any] = subprocess.run) -> Tree:
    """The git top level of *cwd*, opened once; refuses when it cannot be reached
    without a symlink or does not contain *cwd*."""
    try:
        cwd_fd = os.open(cwd, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise TreeError(_NOT_A_TREE) from None
    try:
        path = Path(os.fsdecode(_git(runner, cwd_fd, "rev-parse", "--show-toplevel").rstrip(b"\n")))
        fd = _open_from_root(path)
        try:
            below = _lies_below(cwd_fd, fd)
        except OSError:
            below = False
        if not below:
            os.close(fd)
            raise TreeError("this folder is not inside the git top level its repository names")
    finally:
        os.close(cwd_fd)
    return Tree(fd, path, runner)


def _is_plain(rel: str) -> bool:
    """A relative path with no empty, ``.`` or ``..`` segment; the one trailing slash
    git puts on an untracked nested repository is allowed."""
    return all(name not in ("", ".", "..") for name in rel.removesuffix("/").split("/"))


def listed_paths(tree: Tree) -> list[str]:
    raw = _git(tree.runner, tree.fd, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    paths = list(dict.fromkeys(os.fsdecode(p) for p in raw.split(b"\0") if p))
    if not all(_is_plain(rel) for rel in paths):
        raise TreeError("git lists a path that is not a plain path inside the top level; the index was hand-edited")
    return paths


def _descend(top_fd: int, parts: list[str]) -> int | None:
    """An fd on the folder at *parts* below *top_fd*, or None when any step is
    missing or is not a real folder (a symlink is never followed)."""
    fd = os.dup(top_fd)
    for name in parts:
        try:
            nxt = os.open(name, _DIR, dir_fd=fd)
        except OSError:
            os.close(fd)
            return None
        os.close(fd)
        fd = nxt
    return fd


def _open_file(dir_fd: int, name: str) -> int | None:
    """An fd on the regular file *name* in *dir_fd*, or None for anything else."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError:
        return None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    return fd


def write_tar(tree: Tree, out: BinaryIO) -> None:
    paths = listed_paths(tree)
    with tarfile.open(fileobj=out, mode="w") as tar:
        for rel in paths:
            _add(tar, tree.fd, rel)


def _add(tar: tarfile.TarFile, top_fd: int, rel: str) -> None:
    *folders, name = rel.split("/")
    dir_fd = _descend(top_fd, folders)
    if dir_fd is None:
        return
    try:
        try:
            st = os.lstat(name, dir_fd=dir_fd)
        except OSError:
            return
        info = tarfile.TarInfo(rel)
        info.mtime = int(st.st_mtime)
        if stat.S_ISLNK(st.st_mode):
            try:
                info.linkname = os.readlink(name, dir_fd=dir_fd)
            except OSError:
                return
            info.type = tarfile.SYMTYPE
            info.mode = 0o777
            tar.addfile(info)
            return
        fd = _open_file(dir_fd, name)
        if fd is None:
            return
        with os.fdopen(fd, "rb") as fh:
            fst = os.fstat(fh.fileno())
            info.size = fst.st_size
            info.mtime = int(fst.st_mtime)
            info.mode = 0o755 if fst.st_mode & stat.S_IXUSR else 0o644
            tar.addfile(info, fh)
    finally:
        os.close(dir_fd)


_JOBS_KEY = re.compile(r"^jobs\s*:\s*(#.*)?$")
_JOB_ID = re.compile(r"""^(\s+)(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_-]+))\s*:""")


def _job_ids(text: str) -> set[str]:
    """The keys directly under a block-style top-level ``jobs:``."""
    ids: set[str] = set()
    in_jobs, indent = False, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not in_jobs:
            in_jobs = bool(_JOBS_KEY.match(line))
            continue
        if not line[0].isspace():
            break
        match = _JOB_ID.match(line)
        if match is None:
            continue
        if indent is None:
            indent = match.group(1)
        if match.group(1) == indent:
            ids.add(match.group(2) or match.group(3) or match.group(4))
    return ids


def workflow_defining(tree: Tree, job: str) -> str:
    """The one workflow under ``.github/workflows/`` that defines *job*; refuses when none or several do."""
    found = []
    folder_fd = _descend(tree.fd, WORKFLOWS_DIR.split("/"))
    if folder_fd is not None:
        try:
            try:
                names = sorted(os.listdir(folder_fd))
            except OSError:
                raise TreeError(f"the {WORKFLOWS_DIR}/ folder cannot be listed") from None
            for name in names:
                if not name.endswith((".yml", ".yaml")):
                    continue
                fd = _open_file(folder_fd, name)
                if fd is None:
                    continue
                with os.fdopen(fd, "rb") as fh:
                    data = fh.read(MAX_WORKFLOW_BYTES + 1)
                if len(data) > MAX_WORKFLOW_BYTES:
                    continue
                if job in _job_ids(data.decode(errors="replace")):
                    found.append(f"{WORKFLOWS_DIR}/{name}")
        finally:
            os.close(folder_fd)
    if not found:
        raise TreeError(f"no workflow under {WORKFLOWS_DIR}/ defines job {job}; name one with --workflow")
    if len(found) > 1:
        raise TreeError(f"several workflows define job {job} ({', '.join(found)}); name one with --workflow")
    return found[0]
