"""The working tree as a tar, and the workflow that defines a job in it.

This runs as the operator, outside the sandbox, against a tree, a ``.git`` and a
repository configuration the session wrote and can change while they are read. So
the session's git directory is never handed to git, and its configuration is never
read by anything: no program, include, excludes file, fetch or hook it names can
take effect.

**Finding the top level.** The folder the command runs in is opened once, and the
walk to the top level goes up from that open folder through ``..``, one open folder
at a time, so the top level is that folder or one of its parents by construction and
no path is resolved again. At each folder a ``.git`` that is a real folder holding
``HEAD``, ``objects`` and ``refs`` makes it the top level; a ``.git`` that is a
regular file holding a ``gitdir:`` line (a linked worktree) makes it the top level
only when the git folder it names carries a ``gitdir`` file naming this same
``.git`` file back (compared by inode), so a ``.git`` file pointing at some other
repository's, or the operator's, folder is refused. A ``.git`` symlink is refused.
The walk stops at the filesystem root and at a filesystem boundary, as git's does.

**Listing.** From the session's git folder only the ``index`` file is read, as
bytes, through an fd, without following a symlink, at most 64 MiB. It is checked
before use: index versions 2 to 4 with SHA-1 names are read, and a split index (its
entries live in another file of the session's), a sparse index, and a skip-worktree
``.gitignore`` (whose patterns git would read from an object the copy does not
have) refuse the upload, as does anything that does not parse (a SHA-256
repository's index among them), as does an index path over 4096 bytes, which also
keeps the check linear in the index's size. The copy goes into a fresh private folder under
``<operator home>/.local/state/trailhead/gateway/uploads``, the home taken from the
password database rather than ``HOME``. Every folder on that way is opened without
following a symlink and must be owned by this user, and ``uploads`` must be closed
to group and others; the location is outside every path the sandbox lets a session
write. Beside the index copy the folder holds only ``HEAD``, empty ``objects`` and
``refs``, and a ``config`` written here. The session's ``info/exclude`` is not
copied, so a file ignored only there is uploaded. Git is run from a fixed absolute
path (no ``PATH`` lookup) with an environment built here and nothing inherited,
``GIT_DIR`` and ``GIT_INDEX_FILE`` at the private folder, its working folder the
open top level, lazy fetching and prompting off, under a timeout and with its output
written to a file capped by ``RLIMIT_FSIZE``. Git keeps every ignore pattern it reads
in memory at many times the file's size, so on Linux its address space is capped
(``RLIMIT_AS``, 1 GiB: far above what listing 100,000 entries takes), and on every
platform (macOS enforces no such cap) the ``.gitignore`` files below the top level,
found by a walk that follows no symlink and skips ``.git``, may add up to at most
4 MiB before git is run. The private folder is removed when the tree is closed.

**Archiving.** What is archived is the files git tracks plus the ones it does not
track and does not ignore, as they are on disk now. The listing is read and archived
a name at a time, so the ceilings below bound memory as well as the upload, and a
conflicted path's index stages, which git lists one after another, are archived once. A listed path that is not a
plain relative path (absolute, empty, ``.`` or ``..`` segments) refuses the whole
upload. A path gone from disk is skipped, as is anything that is not a regular file
or a symlink and any entry below a folder that is a symlink: no path is followed
through a symlink. A symlink is archived as a link with its target text and no
content. A regular file with more than one hard link refuses the upload, since a
hard link can alias a file the session cannot read. Files are written into the tar
one at a time, never read whole into memory, and the upload is refused before a
file is read once its size would take the file content over Outpost's 512 MiB, or
an entry over Outpost's 100,000 entries. A workflow file over Outpost's 256 KiB
limit is never read and so defines no job.
"""

from __future__ import annotations

import os
import pwd
import re
import resource
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import weakref
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator

WORKFLOWS_DIR = ".github/workflows"
MAX_WORKFLOW_BYTES = 256 * 1024
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_UPLOAD_ENTRIES = 100_000
MAX_INDEX_BYTES = 64 * 1024 * 1024
MAX_LISTING_BYTES = 64 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 60.0
MAX_GIT_MEMORY = 1024 * 1024 * 1024
MAX_GITIGNORE_BYTES = 4 * 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_FOLDER_DEPTH = 512
GIT_PROGRAMS = ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git")
SCRATCH_PARTS = (".local", "state", "trailhead", "gateway", "uploads")


class TreeError(Exception):
    """A one-line reason the working tree cannot be uploaded."""


