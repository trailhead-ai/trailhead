"""Lifecycle management for the outpost daemon: ``trailhead outpost start|stop|status|restart|open``.

Outpost is a long-running local Node/TS daemon (loopback only, port 7313) plus a
web UI. There is no supervisor (no launchd/systemd) in this version — these
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
* **``open`` never starts anything.** It is a read-only convenience over a daemon
  someone else started: it confirms ``/health`` answers, then hands the loopback
  UI URL to the platform browser. A daemon that isn't answering is a named error
  pointing at ``trailhead outpost start`` — never a browser tab onto a dead port.
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
    EXIT_RUNNING    (0)  pid alive, /health answers (or, under supervision,
                         supervisor reports a pid and /health answers)
    EXIT_STOPPED    (3)  no pidfile (or, under supervision, supervisor reports
                         no pid and no failure — the state an operator's own
                         `stop` leaves)
    EXIT_STALE      (4)  pidfile pointed at a dead pid (now cleaned) —
                         unsupervised path only
    EXIT_RESTARTING (5)  supervised only: supervisor reports a pid (or a
                         restarting sub-state) but /health has not answered yet
    EXIT_FAILED     (6)  supervised only: no pid and the supervisor reports a
                         failure — on Linux a start-limit-hit result or
                         ActiveState=failed, on macOS a non-zero launchd last
                         exit code or a launchd terminating signal

Host-supervisor awareness
--------------------------
``start``, ``stop``, ``restart``, and ``status`` each begin by checking whether
a host-supervisor entry is registered (``trailhead outpost enable`` /
``trailhead.outpost_supervisor.is_enabled``). When one is, the verb drives the
supervisor (launchd on macOS, systemd --user on Linux) through the same
injectable command-runner seam ``outpost_supervisor`` uses, rather than
spawning or signalling the process itself, and touches no pidfile — a leftover
one from a prior unsupervised run is removed the first time a supervised verb
runs. When no entry is registered, every verb runs its unsupervised
detached-pidfile path exactly as described above, unchanged. Resolving
"is a supervisor entry registered" can itself fail (an unsupported platform,
no HOME in the given environment); that failure is treated the same as "no
entry registered" rather than propagated, since either way there is nothing to
drive and the detached path is always available.

``stop`` under a supervisor keeps this module's own bounded wait for
``/health`` to stop answering (the supervisor's own stop command returning
proves nothing about whether the process actually exited), and then re-checks
once more after a settle window: a supervisor whose restart policy is not
limited to failure exits would otherwise silently relaunch what the operator
just told it to stop.

``status`` under a supervisor reads the supervisor's own view of the job
(pid/state for launchd, MainPID/ActiveState/SubState/Result/NRestarts for
systemd) rather than the pidfile, and on Linux also reads ``loginctl
show-user ... -p Linger`` to warn when the daemon will not start at boot with
nobody logged in.
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
import webbrowser
from dataclasses import dataclass
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
EXIT_RESTARTING = 5
EXIT_FAILED = 6

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

#: Fraction of a restart's health timeout used as the pid-settle window when the
#: caller does not name one. The two are proxies for the same thing — how slow
#: this machine is right now — so a caller that allows 30s for /health to answer
#: is also saying a doomed spawn may take well over 0.3s to finish dying.
_PID_SETTLE_FRACTION = 0.1


def _resolve_pid_settle_timeout(
    *, pid_settle_timeout: float | None, restart_health_timeout: float
) -> float:
    """Resolve the pid-settle window for one restart.

    An explicit *pid_settle_timeout* wins outright. Otherwise the window scales
    with *restart_health_timeout*, floored at :data:`_PID_SETTLE_SECONDS` so a
    caller that shortens its health tolerance never ends up with a window too
    short to observe a doomed spawn exit at all.
    """
    if pid_settle_timeout is not None:
        return pid_settle_timeout
    return max(_PID_SETTLE_SECONDS, restart_health_timeout * _PID_SETTLE_FRACTION)


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
# Host-supervisor awareness — outpost_supervisor is imported lazily
# (function-local) in _is_supervised and _supervisor_seam, and handed on from
# there: it imports OutpostLifecycleError from this module at ITS module
# scope, so a module-level import here back would be circular.
# ---------------------------------------------------------------------------


def _is_supervised(
    env: dict[str, str] | None, *, platform: str | None, supervisor_dir: Path | None
) -> bool:
    """True if a host-supervisor entry is registered. Resolving that can itself
    fail (unsupported platform, no HOME in *env*) — treated as "not
    supervised", since the detached path is always available regardless."""
    from trailhead import outpost_supervisor as osup

    try:
        return osup.is_enabled(env, platform=platform, supervisor_dir=supervisor_dir)
    except OutpostLifecycleError:
        return False


