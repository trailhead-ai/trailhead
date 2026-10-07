"""`camp site-export` — write one workspace site to a stream as a tar archive.

This is the remote half of fetching a site from another host: it runs on the
host that owns the workspace and writes an uncompressed tar of
``<workspace>/sites/<site>/`` to a binary stream (the CLI verb passes stdout).
The reader treats the stream as untrusted regardless; this side's job is to
never put anything in it that could reach outside the site.

Contract:

- ``group``, ``slug`` and ``site`` are full-matched against their grammars
  before any path is built or any filesystem call is made. ``re.fullmatch`` is
  used throughout because ``$`` accepts a trailing newline.
- The workspace, ``sites/`` and the site directory are each required to be a
  real directory (``lstat``; a symlink is refused), and the site must carry a
  regular-file ``index.html`` at its root. Any of those failing is a
  `SiteNotFoundError`, which the CLI reports with `EXIT_NOT_FOUND`.
- The walk descends by directory file descriptor, opening each directory with
  ``O_NOFOLLOW | O_DIRECTORY``. Each file is ``lstat``-ed, opened with
  ``O_NOFOLLOW | O_NONBLOCK``, and its type and link count are checked with
  ``fstat`` on the open descriptor before a byte is read, so a file swapped for
  a symlink or FIFO mid-walk is never read through and never blocks.
- Only directories and regular files with a single hard link are archived.
  Symlinks, devices, FIFOs, sockets and multiply-linked files are skipped.
- Members are named relative to the site root, are exactly ``REGTYPE`` or
  ``DIRTYPE``, and carry normalised ownership and permission bits.
"""

from __future__ import annotations

import os
import re
import stat
import tarfile
from typing import BinaryIO, Callable

#: Exit status for "the named workspace or site does not exist, or is not a
#: plain exportable site". Distinct from 1, which is camp's usage/refusal exit,
#: so a caller can tell "nothing there" from "bad invocation".
EXIT_NOT_FOUND = 4

_GROUP_RE = re.compile(r"[a-z0-9-]+")
_SLUG_RE = re.compile(r"[a-z0-9-]+")
_SITE_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class SiteExportUsageError(Exception):
    """A name is outside its grammar; nothing was read."""


class SiteNotFoundError(Exception):
    """The workspace or site is absent, symlinked, or has no root index.html."""


def _validate(group: str, slug: str, site: str) -> None:
    for label, value, rx in (
        ("group", group, _GROUP_RE),
        ("slug", slug, _SLUG_RE),
        ("site", site, _SITE_RE),
    ):
        if not isinstance(value, str) or not rx.fullmatch(value):
            raise SiteExportUsageError(f"{label} {value!r} is not a valid name")


def _open_dir(parent_fd: int | None, name: str, what: str) -> int:
    """Open *name* as a real directory (never through a symlink)."""
    try:
        st = os.lstat(name, dir_fd=parent_fd) if parent_fd is not None else os.lstat(name)
        if not stat.S_ISDIR(st.st_mode):
            raise SiteNotFoundError(f"{what} is not a directory")
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        raise SiteNotFoundError(f"{what} not found") from None
    except OSError as exc:
        raise SiteNotFoundError(f"{what} cannot be opened ({exc.strerror})") from None


def export_site(
    group: str,
    slug: str,
    site: str,
    out: BinaryIO,
    *,
    env: dict[str, str] | None = None,
    before_read: Callable[[str], None] | None = None,
) -> None:
    """Write the tar of ``<workspace>/sites/<site>/`` to *out*.

    *env* resolves the camp state root (tests inject it). *before_read* is
    called with a file's path after it has been ``lstat``-ed and before it is
    opened — the seam tests use to swap the file mid-walk.
    """
    _validate(group, slug, site)
    from ..group import manifest

    workspace = manifest.workspace_dir(group, slug, env=env)
    fds: list[int] = []
    try:
        ws_fd = _open_dir(None, str(workspace), "workspace")
        fds.append(ws_fd)
        sites_fd = _open_dir(ws_fd, "sites", "sites directory")
        fds.append(sites_fd)
        site_fd = _open_dir(sites_fd, site, "site")
        fds.append(site_fd)
        try:
            index = os.lstat("index.html", dir_fd=site_fd)
        except OSError:
            raise SiteNotFoundError("site has no index.html") from None
        if not stat.S_ISREG(index.st_mode):
            raise SiteNotFoundError("site has no index.html")

        root = os.path.join(str(workspace), "sites", site)
        with tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            _walk(tar, site_fd, root, "", before_read)
        out.flush()
    finally:
        for fd in fds:
            os.close(fd)


def _walk(
    tar: tarfile.TarFile,
    dir_fd: int,
    dir_path: str,
    rel: str,
    before_read: Callable[[str], None] | None,
) -> None:
    for name in sorted(os.listdir(dir_fd)):
        member = f"{rel}{name}"
        path = os.path.join(dir_path, name)
        try:
            st = os.lstat(name, dir_fd=dir_fd)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            try:
                child_fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
            except OSError:
                continue
            try:
                info = tarfile.TarInfo(member)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = int(st.st_mtime)
                tar.addfile(info)
                _walk(tar, child_fd, path, f"{member}/", before_read)
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(st.st_mode):
            if before_read is not None:
                before_read(path)
            _add_file(tar, dir_fd, name, member)


def _add_file(tar: tarfile.TarFile, dir_fd: int, name: str, member: str) -> None:
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=dir_fd)
    except OSError:
        return
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            return
        info = tarfile.TarInfo(member)
        info.type = tarfile.REGTYPE
        info.size = st.st_size
        info.mode = 0o644
        info.mtime = int(st.st_mtime)
        with os.fdopen(fd, "rb", closefd=False) as fh:
            tar.addfile(info, fh)
    finally:
        os.close(fd)


def run_cli(args: list[str]) -> None:
    """The `camp site-export` verb: parse flags, stream to stdout, exit."""
    import sys

    from ..cli.parser import CampParser

    parser = CampParser(verb="site-export")
    parser.add_argument("--group")
    parser.add_argument("--slug")
    parser.add_argument("--site")
    parsed = parser.parse_args(args)
    for flag in ("group", "slug", "site"):
        if getattr(parsed, flag) is None:
            parser.die(f"--{flag} is required")

    try:
        export_site(parsed.group, parsed.slug, parsed.site, sys.stdout.buffer)
    except SiteExportUsageError as exc:
        parser.die(str(exc))
    except SiteNotFoundError as exc:
        print(f"camp site-export: {exc}", file=sys.stderr)
        sys.exit(EXIT_NOT_FOUND)