_HARDENED = ("--no-optional-locks", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}")
_NOT_A_TREE = "this folder is not inside a git work tree"
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_UNREADABLE_INDEX = "the repository's index is not one this command can read"
_LONG_PATH = f"the repository's index holds a path longer than {MAX_PATH_BYTES} bytes"
_SCRATCH_CONFIG = f"""[core]
\trepositoryformatversion = 0
\tbare = false
\tfsmonitor = false
\tuntrackedCache = false
\tsplitIndex = false
\tsparseCheckout = false
\thooksPath = {os.devnull}
\texcludesFile = {os.devnull}
[index]
\tsparse = false
"""


def operator_home() -> Path:
    """This user's home as the password database records it, whatever ``HOME`` says."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


class Tree:
    """The git top level, held open: every read and every git run goes through :attr:`fd`."""

    def __init__(
        self,
        fd: int,
        index: bytes | None,
        *,
        home: Path | None,
        runner: Callable[..., Any],
        git_programs: tuple[str, ...],
        timeout: float,
        max_listing: int,
        max_memory: int,
        max_gitignore: int,
    ):
        self.fd, self.index, self.runner = fd, index, runner
        self.git_programs, self.timeout, self.max_listing = git_programs, timeout, max_listing
        self.max_memory, self.max_gitignore = max_memory, max_gitignore
        self._home = home
        self._scratch: Path | None = None
        self._held: list[Path | None] = [None]
        self._close = weakref.finalize(self, _release, fd, self._held)

    def scratch(self) -> Path:
        """The private folder for this upload, made on first use and removed on :meth:`close`."""
        if self._scratch is None:
            self._scratch = _make_scratch(self._home if self._home is not None else operator_home())
            self._held[0] = self._scratch
        return self._scratch

    def spool(self) -> BinaryIO:
        """An unnamed file in the private folder, readable after the tree is closed."""
        return tempfile.TemporaryFile(dir=self.scratch())

    def close(self) -> None:
        self._close()

    def __enter__(self) -> Tree:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _release(fd: int, held: list[Path | None]) -> None:
    os.close(fd)
    if held[0] is not None:
        shutil.rmtree(held[0], ignore_errors=True)


def _read_capped(fd: int, cap: int) -> bytes:
    chunks, total = [], 0
    while total <= cap:
        chunk = os.read(fd, min(1 << 20, cap + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _open_file(dir_fd: int, name: str) -> int | None:
    """An fd on the regular file *name* in *dir_fd*, or None for anything else."""
    try:
        fd = os.open(name, _FILE, dir_fd=dir_fd)
    except OSError:
        return None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    return fd


def _small_file(dir_fd: int, name: str) -> bytes | None:
    fd = _open_file(dir_fd, name)
    if fd is None:
        return None
    try:
        return _read_capped(fd, 4096)
    finally:
        os.close(fd)


def _is_git_dir(fd: int) -> bool:
    try:
        os.lstat("HEAD", dir_fd=fd)
        return all(stat.S_ISDIR(os.stat(name, dir_fd=fd).st_mode) for name in ("objects", "refs"))
    except OSError:
        return False


_NOT_OUR_WORKTREE = "the .git file here names a git folder that is not a worktree of this folder"


def _linked_git_dir(top_fd: int) -> int:
    """The git folder a ``.git`` file in *top_fd* names, held open, once it names this ``.git`` back."""
    link_fd = _open_file(top_fd, ".git")
    if link_fd is None:
        raise TreeError(_NOT_OUR_WORKTREE)
    try:
        text = _read_capped(link_fd, 4096)
        link = os.fstat(link_fd)
    finally:
        os.close(link_fd)
    first = text.split(b"\n", 1)[0].rstrip(b"\r")
    if not first.startswith(b"gitdir: ") or len(text) > 4096:
        raise TreeError("the .git file here has no gitdir: line")
    named_dir = os.fsdecode(first[len(b"gitdir: ") :])
    try:
        git_fd = os.open(named_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=top_fd)
    except (OSError, ValueError):
        raise TreeError(_NOT_OUR_WORKTREE) from None
    back = _small_file(git_fd, "gitdir")
    try:
        named = os.stat(os.fsdecode(back.split(b"\n", 1)[0]), dir_fd=git_fd) if back else None
    except (OSError, ValueError):
        named = None
    if named is None or (named.st_dev, named.st_ino) != (link.st_dev, link.st_ino):
        os.close(git_fd)
        raise TreeError(_NOT_OUR_WORKTREE)
    return git_fd


def _git_dir_at(fd: int) -> int | None:
    """The git folder of the top level *fd*, held open, or None when *fd* is not one."""
    try:
        st = os.lstat(".git", dir_fd=fd)
    except FileNotFoundError:
        return None
    except OSError:
        raise TreeError("the .git entry here cannot be read") from None
    if stat.S_ISREG(st.st_mode):
        return _linked_git_dir(fd)
    if stat.S_ISLNK(st.st_mode):
        raise TreeError("the .git entry here is a symlink")
    if not stat.S_ISDIR(st.st_mode):
        return None
    git_fd = os.open(".git", _DIR, dir_fd=fd)
    if _is_git_dir(git_fd):
        return git_fd
    os.close(git_fd)
    return None


def _discover(cwd_fd: int) -> tuple[int, int]:
    """``(top level fd, git folder fd)`` found by walking up from *cwd_fd* through ``..``."""
    fd = os.dup(cwd_fd)
    device = os.fstat(fd).st_dev
    try:
        while True:
            git_fd = _git_dir_at(fd)
            if git_fd is not None:
                top, fd = fd, -1
                return top, git_fd
            parent = os.open("..", _DIR, dir_fd=fd)
            here, up = os.fstat(fd), os.fstat(parent)
            os.close(fd)
            fd = parent
            if (up.st_dev, up.st_ino) == (here.st_dev, here.st_ino) or up.st_dev != device:
                raise TreeError(_NOT_A_TREE)
    except OSError:
        raise TreeError(_NOT_A_TREE) from None
    finally:
        if fd >= 0:
            os.close(fd)


def _read_index(git_fd: int) -> bytes | None:
    try:
        fd = os.open("index", _FILE, dir_fd=git_fd)
    except FileNotFoundError:
        return None
    except OSError:
        raise TreeError("the repository's index cannot be read") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise TreeError("the repository's index is not a regular file")
        data = _read_capped(fd, MAX_INDEX_BYTES)
    finally:
        os.close(fd)
    if len(data) > MAX_INDEX_BYTES:
        raise TreeError(f"the repository's index is over {MAX_INDEX_BYTES} bytes")
    _check_index(data)
    return data


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    """Git's offset varint (index version 4 name prefixes): ``(value, next position)``."""
    byte = data[pos]
    value = byte & 0x7F
    while byte & 0x80:
        pos += 1
        byte = data[pos]
        value = ((value + 1) << 7) | (byte & 0x7F)
    return value, pos + 1