def _supervisor_seam(env: dict[str, str] | None, platform: str | None, runner):
    """Resolve ``(outpost_supervisor, platform kind, runner)`` for a supervised
    verb. Under supervision the pidfile is meaningless — the supervisor owns
    the process — so a leftover one from a prior unsupervised run is removed
    here, the first time a supervised verb runs, and nothing reads it again."""
    from trailhead import outpost_supervisor as osup

    kind = osup._platform_kind(platform)
    _pidfile(env).unlink(missing_ok=True)
    run = runner if runner is not None else osup.default_runner
    return osup, kind, run


@dataclass
class _SupervisorProbe:
    """One platform-normalized reading of the supervisor's view of the job."""

    pid: int | None
    restarting: bool
    failed: bool
    restart_count: int | None
    recovery_hint: str


def _leading_int(text: str) -> int | None:
    """Parse a leading integer off *text*, or None if it doesn't start with one.

    launchd's `last exit code` prints forms besides a bare integer: a live
    job reads ``(never exited)``, and a failed job can read ``78: EX_CONFIG``
    (a leading integer followed by the symbolic name). Only the last of those
    is a usable exit code; anything else means "no exit code available".
    """
    digits = ""
    for ch in text:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def _probe_darwin(run, service: str) -> _SupervisorProbe:
    result = run(["launchctl", "print", service])
    text = result.stdout or ""
    pid = None
    last_exit = None
    terminating_signal = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("pid"):
            _, _, value = stripped.partition("=")
            value = value.strip()
            if value.isdigit():
                pid = int(value)
        elif stripped.startswith("last exit code"):
            _, _, value = stripped.partition("=")
            last_exit = _leading_int(value.strip())
        elif stripped.startswith("last terminating signal"):
            _, _, value = stripped.partition("=")
            terminating_signal = value.strip()
    failed = pid is None and (
        last_exit not in (None, 0) or terminating_signal not in (None, "", "0")
    )
    return _SupervisorProbe(
        pid=pid,
        restarting=False,
        failed=failed,
        restart_count=None,
        recovery_hint="trailhead outpost start",
    )


def _parse_systemd_show(stdout: str) -> dict[str, str]:
    """Parse ``systemctl show -p ...``'s ``Key=Value`` lines into a dict.

    systemd prints properties in its own internal order, not the order they
    were requested in — a parser that reads by line position rather than by
    key silently reads the wrong field whenever that order differs (it always
    does, in practice).
    """
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


def _probe_linux(run, unit: str) -> _SupervisorProbe:
    result = run(
        [
            "systemctl",
            "--user",
            "show",
            "-p",
            "MainPID,ActiveState,SubState,Result,NRestarts",
            unit,
        ]
    )
    values = _parse_systemd_show(result.stdout or "")
    main_pid_text = values.get("MainPID", "")
    active_state = values.get("ActiveState", "")
    sub_state = values.get("SubState", "")
    result_field = values.get("Result", "")
    n_restarts_text = values.get("NRestarts", "")

    pid = int(main_pid_text) if main_pid_text.isdigit() and main_pid_text != "0" else None
    restarting = sub_state in ("auto-restart", "activating") or active_state == "activating"
    failed = pid is None and (result_field == "start-limit-hit" or active_state == "failed")
    restart_count = int(n_restarts_text) if n_restarts_text.isdigit() else None
    return _SupervisorProbe(
        pid=pid,
        restarting=restarting,
        failed=failed,
        restart_count=restart_count,
        recovery_hint=f"systemctl --user reset-failed {unit} && trailhead outpost start",
    )


def _probe_supervisor(osup, run, kind: str, uid: int | None) -> _SupervisorProbe:
    if kind == "darwin":
        return _probe_darwin(run, osup.launchd_service(uid))
    return _probe_linux(run, osup.SYSTEMD_UNIT_NAME)


