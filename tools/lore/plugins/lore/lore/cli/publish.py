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
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from ..vault import layers as layers_mod
from .common import (
    _load_vault_config,
    _resolve_all_vaults,
    _resolve_lore_state_dir,
    machine_state_key,
)

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
    """Return the publish marker for *vault_root*, or None if absent/unreadable.

    Also None when the marker parses as JSON but is not an object — a
    corrupt/truncated write can leave valid JSON that is not a dict (e.g. a
    bare number), and treating that as "no marker" is what keeps
    :func:`warn_stale_publish`'s ``marker.get("outcome")`` from raising.
    """
    try:
        data = json.loads(marker_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


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


# ---------------------------------------------------------------------------
# request_publish — the write-triggered half. Called after a successful
# record create, record update, or flush; never before the write it follows.
# ---------------------------------------------------------------------------


def _resolve_vault_entry(vault_root: "str | Path"):
    """Return ``(name, Vault-or-None)`` for *vault_root*.

    Matches the resolved root against every configured vault
    (:func:`common._load_vault_config`). Vanilla usage (no ``config.json``) and
    a root that matches no configured entry both fall back to ``("default",
    None)`` — a ``None`` Vault means :func:`request_publish` treats
    ``auto_publish`` as its default of ``True`` rather than gating on a
    config entry that does not exist.
    """
    loaded = _load_vault_config()
    if loaded is not None:
        _, vaults = loaded
        resolved = Path(vault_root).resolve()
        for vault in vaults:
            if Path(vault.path).resolve() == resolved:
                return vault.name, vault
    return "default", None


def _worker_argv(vault_name: str) -> list:
    """Return the argv that re-invokes THIS running lore CLI as the worker.

    ``sys.argv[0]`` is the ``cli/lore`` entry script the running process was
    launched from — whether that is a repo checkout or an installed plugin
    (``${CLAUDE_PLUGIN_ROOT}/cli/lore``) — so resolving it here, from the
    running process, rather than hardcoding a repo-relative path, re-invokes
    the SAME script under the SAME interpreter. This is the shape
    ``run_cli_subprocess`` in ``tests/conftest.py`` already spawns the CLI
    with: ``[sys.executable, str(CLI_PATH), *args]``.
    """
    cli_path = Path(sys.argv[0]).resolve()
    return [sys.executable, str(cli_path), "publish", "--vault", vault_name]


def _spawn_worker(argv: list) -> None:
    """Spawn the publish worker detached (the ``Popen`` new-session idiom).

    Never inherits this process's stdout/stderr — the worker manages its own
    per-vault log (:func:`log_path`) once it enters :func:`_run_publish`; DEVNULL
    here just keeps the caller's own streams (a create's single ``RECORD_ID``
    line, in particular) untouched by anything printed before that point.
    """
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _lock_held(vault_root: "str | Path") -> bool:
    """Return ``True`` iff another worker already holds *vault_root*'s lock.

    A non-blocking probe: takes and immediately releases the same
    ``fcntl.flock`` :func:`cmd_publish` itself takes for single-flight, on the
    same :func:`lock_path`. Used only to skip a redundant spawn — the worker's
    own lock is what actually enforces single-flight, so a race between this
    probe and a spawn landing anyway is harmless (the second worker's own
    non-blocking acquisition attempt exits 0 immediately).
    """
    lp = lock_path(vault_root)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lp, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def request_publish(vault_root: "str | Path", *, clock=time.time) -> None:
    """Request a background publish of *vault_root* — the write-triggered path.

    Called after a successful record create, a successful record update (both
    the in-place and the relocation branch, for the vault the record landed
    in), and a flush. ``lore session candidate`` never calls this.

    Skips both the stamp and the spawn when the vault's ``auto_publish``
    setting is ``False`` (:func:`vault_config.auto_publish_flag`) — an
    operator opting a vault out entirely. Otherwise always writes the request
    stamp (so a burst of writes debounces correctly even when a worker is
    already running), then spawns :func:`_spawn_worker` UNLESS
    :func:`_lock_held` reports a worker already holds this vault's lock.

    Never raises into the caller: a failure in ANY step here — the stamp
    write (permission, ENOSPC), the lock probe (an fcntl error), or the spawn
    itself (e.g. the interpreter or CLI script cannot be found, the OS
    refuses to fork) — is caught and reported as one stderr line naming the
    vault; the write that triggered this already succeeded and its exit code
    must not change because scheduling a publish for it failed.
    """
    from ..vault import config as vault_config_mod

    name, vault = _resolve_vault_entry(vault_root)
    if vault is not None and not vault_config_mod.auto_publish_flag(vault):
        return

    try:
        touch_request_stamp(vault_root, clock=clock)

        if _lock_held(vault_root):
            return

        _spawn_worker(_worker_argv(name))
    except Exception as exc:  # noqa: BLE001 — never fail the write that asked
        print(f"error: could not schedule publish for vault {name!r}: {exc}", file=sys.stderr)


def warn_stale_publish(vault_root: "str | Path") -> None:
    """Print one stderr line if *vault_root*'s last automatic publish did not succeed.

    Reads the publish marker (:func:`read_marker`). Silent when there is no
    marker (nothing has ever run, or the last run cleared it on success — see
    :data:`_CLEARED_OUTCOMES`) or the marker's outcome is still
    :data:`OUTCOME_RUNNING` (a worker is in flight, not yet failed). Any other
    outcome — :data:`OUTCOME_ERROR`, :data:`OUTCOME_ROUNDS_EXHAUSTED`, or one of
    the sync loop's own non-success outcomes (``holding``,
    ``awaiting-person``, ``retries-exhausted``, …) — means the last automatic
    publish ended without succeeding, which is worth surfacing at the next
    intentional write. Called beside the existing throttled freshness note
    (``sync.implicit_pull``) in ``record create``/``record update``.
    """
    marker = read_marker(vault_root)
    if marker is None:
        return
    outcome = marker.get("outcome")
    if outcome in (None, OUTCOME_RUNNING):
        return
    name = Path(vault_root).name
    print(
        f"  lore: {name}: notice: the last automatic publish did not succeed "
        f"({outcome}) — run `lore sync` to retry.",
        file=sys.stderr,
    )


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