def _check_index(data: bytes) -> None:
    """Refuse an index git would need more than these bytes to read (another file, an object)."""
    try:
        _walk_index(data)
    except (IndexError, ValueError, struct.error):
        raise TreeError(_UNREADABLE_INDEX) from None


def _walk_index(data: bytes) -> None:
    end = len(data) - 20
    if data[:4] != b"DIRC" or end < 12:
        raise ValueError
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3, 4):
        raise ValueError
    pos, name = 12, b""
    for _ in range(count):
        start = pos
        mode = struct.unpack(">I", data[start + 24 : start + 28])[0]
        flags = struct.unpack(">H", data[start + 60 : start + 62])[0]
        extended = 0
        pos = start + 62
        if flags & 0x4000:
            if version < 3:
                raise ValueError
            extended = struct.unpack(">H", data[pos : pos + 2])[0]
            pos += 2
        if version == 4:
            strip, pos = _varint(data, pos)
            nul = data.find(b"\0", pos, pos + MAX_PATH_BYTES + 1)
            if strip > len(name):
                raise ValueError
            kept = len(name) - strip
            if nul < 0 or kept + nul - pos > MAX_PATH_BYTES:
                raise TreeError(_LONG_PATH)
            name = name[:kept] + data[pos:nul]
            pos = nul + 1
        else:
            nul = data.find(b"\0", pos, pos + MAX_PATH_BYTES + 1)
            if nul < 0:
                raise TreeError(_LONG_PATH)
            name = data[pos:nul]
            pos = start + ((nul - start + 8) & ~7)
        if pos > end:
            raise ValueError
        if stat.S_IFMT(mode) == stat.S_IFDIR:
            raise TreeError("the repository uses a sparse index, which this command does not read")
        if extended & 0x4000 and name.rsplit(b"/", 1)[-1] == b".gitignore":
            raise TreeError(
                f"the index marks {os.fsdecode(name)} skip-worktree; "
                "its patterns are in an object this command does not read"
            )
    while pos < end:
        signature = data[pos : pos + 4]
        size = struct.unpack(">I", data[pos + 4 : pos + 8])[0]
        if signature == b"link":
            raise TreeError("the repository uses a split index, which this command does not read")
        if signature == b"sdir":
            raise TreeError("the repository uses a sparse index, which this command does not read")
        pos += 8 + size
    if pos != end:
        raise ValueError


