"""Behavioral tests for trailhead/outpost_lifecycle.py — the
``trailhead outpost start|stop|status|restart`` verbs.

TDD: written before the implementation. The real daemon is a Node dist build;
these tests stand a tiny stdlib ``http.server`` in for it (spawned via the
injectable ``node_bin`` seam pointed at ``sys.executable``). Real Node startup
latency is out of scope here — it is proven end-to-end in a later slice.

Every test routes config/state through the per-app override env vars
(OUTPOST_CONFIG_DIR / OUTPOST_STATE_DIR) into tmp_path, so nothing touches a
real ``~/.config``/``~/.local/state`` (Axiom 6).
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

import trailhead
from trailhead import cli, outpost_lifecycle
from trailhead import outpost_supervisor as osup
from trailhead.outpost_lifecycle import (
    EXIT_FAILED,
    EXIT_RESTARTING,
    EXIT_RUNNING,
    EXIT_STALE,
    EXIT_STOPPED,
    OutpostLifecycleError,
)

# A stand-in for the real Node dist build. Binds loopback on HTTP_PORT, serves
# a /health JSON payload carrying contract_version, and exits cleanly on SIGTERM.
FAKE_DAEMON = """\
import http.server
import json
import os
import signal
import threading

port = int(os.environ["HTTP_PORT"])


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps(
                {"ok": True, "contract_version": 1, "uptime_ms": 0}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", port), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *a: stop.set())
stop.wait()
server.shutdown()
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_until(pred, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def _health_reachable(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as r:
            json.loads(r.read())
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _pid(o) -> int:
    return int((o.state_dir / "outpost.pid").read_text().strip())


def _spawn_unrelated_process() -> subprocess.Popen:
    """A live process that is NOT the outpost daemon and never answers
    /health — stands in for the OS reassigning a dead daemon's pid to an
    unrelated process (pid reuse) before stop/start next inspects it."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# Fixture — a validated fake checkout + isolated config/state dirs
# ---------------------------------------------------------------------------


# The three binaries outpost_supervisor.enable() resolves via its injectable
# which_runner seam. Tests that exercise enable() stub these under tmp_path
# rather than passing shutil.which straight through to the real machine PATH —
# CI (and any dev machine) may not have `lore` installed/on PATH, and the
# fixture must not depend on that (Axiom 6 plus this run's CI-red finding).
_STUBBED_BINARIES = ("node", "git", "lore")


def _make_stub_which(bin_dir: Path):
    bin_dir.mkdir(parents=True, exist_ok=True)
    resolved: dict[str, str] = {}
    for name in _STUBBED_BINARIES:
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
        resolved[name] = str(path)

    def which(name: str) -> str | None:
        return resolved.get(name)

    return which


@pytest.fixture()
def outpost(tmp_path):
    checkout = tmp_path / "outpost-checkout"
    entry = checkout / "dist" / "server" / "index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text(FAKE_DAEMON)

    config_home = tmp_path / "cfg"
    state_home = tmp_path / "state"
    config_home.mkdir()
    (config_home / "config.toml").write_text(f'checkout = "{checkout}"\n')

    env = {
        "OUTPOST_CONFIG_DIR": str(config_home),
        "OUTPOST_STATE_DIR": str(state_home),
        "PATH": os.environ.get("PATH", ""),
        # A fake HOME under tmp_path: is_enabled()'s default supervisor-dir
        # resolution needs SOME HOME to resolve under, and pointing it at a
        # throwaway path (rather than leaving it unset) means the unsupervised
        # tests exercise the same "no entry registered" fallback a real host
        # takes, never an exception-driven one (Axiom 6 — never touch the
        # developer's real ~/Library/LaunchAgents).
        "HOME": str(tmp_path / "home"),
    }
    ns = SimpleNamespace(
        env=env,
        checkout=checkout,
        entry=entry,
        config_home=config_home,
        state_dir=state_home,
        supervisor_dir=tmp_path / "supervisor",
        port=_free_port(),
        which_runner=_make_stub_which(tmp_path / "bin"),
    )
    yield ns

    # Teardown: never leak a daemon out of the test.
    pidfile = state_home / "outpost.pid"
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
        except (ValueError, ProcessLookupError, OSError):
            pass


_REPO_ROOT = Path(trailhead.__file__).resolve().parent.parent


def _run_verb(
    o, call: str, *, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run a lifecycle verb (``call`` is the ``ol.<verb>(...)`` expression) in a
    short-lived subprocess, so any daemon it spawns is genuinely orphaned to init
    on exit — exactly as the real CLI process does. Running it in-process would
    leave the daemon as pytest's own child, where a killed process lingers as an
    unreaped zombie and os.kill(pid, 0) never reports it dead (masking the very
    liveness primitive under test)."""
    code = (
        "import os, sys\n"
        "from trailhead import outpost_lifecycle as ol\n"
        f"sys.exit(ol.{call})\n"
    )
    proc_env = {
        **o.env,
        **(extra_env or {}),
        "OUTPOST_TEST_PORT": str(o.port),
        "PYTHONPATH": str(_REPO_ROOT),
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        env=proc_env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _start(o) -> int:
    result = _run_verb(
        o, "start(node_bin=sys.executable, port=int(os.environ['OUTPOST_TEST_PORT']))"
    )
    assert result.returncode == 0, f"start subprocess failed: {result.stderr}"
    return result.returncode


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


def test_start_creates_pidfile_and_live_process(outpost):
    rc = _start(outpost)

    assert rc == 0
    pidfile = outpost.state_dir / "outpost.pid"
    assert pidfile.exists()
    assert _wait_until(lambda: _pid_alive(_pid(outpost)), timeout=3.0)
    assert (outpost.state_dir / "outpost.log").exists()


def test_second_start_is_idempotent_noop(outpost):
    assert _start(outpost) == 0
    first_pid = _pid(outpost)
    assert _wait_until(lambda: _pid_alive(first_pid), timeout=3.0)
    # start()'s idempotency check requires /health to answer, not just a live
    # pid — wait for it the same way test_stop_terminates_and_removes_pidfile
    # does, or the second start races the daemon's own startup and respawns.
    assert _wait_until(lambda: _health_reachable(outpost.port), timeout=5.0)

    assert _start(outpost) == 0
    # No respawn: same pid, still the original live process.
    assert _pid(outpost) == first_pid
    assert _pid_alive(first_pid)


def test_start_recovers_from_stale_pidfile(outpost):
    assert _start(outpost) == 0
    dead_pid = _pid(outpost)
    assert _wait_until(lambda: _pid_alive(dead_pid), timeout=3.0)

    os.kill(dead_pid, signal.SIGKILL)
    assert _wait_until(lambda: not _pid_alive(dead_pid), timeout=3.0)

    assert _start(outpost) == 0
    new_pid = _pid(outpost)
    assert new_pid != dead_pid
    assert _wait_until(lambda: _pid_alive(new_pid), timeout=3.0)


def test_start_does_not_treat_reused_pid_as_already_running(outpost):
    # The recorded pid is alive (liveness check would pass) but belongs to an
    # unrelated process that never answers /health — simulating the OS
    # reassigning a dead daemon's pid before start() runs. start() must not
    # report false idempotency; it must recognize the real daemon is down and
    # spawn it.
    unrelated = _spawn_unrelated_process()
    try:
        assert _wait_until(lambda: _pid_alive(unrelated.pid), timeout=3.0)
        pidfile = outpost.state_dir / "outpost.pid"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(f"{unrelated.pid}\n")

        rc = outpost_lifecycle.start(env=outpost.env, node_bin=sys.executable, port=outpost.port)

        assert rc == 0
        new_pid = _pid(outpost)
        assert new_pid != unrelated.pid
        assert _wait_until(lambda: _pid_alive(new_pid), timeout=3.0)
        assert _pid_alive(unrelated.pid)
    finally:
        unrelated.kill()
        unrelated.wait(timeout=5)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_terminates_and_removes_pidfile(outpost):
    assert _start(outpost) == 0
    pid = _pid(outpost)
    assert _wait_until(lambda: _pid_alive(pid), timeout=3.0)
    assert _wait_until(lambda: _health_reachable(outpost.port), timeout=5.0)

    rc = outpost_lifecycle.stop(env=outpost.env, port=outpost.port)

    assert rc == 0
    assert not (outpost.state_dir / "outpost.pid").exists()
    assert _wait_until(lambda: not _pid_alive(pid), timeout=3.0)


def test_stop_when_not_running_is_noop(outpost):
    assert not (outpost.state_dir / "outpost.pid").exists()
    assert outpost_lifecycle.stop(env=outpost.env) == 0


def test_stop_cleans_stale_pidfile(outpost):
    assert _start(outpost) == 0
    pid = _pid(outpost)
    assert _wait_until(lambda: _pid_alive(pid), timeout=3.0)
    os.kill(pid, signal.SIGKILL)
    assert _wait_until(lambda: not _pid_alive(pid), timeout=3.0)

    assert outpost_lifecycle.stop(env=outpost.env) == 0
    assert not (outpost.state_dir / "outpost.pid").exists()


def test_stop_does_not_signal_reused_pid_without_health_confirmation(outpost):
    # The recorded pid is alive (liveness check would pass) but belongs to an
    # unrelated process that never answers /health — simulating the OS
    # reassigning a dead daemon's pid before stop() runs. stop() must not
    # SIGTERM it; it must treat the pidfile as stale instead.
    unrelated = _spawn_unrelated_process()
    try:
        assert _wait_until(lambda: _pid_alive(unrelated.pid), timeout=3.0)
        pidfile = outpost.state_dir / "outpost.pid"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(f"{unrelated.pid}\n")

        rc = outpost_lifecycle.stop(env=outpost.env, port=outpost.port)

        assert rc == 0
        assert not pidfile.exists()
        assert _pid_alive(unrelated.pid)
    finally:
        unrelated.kill()
        unrelated.wait(timeout=5)


# ---------------------------------------------------------------------------
# status — structured exit codes
# ---------------------------------------------------------------------------


def test_status_running_reports_health(outpost, capsys):
    assert _start(outpost) == 0
    assert _wait_until(lambda: _health_reachable(outpost.port), timeout=5.0)

    rc = outpost_lifecycle.status(env=outpost.env, port=outpost.port)

    assert rc == EXIT_RUNNING
    out = capsys.readouterr().out
    assert "contract_version" in out


def test_status_stopped_when_no_pidfile(outpost):
    assert not (outpost.state_dir / "outpost.pid").exists()
    assert outpost_lifecycle.status(env=outpost.env, port=outpost.port) == EXIT_STOPPED


def test_status_stale_cleans_pidfile(outpost):
    assert _start(outpost) == 0
    pid = _pid(outpost)
    assert _wait_until(lambda: _pid_alive(pid), timeout=3.0)
    os.kill(pid, signal.SIGKILL)
    assert _wait_until(lambda: not _pid_alive(pid), timeout=3.0)

    rc = outpost_lifecycle.status(env=outpost.env, port=outpost.port)

    assert rc == EXIT_STALE
    assert not (outpost.state_dir / "outpost.pid").exists()


# ---------------------------------------------------------------------------
# Named errors — no spawn on a bad daemon path / missing config
# ---------------------------------------------------------------------------


def test_missing_config_raises_named_error(tmp_path):
    env = {
        "OUTPOST_CONFIG_DIR": str(tmp_path / "empty-cfg"),
        "OUTPOST_STATE_DIR": str(tmp_path / "state"),
    }
    with pytest.raises(OutpostLifecycleError):
        outpost_lifecycle.start(env=env, node_bin=sys.executable, port=_free_port())


def test_missing_checkout_key_raises_named_error(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.toml").write_text("port = 7313\n")
    env = {
        "OUTPOST_CONFIG_DIR": str(cfg),
        "OUTPOST_STATE_DIR": str(tmp_path / "state"),
    }
    with pytest.raises(OutpostLifecycleError):
        outpost_lifecycle.start(env=env, node_bin=sys.executable, port=_free_port())


def test_nonexistent_entrypoint_raises_named_error_nothing_spawned(outpost):
    outpost.entry.unlink()  # config still points at the checkout, dist file gone

    with pytest.raises(OutpostLifecycleError):
        outpost_lifecycle.start(env=outpost.env, node_bin=sys.executable, port=outpost.port)
    assert not (outpost.state_dir / "outpost.pid").exists()


def test_entrypoint_outside_checkout_raises_named_error_nothing_spawned(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    # A symlinked dist/ escaping the checkout: the resolved entrypoint lands
    # outside the configured checkout and must be rejected before any spawn.
    outside = tmp_path / "outside"
    (outside / "server").mkdir(parents=True)
    (outside / "server" / "index.js").write_text(FAKE_DAEMON)
    (checkout / "dist").symlink_to(outside)

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.toml").write_text(f'checkout = "{checkout}"\n')
    state = tmp_path / "state"
    env = {
        "OUTPOST_CONFIG_DIR": str(cfg),
        "OUTPOST_STATE_DIR": str(state),
    }

    with pytest.raises(OutpostLifecycleError):
        outpost_lifecycle.start(env=env, node_bin=sys.executable, port=_free_port())
    assert not (state / "outpost.pid").exists()


# ---------------------------------------------------------------------------
# restart — rebuild-then-restart
# ---------------------------------------------------------------------------


def _build_script(
    tmp_path: Path,
    *,
    exit_code: int = 0,
    assets: tuple[str, ...] = ("index-abc123.js", "index-def456.css"),
    record_old_pid_env: str | None = None,
) -> list[str]:
    """A fake build_cmd: writes dist-web/assets/* under cwd, then exits.

    When ``record_old_pid_env`` names an env var holding a pid, the script also
    writes ``build_order.txt`` recording whether that pid was still alive at the
    moment the build ran — used to prove build-before-stop ordering.
    """
    script = tmp_path / "fake_build.py"
    lines = [
        "import os, pathlib, sys",
        "assets = pathlib.Path('dist-web/assets')",
        "assets.mkdir(parents=True, exist_ok=True)",
    ]
    for name in assets:
        lines.append(f"(assets / {name!r}).write_text('built')")
    if record_old_pid_env:
        lines += [
            f"old_pid = os.environ.get({record_old_pid_env!r})",
            "alive = True",
            "if old_pid:",
            "    try:",
            "        os.kill(int(old_pid), 0)",
            "    except ProcessLookupError:",
            "        alive = False",
            "    pathlib.Path('build_order.txt').write_text('alive' if alive else 'dead')",
        ]
    lines.append(f"sys.exit({exit_code})")
    script.write_text("\n".join(lines) + "\n")
    return [sys.executable, str(script)]


def _restart(
    o,
    build_cmd: list[str],
    extra_env: dict[str, str] | None = None,
    restart_health_timeout: float | None = None,
    pid_settle_timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run ``restart`` the same orphaning way ``_start`` does. No cwd is set:
    ``restart`` runs the build with ``cwd=<checkout>`` itself, so the fake build
    script's relative writes land in the checkout regardless of the caller's cwd."""
    call = (
        "restart(node_bin=sys.executable, "
        "build_cmd=eval(os.environ['OUTPOST_TEST_BUILD_CMD']), "
        "port=int(os.environ['OUTPOST_TEST_PORT'])"
    )
    if restart_health_timeout is not None:
        call += f", restart_health_timeout={restart_health_timeout!r}"
    if pid_settle_timeout is not None:
        call += f", pid_settle_timeout={pid_settle_timeout!r}"
    call += ")"
    return _run_verb(
        o,
        call,
        extra_env={**(extra_env or {}), "OUTPOST_TEST_BUILD_CMD": repr(build_cmd)},
    )


def test_restart_running_daemon_builds_before_stopping(outpost, tmp_path):
    assert _start(outpost) == 0
    old_pid = _pid(outpost)
    assert _wait_until(lambda: _pid_alive(old_pid), timeout=3.0)
    assert _wait_until(lambda: _health_reachable(outpost.port), timeout=5.0)

    build_cmd = _build_script(tmp_path, record_old_pid_env="OUTPOST_TEST_OLD_PID")
    result = _restart(outpost, build_cmd, extra_env={"OUTPOST_TEST_OLD_PID": str(old_pid)})

    assert result.returncode == 0, result.stderr
    assert (outpost.checkout / "build_order.txt").read_text() == "alive"

    new_pid = _pid(outpost)
    assert new_pid != old_pid
    assert _wait_until(lambda: _pid_alive(new_pid), timeout=3.0)
    assert _wait_until(lambda: not _pid_alive(old_pid), timeout=3.0)
    assert _wait_until(lambda: _health_reachable(outpost.port), timeout=3.0)


def test_restart_output_includes_asset_filenames(outpost, tmp_path):
    assert _start(outpost) == 0
    assert _wait_until(lambda: _health_reachable(outpost.port), timeout=5.0)

    build_cmd = _build_script(tmp_path, assets=("index-abc123.js", "index-def456.css"))
    result = _restart(outpost, build_cmd)

    assert result.returncode == 0, result.stderr
    assert "index-abc123.js" in result.stdout
    assert "index-def456.css" in result.stdout


def test_restart_build_failure_raises_named_error_daemon_untouched(outpost, tmp_path):
    assert _start(outpost) == 0
    old_pid = _pid(outpost)
    assert _wait_until(lambda: _pid_alive(old_pid), timeout=3.0)
    assert _wait_until(lambda: _health_reachable(outpost.port), timeout=5.0)

    build_cmd = _build_script(tmp_path, exit_code=1)

    with pytest.raises(OutpostLifecycleError):
        outpost_lifecycle.restart(env=outpost.env, build_cmd=build_cmd, port=outpost.port)

    assert _pid_alive(old_pid)
    pidfile = outpost.state_dir / "outpost.pid"
    assert pidfile.exists()
    assert int(pidfile.read_text().strip()) == old_pid


def test_restart_when_stopped_builds_then_starts(outpost, tmp_path):
    assert not (outpost.state_dir / "outpost.pid").exists()

    build_cmd = _build_script(tmp_path)
    result = _restart(outpost, build_cmd)

    assert result.returncode == 0, result.stderr
    pidfile = outpost.state_dir / "outpost.pid"
    assert pidfile.exists()
    new_pid = int(pidfile.read_text().strip())
    assert _wait_until(lambda: _pid_alive(new_pid), timeout=3.0)


# A stand-in for a daemon that spawns (so its pid is alive) but never binds its
# HTTP server — simulates a new process that dies/hangs post-EADDRINUSE before
# it can serve /health.
_HUNG_DAEMON = """\
import signal
import threading

stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *a: stop.set())
stop.wait()
"""


def test_restart_raises_when_new_daemon_never_answers_health(outpost, tmp_path):
    # Simulate a rebuild that succeeds but whose restarted process never comes
    # up healthy (e.g. died on EADDRINUSE against a still-alive old daemon).
    outpost.entry.write_text(_HUNG_DAEMON)

    build_cmd = _build_script(tmp_path)
    result = _restart(outpost, build_cmd)

    assert result.returncode != 0
    assert "OutpostLifecycleError" in result.stderr
    assert "health" in result.stderr.lower()


def test_restart_raises_when_new_process_dies_but_stale_process_still_answers_health(
    outpost, tmp_path
):
    # An unmanaged process is already bound to the port (not tracked by any
    # pidfile trailhead knows about — e.g. a daemon started outside trailhead,
    # or one whose pidfile was lost). restart's spawn dies immediately on
    # EADDRINUSE, but /health keeps answering because the unmanaged process
    # answers it. restart must not report success: the pid it recorded is
    # dead, so it isn't the process actually serving /health.
    unmanaged_script = tmp_path / "unmanaged_daemon.py"
    unmanaged_script.write_text(FAKE_DAEMON)
    unmanaged = subprocess.Popen(
        [sys.executable, str(unmanaged_script)],
        env={**os.environ, "HTTP_PORT": str(outpost.port)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_until(lambda: _health_reachable(outpost.port), timeout=3.0)

        build_cmd = _build_script(tmp_path)
        # The settle window is named explicitly, and generously: this test's
        # whole subject is observing the doomed spawn die, and the default
        # window is a race the machine wins under parallel load. Waiting longer
        # costs nothing here — the check returns as soon as the pid is observed
        # dead, and only a spawn that never dies pays the full window.
        result = _restart(
            outpost, build_cmd, restart_health_timeout=1.0, pid_settle_timeout=10.0
        )

        assert result.returncode != 0
        assert "OutpostLifecycleError" in result.stderr
    finally:
        unmanaged.kill()
        unmanaged.wait(timeout=5)


def test_restart_missing_build_command_raises_named_error(outpost):
    with pytest.raises(OutpostLifecycleError, match="not-a-real-build-command"):
        outpost_lifecycle.restart(
            env=outpost.env,
            build_cmd=["not-a-real-build-command"],
            port=outpost.port,
        )


def test_restart_build_failure_includes_stdout_diagnostics(outpost, tmp_path):
    script = tmp_path / "fake_build_stdout.py"
    script.write_text(
        "import sys\n"
        "print('tsc: error TS2322 something is wrong')\n"
        "sys.exit(1)\n"
    )
    build_cmd = [sys.executable, str(script)]

    with pytest.raises(OutpostLifecycleError, match="TS2322"):
        outpost_lifecycle.restart(env=outpost.env, build_cmd=build_cmd, port=outpost.port)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_outpost_verbs_parse():
    parser = cli._build_parser()
    for verb in ("start", "stop", "status", "restart", "open", "enable", "disable"):
        args = parser.parse_args(["outpost", verb])
        assert args.command == "outpost"
        assert args.outpost_command == verb


def test_a_lifecycle_error_reaches_the_operator_as_one_clean_line(monkeypatch, capsys):
    """Raise the real error through the real CLI and read what an operator sees.

    Membership in `cli._TRAILHEAD_ERRORS` is the mechanism; the clean one-line
    refusal is the behaviour. Asserting the membership passes just as well when
    the guard around it has been removed, so this drives `cli.main` instead and
    reads stderr — the same route `test_orchestration_hardening.py` uses for the
    install-side errors.
    """

    def boom(*args, **kwargs):
        raise OutpostLifecycleError("the daemon refused to start")

    # `cli` imports `start` by name at module import, so its own binding is the
    # one the dispatch table holds — patching `outpost_lifecycle.start` would
    # leave the real daemon spawn in place.
    monkeypatch.setattr(cli, "start", boom)
    monkeypatch.setattr(sys, "argv", ["trailhead", "outpost", "start"])
    code = cli.main()
    err = capsys.readouterr().err

    assert code == 1
    assert err.strip() == "trailhead: the daemon refused to start"
    assert "Traceback" not in err


# ---------------------------------------------------------------------------
# restart's pid-settle window: caller-tunable, and derived from the health
# timeout when the caller does not name one
# ---------------------------------------------------------------------------


def test_settle_window_defaults_to_a_fraction_of_the_health_timeout():
    """A caller declaring a generous health tolerance is declaring a slow machine."""
    resolved = outpost_lifecycle._resolve_pid_settle_timeout(
        pid_settle_timeout=None, restart_health_timeout=30.0
    )
    assert resolved == pytest.approx(30.0 * outpost_lifecycle._PID_SETTLE_FRACTION)


def test_settle_window_never_shrinks_below_the_floor():
    """A short health timeout must not shrink the window below the fixed floor."""
    resolved = outpost_lifecycle._resolve_pid_settle_timeout(
        pid_settle_timeout=None, restart_health_timeout=1.0
    )
    assert resolved == outpost_lifecycle._PID_SETTLE_SECONDS


def test_an_explicit_settle_window_wins_over_the_derivation():
    resolved = outpost_lifecycle._resolve_pid_settle_timeout(
        pid_settle_timeout=7.5, restart_health_timeout=30.0
    )
    assert resolved == 7.5


def _stub_restart_up_to_the_pid_check(monkeypatch, tmp_path, recorder):
    """Stub everything ``restart`` does before the pid-liveness check.

    Leaves exactly that check live, so a test can pin which settle window
    ``restart`` actually hands it — the wiring, not just the arithmetic.
    """
    monkeypatch.setattr(outpost_lifecycle, "_resolve_checkout", lambda env: tmp_path)
    monkeypatch.setattr(
        outpost_lifecycle.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(outpost_lifecycle, "stop", lambda **k: 0)
    monkeypatch.setattr(outpost_lifecycle, "start", lambda **k: 0)
    monkeypatch.setattr(outpost_lifecycle, "_wait_for_health", lambda port, timeout: {})
    monkeypatch.setattr(outpost_lifecycle, "_read_pid", lambda pidfile: 4242)

    def _record(pid, timeout):
        recorder.append(timeout)
        return True

    monkeypatch.setattr(outpost_lifecycle, "_settled_pid_alive", _record)


def test_restart_hands_the_derived_window_to_the_liveness_check(monkeypatch, tmp_path):
    """The wiring pin: restart must not keep using the bare module constant."""
    seen: list[float] = []
    _stub_restart_up_to_the_pid_check(monkeypatch, tmp_path, seen)

    outpost_lifecycle.restart(build_cmd=["true"], restart_health_timeout=30.0)

    assert seen == [pytest.approx(30.0 * outpost_lifecycle._PID_SETTLE_FRACTION)]


def test_restart_hands_an_explicit_window_to_the_liveness_check(monkeypatch, tmp_path):
    seen: list[float] = []
    _stub_restart_up_to_the_pid_check(monkeypatch, tmp_path, seen)

    outpost_lifecycle.restart(
        build_cmd=["true"], restart_health_timeout=30.0, pid_settle_timeout=7.5
    )

    assert seen == [7.5]


# ---------------------------------------------------------------------------
# open — hand the running daemon's UI URL to the browser
# ---------------------------------------------------------------------------


class _RecordingOpener:
    """Stands in for webbrowser.open: records the URLs it was handed and
    reports whether a browser was successfully launched."""

    def __init__(self, result: bool = True):
        self.result = result
        self.urls: list[str] = []

    def __call__(self, url: str) -> bool:
        self.urls.append(url)
        return self.result


def test_open_hands_the_running_daemons_url_to_the_browser(outpost):
    assert _start(outpost) == 0
    assert _wait_until(lambda: _health_reachable(outpost.port), timeout=5.0)
    opener = _RecordingOpener()

    rc = outpost_lifecycle.open_ui(env=outpost.env, port=outpost.port, opener=opener)

    assert rc == 0
    assert opener.urls == [f"http://127.0.0.1:{outpost.port}/"]


def test_open_refuses_and_opens_nothing_when_the_daemon_is_not_answering(outpost):
    opener = _RecordingOpener()

    with pytest.raises(OutpostLifecycleError) as exc:
        outpost_lifecycle.open_ui(env=outpost.env, port=outpost.port, opener=opener)

    assert "trailhead outpost start" in str(exc.value)
    assert opener.urls == []


def test_open_raises_when_no_browser_could_be_launched(outpost):
    assert _start(outpost) == 0
    assert _wait_until(lambda: _health_reachable(outpost.port), timeout=5.0)
    opener = _RecordingOpener(result=False)

    with pytest.raises(OutpostLifecycleError) as exc:
        outpost_lifecycle.open_ui(env=outpost.env, port=outpost.port, opener=opener)

    assert f"http://127.0.0.1:{outpost.port}/" in str(exc.value)


def test_cli_open_verb_dispatches_to_open_ui(monkeypatch):
    """`trailhead outpost open` routes to the open verb, not to another one."""
    called: list[str] = []
    monkeypatch.setattr(cli, "open_ui", lambda: called.append("open") or 0)
    monkeypatch.setattr(cli, "status", lambda: called.append("status") or 0)
    monkeypatch.setattr(sys, "argv", ["trailhead", "outpost", "open"])

    assert cli.main() == 0
    assert called == ["open"]


# ---------------------------------------------------------------------------
# Supervised verbs — start/stop/restart/status drive the host supervisor when
# a supervisor entry is registered (trailhead outpost enable). No process is
# ever spawned in this branch; a recording runner stands in for
# launchctl/systemctl and a small in-process HTTP server stands in for the
# supervised daemon's /health endpoint.
# ---------------------------------------------------------------------------


class _RecordingRunner:
    """Records every argv it is called with and returns a CompletedProcess
    whose returncode/stdout come from the first matching argv prefix in
    ``returncode_by_prefix`` / ``stdout_by_prefix``. ``on_call`` (argv) -> None
    lets a test simulate a side effect of a real supervisor command, e.g. a
    `stop` argv actually shutting down the fake daemon's health server."""

    def __init__(self, on_call=None, returncode_by_prefix=None, stdout_by_prefix=None):
        self.calls: list[list] = []
        self._on_call = on_call
        self._returncode_by_prefix = returncode_by_prefix or {}
        self._stdout_by_prefix = stdout_by_prefix or {}

    def __call__(self, argv: list) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        if self._on_call is not None:
            self._on_call(argv)
        returncode = 0
        for prefix, code in self._returncode_by_prefix.items():
            if argv[: len(prefix)] == list(prefix):
                returncode = code
        for prefix, text in self._stdout_by_prefix.items():
            if argv[: len(prefix)] == list(prefix):
                return subprocess.CompletedProcess(argv, returncode, text, "")
        return subprocess.CompletedProcess(argv, returncode, "", "")


class _FakeHealthServer:
    """A tiny in-process stand-in for the supervised daemon's /health. start()
    and stop() are independently callable so a test can simulate the
    supervisor stopping and (if wanted) relaunching the daemon."""

    def __init__(self, port: int):
        self.port = port
        self._httpd = None
        self._thread = None

    def start(self) -> None:
        import http.server

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    body = json.dumps({"ok": True, "contract_version": 1}).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *a):
                pass

        self._httpd = http.server.HTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self._thread = None


@pytest.fixture()
def health_server(outpost):
    server = _FakeHealthServer(outpost.port)
    yield server
    server.stop()


def _write_supervisor_entry(outpost, platform: str) -> None:
    """Register *something* at the target path so is_enabled() reports True —
    the supervised verbs never read the entry's contents, only its presence."""
    target_dir = outpost.supervisor_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"{osup.LAUNCHD_LABEL}.plist" if platform == "darwin" else osup.SYSTEMD_UNIT_NAME
    (target_dir / name).write_text("placeholder\n")


def _systemd_show(
    *, main_pid: str = "0", active_state: str = "", sub_state: str = "", result: str = "", n_restarts: str = "0"
) -> str:
    """A ``systemctl show -p ... `` stand-in whose line order is deliberately
    the REVERSE of the requested property order (MainPID,ActiveState,SubState,
    Result,NRestarts) — real systemd prints in its own property order, not the
    caller's, so a parser that reads by position rather than by ``Key=Value``
    would read the wrong field."""
    return (
        f"NRestarts={n_restarts}\n"
        f"Result={result}\n"
        f"SubState={sub_state}\n"
        f"ActiveState={active_state}\n"
        f"MainPID={main_pid}\n"
    )


# ---- start -----------------------------------------------------------------


def test_supervised_start_records_kickstart_spawns_nothing_writes_no_pidfile(outpost, health_server):
    _write_supervisor_entry(outpost, "darwin")
    health_server.start()
    runner = _RecordingRunner()

    rc = outpost_lifecycle.start(
        env=outpost.env,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
        start_health_timeout=3.0,
    )

    assert rc == 0
    assert runner.calls == [["launchctl", "kickstart", f"gui/501/{osup.LAUNCHD_LABEL}"]]
    assert not (outpost.state_dir / "outpost.pid").exists()


def test_supervised_start_raises_named_error_when_health_never_answers(outpost):
    _write_supervisor_entry(outpost, "darwin")
    # No health server started — /health never answers.
    runner = _RecordingRunner()

    with pytest.raises(OutpostLifecycleError, match="supervisor"):
        outpost_lifecycle.start(
            env=outpost.env,
            port=outpost.port,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            uid=501,
            start_health_timeout=0.3,
        )


def test_supervised_start_linux_records_reset_failed_before_start(outpost, health_server):
    _write_supervisor_entry(outpost, "linux")
    health_server.start()
    runner = _RecordingRunner()

    rc = outpost_lifecycle.start(
        env=outpost.env,
        port=outpost.port,
        platform="linux",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        start_health_timeout=3.0,
    )

    assert rc == 0
    assert runner.calls == [
        ["systemctl", "--user", "reset-failed", osup.SYSTEMD_UNIT_NAME],
        ["systemctl", "--user", "start", osup.SYSTEMD_UNIT_NAME],
    ]


def test_supervised_start_darwin_raises_named_error_naming_kickstart_when_it_fails(outpost):
    _write_supervisor_entry(outpost, "darwin")
    runner = _RecordingRunner(returncode_by_prefix={("launchctl", "kickstart"): 1})

    with pytest.raises(OutpostLifecycleError, match="kickstart"):
        outpost_lifecycle.start(
            env=outpost.env,
            port=outpost.port,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            uid=501,
            start_health_timeout=0.3,
        )


def test_supervised_start_linux_raises_named_error_naming_systemctl_start_when_it_fails(outpost):
    _write_supervisor_entry(outpost, "linux")
    runner = _RecordingRunner(
        returncode_by_prefix={("systemctl", "--user", "start"): 1}
    )

    with pytest.raises(OutpostLifecycleError, match="systemctl --user start"):
        outpost_lifecycle.start(
            env=outpost.env,
            port=outpost.port,
            platform="linux",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            start_health_timeout=0.3,
        )


# ---- stop --------------------------------------------------------------


def test_supervised_stop_records_kill_term_argv_and_returns_once_health_stops(outpost, health_server):
    _write_supervisor_entry(outpost, "darwin")
    health_server.start()

    def on_call(argv):
        if argv == ["launchctl", "kill", "TERM", osup.launchd_service(501)]:
            health_server.stop()

    runner = _RecordingRunner(on_call=on_call)

    rc = outpost_lifecycle.stop(
        env=outpost.env,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
        timeout=3.0,
        pid_settle_timeout=0.3,
    )

    assert rc == 0
    # A pre-stop probe (to record the pid the relaunch check compares against)
    # comes first, then the kill; any further calls in the settle window are
    # the supervisor-state re-probe (launchctl print), never another stop.
    assert runner.calls[0][:2] == ["launchctl", "print"]
    assert runner.calls[1] == ["launchctl", "kill", "TERM", osup.launchd_service(501)]
    assert all(call[:2] == ["launchctl", "print"] for call in runner.calls[2:])


def test_supervised_stop_raises_did_not_exit_when_health_keeps_answering(outpost, health_server):
    _write_supervisor_entry(outpost, "darwin")
    health_server.start()
    runner = _RecordingRunner()  # never stops the health server

    with pytest.raises(OutpostLifecycleError, match="did not exit"):
        outpost_lifecycle.stop(
            env=outpost.env,
            port=outpost.port,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            uid=501,
            timeout=0.3,
        )


def test_supervised_stop_raises_named_error_when_supervisor_relaunches_it(outpost, health_server):
    _write_supervisor_entry(outpost, "darwin")
    health_server.start()

    def on_call(argv):
        if argv == ["launchctl", "kill", "TERM", osup.launchd_service(501)]:
            health_server.stop()
            threading.Timer(0.05, health_server.start).start()

    runner = _RecordingRunner(on_call=on_call)

    with pytest.raises(OutpostLifecycleError, match="relaunch"):
        outpost_lifecycle.stop(
            env=outpost.env,
            port=outpost.port,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            uid=501,
            timeout=3.0,
            pid_settle_timeout=0.5,
        )


def test_supervised_stop_raises_named_error_when_supervisor_reports_a_different_pid_before_health_answers(
    outpost, health_server
):
    # The supervisor can relaunch the job before /health comes back up (still
    # starting). A settle window that only re-polls /health misses this — it
    # also has to re-check the supervisor's own reported pid/state. A REAL
    # relaunch replaces the process, so the reported pid changes from the one
    # recorded before this stop (9191 -> 4242); that's the signal, not merely
    # "some pid is reported".
    _write_supervisor_entry(outpost, "darwin")
    health_server.start()

    stdout_by_prefix = {("launchctl", "print"): "state = running\n\tpid = 9191\n"}

    def on_call(argv):
        if argv == ["launchctl", "kill", "TERM", osup.launchd_service(501)]:
            health_server.stop()
            stdout_by_prefix[("launchctl", "print")] = "state = running\n\tpid = 4242\n"

    runner = _RecordingRunner(on_call=on_call, stdout_by_prefix=stdout_by_prefix)

    with pytest.raises(OutpostLifecycleError, match="relaunch"):
        outpost_lifecycle.stop(
            env=outpost.env,
            port=outpost.port,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            uid=501,
            timeout=3.0,
            pid_settle_timeout=0.5,
        )


def test_supervised_stop_succeeds_when_the_same_pid_lingers_past_the_settle_window(
    outpost, health_server
):
    # `launchctl kill TERM` only sends the signal — outpost closes its
    # listener at once so /health goes silent, but an open SSE subscriber can
    # keep the OLD process (and hence its pid, unchanged, still reported by
    # `launchctl print`) alive past the settle window until its own 2s
    # backstop fires. That must not read as a relaunch: the pid never
    # differed from the one recorded before this stop, and it never
    # disappeared and reappeared.
    _write_supervisor_entry(outpost, "darwin")
    health_server.start()

    def on_call(argv):
        if argv == ["launchctl", "kill", "TERM", osup.launchd_service(501)]:
            health_server.stop()

    runner = _RecordingRunner(
        on_call=on_call,
        stdout_by_prefix={("launchctl", "print"): "state = running\n\tpid = 9191\n"},
    )

    rc = outpost_lifecycle.stop(
        env=outpost.env,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
        timeout=3.0,
        pid_settle_timeout=0.3,
    )

    assert rc == 0


# ---- status --------------------------------------------------------------


def test_supervised_status_running_reports_exit_running(outpost, health_server):
    _write_supervisor_entry(outpost, "darwin")
    health_server.start()
    runner = _RecordingRunner(stdout_by_prefix={("launchctl", "print"): "state = running\n\tpid = 4242\n"})

    rc = outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
    )

    assert rc == EXIT_RUNNING


def test_supervised_status_restarting_reports_exit_restarting_with_count(outpost):
    _write_supervisor_entry(outpost, "linux")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("systemctl", "--user", "show"): _systemd_show(
                active_state="activating", sub_state="auto-restart", n_restarts="3"
            ),
            ("loginctl",): "yes\n",
        }
    )

    rc = outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="linux",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        user="alice",
    )

    assert rc == EXIT_RESTARTING


def test_supervised_status_restarting_message_names_the_restart_count(outpost, capsys):
    _write_supervisor_entry(outpost, "linux")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("systemctl", "--user", "show"): _systemd_show(
                active_state="activating", sub_state="auto-restart", n_restarts="3"
            ),
            ("loginctl",): "yes\n",
        }
    )

    outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="linux",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        user="alice",
    )

    assert "3" in capsys.readouterr().out