def _supervised_start(
    env: dict[str, str] | None,
    *,
    port: int,
    health_timeout: float,
    platform: str | None,
    runner,
    uid: int | None,
) -> int:
    osup, kind, run = _supervisor_seam(env, platform, runner)

    if kind == "darwin":
        command = ["launchctl", "kickstart", osup.launchd_service(uid)]
        result = run(command)
        if result.returncode != 0:
            raise OutpostLifecycleError(
                f"outpost start: '{' '.join(command)}' failed (exit "
                f"{result.returncode}): {(result.stderr or result.stdout or '').strip()}"
            )
    else:
        # reset-failed is best-effort: it legitimately "fails" (nonzero) when
        # there is nothing to reset, which is the common case.
        run(["systemctl", "--user", "reset-failed", osup.SYSTEMD_UNIT_NAME])
        command = ["systemctl", "--user", "start", osup.SYSTEMD_UNIT_NAME]
        result = run(command)
        if result.returncode != 0:
            raise OutpostLifecycleError(
                f"outpost start: '{' '.join(command)}' failed (exit "
                f"{result.returncode}): {(result.stderr or result.stdout or '').strip()}"
            )

    if _wait_for_health(port, health_timeout) is None:
        raise OutpostLifecycleError(
            "outpost start: the host supervisor was told to start outpost, but "
            f"/health never answered on port {port} within {health_timeout:.0f}s; "
            "check the outpost log."
        )
    print(f"outpost started via the host supervisor; /health ok on port {port}.")
    return 0


def _supervised_stop(
    env: dict[str, str] | None,
    *,
    port: int,
    timeout: float,
    health_timeout: float,
    pid_settle_timeout: float | None,
    platform: str | None,
    runner,
    uid: int | None = None,
) -> int:
    osup, kind, run = _supervisor_seam(env, platform, runner)

    # Recorded before the stop so the settle-window re-probe can tell a
    # genuine relaunch (a DIFFERENT pid, or a pid reappearing after the
    # supervisor first reported none) from the old process simply still
    # winding down under the same pid.
    pid_before_stop = _probe_supervisor(osup, run, kind, uid).pid

    if kind == "darwin":
        # `launchctl kill TERM gui/<uid>/<label>` targets the same domain as
        # `kickstart`/`print` (including over ssh) rather than the bare-label
        # `launchctl stop <label>`. A SIGTERM'd job that exits 0 stays stopped
        # under KeepAlive.SuccessfulExit=false.
        run(["launchctl", "kill", "TERM", osup.launchd_service(uid)])
    else:
        run(["systemctl", "--user", "stop", osup.SYSTEMD_UNIT_NAME])

    deadline = time.time() + timeout
    while True:
        if _probe_health(port, health_timeout) is None:
            break
        if time.time() >= deadline:
            raise OutpostLifecycleError(
                f"outpost (host supervisor) did not exit within {timeout:.0f}s of stop."
            )
        time.sleep(0.05)

    # A relaunch during the settle window can show up two ways: /health comes
    # back up, or the supervisor's own view reports the job running again
    # even before /health answers (still starting). Checking /health alone
    # misses the second case entirely.
    #
    # But the supervisor still reporting *a* pid is not by itself evidence of
    # a relaunch: `launchctl kill TERM` only sends the signal, and an open SSE
    # subscriber can keep the OLD process (same pid, unchanged) alive past
    # /health going silent, until its own backstop timer fires — `launchctl
    # print` keeps reporting that same pid the whole time. Only a pid that
    # DIFFERS from the one recorded before this stop, or one that reappears
    # after the supervisor first reported none, means the job actually
    # restarted.
    settle = _resolve_pid_settle_timeout(
        pid_settle_timeout=pid_settle_timeout, restart_health_timeout=timeout
    )
    settle_deadline = time.time() + settle
    seen_no_pid = False
    while True:
        if _probe_health(port, min(health_timeout, 0.2)) is not None:
            raise OutpostLifecycleError(
                "outpost stop: the host supervisor relaunched outpost after stop "
                f"(/health answered again within {settle:.1f}s of the stop completing); "
                "its restart policy may not be limited to failure exits."
            )
        probe = _probe_supervisor(osup, run, kind, uid)
        relaunched_pid = probe.pid is not None and (
            seen_no_pid or probe.pid != pid_before_stop
        )
        if relaunched_pid or probe.restarting:
            raise OutpostLifecycleError(
                "outpost stop: the host supervisor relaunched outpost after stop "
                f"(it reports the job running again within {settle:.1f}s of the stop "
                "completing); its restart policy may not be limited to failure exits."
            )
        if probe.pid is None:
            seen_no_pid = True
        if time.time() >= settle_deadline:
            break
        time.sleep(_PID_SETTLE_POLL_INTERVAL)

    print("outpost stopped via the host supervisor.")
    return 0


