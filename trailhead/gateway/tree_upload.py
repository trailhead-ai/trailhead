"""The working tree as a tar, and the workflow that defines a job in it.

Everything is read from the git top level of the folder the command runs in: the
files git tracks (``--cached``) plus the ones it does not yet track and does not
ignore (``--others --exclude-standard``), as they are on disk now. A path git lists
that is gone from disk is skipped, as is anything that is not a regular file or a
symlink, which covers submodules and nested repositories (a directory), and any
entry below a folder that is a symlink: no path is followed through a symlink, so
nothing outside the top level can be read. A symlink is archived as a link with its
target text and no content. Git runs without the repository's own fsmonitor or hooks,
and the folder the command runs in must lie inside the top level git reports. Files are written into
the tar one at a time, never read whole into memory.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import BinaryIO

WORKFLOWS_DIR = ".github/workflows"


class TreeError(Exception):
    """A one-line reason the working tree cannot be uploaded."""


_HARDENED = ("--no-optional-locks", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}")
_NOT_A_TREE = "this folder is not inside a git work tree"


def _git(cwd: Path, *args: str) -> bytes:
    """Run git the way an operator-side command must against a tree a session wrote:
    no program the repository's own config names (an fsmonitor, a hook) may run."""
    try:
        done = subprocess.run(["git", *_HARDENED, *args], cwd=cwd, capture_output=True, check=False)
    except (FileNotFoundError, NotADirectoryError):
        raise TreeError(_NOT_A_TREE) from None
    if done.returncode != 0:
        raise TreeError(_NOT_A_TREE)
    return done.stdout


def top_level(cwd: Path) -> Path:
    top = Path(os.fsdecode(_git(cwd, "rev-parse", "--show-toplevel").rstrip(b"\n")))
    if not Path(os.path.realpath(cwd)).is_relative_to(os.path.realpath(top)):
        raise TreeError("this folder is not inside the git top level its repository names")
    return top


def listed_paths(top: Path) -> list[str]:
    raw = _git(top, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return list(dict.fromkeys(os.fsdecode(p) for p in raw.split(b"\0") if p))


_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


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


def write_tar(top: Path, out: BinaryIO) -> None:
    top_fd = os.open(top, _DIR)
    try:
        with tarfile.open(fileobj=out, mode="w") as tar:
            for rel in listed_paths(top):
                _add(tar, top_fd, rel)
    finally:
        os.close(top_fd)


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


def workflow_defining(top: Path, job: str) -> str:
    """The one workflow under ``.github/workflows/`` that defines *job*; refuses when none or several do."""
    found = []
    top_fd = os.open(top, _DIR)
    try:
        folder_fd = _descend(top_fd, WORKFLOWS_DIR.split("/"))
    finally:
        os.close(top_fd)
    if folder_fd is not None:
        try:
            for name in sorted(os.listdir(folder_fd)):
                if not name.endswith((".yml", ".yaml")):
                    continue
                fd = _open_file(folder_fd, name)
                if fd is None:
                    continue
                with os.fdopen(fd, "rb") as fh:
                    text = fh.read().decode(errors="replace")
                if job in _job_ids(text):
                    found.append(f"{WORKFLOWS_DIR}/{name}")
        finally:
            os.close(folder_fd)
    if not found:
        raise TreeError(f"no workflow under {WORKFLOWS_DIR}/ defines job {job}; name one with --workflow")
    if len(found) > 1:
        raise TreeError(f"several workflows define job {job} ({', '.join(found)}); name one with --workflow")
    return found[0]