def test_supervised_status_failed_linux_reports_exit_failed_with_recovery_command(outpost, capsys):
    _write_supervisor_entry(outpost, "linux")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("systemctl", "--user", "show"): _systemd_show(
                active_state="failed", sub_state="dead", result="start-limit-hit", n_restarts="5"
            ),
            ("loginctl",): "yes\n",
        }
    )

    rc = outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="linux",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        user="alice",
    )

    assert rc == EXIT_FAILED
    out = capsys.readouterr().out
    assert "reset-failed" in out


def test_supervised_status_failed_linux_active_state_failed_without_start_limit_hit_reports_exit_failed(
    outpost, capsys
):
    # A unit can land in ActiveState=failed (e.g. its RestartSec exhausted a
    # non-start-limit failure path) without systemd ever setting
    # Result=start-limit-hit. That must still surface as EXIT_FAILED, not the
    # stopped default.
    _write_supervisor_entry(outpost, "linux")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("systemctl", "--user", "show"): _systemd_show(
                active_state="failed", sub_state="dead", result="", n_restarts="1"
            ),
            ("loginctl",): "yes\n",
        }
    )

    rc = outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="linux",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        user="alice",
    )

    assert rc == EXIT_FAILED
    out = capsys.readouterr().out
    assert "reset-failed" in out


