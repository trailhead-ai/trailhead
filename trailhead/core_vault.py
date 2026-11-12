"""Sync the bundled core-vault source folder into trailhead's own copy.

Contract:

* The repo's ``core-vault/`` folder is the source: hand-edited, reviewed like
  source, never written by lore. Install copies it into a directory trailhead
  owns (``core_copy_dir``); nothing else writes there.
* ``sync_core_copy`` makes ``dest`` hold exactly the source's regular files,
  compared by relative path and bytes. Dot-files and dot-directories (such as
  ``.DS_Store``) are ignored on both sides of the compare and in the copy.
* When ``dest`` already matches, nothing is written and False is returned.
  Otherwise the tree is built in a uniquely named staging sibling and swapped
  in by rename, so ``dest`` is never half-written, and True is returned.
* Leftover staging/backup siblings from a crashed run are swept before a
  changing sync starts.
* ``before_swap``, when given, is called exactly once, after staging is fully
  built and before the swap, and only when ``dest`` is going to change.
* Symlinks and other non-regular source entries, a missing source, a ``dest``
  that is a symlink or not a directory, and a staging-named sibling that is a
  symlink or not a directory all raise ``CoreVaultSyncError`` before anything
  is written.

Standard library only; every path comes from ``trailhead.paths``.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

from trailhead.paths import state_dir


class CoreVaultSyncError(Exception):
    """The core-vault copy could not be synced safely."""


def core_copy_dir(env: dict[str, str] | None = None) -> Path:
    """Return the directory trailhead owns for the core-vault copy."""
    return state_dir("trailhead", env=env) / "core-vault"


def _read_tree(root: Path, *, label: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            if entry.name.startswith("."):
                continue
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise CoreVaultSyncError(f"{label} contains a symlink: {entry}")
            if stat.S_ISDIR(mode):
                walk(entry)
            elif stat.S_ISREG(mode):
                files[entry.relative_to(root).as_posix()] = entry.read_bytes()
            else:
                raise CoreVaultSyncError(f"{label} contains a non-regular entry: {entry}")

    walk(root)
    return files


def _is_real_dir(path: Path) -> bool:
    return stat.S_ISDIR(path.lstat().st_mode)


def _leftovers(dest: Path) -> list[Path]:
    if not dest.parent.exists():
        return []
    prefixes = (f".{dest.name}.staging-", f".{dest.name}.old-")
    return [p for p in sorted(dest.parent.iterdir()) if p.name.startswith(prefixes)]


def sync_core_copy(
    source: Path,
    dest: Path,
    *,
    before_swap: Callable[[], None] | None = None,
) -> bool:
    """Make ``dest`` mirror ``source``'s regular files; return whether it changed."""
    source = Path(source)
    dest = Path(dest)

    if not source.exists() and not source.is_symlink():
        raise CoreVaultSyncError(f"core-vault source is missing: {source}")
    if not _is_real_dir(source):
        raise CoreVaultSyncError(f"core-vault source is not a directory: {source}")
    wanted = _read_tree(source, label="core-vault source")

    dest_exists = os.path.lexists(dest)
    if dest_exists and not _is_real_dir(dest):
        raise CoreVaultSyncError(f"core-vault dest is a symlink or not a directory: {dest}")
    if dest_exists:
        try:
            if _read_tree(dest, label="core-vault dest") == wanted:
                return False
        except CoreVaultSyncError:
            pass

    leftovers = _leftovers(dest)
    for stale in leftovers:
        if not _is_real_dir(stale):
            raise CoreVaultSyncError(f"core-vault staging path is a symlink or not a directory: {stale}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in leftovers:
        shutil.rmtree(stale)

    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.staging-", dir=dest.parent))
    old: Path | None = None
    try:
        for rel, data in wanted.items():
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        if before_swap is not None:
            before_swap()

        if dest_exists:
            old = Path(tempfile.mkdtemp(prefix=f".{dest.name}.old-", dir=dest.parent))
            old.rmdir()
            os.rename(dest, old)
        try:
            os.rename(staging, dest)
        except BaseException:
            if old is not None:
                os.rename(old, dest)
                old = None
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if old is not None and old.exists():
            shutil.rmtree(old, ignore_errors=True)
    return True
