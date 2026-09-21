"""``lore publish`` — the per-vault debounced, single-flight publish worker.

A record write does not want to wait on a commit-and-push round trip, and a
burst of writes should not spawn a commit-and-push per write. This module is
the worker a write's trigger spawns detached (the spawn itself, and the
request-stamp writer that decides WHEN to spawn it, belong to a later task —
see :func:`touch_request_stamp` and the path helpers below, which that task
imports rather than reimplementing).

**Single-flight, not mutual exclusion with the vault write lock.** The worker
takes its OWN lock — a plain, non-blocking ``fcntl.flock`` on a file under
``state_dir("lore")/publish/``, keyed exactly like the resolve-session and
failed-vault markers (:func:`common.machine_state_key`) — distinct from
``locking.vault_write_lock``'s ``.lore.lock`` inside the vault itself. A second
worker for the SAME vault that finds this lock held exits 0 immediately,
having done nothing; :func:`lore.cli.sync.cmd_sync` still takes the vault lock
itself, later, for its own critical section.

**Debounce, not delay.** The worker waits until the vault's *request stamp*
(:func:`touch_request_stamp`) has been unchanged for the quiet window, so a
burst of writes collapses into the one sync that runs after the burst ends,
not one sync per write. The stamp's value is a wall-clock timestamp written
into the stamp file's CONTENT, not read off the file's mtime — mtime is real
OS time with second-scale granularity on some filesystems, and the whole point
of the injected clock/sleeper (see :func:`cmd_publish`) is that no test here
ever sleeps for real.

**The sync itself is one in-process call to** :func:`lore.cli.sync.cmd_sync`,
the same bare-namespace shape ``cli.flush``'s tail already uses. Its outcome
is read back off ``--json``'s printed report (there is no other channel a
bare-namespace, in-process call exposes) — see :func:`_default_sync_call`.
That default is a seam (``args.sync_call``), alongside the injected clock and
sleeper, so this module's OWN bookkeeping (round bounding, marker writes,
lock/log handling) can be tested without paying for a real git round trip on
every case — ``cmd_sync``'s own behavior has its own suite. Production wiring
(:func:`add_publish_subparser`) never sets it, so the real path always runs
the genuine sync.
"""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from ..vault import layers as layers_mod
from .common import _resolve_all_vaults, _resolve_lore_state_dir, machine_state_key

#: Marker/lock/log/stamp directory under ``state_dir("lore")``.
PUBLISH_DIRNAME = "publish"

#: Default debounce window, in seconds, when ``--quiet-for`` is not given.
DEFAULT_QUIET_FOR = 5.0

#: Bound on retry rounds — a stamp that keeps refreshing, or a sync that keeps
#: reporting ``in-progress``, cannot pin the worker open forever. The sweep is
#: the floor that eventually catches whatever this bound gives up on.
MAX_ROUNDS = 3

#: The marker's outcome while the worker is between "entered" and "exited".
OUTCOME_RUNNING = "running"

#: Terminal outcome when the round bound was hit without a determinate ending.
OUTCOME_ROUNDS_EXHAUSTED = "rounds-exhausted"

#: Terminal outcome when the worker itself raised, rather than the sync loop
#: reporting a determinate (if unhappy) ending.
OUTCOME_ERROR = "error"

#: Outcomes literal enough that nothing is left to observe — the marker is
#: cleared rather than left recording a success nobody needs to see.
_CLEARED_OUTCOMES = frozenset({"published", "converged"})

#: The sync loop's own contended-vault outcome literal (``sync.SYNC_IN_PROGRESS``,
#: duplicated here as a plain string rather than imported — importing ``sync``
#: at module load time would pull the whole sync module into every publish-arg
#: parse; the string is stable, closed CLI-facing vocabulary).
_SYNC_IN_PROGRESS_OUTCOME = "in-progress"


def publish_state_root() -> Path:
    """Return ``state_dir("lore")/publish`` — the marker/lock/log/stamp directory."""
    return _resolve_lore_state_dir() / PUBLISH_DIRNAME


def _suffixed_path(vault_root: "str | Path", suffix: str) -> Path:
    """Return ``<publish root>/<machine_state_key><suffix>``, confined to that root.

    The one place every publish-worker file (lock, marker, log, request stamp)
    derives its path, mirroring ``cli.resolve_state._suffixed_marker_path``.

    Raises:
        layers_mod.LayerConfinementError: if the path escapes the publish root.
    """
    root = publish_state_root()
    candidate = root / f"{machine_state_key(vault_root)}{suffix}"
    layers_mod.assert_within_root(candidate, root)
    return candidate