def test_supervised_status_failed_darwin_nonzero_last_exit_reports_exit_failed(outpost, capsys):
    _write_supervisor_entry(outpost, "darwin")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("launchctl", "print"): "state = not running\n\tlast exit code = 78\n",
        }
    )

    rc = outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
    )

    assert rc == EXIT_FAILED
    assert "trailhead outpost start" in capsys.readouterr().out


def test_supervised_status_stopped_reports_exit_stopped(outpost):
    _write_supervisor_entry(outpost, "darwin")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("launchctl", "print"): "state = not running\n\tlast exit code = 0\n",
        }
    )

    rc = outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
    )

    assert rc == EXIT_STOPPED


def test_supervised_status_darwin_never_exited_form_reports_exit_stopped(outpost):
    # A live-until-now job's `last exit code` reads `(never exited)` on a real
    # Mac, not a bare integer — `int()`-ing that raises. With no pid (the "not
    # running" case here) and no numeric exit code, this must read as stopped,
    # not blow up.
    _write_supervisor_entry(outpost, "darwin")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("launchctl", "print"): "state = not running\n\tlast exit code = (never exited)\n",
        }
    )

    rc = outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
    )

    assert rc == EXIT_STOPPED


def test_supervised_status_darwin_named_exit_code_form_reports_exit_failed(outpost, capsys):
    # A failed job can print `last exit code = 78: EX_CONFIG` — a leading
    # integer followed by the symbolic name, not a bare integer.
    _write_supervisor_entry(outpost, "darwin")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("launchctl", "print"): "state = not running\n\tlast exit code = 78: EX_CONFIG\n",
        }
    )

    rc = outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
    )

    assert rc == EXIT_FAILED
    assert "trailhead outpost start" in capsys.readouterr().out


