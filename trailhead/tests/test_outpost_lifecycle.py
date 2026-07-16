"""Behavioral tests for trailhead/outpost_lifecycle.py — the
``trailhead outpost start|stop|status`` verbs.

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


def _start(o) -> int:
    """Run ``start`` in a short-lived subprocess so the daemon is genuinely
    orphaned to init on exit — exactly as the real CLI process does. Running it
    in-process would leave the daemon as pytest's own child, where a killed
    process lingers as an unreaped zombie and os.kill(pid, 0) never reports it
    dead (masking the very liveness primitive under test)."""
    code = (
        "import os, sys\n"
        "from trailhead import outpost_lifecycle as ol\n"
        "sys.exit(ol.start(node_bin=sys.executable, "
        "port=int(os.environ['OUTPOST_TEST_PORT'])))\n"
    )
    proc_env = {
        **o.env,
        "OUTPOST_TEST_PORT": str(o.port),
        "PYTHONPATH": str(_REPO_ROOT),
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=proc_env,
        capture_output=True,
        text=True,
        timeout=15,
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
# CLI wiring
# ---------------------------------------------------------------------------


def test_outpost_verbs_parse():
    parser = cli._build_parser()
    for verb in ("start", "stop", "status"):
        args = parser.parse_args(["outpost", verb])
        assert args.command == "outpost"
        assert args.outpost_command == verb


def test_named_error_registered_for_clean_cli_output():
    # The top-level guard converts these into a clean 'trailhead: <msg>' line.
    assert OutpostLifecycleError in cli._TRAILHEAD_ERRORS
