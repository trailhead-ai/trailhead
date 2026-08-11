"""Lifecycle management for the outpost daemon: ``trailhead outpost start|stop|status|restart``.

Outpost is a long-running local Node/TS daemon (loopback only, port 7313) plus a
web UI. There is no supervisor (no launchd/systemd) in this version — these four
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
* **Identity confirmation, not just liveness.** A live pid alone doesn't prove
  it's *our* daemon — pids get reused by the OS. ``start``'s idempotency check
  and ``stop`` both probe ``/health`` before trusting a live recorded pid; if it
  doesn't answer, the pidfile is treated as stale (cleaned, nothing signaled)
  rather than risking a SIGTERM to an unrelated process. ``status`` already did
  this via its own ``/health`` probe.
* **Idempotence.** A second ``start`` while already running is a no-op. ``stop`` on a
  stopped daemon is a no-op.
* **Rebuild-before-restart.** ``restart`` resolves the checkout, runs the (injectable,
  default ``["npm", "run", "build"]``) full build with ``cwd=<checkout>``, THEN calls
  ``stop`` then ``start``. The build runs *before* stop: a nonzero build exit raises
  :class:`OutpostLifecycleError` (including both stdout and stderr, since tsc/vite
  diagnostics land on stdout) and neither stop nor start ever runs, so a running
  daemon is left untouched on a broken build. A missing build tool (e.g. no ``npm``
  on PATH) is likewise wrapped in :class:`OutpostLifecycleError` rather than
  propagating a raw ``FileNotFoundError``. Because ``stop`` on a stopped daemon is
  already a no-op, ``restart`` also serves as "build and start" when nothing is
  running. On success it prints the hashed web bundle filenames found under
  ``<checkout>/dist-web/assets/`` so a stale-looking UI is diagnosable at a glance.
  ``restart`` does not trust ``start``'s return value alone: it polls ``/health``
  briefly afterward (allowing for startup latency), proving *some* process is
  answering on the port, and then confirms the pid ``start()`` recorded is
  still alive, proving it's *our* spawn that answered rather than an
  unmanaged/old process still holding the port. :class:`OutpostLifecycleError`
  is raised if either check fails — otherwise a doomed spawn (e.g. dying on
  EADDRINUSE against a not-yet-dead old daemon) would be reported as a
  successful restart while the old daemon keeps serving stale content.

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

# `npm run build` chains build:web + tsc + the migrations copy, producing both the
# compiled dist/server/index.js entrypoint and the dist-web/ static bundle.
DEFAULT_BUILD_CMD = ["npm", "run", "build"]

# vite emits content-hashed asset filenames under this directory relative to the
# checkout root; printing them after a build makes staleness visible at a glance.
WEB_ASSETS_DIR_PARTS = ("dist-web", "assets")

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


def _resolve_checkout(env: dict[str, str] | None) -> Path:
    """Read the outpost config and resolve+validate just the checkout directory.

    Returns the canonicalized checkout path. Raises OutpostLifecycleError if the
    config is missing/malformed or the checkout key is absent, not absolute, or
    not an existing directory. Does not touch the built entrypoint — callers that
    need it (e.g. before spawning) should go through :func:`_resolve_entrypoint`;
    callers that are about to *build* it (e.g. ``restart``) should not require it
    to already exist.
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

    return checkout.resolve()


def _resolve_entrypoint(env: dict[str, str] | None) -> tuple[Path, Path]:
    """Read the outpost config, resolve + validate the daemon entrypoint.

    Returns ``(checkout, entrypoint)``, both canonicalized. Raises
    OutpostLifecycleError if the config is missing/malformed, the checkout key is
    absent or not absolute, or the resolved entrypoint escapes the checkout, does
    not exist, or is not a regular file.
    """
    checkout = _resolve_checkout(env)
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
    # If pid is our own child (e.g. the process start() just spawned, checked
    # later in the same restart() call), a dead-but-unreaped child is a zombie:
    # os.kill(pid, 0) reports zombies as alive since the OS still holds the
    # process table entry until it's waited on. Reap opportunistically first so
    # a crashed spawn (e.g. EADDRINUSE) is detected as dead promptly rather than
    # appearing alive until something else reaps it.
    try:
        reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass  # not our child — e.g. pid read from a pidfile written by a prior process
    else:
        if reaped_pid == pid:
            return False
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


def _wait_for_health(port: int, total_timeout: float, poll_interval: float = 0.1) -> dict | None:
    """Poll /health repeatedly, allowing for startup latency, until it answers
    or ``total_timeout`` elapses. Returns the parsed payload, or None on timeout."""
    deadline = time.time() + total_timeout
    while True:
        health = _probe_health(port, poll_interval)
        if health is not None:
            return health
        if time.time() >= deadline:
            return None
        time.sleep(poll_interval)


# A spawn that's about to die on EADDRINUSE typically crashes within tens of ms
# of the bind attempt. /health can answer (from a stale process still holding
# the port) before that crash lands, so the pid-liveness check settles briefly
# rather than sampling once — a single immediate sample would race the crash.
_PID_SETTLE_SECONDS = 0.3
_PID_SETTLE_POLL_INTERVAL = 0.02