def test_supervised_status_darwin_terminating_signal_reports_exit_failed(outpost, capsys):
    # A job killed by a signal (e.g. OOM) prints a `last terminating signal`
    # line instead of a nonzero `last exit code` — that must also read as a
    # failure needing recovery, not a clean stop.
    _write_supervisor_entry(outpost, "darwin")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("launchctl", "print"): (
                "state = not running\n"
                "\tlast exit code = 0\n"
                "\tlast terminating signal = SIGKILL\n"
            ),
        }
    )

    rc = outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
    )

    assert rc == EXIT_FAILED
    assert "trailhead outpost start" in capsys.readouterr().out


def test_supervised_status_linux_linger_no_prints_boot_warning(outpost, capsys):
    _write_supervisor_entry(outpost, "linux")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("systemctl", "--user", "show"): _systemd_show(active_state="inactive", sub_state="dead"),
            ("loginctl",): "no\n",
        }
    )

    outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="linux",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        user="alice",
    )

    out = capsys.readouterr().out
    assert "enable-linger" in out


def test_supervised_status_linux_linger_yes_omits_boot_warning(outpost, capsys):
    _write_supervisor_entry(outpost, "linux")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("systemctl", "--user", "show"): _systemd_show(active_state="inactive", sub_state="dead"),
            ("loginctl",): "yes\n",
        }
    )

    outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="linux",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        user="alice",
    )

    out = capsys.readouterr().out
    assert "enable-linger" not in out


