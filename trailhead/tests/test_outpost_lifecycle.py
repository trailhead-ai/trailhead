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
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

import trailhead
from trailhead import cli, outpost_lifecycle
from trailhead.outpost_lifecycle import (
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
    }
    ns = SimpleNamespace(
        env=env,
        checkout=checkout,
        entry=entry,
        config_home=config_home,
        state_dir=state_home,
        port=_free_port(),
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
    for verb in ("start", "stop", "status", "restart"):
        args = parser.parse_args(["outpost", verb])
        assert args.command == "outpost"
        assert args.outpost_command == verb


def test_named_error_registered_for_clean_cli_output():
    # The top-level guard converts these into a clean 'trailhead: <msg>' line.
    assert OutpostLifecycleError in cli._TRAILHEAD_ERRORS


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
