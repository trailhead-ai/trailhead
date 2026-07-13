"""Lifecycle management for the outpost daemon: ``trailhead outpost start|stop|status``.

Outpost is a long-running local Node/TS daemon (loopback only, port 7313) plus a
web UI. There is no supervisor (no launchd/systemd) in this version — these three
verbs ARE the stable management interface. A supervised backend can slot in behind
the same verbs later.

Contract & invariants
---------------------
* **Loopback only.** The daemon binds ``127.0.0.1``; this module only ever talks to
  that address. It never assumes macOS — process control goes through stdlib
  ``os``/``signal`` primitives that also map onto Windows.
* **Config-resolved daemon location.** The outpost checkout is read from
  ``config_dir("outpost")/config.toml`` (key ``checkout``, an absolute path). The
  daemon entrypoint is the built dist file at ``<checkout>/dist/server/index.js``.
  Before any spawn the entrypoint is canonicalized and validated: it must resolve
  INSIDE the configured checkout, must exist, and must be a regular file. Any
  failure raises :class:`OutpostLifecycleError` (a named error → clean
  ``trailhead: <message>`` line, never a traceback) and spawns nothing.
* **Pidfile + log under state.** ``start`` writes ``outpost.pid`` and appends the
  daemon's stdout/stderr to ``outpost.log`` under ``state_dir("outpost")``. The log
  redirect is mandatory: a detached child inheriting the CLI's stdout can block or
  SIGPIPE once the launching terminal goes away.
* **Detachment.** ``start`` spawns with ``start_new_session=True`` so the daemon
  survives the CLI process exiting. No double-fork / explicit setsid is needed.
* **Liveness / stale detection.** ``os.kill(pid, 0)`` raising ``ProcessLookupError``
  is the authoritative "this pid is dead" primitive. A pidfile pointing at a dead
  pid is *stale*; it is detected and cleaned on ``start`` (recovering cleanly) and
  reported+cleaned on ``status``/``stop``.
* **Idempotence.** A second ``start`` while already running is a no-op. ``stop`` on a
  stopped daemon is a no-op.

status exit codes (structured, so callers/tests can branch on state):
    EXIT_RUNNING (0)  pid alive
    EXIT_STOPPED (3)  no pidfile
    EXIT_STALE   (4)  pidfile pointed at a dead pid (now cleaned)
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from trailhead.paths import config_dir, ensure_dir, state_dir

APP = "outpost"
CONFIG_FILENAME = "config.toml"
PIDFILE_NAME = "outpost.pid"
LOG_NAME = "outpost.log"

# The built daemon entrypoint, relative to the outpost checkout root. The outpost
# build compiles server/index.ts to this path (tsc outDir=dist, rootDir=repo root).
DAEMON_ENTRYPOINT_PARTS = ("dist", "server", "index.js")

DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 7313

EXIT_RUNNING = 0
EXIT_STOPPED = 3
EXIT_STALE = 4

# How long to wait for the daemon to exit after SIGTERM before giving up.
_STOP_TIMEOUT_SECONDS = 10.0


class OutpostLifecycleError(Exception):
    """Raised for config/daemon-path resolution failures and lifecycle errors.

    Part of the CLI's named-error family: surfaces as a clean
    ``trailhead: <message>`` line, never a raw traceback.
    """


# ---------------------------------------------------------------------------
# Resolution & validation
# ---------------------------------------------------------------------------


def _pidfile(env: dict[str, str] | None) -> Path:
    return state_dir(APP, env=env) / PIDFILE_NAME


def _resolve_entrypoint(env: dict[str, str] | None) -> tuple[Path, Path]:
    """Read the outpost config, resolve + validate the daemon entrypoint.

    Returns ``(checkout, entrypoint)``, both canonicalized. Raises
    OutpostLifecycleError if the config is missing/malformed, the checkout key is
    absent or not absolute, or the resolved entrypoint escapes the checkout, does
    not exist, or is not a regular file.
    """
    config_path = config_dir(APP, env=env) / CONFIG_FILENAME
    if not config_path.is_file():
        raise OutpostLifecycleError(
            f"outpost config not found at {config_path}. "
            "Create it with a 'checkout' key pointing at your outpost checkout."
        )

    try:
        with config_path.open("rb") as f:
            config = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise OutpostLifecycleError(f"outpost config at {config_path} is unreadable: {exc}")

    checkout_value = config.get("checkout")
    if not checkout_value:
        raise OutpostLifecycleError(
            f"outpost config at {config_path} is missing the required 'checkout' key "
            "(absolute path to your outpost checkout)."
        )
    checkout = Path(checkout_value)
    if not checkout.is_absolute():
        raise OutpostLifecycleError(
            f"outpost 'checkout' must be an absolute path, got {checkout_value!r}."
        )
    if not checkout.is_dir():
        raise OutpostLifecycleError(
            f"outpost checkout {checkout} does not exist or is not a directory."
        )

    checkout = checkout.resolve()
    entrypoint = checkout.joinpath(*DAEMON_ENTRYPOINT_PARTS).resolve()

    if not entrypoint.is_relative_to(checkout):
        raise OutpostLifecycleError(
            f"outpost daemon entrypoint {entrypoint} resolves outside the configured "
            f"checkout {checkout}; refusing to spawn."
        )
    if not entrypoint.exists():
        raise OutpostLifecycleError(
            f"outpost daemon entrypoint {entrypoint} does not exist. "
            "Build the daemon (npm run build) before starting."
        )
    if not entrypoint.is_file():
        raise OutpostLifecycleError(
            f"outpost daemon entrypoint {entrypoint} is not a regular file."
        )
    return checkout, entrypoint


# ---------------------------------------------------------------------------
# Liveness helpers
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — alive from our perspective.
        return True
    return True


def _read_pid(pidfile: Path) -> int | None:
    """Return the pid recorded in pidfile, or None if absent/garbage."""
    try:
        text = pidfile.read_text().strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _probe_health(port: int, timeout: float) -> dict | None:
    """GET /health off the loopback daemon; return the parsed JSON or None."""
    url = f"http://{DAEMON_HOST}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------


def start(
    *,
    env: dict[str, str] | None = None,
    node_bin: str = "node",
    port: int = DAEMON_PORT,
) -> int:
    """Spawn the outpost daemon detached. Idempotent when already running.

    Resolves and validates the daemon entrypoint BEFORE any spawn, so a bad
    config/path fails loudly without leaving a half-started daemon. A stale
    pidfile (dead pid) is cleaned and start proceeds.
    """
    checkout, entrypoint = _resolve_entrypoint(env)

    pidfile = _pidfile(env)
    existing = _read_pid(pidfile)
    if existing is not None and _pid_alive(existing):
        print(f"outpost already running (pid {existing}).")
        return 0
    if existing is not None:
        # Stale pidfile — the recorded process is gone.
        pidfile.unlink(missing_ok=True)

    state = ensure_dir(state_dir(APP, env=env))
    log_path = state / LOG_NAME

    child_env = dict(env) if env is not None else dict(os.environ)
    child_env["HTTP_PORT"] = str(port)

    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            [node_bin, str(entrypoint)],
            cwd=str(checkout),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    pidfile.write_text(f"{proc.pid}\n")
    print(f"outpost started (pid {proc.pid}); logs at {log_path}.")
    return 0


def stop(
    *,
    env: dict[str, str] | None = None,
    timeout: float = _STOP_TIMEOUT_SECONDS,
) -> int:
    """SIGTERM the daemon, wait for a clean exit, and remove the pidfile.

    No-op when not running; a stale pidfile is simply cleaned.
    """
    pidfile = _pidfile(env)
    pid = _read_pid(pidfile)
    if pid is None:
        print("outpost is not running.")
        return 0

    if not _pid_alive(pid):
        pidfile.unlink(missing_ok=True)
        print("outpost is not running; removed stale pidfile.")
        return 0

    os.kill(pid, signal.SIGTERM)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.05)
    else:
        raise OutpostLifecycleError(
            f"outpost (pid {pid}) did not exit within {timeout:.0f}s of SIGTERM."
        )

    pidfile.unlink(missing_ok=True)
    print(f"outpost stopped (pid {pid}).")
    return 0


def status(
    *,
    env: dict[str, str] | None = None,
    port: int = DAEMON_PORT,
    timeout: float = 2.0,
) -> int:
    """Report daemon liveness + /health, returning a structured exit code."""
    pidfile = _pidfile(env)
    pid = _read_pid(pidfile)

    if pid is None:
        print("outpost: stopped (no pidfile).")
        return EXIT_STOPPED

    if not _pid_alive(pid):
        pidfile.unlink(missing_ok=True)
        print(f"outpost: stale (pid {pid} is dead; removed stale pidfile).")
        return EXIT_STALE

    health = _probe_health(port, timeout)
    if health is None:
        print(f"outpost: running (pid {pid}); /health unreachable on port {port}.")
        return EXIT_RUNNING

    contract_version = health.get("contract_version")
    print(f"outpost: running (pid {pid}); /health ok, contract_version={contract_version}.")
    return EXIT_RUNNING
