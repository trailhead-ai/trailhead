"""trailhead update — re-wire to the latest pinned manifest versions (Slice 5).

Pipeline:
  1. Acquire the advisory lock (R-8) — fail fast if already locked.
  2. Load the install manifest from the configured source (D-7 — uses
     config.registry, never a hardcoded upstream URL).
  3. Check each entry's rev against the last-known wired state
     (state_dir("trailhead")/update_state.json).  Entries whose revs are
     unchanged → skip (no-op for that repo).
  4. For entries with changed revs: verify-in-place or clone+verify (Slice 2).
  5. Re-wire the active capability set (Slice 3 wire.wire).
  6. Write the new update_state.json.
  7. Release the lock.
  8. Print the summary (restart note, newly-wired list if R-8 grew).

R-8 lock:
  An O_CREAT|O_EXCL file at state_dir("trailhead")/trailhead.lock guards
  against concurrent wire()/config-toggle/update.  The lock is always released
  in a finally block.  A pre-existing lock → named error + nonzero exit.

A-5 unreachable-source error:
  "trailhead: cannot reach update source
    source: <url>
  Check your connection, or confirm the source with `trailhead config registry`.
  To use a local copy, set a file:// source."

D-7: no hardcoded upstream URL in logic.  config.registry is the sole source.

A-9 hygiene:
  - errors → stderr; summary → stdout
  - nonzero exit on failure

Hermeticity (B-3):
  load_install_manifest, wire, verify_present_repo are imported at module level
  so tests can patch them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from trailhead.config import load_config, save_config
from trailhead.fetch import FetchError, verify_present_repo
from trailhead.manifest import InstallManifest, InstallManifestError, load_install_manifest
from trailhead.paths import ensure_dir, state_dir
from trailhead.presets import resolve as resolve_preset
from trailhead.wire import WireError, wire, _default_manifest_paths

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MANIFEST_PATH = Path(__file__).parent / "install_manifest.toml"
_REPO_ROOT = Path(__file__).parent.parent

_LOCK_FILENAME = "trailhead.lock"
_UPDATE_STATE_FILENAME = "update_state.json"


# ---------------------------------------------------------------------------
# Lock helpers (R-8)
# ---------------------------------------------------------------------------


class LockError(Exception):
    """Raised when the update lock is already held."""


def _acquire_lock(lock_path: Path) -> None:
    """Acquire an exclusive advisory lock at lock_path.

    Uses O_CREAT|O_EXCL so only one process can hold the lock at a time.
    Raises LockError if the file already exists (lock is held).
    """
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, f"locked by pid {os.getpid()}\n".encode())
        os.close(fd)
    except FileExistsError:
        raise LockError(
            f"trailhead: another trailhead operation is already running "
            f"(lock file exists at {lock_path}).\n"
            f"If no other process is running, remove the lock file and retry:\n"
            f"  rm {lock_path}"
        )


def _release_lock(lock_path: Path) -> None:
    """Release the advisory lock (delete the file)."""
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Update state persistence
# ---------------------------------------------------------------------------


def _load_update_state(state_path: Path) -> dict[str, str]:
    """Load the last-known wired revs from update_state.json."""
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text())
        return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_update_state(state_path: Path, revs: dict[str, str]) -> None:
    """Write the current wired revs to update_state.json."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(revs))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_update(
    *,
    env: dict[str, str] | None = None,
) -> int:
    """Execute the update pipeline. Returns an int exit code.

    Args:
        env:  Env dict for path resolution (hermeticity).
    """
    _env = env if env is not None else {}

    # Resolve state dir
    try:
        _state_dir = state_dir("trailhead", env=_env)
        ensure_dir(_state_dir)
    except Exception as exc:
        print(f"trailhead: cannot access state dir: {exc}", file=sys.stderr)
        return 1

    lock_path = _state_dir / _LOCK_FILENAME
    state_file = _state_dir / _UPDATE_STATE_FILENAME

    # ----------------------------------------------------------------
    # Step 1: Acquire lock (R-8)
    # ----------------------------------------------------------------
    try:
        _acquire_lock(lock_path)
    except LockError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        return _run_update_locked(
            _env=_env,
            state_file=state_file,
            lock_path=lock_path,
        )
    finally:
        _release_lock(lock_path)