def _supervised_status(
    env: dict[str, str] | None,
    *,
    port: int,
    health_timeout: float,
    platform: str | None,
    runner,
    uid: int | None,
    user: str | None,
) -> int:
    osup, kind, run = _supervisor_seam(env, platform, runner)

    probe = _probe_supervisor(osup, run, kind, uid)
    health = _probe_health(port, health_timeout) if probe.pid is not None else None

    linger_warning = ""
    if kind == "linux":
        environ = env if env is not None else dict(os.environ)
        resolved_user = osup.resolve_user(environ, user)
        linger_result = run(["loginctl", "show-user", resolved_user, "-p", "Linger", "--value"])
        if (linger_result.stdout or "").strip() == "no":
            linger_warning = (
                f" The daemon will not start at boot with nobody logged in until "
                f"you run: loginctl enable-linger {resolved_user}."
            )

    if probe.pid is not None and health is not None:
        print(f"outpost: running (host supervisor, pid {probe.pid}); /health ok.{linger_warning}")
        return EXIT_RUNNING
    if probe.pid is not None or probe.restarting:
        count = f" ({probe.restart_count} restarts so far)" if probe.restart_count else ""
        print(
            f"outpost: restarting (host supervisor){count}; /health not yet "
            f"answering.{linger_warning}"
        )
        return EXIT_RESTARTING
    if probe.failed:
        print(
            f"outpost: failed (host supervisor reports a failure); "
            f"recovery: {probe.recovery_hint}.{linger_warning}"
        )
        return EXIT_FAILED
    print(f"outpost: stopped (host supervisor).{linger_warning}")
    return EXIT_STOPPED