def test_supervised_status_removes_leftover_pidfile(outpost):
    _write_supervisor_entry(outpost, "darwin")
    pidfile = outpost.state_dir / "outpost.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text("99999\n")
    runner = _RecordingRunner(
        stdout_by_prefix={
            ("launchctl", "print"): "state = not running\n\tlast exit code = 0\n",
        }
    )

    outpost_lifecycle.status(
        env=outpost.env,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
    )

    assert not pidfile.exists()


# ---- restart --------------------------------------------------------------


def test_supervised_restart_builds_then_runs_restart_argv_confirms_health_and_pid(
    outpost, health_server, tmp_path
):
    _write_supervisor_entry(outpost, "darwin")
    build_cmd = _build_script(tmp_path)

    # A pid that's genuinely alive throughout the test (this test process
    # itself) — the settle-window liveness confirmation added for the "pid
    # dies within the settle window" case would otherwise reject any made-up
    # pid here too. The pre-restart probe reads a DIFFERENT (made-up) pid, so
    # this also exercises the "confirm the pid changed" check with a real
    # relaunch rather than accidentally satisfying it by coincidence.
    live_pid = os.getpid()
    stdout_by_prefix = {("launchctl", "print"): "state = running\n\tpid = 424242\n"}

    def on_call(argv):
        if argv[:2] == ["launchctl", "kickstart"]:
            health_server.start()
            stdout_by_prefix[("launchctl", "print")] = f"state = running\n\tpid = {live_pid}\n"

    runner = _RecordingRunner(on_call=on_call, stdout_by_prefix=stdout_by_prefix)

    rc = outpost_lifecycle.restart(
        env=outpost.env,
        build_cmd=build_cmd,
        port=outpost.port,
        platform="darwin",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        uid=501,
        restart_health_timeout=3.0,
        pid_settle_timeout=0.3,
    )

    assert rc == 0
    kickstart_calls = [c for c in runner.calls if c[:2] == ["launchctl", "kickstart"]]
    assert kickstart_calls == [["launchctl", "kickstart", "-k", f"gui/501/{osup.LAUNCHD_LABEL}"]]