def _settled_pid_alive(pid: int, timeout: float = _PID_SETTLE_SECONDS) -> bool:
    """Poll pid liveness for a short settle window. Returns False as soon as
    the pid is observed dead; returns True only if it stayed alive throughout."""
    deadline = time.time() + timeout
    while True:
        if not _pid_alive(pid):
            return False
        if time.time() >= deadline:
            return True
        time.sleep(_PID_SETTLE_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------


def start(
    *,
    env: dict[str, str] | None = None,
    node_bin: str = "node",
    port: int = DAEMON_PORT,
    health_timeout: float = 2.0,
) -> int:
    """Spawn the outpost daemon detached. Idempotent when already running.

    Resolves and validates the daemon entrypoint BEFORE any spawn, so a bad
    config/path fails loudly without leaving a half-started daemon. A stale
    pidfile is cleaned and start proceeds: either the recorded pid is dead,
    or it's alive but doesn't answer /health, meaning the OS has reused it
    for an unrelated process since the daemon died.
    """
    checkout, entrypoint = _resolve_entrypoint(env)

    pidfile = _pidfile(env)
    existing = _read_pid(pidfile)
    if existing is not None and _pid_alive(existing) and _probe_health(port, health_timeout) is not None:
        print(f"outpost already running (pid {existing}).")
        return 0
    if existing is not None:
        # Stale pidfile — the recorded process is gone, or its pid has been
        # reused by an unrelated process. Either way, not the daemon.
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
    port: int = DAEMON_PORT,
    timeout: float = _STOP_TIMEOUT_SECONDS,
    health_timeout: float = 2.0,
) -> int:
    """SIGTERM the daemon, wait for a clean exit, and remove the pidfile.

    No-op when not running. A pidfile is treated as stale (cleaned, nothing
    signaled) both when its pid is dead and when the pid is alive but doesn't
    answer /health — the latter means the OS has reused the pid for an
    unrelated process since the daemon died, and signaling it would kill the
    wrong process.
    """
    pidfile = _pidfile(env)
    pid = _read_pid(pidfile)
    if pid is None:
        print("outpost is not running.")
        return 0

    if not _pid_alive(pid) or _probe_health(port, health_timeout) is None:
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
    health_timeout: float = 2.0,
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

    health = _probe_health(port, health_timeout)
    if health is None:
        print(f"outpost: running (pid {pid}); /health unreachable on port {port}.")
        return EXIT_RUNNING

    contract_version = health.get("contract_version")
    print(f"outpost: running (pid {pid}); /health ok, contract_version={contract_version}.")
    return EXIT_RUNNING


def restart(
    *,
    env: dict[str, str] | None = None,
    node_bin: str = "node",
    build_cmd: list[str] | None = None,
    port: int = DAEMON_PORT,
    health_timeout: float = 2.0,
    stop_timeout: float = _STOP_TIMEOUT_SECONDS,
    restart_health_timeout: float = 5.0,
) -> int:
    """Rebuild the outpost checkout, then stop and restart the daemon.

    Resolves + validates the checkout first (not the built entrypoint — the build
    about to run is what produces it). Runs the full build with ``cwd=<checkout>``
    BEFORE touching the running daemon: a nonzero build exit raises
    OutpostLifecycleError and stop/start never run, leaving any running daemon
    untouched. ``stop`` on a stopped daemon is already a no-op, so this also works
    as "build and start" when nothing is running.

    After ``start``, restart does not simply trust its return value: a doomed
    spawn (e.g. the new process dying on EADDRINUSE against a not-yet-dead old
    daemon) would otherwise be reported as a successful restart while the old
    process keeps serving the stale bundle. So restart polls ``/health`` for up
    to ``restart_health_timeout`` seconds (allowing for normal startup latency)
    to prove the port answers, then reads back the pid ``start()`` recorded and
    confirms it is still alive, to prove it's our spawn — not a stale process
    still holding the port — that answered. OutpostLifecycleError is raised,
    naming what happened, if either check fails.
    """
    checkout = _resolve_checkout(env)
    cmd = build_cmd if build_cmd is not None else list(DEFAULT_BUILD_CMD)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(checkout),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise OutpostLifecycleError(
            f"outpost build command {cmd!r} could not be run: {exc}. "
            "Is npm installed and on PATH?"
        )
    if result.returncode != 0:
        raise OutpostLifecycleError(
            f"outpost build failed (exit {result.returncode}): {' '.join(cmd)}\n"
            f"{result.stdout.strip()}\n"
            f"{result.stderr.strip()}"
        )

    assets_dir = checkout.joinpath(*WEB_ASSETS_DIR_PARTS)
    asset_names = sorted(p.name for p in assets_dir.glob("*") if p.is_file()) if assets_dir.is_dir() else []
    if asset_names:
        print(f"outpost build ok; web bundle: {', '.join(asset_names)}")
    else:
        print(f"outpost build ok; no assets found under {assets_dir}.")

    stop(env=env, port=port, timeout=stop_timeout, health_timeout=health_timeout)
    rc = start(env=env, node_bin=node_bin, port=port, health_timeout=health_timeout)

    if _wait_for_health(port, restart_health_timeout) is None:
        raise OutpostLifecycleError(
            f"outpost restart: rebuilt and spawned a new process but /health never "
            f"answered on port {port} within {restart_health_timeout:.0f}s; the new "
            "daemon may have failed to start (check the outpost log)."
        )

    # /health answering alone doesn't prove OUR spawn is what answered it — an
    # unmanaged/old process could still be holding the port, in which case our
    # spawn just died on EADDRINUSE while the stale process keeps serving.
    # Confirm the pid start() recorded is still alive before trusting success.
    new_pid = _read_pid(_pidfile(env))
    if new_pid is None or not _settled_pid_alive(new_pid):
        raise OutpostLifecycleError(
            "outpost restart: /health answered but the process start() spawned "
            f"(pid {new_pid}) is not alive; another process is likely still "
            f"holding port {port} (check the outpost log)."
        )

    return rc