def _supervised_restart(
    env: dict[str, str] | None,
    *,
    port: int,
    restart_health_timeout: float,
    pid_settle_timeout: float | None,
    platform: str | None,
    runner,
    uid: int | None,
) -> int:
    osup, kind, run = _supervisor_seam(env, platform, runner)

    # Recorded before the restart so the reported pid afterward can be
    # confirmed to have actually changed — a 0 exit and an answering /health
    # don't by themselves prove the job restarted rather than the still-alive
    # old process simply being observed again.
    pid_before_restart = _probe_supervisor(osup, run, kind, uid).pid

    if kind == "darwin":
        command = ["launchctl", "kickstart", "-k", osup.launchd_service(uid)]
        result = run(command)
        if result.returncode != 0:
            raise OutpostLifecycleError(
                f"outpost restart: '{' '.join(command)}' failed (exit "
                f"{result.returncode}): {(result.stderr or result.stdout or '').strip()}"
            )
    else:
        # reset-failed is best-effort, same as start(): it legitimately
        # "fails" (nonzero) when there is nothing to reset, the common case.
        run(["systemctl", "--user", "reset-failed", osup.SYSTEMD_UNIT_NAME])
        command = ["systemctl", "--user", "restart", osup.SYSTEMD_UNIT_NAME]
        result = run(command)
        if result.returncode != 0:
            raise OutpostLifecycleError(
                f"outpost restart: '{' '.join(command)}' failed (exit "
                f"{result.returncode}): {(result.stderr or result.stdout or '').strip()}"
            )

    if _wait_for_health(port, restart_health_timeout) is None:
        raise OutpostLifecycleError(
            "outpost restart: rebuilt and told the host supervisor to restart "
            f"outpost, but /health never answered on port {port} within "
            f"{restart_health_timeout:.0f}s; the new daemon may have failed to "
            "start (check the outpost log)."
        )

    settle_timeout = _resolve_pid_settle_timeout(
        pid_settle_timeout=pid_settle_timeout, restart_health_timeout=restart_health_timeout
    )
    deadline = time.time() + settle_timeout
    pid = None
    while True:
        probe = _probe_supervisor(osup, run, kind, uid)
        if probe.pid is not None:
            pid = probe.pid
            break
        if time.time() >= deadline:
            break
        time.sleep(_PID_SETTLE_POLL_INTERVAL)

    if pid is None:
        raise OutpostLifecycleError(
            "outpost restart: /health answered but the host supervisor reports "
            f"no pid for outpost; another process may be holding port {port} "
            "(check the outpost log)."
        )

    # A 0 exit from the restart command and /health answering don't prove the
    # job actually restarted — the still-running old process could be what
    # answered. The reported pid must differ from the one recorded before
    # this restart; when nothing was running beforehand there is no old pid
    # to have stayed the same as, so any newly reported pid is accepted.
    if pid_before_restart is not None and pid == pid_before_restart:
        raise OutpostLifecycleError(
            f"outpost restart: the host supervisor reports the same pid ({pid}) "
            "after restart; the process may not have actually restarted "
            "(check the outpost log)."
        )

    # /health answering and the supervisor reporting a pid once don't prove
    # THAT pid is what's actually serving it — a doomed spawn can be observed
    # alive for a moment before it exits. Hold it live across the settle
    # window, the same confirmation the unsupervised restart path applies to
    # its own spawned pid.
    if not _settled_pid_alive(pid, settle_timeout):
        raise OutpostLifecycleError(
            "outpost restart: /health answered but the host supervisor's "
            f"reported pid ({pid}) is not alive; another process may be "
            f"holding port {port} (check the outpost log)."
        )

    print(f"outpost restarted via the host supervisor (pid {pid}).")
    return 0


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------


def start(
    *,
    env: dict[str, str] | None = None,
    node_bin: str = "node",
    port: int = DAEMON_PORT,
    health_timeout: float = 2.0,
    platform: str | None = None,
    supervisor_dir: Path | None = None,
    runner=None,
    uid: int | None = None,
    user: str | None = None,
    start_health_timeout: float = 10.0,
) -> int:
    """Spawn the outpost daemon detached. Idempotent when already running.

    Resolves and validates the daemon entrypoint BEFORE any spawn, so a bad
    config/path fails loudly without leaving a half-started daemon. A stale
    pidfile is cleaned and start proceeds: either the recorded pid is dead,
    or it's alive but doesn't answer /health, meaning the OS has reused it
    for an unrelated process since the daemon died.

    When a host-supervisor entry is registered, this drives the supervisor
    instead (``launchctl kickstart`` / ``systemctl --user start``) and spawns
    nothing itself; see the module docstring's "Host-supervisor awareness".
    """
    if _is_supervised(env, platform=platform, supervisor_dir=supervisor_dir):
        return _supervised_start(
            env,
            port=port,
            health_timeout=start_health_timeout,
            platform=platform,
            runner=runner,
            uid=uid,
        )

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
    platform: str | None = None,
    supervisor_dir: Path | None = None,
    runner=None,
    uid: int | None = None,
    user: str | None = None,
    pid_settle_timeout: float | None = None,
) -> int:
    """SIGTERM the daemon, wait for a clean exit, and remove the pidfile.

    No-op when not running. A pidfile is treated as stale (cleaned, nothing
    signaled) both when its pid is dead and when the pid is alive but doesn't
    answer /health — the latter means the OS has reused the pid for an
    unrelated process since the daemon died, and signaling it would kill the
    wrong process.

    When a host-supervisor entry is registered, this drives the supervisor's
    stop command instead of signalling a pid, and re-checks after a settle
    window that the supervisor did not relaunch outpost; see the module
    docstring's "Host-supervisor awareness".
    """
    if _is_supervised(env, platform=platform, supervisor_dir=supervisor_dir):
        return _supervised_stop(
            env,
            port=port,
            timeout=timeout,
            health_timeout=health_timeout,
            pid_settle_timeout=pid_settle_timeout,
            platform=platform,
            runner=runner,
            uid=uid,
        )

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
    platform: str | None = None,
    supervisor_dir: Path | None = None,
    runner=None,
    uid: int | None = None,
    user: str | None = None,
) -> int:
    """Report daemon liveness + /health, returning a structured exit code.

    When a host-supervisor entry is registered, this reads the supervisor's
    own view of the job instead of the pidfile, and reports one of four
    states (running/restarting/failed/stopped); see the module docstring's
    "Host-supervisor awareness".
    """
    if _is_supervised(env, platform=platform, supervisor_dir=supervisor_dir):
        return _supervised_status(
            env,
            port=port,
            health_timeout=health_timeout,
            platform=platform,
            runner=runner,
            uid=uid,
            user=user,
        )

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