def test_supervised_restart_darwin_raises_named_error_naming_kickstart_when_it_fails(outpost, tmp_path):
    _write_supervisor_entry(outpost, "darwin")
    build_cmd = _build_script(tmp_path)
    runner = _RecordingRunner(returncode_by_prefix={("launchctl", "kickstart", "-k"): 1})

    with pytest.raises(OutpostLifecycleError, match="kickstart"):
        outpost_lifecycle.restart(
            env=outpost.env,
            build_cmd=build_cmd,
            port=outpost.port,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            uid=501,
            restart_health_timeout=0.3,
        )


def test_supervised_restart_linux_raises_named_error_naming_systemctl_restart_when_it_fails(
    outpost, tmp_path
):
    _write_supervisor_entry(outpost, "linux")
    build_cmd = _build_script(tmp_path)
    runner = _RecordingRunner(returncode_by_prefix={("systemctl", "--user", "restart"): 1})

    with pytest.raises(OutpostLifecycleError, match="systemctl --user restart"):
        outpost_lifecycle.restart(
            env=outpost.env,
            build_cmd=build_cmd,
            port=outpost.port,
            platform="linux",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            restart_health_timeout=0.3,
        )


def test_supervised_restart_linux_records_reset_failed_before_restart(outpost, health_server, tmp_path):
    _write_supervisor_entry(outpost, "linux")
    build_cmd = _build_script(tmp_path)
    live_pid = os.getpid()
    stdout_by_prefix = {
        ("systemctl", "--user", "show"): _systemd_show(main_pid="424242", active_state="active")
    }

    def on_call(argv):
        if argv == ["systemctl", "--user", "restart", osup.SYSTEMD_UNIT_NAME]:
            health_server.start()
            stdout_by_prefix[("systemctl", "--user", "show")] = _systemd_show(
                main_pid=str(live_pid), active_state="active"
            )

    runner = _RecordingRunner(on_call=on_call, stdout_by_prefix=stdout_by_prefix)

    rc = outpost_lifecycle.restart(
        env=outpost.env,
        build_cmd=build_cmd,
        port=outpost.port,
        platform="linux",
        supervisor_dir=outpost.supervisor_dir,
        runner=runner,
        restart_health_timeout=3.0,
        pid_settle_timeout=0.3,
    )

    assert rc == 0
    assert runner.calls[0][:3] == ["systemctl", "--user", "show"]
    assert runner.calls[1:3] == [
        ["systemctl", "--user", "reset-failed", osup.SYSTEMD_UNIT_NAME],
        ["systemctl", "--user", "restart", osup.SYSTEMD_UNIT_NAME],
    ]


