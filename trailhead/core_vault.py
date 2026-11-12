"""Sync the bundled core-vault source folder into trailhead's own copy and
register that copy with lore as the read-only, trusted vault ``core``.

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

``install_core_vault`` is the install step built on that sync. It asks lore
(``lore vault ls --json``, never lore's config file) what is registered, then:

* no ``core`` vault: sync the copy, then ``lore vault add core --scope read-only
  --trusted --path <copy>`` (``vault add`` indexes the copy itself);
* ``core`` owned by trailhead: sync the copy and, when it changed, ``lore
  reindex``. Ownership means ``core``'s path equals the copy's path (both
  ``Path.resolve()``d), its scope is ``read-only`` and it is not shared;
* any other ``core``, or any vault under another name whose resolved path equals
  or contains the copy (a writable alias could write into the trusted copy), or
  any vault whose resolved path is inside the copy (a swap would delete its records):
  decline, naming the clashing path, the reason and the remedy.

No-write guarantees: a decline, an unparseable or failed ``vault ls``, and a
vault entry whose path cannot be compared all happen before anything is written
(no copy directory, no ``vault add``, no ``reindex``, no marker). Install never
deletes or edits a registration.

Reindex marker: ``core-vault.reindex-pending`` sits beside the copy in
trailhead's state dir (outside the vault). It is written before a changed copy
is swapped in and removed only after ``lore reindex`` succeeds, or after a fresh
``vault add`` succeeds (it indexes the copy itself, so a stale marker needs no
separate reindex). A failed or interrupted refresh, or a failed ``vault add``,
leaves the marker, so the next run retries even when the copy is unchanged.

Failures (a non-zero lore call, bad JSON, a sync error) print a ``trailhead:``
warning naming the step, saying install otherwise succeeded and what to run; lore's
stderr is never echoed. The caller's exit code and later steps are unaffected.

Rollback: an older lore refuses a config carrying a read-only entry. Before
downgrading, run ``lore vault delete core`` with the current lore, then remove
the copy and the marker. On a machine an older lore already refuses, delete the
``core`` entry from lore's ``config.json`` by hand.

Update, uninstall and doctor do not handle the core vault here.

Standard library only; every path comes from ``trailhead.paths``.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
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


MARKER_NAME = "core-vault.reindex-pending"
_RETRY_HINT = "run `lore vault ls`, then re-run `trailhead install`"


@dataclass(frozen=True)
class CoreVaultOutcome:
    """What the core-vault step did: registered, up_to_date, refreshed, skipped, declined or failed."""

    outcome: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"outcome": self.outcome, "reason": self.reason}


def skipped_core_vault(*, lore_enabled: bool, file=None) -> CoreVaultOutcome:
    """Print and return the skip outcome; writes nothing. ``lore_enabled`` names the cause."""
    if lore_enabled:
        reason = "the lore CLI is unavailable"
    else:
        reason = "lore is turned off (--no-lore or deselected)"
    print(f"trailhead: skipped the core vault: {reason}", file=file)
    return CoreVaultOutcome("skipped", reason)


def _fail(step: str, detail: str, *, lore_bin: Path) -> CoreVaultOutcome:
    reason = f"{step} {detail}"
    print(
        f"trailhead: core vault: {reason}; install otherwise succeeded. "
        f"The core vault is not up to date: {_RETRY_HINT} (lore: {lore_bin}).",
        file=sys.stderr,
    )
    return CoreVaultOutcome("failed", reason)


def _decline(path: str, reason: str) -> CoreVaultOutcome:
    print(
        f"trailhead: core vault not registered: {reason} (path: {path}); nothing was changed. "
        "Rename or remove that vault, then re-run `trailhead install`.",
        file=sys.stderr,
    )
    return CoreVaultOutcome("declined", f"{reason} (path: {path})")


def _parse_vaults(text: str) -> list[tuple[dict, Path]] | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    vaults: list[tuple[dict, Path]] = []
    for entry in data:
        if not isinstance(entry, dict):
            return None
        path = entry.get("path")
        if not isinstance(entry.get("name"), str) or not isinstance(path, str) or not Path(path).is_absolute():
            return None
        try:
            vaults.append((entry, Path(path).resolve()))
        except (OSError, RuntimeError, ValueError):
            return None
    return vaults


def install_core_vault(
    *,
    source: Path,
    env: dict[str, str],
    lore_bin: Path,
    runner=None,
    file=None,
) -> CoreVaultOutcome:
    """Sync the core-vault copy and register or refresh it with lore; print one line.

    ``runner(args, **kw)`` returns a CompletedProcess-like object (default
    ``subprocess.run``). ``file`` receives the non-warning line (default stdout);
    warnings and declines always go to stderr. Never raises for a lore or sync problem.
    """
    run = runner if runner is not None else subprocess.run

    def lore(*args: str):
        return run([str(lore_bin), *args], env=env, capture_output=True, text=True)

    copy = core_copy_dir(env)
    marker = copy.parent / MARKER_NAME
    try:
        resolved_copy = copy.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return _fail("copy", f"location could not be resolved ({type(exc).__name__})", lore_bin=lore_bin)

    try:
        ls = lore("vault", "ls", "--json")
    except OSError as exc:
        return _fail("vault ls", f"could not run lore ({type(exc).__name__})", lore_bin=lore_bin)
    if ls.returncode != 0:
        return _fail("vault ls", f"failed (exit {ls.returncode})", lore_bin=lore_bin)
    vaults = _parse_vaults(ls.stdout or "")
    if vaults is None:
        return _fail("vault ls", "did not print the expected JSON", lore_bin=lore_bin)

    owned = False
    elsewhere: Path | None = None
    for entry, path in vaults:
        at_copy = path == resolved_copy
        if resolved_copy in path.parents:
            return _decline(str(path), f"a vault ({entry['name']}) is inside trailhead's copy")
        if entry["name"] == "core":
            if at_copy and entry.get("scope") == "read-only" and entry.get("shared") is False:
                owned = True
                continue
            if at_copy:
                return _decline(str(path), "a vault named core is at the copy but is not read-only and unshared")
            elsewhere = elsewhere or path
        elif at_copy or path in resolved_copy.parents:
            return _decline(str(path), f"another vault ({entry['name']}) is or contains trailhead's copy")
    if elsewhere is not None:
        return _decline(str(elsewhere), "a vault named core already exists and is not trailhead's copy")

    def write_marker() -> None:
        if owned:
            marker.write_text("reindex pending\n")

    try:
        changed = sync_core_copy(source, copy, before_swap=write_marker)
    except (CoreVaultSyncError, OSError) as exc:
        return _fail("copy", f"could not sync the core-vault copy ({type(exc).__name__})", lore_bin=lore_bin)

    if not owned:
        try:
            added = lore("vault", "add", "core", "--scope", "read-only", "--trusted", "--path", str(resolved_copy))
        except OSError as exc:
            return _fail("vault add", f"could not run lore ({type(exc).__name__})", lore_bin=lore_bin)
        if added.returncode != 0:
            return _fail("vault add", f"failed (exit {added.returncode})", lore_bin=lore_bin)
        try:
            marker.unlink(missing_ok=True)
        except OSError as exc:
            return _fail("reindex marker", f"could not be cleared ({type(exc).__name__})", lore_bin=lore_bin)
        print(f"trailhead: core vault registered as 'core' (read-only) at {copy}", file=file)
        for line in (added.stdout or "").splitlines():
            if line.startswith("Note:"):
                print(f"trailhead: core vault: {line}", file=file)
        return CoreVaultOutcome("registered", f"registered at {copy}")

    pending = marker.exists()
    if not (changed or pending):
        print(f"trailhead: core vault up to date ({copy})", file=file)
        return CoreVaultOutcome("up_to_date", "copy matches the source")
    try:
        reindexed = lore("reindex")
    except OSError as exc:
        return _fail("reindex", f"could not run lore ({type(exc).__name__})", lore_bin=lore_bin)
    if reindexed.returncode != 0:
        return _fail(
            "reindex", f"failed (exit {reindexed.returncode}); it is retried on the next install", lore_bin=lore_bin
        )
    try:
        marker.unlink(missing_ok=True)
    except OSError as exc:
        return _fail("reindex marker", f"could not be cleared ({type(exc).__name__})", lore_bin=lore_bin)
    reason = "copy refreshed and reindexed" if changed else "retried a pending reindex"
    print(f"trailhead: core vault refreshed: {reason} ({copy})", file=file)
    return CoreVaultOutcome("refreshed", reason)