def open_ui(
    *,
    env: dict[str, str] | None = None,
    port: int = DAEMON_PORT,
    health_timeout: float = 2.0,
    opener=webbrowser.open,
) -> int:
    """Open the outpost web UI in the platform's default browser.

    Read-only: it spawns nothing and touches no pidfile. The daemon must already
    be answering ``/health`` — the same identity probe the other verbs use, so a
    stale pidfile or an unrelated process on the port can't pass for a running
    UI. When it isn't, this raises OutpostLifecycleError naming the start verb
    rather than handing the browser a URL that will fail to connect.

    ``opener`` is the injectable browser seam (default :func:`webbrowser.open`,
    stdlib and cross-platform — matching this module's no-macOS-assumptions
    invariant). It returns False when no browser could be launched at all, which
    is likewise a named error: silently printing success there would leave the
    operator waiting on a window that is never going to appear.
    """
    if _probe_health(port, health_timeout) is None:
        raise OutpostLifecycleError(
            f"outpost is not answering on {DAEMON_HOST}:{port}; "
            "start it with 'trailhead outpost start'."
        )

    url = f"http://{DAEMON_HOST}:{port}/"
    if not opener(url):
        raise OutpostLifecycleError(
            f"could not launch a browser for {url}; open it manually."
        )
    print(f"opened {url}")
    return 0


def restart(
    *,
    env: dict[str, str] | None = None,
    node_bin: str = "node",
    build_cmd: list[str] | None = None,
    port: int = DAEMON_PORT,
    health_timeout: float = 2.0,
    stop_timeout: float = _STOP_TIMEOUT_SECONDS,
    restart_health_timeout: float = 5.0,
    pid_settle_timeout: float | None = None,
    platform: str | None = None,
    supervisor_dir: Path | None = None,
    runner=None,
    uid: int | None = None,
    user: str | None = None,
) -> int:
    """Rebuild the outpost checkout, then stop and restart the daemon.

    When a host-supervisor entry is registered, the rebuild runs exactly as
    below and then the supervisor is told to restart the job (``launchctl
    kickstart -k`` / ``systemctl --user restart``) instead of this module
    calling ``stop``/``start`` itself; the pid is read back from the
    supervisor rather than the pidfile. See the module docstring's
    "Host-supervisor awareness".

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

    That liveness check waits ``pid_settle_timeout`` seconds for a doomed spawn
    to finish dying. It is a race the caller can lose on a loaded machine — a
    process dying on EADDRINUSE that has not exited yet still reads as alive,
    and the restart is then reported successful. Left unset, the window scales
    with ``restart_health_timeout`` (see :func:`_resolve_pid_settle_timeout`);
    pass it explicitly to widen the window on a machine known to be slow.
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

    if _is_supervised(env, platform=platform, supervisor_dir=supervisor_dir):
        return _supervised_restart(
            env,
            port=port,
            restart_health_timeout=restart_health_timeout,
            pid_settle_timeout=pid_settle_timeout,
            platform=platform,
            runner=runner,
            uid=uid,
        )

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
    settle_timeout = _resolve_pid_settle_timeout(
        pid_settle_timeout=pid_settle_timeout,
        restart_health_timeout=restart_health_timeout,
    )
    new_pid = _read_pid(_pidfile(env))
    if new_pid is None or not _settled_pid_alive(new_pid, settle_timeout):
        raise OutpostLifecycleError(
            "outpost restart: /health answered but the process start() spawned "
            f"(pid {new_pid}) is not alive; another process is likely still "
            f"holding port {port} (check the outpost log)."
        )

    return rc