def lock_path(vault_root: "str | Path") -> Path:
    """Return the worker's own single-flight lock file for *vault_root*."""
    return _suffixed_path(vault_root, ".lock")


def marker_path(vault_root: "str | Path") -> Path:
    """Return the machine-local publish marker path for *vault_root*."""
    return _suffixed_path(vault_root, ".json")


def log_path(vault_root: "str | Path") -> Path:
    """Return the worker's per-vault stdout/stderr log path for *vault_root*."""
    return _suffixed_path(vault_root, ".log")


def request_stamp_path(vault_root: "str | Path") -> Path:
    """Return the vault's publish-request stamp path."""
    return _suffixed_path(vault_root, ".request")


def touch_request_stamp(vault_root: "str | Path", *, clock=time.time) -> None:
    """Record that *vault_root* has a pending publish request, now.

    Called by a record create, a record update, or a flush after a successful
    write (the next task), before it spawns the worker detached. Idempotent:
    a burst of writes just keeps moving this stamp forward, which is exactly
    what lets the worker's debounce collapse the burst into one sync.
    """
    path = request_stamp_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(repr(clock()), encoding="utf-8")


def _read_request_stamp(vault_root: "str | Path") -> "float | None":
    """Return the vault's request-stamp timestamp, or None if absent/unreadable."""
    try:
        return float(request_stamp_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_marker(vault_root: "str | Path") -> "dict | None":
    """Return the publish marker for *vault_root*, or None if absent/unreadable."""
    try:
        return json.loads(marker_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_marker(vault_root: "str | Path", marker: dict) -> dict:
    path = marker_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return marker


def _clear_marker(vault_root: "str | Path") -> bool:
    try:
        marker_path(vault_root).unlink()
        return True
    except OSError:
        return False


def _resolve_vault_path(vault_name: str) -> "Path | None":
    """Return the configured path for *vault_name*, or None if unknown."""
    from ..vault import config as vault_config_mod

    targets, error = _resolve_all_vaults()
    if error is not None:
        return None
    wanted = vault_config_mod.normalize_vault_name(vault_name)
    for name, path in targets:
        if name == wanted:
            return Path(path)
    return None


def _default_sync_call(vault_name: str) -> "tuple[int, str | None]":
    """Run ``cmd_sync`` in-process for one vault; return ``(rc, outcome)``.

    ``outcome`` is read back off the ``--json`` report ``cmd_sync`` prints for
    itself — the only channel a bare-namespace, in-process call exposes for
    which of ``sync.SYNC_OUTCOMES`` this vault landed on. ``None`` when the
    vault never reached a determinate outcome this run (e.g. a hard failure
    ``cmd_sync`` never assigns one to — see its own ``--json`` docstring).
    """
    from . import sync as sync_mod

    out_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf):
        rc = sync_mod.cmd_sync(
            SimpleNamespace(vault=vault_name, message=None, pull_only=False, json=True)
        )
    text = out_buf.getvalue()
    sys.stdout.write(text)

    lines = text.splitlines()
    start = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i] == "{":
            start = i
            break
    if start is None:
        return rc, None
    try:
        payload = json.loads("\n".join(lines[start:]))
    except json.JSONDecodeError:
        return rc, None
    for entry in payload.get("vaults", []):
        if entry.get("vault") == vault_name:
            return rc, entry.get("outcome")
    return rc, None


def _quiet_wait(vault_root: "str | Path", *, quiet_for: float, clock, sleeper) -> None:
    """Block (via *sleeper*) until the request stamp has been quiet for *quiet_for*.

    Re-reads the stamp on every iteration, so a refresh that lands mid-wait
    (a second write arriving before the window elapses) extends the wait
    relative to ITS timestamp, not the original one — no separate "was it
    refreshed" bookkeeping is needed, the reread already gives the right
    answer. Returns immediately if there is no stamp at all (nothing to wait
    on for a vault with no pending request).
    """
    while True:
        stamp = _read_request_stamp(vault_root)
        if stamp is None:
            return
        remaining = quiet_for - (clock() - stamp)
        if remaining <= 0:
            return
        sleeper(remaining)