def top_level(
    cwd: Path,
    *,
    home: Path | None = None,
    runner: Callable[..., Any] = subprocess.run,
    git_programs: tuple[str, ...] = GIT_PROGRAMS,
    timeout: float = GIT_TIMEOUT_SECONDS,
    max_listing: int = MAX_LISTING_BYTES,
    max_memory: int = MAX_GIT_MEMORY,
    max_gitignore: int = MAX_GITIGNORE_BYTES,
) -> Tree:
    """The git top level of *cwd*, opened once, with a checked copy of its index.

    *home* is where the private folder git runs against is made (default: the
    operator's home from the password database)."""
    try:
        cwd_fd = os.open(cwd, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise TreeError(_NOT_A_TREE) from None
    try:
        top_fd, git_fd = _discover(cwd_fd)
    finally:
        os.close(cwd_fd)
    try:
        index = _read_index(git_fd)
    except BaseException:
        os.close(top_fd)
        raise
    finally:
        os.close(git_fd)
    return Tree(
        top_fd,
        index,
        home=home,
        runner=runner,
        git_programs=git_programs,
        timeout=timeout,
        max_listing=max_listing,
        max_memory=max_memory,
        max_gitignore=max_gitignore,
    )


def _make_scratch(home: Path) -> Path:
    """A fresh 0700 folder under ``home/.local/state/trailhead/gateway/uploads``."""
    where = home.joinpath(*SCRATCH_PARTS)
    try:
        fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise TreeError(f"the operator home {home} cannot be opened") from None
    try:
        for name in SCRATCH_PARTS:
            try:
                os.mkdir(name, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            except OSError:
                raise TreeError(f"{where} cannot be made") from None
            try:
                nxt = os.open(name, _DIR, dir_fd=fd)
            except OSError:
                raise TreeError(f"a folder on the way to {where} is a symlink or cannot be opened") from None
            os.close(fd)
            fd = nxt
            if os.fstat(fd).st_uid != os.getuid():
                raise TreeError(f"a folder on the way to {where} is not owned by this user")
        if os.fstat(fd).st_mode & 0o077:
            raise TreeError(f"{where} is not private to this user (mode 0700)")
        name = f"upload-{secrets.token_hex(8)}"
        os.mkdir(name, 0o700, dir_fd=fd)
        scratch = os.open(name, _DIR, dir_fd=fd)
    except OSError:
        raise TreeError(f"a private folder cannot be made in {where}") from None
    finally:
        os.close(fd)
    try:
        for folder in ("objects", "refs"):
            os.mkdir(folder, 0o700, dir_fd=scratch)
        _write_new(scratch, "HEAD", b"ref: refs/heads/main\n")
        _write_new(scratch, "config", _SCRATCH_CONFIG.encode())
    finally:
        os.close(scratch)
    return where / name


def _write_new(dir_fd: int, name: str, data: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _git_program(candidates: tuple[str, ...]) -> str:
    for program in candidates:
        if os.access(program, os.X_OK):
            return program
    raise TreeError(f"git is not installed at any of {', '.join(candidates)}")


def _list(tree: Tree) -> Iterator[bytes]:
    """``git ls-files`` for the open top level, run against the private copy of the index."""
    program = _git_program(tree.git_programs)
    scratch = tree.scratch()
    if tree.index is not None:
        _write_new_path(scratch / "index", tree.index)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(scratch),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_DIR": str(scratch),
        "GIT_INDEX_FILE": str(scratch / "index"),
        "GIT_WORK_TREE": ".",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    _check_ignore_files(tree.fd, tree.max_gitignore)
    top_fd, cap, memory = tree.fd, tree.max_listing, tree.max_memory
    bounds_memory = sys.platform.startswith("linux")

    def inside() -> None:
        os.fchdir(top_fd)
        resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
        if bounds_memory:
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))

    with open(scratch / "listing", "x+b") as out:
        try:
            done = tree.runner(
                [program, *_HARDENED, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.DEVNULL,
                env=env,
                preexec_fn=inside,
                timeout=tree.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise TreeError(f"git did not list the working tree within {tree.timeout:g} seconds") from None
        except (OSError, subprocess.SubprocessError):
            raise TreeError("git could not be run to list the working tree") from None
        if done.returncode != 0:
            raise TreeError("git could not list the working tree")
        if os.fstat(out.fileno()).st_size > cap:
            raise TreeError(f"git listed more than {cap} bytes of paths")
        out.seek(0)
        yield from _split_names(out)


def _split_names(listing: BinaryIO) -> Iterator[bytes]:
    """The NUL-terminated names in *listing*, read a chunk at a time."""
    tail = b""
    while chunk := listing.read(1 << 16):
        *names, tail = (tail + chunk).split(b"\0")
        if len(tail) > MAX_PATH_BYTES:
            raise TreeError(f"git listed a path longer than {MAX_PATH_BYTES} bytes")
        yield from names
    if tail:
        yield tail


def _check_ignore_files(top_fd: int, cap: int) -> None:
    """Refuse when the ``.gitignore`` files below *top_fd* add up to more than *cap* bytes.

    Git holds every pattern it reads in memory, many times the file's size, and only
    Linux enforces the address-space limit put on it; this bounds what git can be given
    to read on every platform. Folders are entered without following a symlink, and
    ``.git`` folders are not entered.
    """
    total = 0
    stack = [(os.dup(top_fd), None)]
    try:
        while stack:
            fd, pending = stack[-1]
            if pending is None:
                pending = []
                try:
                    entries = list(os.scandir(fd))
                except OSError:
                    entries = []
                for entry in entries:
                    if entry.name == ".gitignore" and entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                        if total > cap:
                            raise TreeError(f"the .gitignore files add up to more than {cap} bytes")
                    elif entry.name != ".git" and entry.is_dir(follow_symlinks=False):
                        pending.append(entry.name)
                stack[-1] = (fd, pending)
            if not pending:
                os.close(fd)
                stack.pop()
                continue
            if len(stack) > MAX_FOLDER_DEPTH:
                raise TreeError(f"folders nest deeper than {MAX_FOLDER_DEPTH} levels")
            try:
                child = os.open(pending.pop(), _DIR, dir_fd=fd)
            except OSError:
                continue
            stack.append((child, None))
    finally:
        for fd, _ in stack:
            os.close(fd)


def _write_new_path(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _is_plain(rel: str) -> bool:
    """A relative path with no empty, ``.`` or ``..`` segment; the one trailing slash
    git puts on an untracked nested repository is allowed."""
    return all(name not in ("", ".", "..") for name in rel.removesuffix("/").split("/"))


def listed_paths(tree: Tree) -> Iterator[str]:
    """The paths git lists, as they arrive; a conflicted path's stages, which git lists
    one after another, are given once."""
    previous = None
    for raw in _list(tree):
        if not raw or raw == previous:
            continue
        previous = raw
        rel = os.fsdecode(raw)
        if not _is_plain(rel):
            raise TreeError("git lists a path that is not a plain path inside the top level; the index was hand-edited")
        yield rel


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


class _Budget:
    def __init__(self, max_bytes: int, max_entries: int):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.bytes = self.entries = 0

    def take(self, size: int) -> None:
        if self.entries + 1 > self.max_entries:
            raise TreeError(f"the upload has more than Outpost's limit of {self.max_entries} entries")
        if self.bytes + size > self.max_bytes:
            raise TreeError(f"the upload has more than Outpost's limit of {self.max_bytes} bytes of file content")
        self.entries += 1
        self.bytes += size


def write_tar(
    tree: Tree, out: BinaryIO, *, max_bytes: int = MAX_UPLOAD_BYTES, max_entries: int = MAX_UPLOAD_ENTRIES
) -> None:
    budget = _Budget(max_bytes, max_entries)
    with tarfile.open(fileobj=out, mode="w") as tar:
        for rel in listed_paths(tree):
            _add(tar, tree.fd, rel, budget)


def _add(tar: tarfile.TarFile, top_fd: int, rel: str, budget: _Budget) -> None:
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
            budget.take(0)
            info.type = tarfile.SYMTYPE
            info.mode = 0o777
            tar.addfile(info)
            return
        fd = _open_file(dir_fd, name)
        if fd is None:
            return
        with os.fdopen(fd, "rb") as fh:
            fst = os.fstat(fh.fileno())
            if fst.st_nlink > 1:
                raise TreeError(f"{rel} has more than one hard link; copy it instead of linking it")
            budget.take(fst.st_size)
            info.size = fst.st_size
            info.mtime = int(fst.st_mtime)
            info.mode = 0o755 if fst.st_mode & stat.S_IXUSR else 0o644
            try:
                tar.addfile(info, fh)
            except OSError:
                raise TreeError(f"{rel} changed while it was read") from None
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
