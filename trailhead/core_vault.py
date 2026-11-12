"""Sync the bundled core-vault source folder into trailhead's own copy and
register that copy with lore as the read-only, trusted vault ``core``.

Contract:

* The repo's ``core-vault/`` folder is the source: hand-edited, reviewed like
  source, never written by lore. Install copies it into a directory trailhead
  owns (``core_copy_dir``); nothing else writes there.
* ``sync_core_copy`` makes ``dest`` hold exactly the source's regular files,
  compared by relative path and bytes. Dot-files and dot-directories in the source
  (such as ``.DS_Store``) are ignored and never copied. The copy is compared strictly:
  a dot-name, symlink, non-regular entry, empty directory or any other entry the
  source would not produce makes it differ, so it is rebuilt.
* When ``dest`` already matches, nothing is written and False is returned.
  Otherwise the tree is built in a uniquely named staging sibling and swapped
  in by rename, so ``dest`` is never half-written, and True is returned.
* Leftover staging/backup siblings from a crashed run are swept before a
  changing sync starts; one that is a file or symlink is unlinked (a symlink is
  never followed).
* ``before_swap``, when given, is called exactly once, after staging is fully
  built and before the swap, and only when ``dest`` is going to change.
* Symlinks and other non-regular source entries (checked on the opened file
  descriptor, never following a symlink), a missing source, and a ``dest`` that is
  a symlink or not a directory raise ``CoreVaultSyncError`` before anything is
  written.

``install_core_vault`` is the install step built on that sync. It asks lore
(``lore vault ls --json``, never lore's config file) what is registered, then:

* no ``core`` vault: sync the copy, then ``lore vault add core --scope read-only
  --trusted --path <copy>`` (``vault add`` indexes the copy itself);
* ``core`` owned by trailhead: sync the copy and, when it changed, ``lore
  reindex``. Ownership means ``core``'s path equals the copy's path (both
  resolved), its scope is ``read-only`` and it is not shared;
* any other ``core``, or any vault under another name whose resolved path equals
  or contains the copy (a writable alias could write into the trusted copy), or
  any vault whose resolved path is inside the copy (a swap would delete its records):
  decline, naming the clashing path, the reason and the remedy.

No-write guarantees: a decline, an unparseable or failed ``vault ls``, and a
vault entry whose path cannot be compared all happen before anything is written
(no copy directory, no ``vault add``, no ``reindex``, no marker). Install never
deletes or edits a registration.

Reindex marker: ``core-vault.reindex-pending`` sits beside the copy in
trailhead's state dir (outside the vault). It is written (as a fresh regular file; a
symlink or file already there is replaced, never followed) before a changed copy
is swapped in and removed only after ``lore reindex`` succeeds, or after a fresh
``vault add`` succeeds (it indexes the copy itself, so a stale marker needs no
separate reindex). A failed or interrupted refresh, or a failed ``vault add``,
leaves the marker, so the next run retries even when the copy is unchanged.

Symlink loops are detected explicitly (strict resolution), so Python 3.11, 3.12
and 3.13 agree: a loop in the copy's location fails the ``copy`` step before any
lore call, and a loop (or NUL byte) in a registered vault's path fails the ``vault
ls`` step with its own reason, before anything is written. A path that merely does
not exist yet is still comparable. A copy location that cannot be computed (a
relative ``TRAILHEAD_STATE_DIR``) fails the ``copy`` step the same way.

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
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from trailhead.capabilities import ManifestError, load_manifest
from trailhead.paths import PathResolutionError, state_dir
from trailhead.wire import default_manifest_paths


class CoreVaultSyncError(Exception):
    """The core-vault copy could not be synced safely."""


def core_copy_dir(env: dict[str, str] | None = None) -> Path:
    """Return the directory trailhead owns for the core-vault copy."""
    return state_dir("trailhead", env=env) / "core-vault"


def _read_regular(path: Path, *, label: str, fstat: Callable[[int], os.stat_result]) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(fstat(fd).st_mode):
            raise CoreVaultSyncError(f"{label} contains a non-regular entry: {path}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(fd)


def _read_tree(
    root: Path,
    *,
    label: str,
    fstat: Callable[[int], os.stat_result] = os.fstat,
    strict: bool = False,
) -> dict[str, bytes]:
    """Read ``root``'s regular files by relative path, never following a symlink.

    Non-strict (the source) skips dot-names. Strict (the copy) skips nothing and
    refuses an empty directory, so any entry the source would not have produced
    raises ``CoreVaultSyncError``.
    """
    files: dict[str, bytes] = {}

    def walk(directory: Path) -> bool:
        has_file = False
        for entry in sorted(directory.iterdir()):
            if not strict and entry.name.startswith("."):
                continue
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise CoreVaultSyncError(f"{label} contains a symlink: {entry}")
            if stat.S_ISDIR(mode):
                if not walk(entry) and strict:
                    raise CoreVaultSyncError(f"{label} contains an empty directory: {entry}")
                has_file = True
            elif stat.S_ISREG(mode):
                files[entry.relative_to(root).as_posix()] = _read_regular(entry, label=label, fstat=fstat)
                has_file = True
            else:
                raise CoreVaultSyncError(f"{label} contains a non-regular entry: {entry}")
        return has_file

    walk(root)
    return files


def _write_marker(marker: Path) -> None:
    """Write the marker as a fresh regular file; a link or file already there is replaced, never followed."""
    marker.unlink(missing_ok=True)
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o666)
    with os.fdopen(fd, "w") as handle:
        handle.write("reindex pending\n")


def _remove_leftover(path: Path) -> None:
    if _is_real_dir(path):
        shutil.rmtree(path)
    else:
        path.unlink()


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
    rename: Callable[[Path, Path], None] = os.rename,
    fstat: Callable[[int], os.stat_result] = os.fstat,
) -> bool:
    """Make ``dest`` mirror ``source``'s regular files; return whether it changed.

    ``rename`` performs the swap renames and ``fstat`` checks each opened file; tests inject them.
    """
    source = Path(source)
    dest = Path(dest)

    if not source.exists() and not source.is_symlink():
        raise CoreVaultSyncError(f"core-vault source is missing: {source}")
    if not _is_real_dir(source):
        raise CoreVaultSyncError(f"core-vault source is not a directory: {source}")
    wanted = _read_tree(source, label="core-vault source", fstat=fstat)

    dest_exists = os.path.lexists(dest)
    if dest_exists and not _is_real_dir(dest):
        raise CoreVaultSyncError(f"core-vault dest is a symlink or not a directory: {dest}")
    if dest_exists:
        try:
            if _read_tree(dest, label="core-vault dest", fstat=fstat, strict=True) == wanted:
                return False
        except CoreVaultSyncError:
            pass

    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in _leftovers(dest):
        _remove_leftover(stale)

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
            rename(dest, old)
        try:
            rename(staging, dest)
        except BaseException:
            if old is not None:
                rename(old, dest)
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


class _UnresolvablePath(Exception):
    """A path could not be resolved (symlink loop, NUL byte, OS error)."""


def _resolve(path: Path) -> Path:
    """Resolve ``path`` the same way on every Python version.

    Strict resolution detects a symlink loop explicitly (3.13's non-strict
    ``resolve`` returns a looping path unchanged instead of raising). A path that
    does not exist yet, or sits under a file, resolves non-strictly as before.
    """
    try:
        return path.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        pass
    except (OSError, RuntimeError, ValueError) as exc:
        raise _UnresolvablePath(type(exc).__name__) from exc
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise _UnresolvablePath(type(exc).__name__) from exc


def _parse_vaults(text: str) -> list[tuple[dict, Path]] | None:
    """Return the ``(entry, resolved path)`` pairs, None for malformed JSON.

    Raises ``_UnresolvablePath`` for an entry whose path cannot be resolved.
    """
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
        vaults.append((entry, _resolve(Path(path))))
    return vaults


@dataclass(frozen=True)
class CoreOwnership:
    """The answer of ``core_ownership``.

    ``state`` is ``owned``, ``missing`` (no vault named core, nothing in the way) or
    ``declined``. A declined answer carries ``reason`` and the clashing vault's
    resolved ``path``; the reason names the clash, the path and remedy are the
    caller's to add.
    """

    state: str
    reason: str = ""
    path: Path | None = None


def core_ownership(vaults: list[tuple[dict, Path]], resolved_copy: Path) -> CoreOwnership:
    """Decide whether the vault named ``core`` is trailhead's own copy.

    ``vaults`` is ``_parse_vaults`` output. Owned means exactly one vault named
    ``core``, at the copy, read-only and unshared, with no other vault at, containing
    or inside the copy. Entries are judged in listing order and the first clash
    decides; a clash found after the whole listing is read (a ``core`` elsewhere, or
    a second ``core`` at the copy) is judged last.
    """
    owned = 0
    elsewhere: Path | None = None
    for entry, path in vaults:
        at_copy = path == resolved_copy
        if resolved_copy in path.parents:
            return CoreOwnership("declined", f"a vault ({entry['name']}) is inside trailhead's copy", path)
        if entry["name"] == "core":
            if at_copy and entry.get("scope") == "read-only" and entry.get("shared") is False:
                owned += 1
                continue
            if at_copy:
                return CoreOwnership(
                    "declined", "a vault named core is at the copy but is not read-only and unshared", path
                )
            elsewhere = elsewhere or path
        elif at_copy or path in resolved_copy.parents:
            return CoreOwnership(
                "declined", f"another vault ({entry['name']}) is or contains trailhead's copy", path
            )
    if elsewhere is not None:
        return CoreOwnership("declined", "a vault named core already exists and is not trailhead's copy", elsewhere)
    if owned > 1:
        return CoreOwnership("declined", "more than one vault named core is registered", resolved_copy)
    return CoreOwnership("owned" if owned else "missing")


def install_core_vault(
    *,
    source: Path,
    env: dict[str, str],
    lore_bin: Path,
    runner=None,
    file=None,
    rename: Callable[[Path, Path], None] = os.rename,
) -> CoreVaultOutcome:
    """Sync the core-vault copy and register or refresh it with lore; print one line.

    ``runner(args, **kw)`` returns a CompletedProcess-like object (default
    ``subprocess.run``). ``file`` receives the non-warning line (default stdout);
    warnings and declines always go to stderr. ``rename`` is passed to the copy sync
    (tests inject a failing swap). Never raises for a lore or sync problem.
    """
    run = runner if runner is not None else subprocess.run

    def lore(*args: str):
        return run([str(lore_bin), *args], env=env, capture_output=True, text=True)

    try:
        copy = core_copy_dir(env)
        resolved_copy = _resolve(copy)
    except PathResolutionError as exc:
        return _fail("copy", f"location could not be resolved ({type(exc).__name__})", lore_bin=lore_bin)
    except _UnresolvablePath as exc:
        return _fail("copy", f"location could not be resolved ({exc})", lore_bin=lore_bin)
    marker = copy.parent / MARKER_NAME

    try:
        ls = lore("vault", "ls", "--json")
    except OSError as exc:
        return _fail("vault ls", f"could not run lore ({type(exc).__name__})", lore_bin=lore_bin)
    if ls.returncode != 0:
        return _fail("vault ls", f"failed (exit {ls.returncode})", lore_bin=lore_bin)
    try:
        vaults = _parse_vaults(ls.stdout or "")
    except _UnresolvablePath as exc:
        return _fail(
            "vault ls", f"listed a registered vault whose path could not be resolved ({exc})", lore_bin=lore_bin
        )
    if vaults is None:
        return _fail("vault ls", "did not print the expected JSON", lore_bin=lore_bin)

    ownership = core_ownership(vaults, resolved_copy)
    if ownership.state == "declined":
        return _decline(str(ownership.path), ownership.reason)
    owned = ownership.state == "owned"

    def write_marker() -> None:
        if owned:
            _write_marker(marker)

    try:
        changed = sync_core_copy(source, copy, before_swap=write_marker, rename=rename)
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


LORE_TIMEOUT_SECONDS = 30
_RECORD_ID = re.compile(r"[a-z][a-z0-9-]*/[a-z0-9][a-z0-9-]*")


def resolve_lore_bin(manifest_paths: dict[str, Path] | None = None) -> Path | None:
    """The lore binary named by lore's manifest ``cli_bin``, or None.

    None when there is no lore manifest, it cannot be loaded, it declares no
    ``cli_bin`` (lore is disabled), or the binary is not on disk (``load_manifest`` refuses a
    ``cli_bin`` that does not exist). Independent of install's resolved config.
    """
    paths = default_manifest_paths() if manifest_paths is None else manifest_paths
    manifest_path = paths.get("lore")
    if manifest_path is None:
        return None
    try:
        manifest = load_manifest(manifest_path)
    except (ManifestError, OSError):
        return None
    if manifest.cli_bin is None:
        return None
    return manifest.plugin_root / manifest.cli_bin


@dataclass(frozen=True)
class ShowResult:
    """Exit code and the two streams ``trailhead core show`` prints."""

    code: int
    stdout: str = ""
    stderr: str = ""


def _plain(text: str) -> str:
    return "".join(c if c.isprintable() else "?" for c in text)


def _unavailable(reason: str) -> ShowResult:
    return ShowResult(3, stderr=f"trailhead: core vault unavailable: {_plain(reason)}\n")


def core_show(
    record_id: str,
    *,
    env: dict[str, str],
    lore_bin: Path | None,
    runner=None,
) -> ShowResult:
    """Read ``record_id`` from the vault named core, only when that is trailhead's own copy.

    Exit codes: 0 the record (lore's stdout); 1 owned but lore failed (lore's stderr);
    2 the id was refused before any lore call; 3 the core vault is unavailable (one
    stderr line, empty stdout). Both lore calls run under ``LORE_TIMEOUT_SECONDS``.
    """
    if _RECORD_ID.fullmatch(record_id) is None:
        return ShowResult(2, stderr=f"trailhead: invalid record id {_plain(record_id)!r}: expected <kind>/<name>\n")
    if lore_bin is None:
        return _unavailable("the lore CLI is not available; run `trailhead install`")
    run = runner if runner is not None else subprocess.run

    def lore(*args: str):
        return run([str(lore_bin), *args], env=env, capture_output=True, text=True, timeout=LORE_TIMEOUT_SECONDS)

    try:
        resolved_copy = _resolve(core_copy_dir(env))
    except (PathResolutionError, _UnresolvablePath) as exc:
        return _unavailable(f"trailhead's copy location could not be resolved ({type(exc).__name__})")
    try:
        ls = lore("vault", "ls", "--json")
    except subprocess.TimeoutExpired:
        return _unavailable(f"`lore vault ls` timed out after {LORE_TIMEOUT_SECONDS}s")
    except OSError as exc:
        return _unavailable(f"could not run lore ({type(exc).__name__})")
    if ls.returncode != 0:
        return _unavailable(f"`lore vault ls` failed (exit {ls.returncode})")
    try:
        vaults = _parse_vaults(ls.stdout or "")
    except _UnresolvablePath as exc:
        return _unavailable(f"`lore vault ls` listed a vault whose path could not be resolved ({exc})")
    if vaults is None:
        return _unavailable("`lore vault ls` did not print the expected JSON")
    ownership = core_ownership(vaults, resolved_copy)
    if ownership.state == "missing":
        return _unavailable("no vault named core is registered; run `trailhead install`")
    if ownership.state == "declined":
        return _unavailable(
            f"{ownership.reason} (path: {ownership.path}); rename or remove that vault, then re-run `trailhead install`"
        )
    try:
        shown = lore("record", "show", record_id, "--vault", "core")
    except subprocess.TimeoutExpired:
        return ShowResult(1, stderr=f"trailhead: `lore record show` timed out after {LORE_TIMEOUT_SECONDS}s\n")
    except OSError as exc:
        return ShowResult(1, stderr=f"trailhead: could not run lore ({type(exc).__name__})\n")
    if shown.returncode != 0:
        return ShowResult(1, stderr=shown.stderr or "")
    return ShowResult(0, stdout=shown.stdout or "")