def _run_update_locked(
    *,
    _env: dict[str, str],
    state_file: Path,
    lock_path: Path,
) -> int:
    """Execute update logic while holding the lock."""
    # ----------------------------------------------------------------
    # Step 2: Load config and install manifest (D-7 — registry from config)
    # ----------------------------------------------------------------
    cfg = load_config(env=_env)

    try:
        manifest = load_install_manifest(
            _MANIFEST_PATH,
            cfg.registry,
            local_root=_REPO_ROOT,
        )
    except (InstallManifestError, FetchError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"trailhead: failed to load install manifest: {exc}", file=sys.stderr)
        return 1

    # ----------------------------------------------------------------
    # Step 3: Compare revs against last-known state
    # ----------------------------------------------------------------
    last_state = _load_update_state(state_file)
    changed_entries = []
    unchanged_entries = []

    for entry in manifest.repos:
        last_rev = last_state.get(entry.name)
        if last_rev == entry.rev:
            unchanged_entries.append(entry)
        else:
            changed_entries.append(entry)

    if not changed_entries:
        print("trailhead update: already up-to-date (nothing to update)")
        return 0

    # ----------------------------------------------------------------
    # Step 4: Verify changed repos in place (already-present-repo case)
    # ----------------------------------------------------------------
    for entry in changed_entries:
        try:
            verify_present_repo(entry, repo_path=_REPO_ROOT)
        except FetchError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    # ----------------------------------------------------------------
    # Step 5: Re-wire the active capability set
    # ----------------------------------------------------------------
    # Determine what to wire — use config's active capabilities
    selection: dict[str, set[str]] = {}
    for tool, caps in cfg.capabilities.items():
        selection[tool] = set(caps)

    if not selection:
        # No capabilities configured — nothing to wire
        print("trailhead update: no capabilities configured; run `trailhead install` first")
        return 0

    # Track what was previously wired vs. what will be wired (R-8 newly-wired)
    prev_wired = set(cfg.capabilities.keys())

    manifest_paths = _default_manifest_paths()
    try:
        wire(selection, manifest_paths=manifest_paths, env=_env)
    except WireError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"trailhead: wire failed: {exc}", file=sys.stderr)
        return 1

    # ----------------------------------------------------------------
    # Step 6: Save updated state
    # ----------------------------------------------------------------
    new_state = {entry.name: entry.rev for entry in manifest.repos}
    _save_update_state(state_file, new_state)

    # ----------------------------------------------------------------
    # Step 7: Print summary
    # ----------------------------------------------------------------
    newly_wired = sorted(set(selection.keys()) - prev_wired)
    _print_update_summary(
        changed_entries=changed_entries,
        selection=selection,
        newly_wired=newly_wired,
    )

    return 0


def _print_update_summary(
    *,
    changed_entries: list,
    selection: dict[str, set[str]],
    newly_wired: list[str],
) -> None:
    """Print the update summary to stdout."""
    print("trailhead update: complete")
    print("")

    for entry in changed_entries:
        print(f"  updated: {entry.name}@{entry.rev[:8]}")

    # R-8: newly wired summary
    if newly_wired:
        print("")
        print(f"newly wired: {', '.join(newly_wired)}")

    print("")
    print("wired:")
    for tool, caps in sorted(selection.items()):
        cap_str = ", ".join(sorted(caps)) if caps else "base"
        print(f"  {tool} ({cap_str})")

    print("")
    print("start a fresh Claude Code session to apply the updated tools")