def test_supervised_restart_raises_when_reported_pid_did_not_change(outpost, health_server, tmp_path):
    # kickstart -k can return 0 and /health can answer while the job never
    # actually restarted (e.g. a supervisor bug, or a race that re-reads the
    # still-running old process). The pid the supervisor reports afterward
    # must differ from the one recorded before this restart, or the "restart"
    # cannot be trusted to have happened at all.
    _write_supervisor_entry(outpost, "darwin")
    build_cmd = _build_script(tmp_path)
    live_pid = os.getpid()

    def on_call(argv):
        if argv[:2] == ["launchctl", "kickstart"]:
            health_server.start()

    runner = _RecordingRunner(
        on_call=on_call,
        stdout_by_prefix={("launchctl", "print"): f"state = running\n\tpid = {live_pid}\n"},
    )

    with pytest.raises(OutpostLifecycleError, match="same pid"):
        outpost_lifecycle.restart(
            env=outpost.env,
            build_cmd=build_cmd,
            port=outpost.port,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            uid=501,
            restart_health_timeout=3.0,
            pid_settle_timeout=0.3,
        )


def test_supervised_restart_build_failure_raises_without_touching_supervisor(outpost, tmp_path):
    _write_supervisor_entry(outpost, "darwin")
    build_cmd = _build_script(tmp_path, exit_code=1)
    runner = _RecordingRunner()

    with pytest.raises(OutpostLifecycleError):
        outpost_lifecycle.restart(
            env=outpost.env,
            build_cmd=build_cmd,
            port=outpost.port,
            platform="darwin",
            supervisor_dir=outpost.supervisor_dir,
            runner=runner,
            uid=501,
        )

    assert runner.calls == []


