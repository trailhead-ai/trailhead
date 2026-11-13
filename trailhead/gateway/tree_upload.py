"""The working tree as a tar, and the workflow that defines a job in it.

Everything is read from the git top level of the folder the command runs in: the
files git tracks (``--cached``) plus the ones it does not yet track and does not
ignore (``--others --exclude-standard``), as they are on disk now. A path git lists
that is gone from disk is skipped, as is anything that is not a regular file or a
symlink, which covers submodules and nested repositories (a directory). A symlink
is archived as a link with its target text and no content. Files are written into
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


def _git(cwd: Path, *args: str) -> bytes:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except (FileNotFoundError, NotADirectoryError):
        raise TreeError("this folder is not inside a git work tree") from None
    if done.returncode != 0:
        raise TreeError("this folder is not inside a git work tree")
    return done.stdout


def top_level(cwd: Path) -> Path:
    return Path(os.fsdecode(_git(cwd, "rev-parse", "--show-toplevel").rstrip(b"\n")))


def listed_paths(top: Path) -> list[str]:
    raw = _git(top, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return list(dict.fromkeys(os.fsdecode(p) for p in raw.split(b"\0") if p))


def write_tar(top: Path, out: BinaryIO) -> None:
    with tarfile.open(fileobj=out, mode="w") as tar:
        for rel in listed_paths(top):
            path = top / rel
            try:
                st = path.lstat()
            except FileNotFoundError:
                continue
            info = tarfile.TarInfo(rel)
            info.mtime = int(st.st_mtime)
            if stat.S_ISLNK(st.st_mode):
                info.type = tarfile.SYMTYPE
                info.linkname = os.readlink(path)
                info.mode = 0o777
                tar.addfile(info)
            elif stat.S_ISREG(st.st_mode):
                info.size = st.st_size
                info.mode = 0o755 if st.st_mode & stat.S_IXUSR else 0o644
                with path.open("rb") as fh:
                    tar.addfile(info, fh)


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
    folder = top / WORKFLOWS_DIR
    found = []
    for path in sorted(folder.glob("*.y*ml")) if folder.is_dir() else []:
        if path.suffix not in (".yml", ".yaml") or not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if job in _job_ids(text):
            found.append(f"{WORKFLOWS_DIR}/{path.name}")
    if not found:
        raise TreeError(f"no workflow under {WORKFLOWS_DIR}/ defines job {job}; name one with --workflow")
    if len(found) > 1:
        raise TreeError(f"several workflows define job {job} ({', '.join(found)}); name one with --workflow")
    return found[0]