def _run_rounds(
    vault_root: "str | Path", vault_name: str, *, quiet_for: float, clock, sleeper, sync_call
) -> "tuple[int, str]":
    """Debounce-then-sync, retrying up to :data:`MAX_ROUNDS` times.

    A round that reports the sync loop's own contention outcome
    (``in-progress``), or whose request stamp moved during the sync, is not
    terminal — another write landed in the window and deserves its own pass.
    Anything else (``published``, ``converged``, ``holding``,
    ``awaiting-person``, ``retries-exhausted``, ...) ends the loop.
    """
    last_rc = 0
    for _round in range(MAX_ROUNDS):
        _quiet_wait(vault_root, quiet_for=quiet_for, clock=clock, sleeper=sleeper)
        stamp_before = _read_request_stamp(vault_root)
        rc, outcome = sync_call(vault_name)
        last_rc = rc
        stamp_after = _read_request_stamp(vault_root)
        if outcome == _SYNC_IN_PROGRESS_OUTCOME or stamp_after != stamp_before:
            continue
        return rc, (outcome or OUTCOME_ERROR)
    return last_rc, OUTCOME_ROUNDS_EXHAUSTED


def _run_publish(
    vault_root: Path, vault_name: str, *, quiet_for: float, clock, sleeper, sync_call
) -> int:
    """Write the entry marker, run the debounced sync loop under a per-vault
    log, and rewrite the marker with the terminal outcome."""
    marker = {
        "requested-at": _read_request_stamp(vault_root),
        "started-at": clock(),
        "pid": os.getpid(),
        "outcome": OUTCOME_RUNNING,
    }
    _write_marker(vault_root, marker)

    lp = log_path(vault_root)
    lp.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(lp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(log_fd, "w") as log_file:
            with contextlib.redirect_stdout(log_file), contextlib.redirect_stderr(log_file):
                rc, outcome = _run_rounds(
                    vault_root, vault_name,
                    quiet_for=quiet_for, clock=clock, sleeper=sleeper, sync_call=sync_call,
                )
    except Exception:
        marker["outcome"] = OUTCOME_ERROR
        _write_marker(vault_root, marker)
        raise

    if outcome in _CLEARED_OUTCOMES:
        _clear_marker(vault_root)
    else:
        marker["outcome"] = outcome
        _write_marker(vault_root, marker)
    return rc


def cmd_publish(args) -> int:
    """Take this vault's publish lock (non-blocking, single-flight) and run
    the debounced sync loop, or exit 0 immediately if another worker holds it.

    ``clock``, ``sleeper``, and ``sync_call`` are read off *args* with
    ``getattr`` and default to real wall-clock time, real ``time.sleep``, and
    :func:`_default_sync_call` — the CLI wiring never sets them; tests do.
    """
    vault_name = args.vault
    quiet_for = float(getattr(args, "quiet_for", None) or DEFAULT_QUIET_FOR)
    clock = getattr(args, "clock", None) or time.time
    sleeper = getattr(args, "sleeper", None) or time.sleep
    sync_call = getattr(args, "sync_call", None) or _default_sync_call

    vault_path = _resolve_vault_path(vault_name)
    if vault_path is None:
        print(f"error: unknown vault: {vault_name!r}", file=sys.stderr)
        return 1

    lp = lock_path(vault_path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lp, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        print(f"publish already in progress for vault {vault_name!r}", file=sys.stderr)
        return 0

    try:
        return _run_publish(
            vault_path, vault_name,
            quiet_for=quiet_for, clock=clock, sleeper=sleeper, sync_call=sync_call,
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def add_publish_subparser(sub) -> None:
    """Register the ``publish`` command parser."""
    p_publish = sub.add_parser(
        "publish",
        help="Debounced, single-flight commit-and-publish worker for one vault",
        description=(
            "Wait for the vault's publish requests to go quiet, then run the "
            "same commit/pull/push loop `lore sync` runs, for that one vault. "
            "Exits 0 immediately, doing nothing, if another publish worker for "
            "this vault is already running."
        ),
    )
    p_publish.add_argument(
        "--vault", required=True,
        help="The vault to publish",
    )
    p_publish.add_argument(
        "--quiet-for", type=float, default=DEFAULT_QUIET_FOR,
        help=f"Debounce window in seconds (default: {DEFAULT_QUIET_FOR})",
    )
    p_publish.set_defaults(func=cmd_publish)