def test_supervised_restart_raises_when_reported_pid_dies_within_settle_window(
    outpost, health_server, tmp_path
):
    # /health answering alone doesn't prove the pid the supervisor reported is
    # the one that's actually up — a doomed process can still be observed
    # "alive" for a moment before it exits. The check must hold the pid live
    # across the settle window (like the unsupervised restart path does with
    # _settled_pid_alive), not just take one live sample.
    _write_supervisor_entry(outpost, "darwin")
    build_cmd = _build_script(tmp_path)

    dying = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.1)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    stdout_by_prefix = {("launchctl", "print"): "state = not running\n"}

    def on_call(argv):
        if argv[:2] == ["launchctl", "kickstart"]:
            health_server.start()
            stdout_by_prefix[("launchctl", "print")] = f"state = running\n\tpid = {dying.pid}\n"

    runner = _RecordingRunner(on_call=on_call, stdout_by_prefix=stdout_by_prefix)

    try:
        with pytest.raises(OutpostLifecycleError, match="not alive"):
            outpost_lifecycle.restart(
                env=outpost.env,
                build_cmd=build_cmd,
                port=outpost.port,
                platform="darwin",
                supervisor_dir=outpost.supervisor_dir,
                runner=runner,
                uid=501,
                restart_health_timeout=3.0,
                pid_settle_timeout=1.0,
            )
    finally:
        try:
            dying.wait(timeout=5)
        except (subprocess.TimeoutExpired, ChildProcessError):
            pass
