"""Tests for `ranger start` — probe Outpost, ask it to start the hub, attach tmux.
Every test fences HOME, XDG roots and per-app *_DIR overrides into tmp_path and
talks to a local stub HTTP server on an ephemeral port; tmux is never executed
except by the TRAILHEAD_TMUX_REAL-gated smoke.
"""

import json
import os
import pty
import select
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from trailhead.paths import state_dir
from trailhead.ranger.cli import main
from trailhead.ranger.config import HubConfig, write_hub_config
from trailhead.ranger.outpost_client import tmux_attach_argv

FIXTURE = Path(__file__).parent / "fixtures" / "ranger-contract.json"
CREDENTIAL = "s3cret-operator-credential"


def _env(tmp_path: Path, *, tmux: bool = True, extra: dict | None = None) -> dict[str, str]:
    root = tmp_path / "fence"
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    if tmux:
        stub = bindir / "tmux"
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    env = {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_STATE_HOME": str(root / "state"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "RANGER_CONFIG_DIR": str(root / "ranger-config"),
        "RANGER_STATE_DIR": str(root / "ranger-state"),
        "RANGER_CACHE_DIR": str(root / "ranger-cache"),
        "OUTPOST_STATE_DIR": str(root / "outpost-state"),
        "CAMP_CONFIG_DIR": str(root / "camp-config"),
        "CAMP_STATE_DIR": str(root / "camp-state"),
        "CAMP_CACHE_DIR": str(root / "camp-cache"),
        "PATH": str(bindir),
    }
    env.update(extra or {})
    return env


def _configure(env, *accounts):
    for account in accounts:
        write_hub_config(
            HubConfig(name=f"Hub {account}", account=account, launch_account="default"), env=env
        )


def _write_credential(env, value=CREDENTIAL):
    path = state_dir("outpost", env=env) / json.loads(FIXTURE.read_text())["credential_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    path.chmod(0o600)


class _Stub:
    def __init__(self, *, health="outpost", status=200, body=None, delay=0.0):
        self.health = health
        self.status = status
        self.body = body if body is not None else {"started": True, "socket": "outpost-hubs", "target": "hub-x"}
        self.delay = delay
        self.posts: list[tuple[str, str | None]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/health" and isinstance(stub.health, (dict, list)):
                    payload = json.dumps(stub.health).encode()
                    ctype = "application/json"
                elif self.path == "/health" and stub.health == "outpost":
                    payload = json.dumps({"ok": True, "contract_version": 8}).encode()
                    ctype = "application/json"
                else:
                    payload, ctype = b"<html>not outpost</html>", "text/html"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                stub.posts.append((self.path, self.headers.get("Authorization")))
                time.sleep(stub.delay)
                payload = json.dumps(stub.body).encode()
                try:
                    self.send_response(stub.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    made = []

    def make(**kw):
        s = _Stub(**kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


class _Exec:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, env):
        self.calls.append((argv, env))


def _start(env, *, port, argv=("start",), timeout=5.0):
    ex = _Exec()
    rc = main(list(argv), env=env, port=port, request_timeout=timeout, exec_fn=ex)
    return rc, ex


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("account", ["acme", "x1-y2"])
def test_up_posts_with_bearer_then_execs_tmux_attach(tmp_path, stub, account):
    env = _env(tmp_path)
    _configure(env, account)
    _write_credential(env)
    s = stub()
    rc, ex = _start(env, port=s.port)
    assert rc == 0
    assert s.posts == [(f"/operator/hubs/{account}/start", f"Bearer {CREDENTIAL}")]
    assert ex.calls[0][0] == ["tmux", "-L", "outpost-hubs", "attach-session", "-t", f"=hub-{account}"]


def test_response_naming_other_socket_or_target_does_not_change_argv(tmp_path, stub):
    env = _env(tmp_path)
    _configure(env, "acme")
    _write_credential(env)
    s = stub(body={"started": False, "socket": "evil-sock", "target": "hub-other"})
    _, ex = _start(env, port=s.port)
    assert ex.calls[0][0] == ["tmux", "-L", "outpost-hubs", "attach-session", "-t", "=hub-acme"]


def test_tmux_set_removes_it_from_exec_env_and_names_inner_detach(tmp_path, stub, capsys):
    env = _env(tmp_path, extra={"TMUX": "/tmp/tmux-1/default,1,0", "KEEP": "1"})
    _configure(env, "acme")
    _write_credential(env)
    s = stub()
    _, ex = _start(env, port=s.port)
    out = capsys.readouterr().out
    assert "TMUX" not in ex.calls[0][1]
    assert ex.calls[0][1]["KEEP"] == "1"
    assert "prefix twice" in out
    assert "hub-acme" in out or "acme" in out


def test_tmux_unset_leaves_env_unchanged_and_names_plain_detach(tmp_path, stub, capsys):
    env = _env(tmp_path, extra={"KEEP": "1"})
    _configure(env, "acme")
    _write_credential(env)
    s = stub()
    _, ex = _start(env, port=s.port)
    out = capsys.readouterr().out
    assert ex.calls[0][1] == env
    assert "prefix twice" not in out
    assert "detach" in out


def test_outpost_down_refuses_names_start_command_no_credential_read(tmp_path, capsys):
    env = _env(tmp_path)
    _configure(env, "acme")
    # no credential file: a read would raise the restart message instead
    rc, ex = _start(env, port=_free_port())
    err = capsys.readouterr().err
    assert rc != 0 and ex.calls == []
    assert "trailhead outpost start" in err
    assert "restart" not in err


def test_non_outpost_listener_is_treated_as_down(tmp_path, stub, capsys):
    env = _env(tmp_path)
    _configure(env, "acme")
    _write_credential(env)
    s = stub(health="other")
    rc, ex = _start(env, port=s.port)
    assert rc != 0 and ex.calls == [] and s.posts == []
    assert "trailhead outpost start" in capsys.readouterr().err


@pytest.mark.parametrize(
    "health",
    [
        {},
        {"ok": True},
        {"contract_version": 8},
        {"ok": False, "contract_version": 8},
        {"ok": True, "contract_version": "8"},
        {"ok": True, "contract_version": True},
        [1, 2],
    ],
)
def test_json_listener_without_outpost_health_shape_is_down(tmp_path, stub, capsys, health):
    env = _env(tmp_path)
    _configure(env, "acme")
    # no credential file: a read would raise the restart message instead
    s = stub(health=health)
    rc, ex = _start(env, port=s.port)
    err = capsys.readouterr().err
    assert rc != 0 and ex.calls == [] and s.posts == []
    assert "trailhead outpost start" in err
    assert "restart" not in err


def test_hub_exited_at_launch_prints_output(tmp_path, stub, capsys):
    env = _env(tmp_path)
    _configure(env, "acme")
    _write_credential(env)
    s = stub(status=502, body={"error": "hub_exited_at_launch", "step": "launch", "output": "boom line\nsecond"})
    rc, ex = _start(env, port=s.port)
    err = capsys.readouterr().err
    assert rc != 0 and ex.calls == []
    assert "ranger: hub exited at launch:" in err
    assert "boom line\nsecond" in err


def test_401_says_restart_outpost(tmp_path, stub, capsys):
    env = _env(tmp_path)
    _configure(env, "acme")
    _write_credential(env)
    s = stub(status=401, body={"error": "operator_credential_required"})
    rc, ex = _start(env, port=s.port)
    assert rc != 0 and ex.calls == []
    assert "trailhead outpost restart" in capsys.readouterr().err


def test_other_refusal_prints_its_message(tmp_path, stub, capsys):
    env = _env(tmp_path)
    _configure(env, "acme")
    _write_credential(env)
    s = stub(status=422, body={"error": "trailhead_refused", "message": "no such launch account"})
    rc, ex = _start(env, port=s.port)
    assert rc != 0 and ex.calls == []
    assert "no such launch account" in capsys.readouterr().err


def test_unresponsive_start_times_out_without_exec(tmp_path, stub, capsys):
    env = _env(tmp_path)
    _configure(env, "acme")
    _write_credential(env)
    s = stub(delay=2.0)
    rc, ex = _start(env, port=s.port, timeout=0.3)
    err = capsys.readouterr().err
    assert rc != 0 and ex.calls == []
    assert "timed out" in err


def test_missing_credential_says_restart_and_does_not_post(tmp_path, stub, capsys):
    env = _env(tmp_path)
    _configure(env, "acme")
    s = stub()
    rc, ex = _start(env, port=s.port)
    assert rc != 0 and ex.calls == [] and s.posts == []
    assert "trailhead outpost restart" in capsys.readouterr().err


def test_tmux_missing_refuses_before_post(tmp_path, stub, capsys):
    env = _env(tmp_path, tmux=False)
    _configure(env, "acme")
    _write_credential(env)
    s = stub()
    rc, ex = _start(env, port=s.port)
    assert rc != 0 and ex.calls == [] and s.posts == []
    assert "tmux" in capsys.readouterr().err


def test_no_hub_configured_refuses_naming_setup(tmp_path, stub, capsys):
    env = _env(tmp_path)
    s = stub()
    rc, ex = _start(env, port=s.port)
    assert rc != 0 and ex.calls == [] and s.posts == []
    assert "ranger setup" in capsys.readouterr().err


def test_single_hub_is_used_without_account(tmp_path, stub):
    env = _env(tmp_path)
    _configure(env, "solo")
    _write_credential(env)
    s = stub()
    _, ex = _start(env, port=s.port)
    assert ex.calls[0][0][-1] == "=hub-solo"


def test_two_hubs_without_account_refuses_listing_both(tmp_path, stub, capsys):
    env = _env(tmp_path)
    _configure(env, "one", "two")
    _write_credential(env)
    s = stub()
    rc, ex = _start(env, port=s.port)
    err = capsys.readouterr().err
    assert rc != 0 and ex.calls == [] and s.posts == []
    assert "one" in err and "two" in err


def test_two_hubs_with_account_picks_it(tmp_path, stub):
    env = _env(tmp_path)
    _configure(env, "one", "two")
    _write_credential(env)
    s = stub()
    _, ex = _start(env, port=s.port, argv=("start", "--account", "two"))
    assert ex.calls[0][0][-1] == "=hub-two"


def test_invalid_account_refused_before_any_request(tmp_path, stub, capsys):
    env = _env(tmp_path)
    s = stub()
    rc, ex = _start(env, port=s.port, argv=("start", "--account", "../x"))
    assert rc != 0 and ex.calls == [] and s.posts == []
    assert "ranger:" in capsys.readouterr().err


@pytest.mark.parametrize("account", ["acme", "b2-c3"])
def test_socket_and_session_pattern_match_contract_fixture(account):
    contract = json.loads(FIXTURE.read_text())
    argv = tmux_attach_argv(account)
    assert argv[argv.index("-L") + 1] == contract["tmux_socket"]
    assert argv[-1] == "=" + contract["hub_session_pattern"].replace("<account>", account)


def test_start_route_matches_contract_fixture(tmp_path, stub):
    contract = json.loads(FIXTURE.read_text())
    env = _env(tmp_path)
    _configure(env, "acme")
    _write_credential(env)
    s = stub()
    _start(env, port=s.port)
    method, route = contract["start_route"].split(" ", 1)
    assert method == "POST"
    assert s.posts[0][0] == route.replace(":account", "acme")


@pytest.mark.skipif(os.environ.get("TRAILHEAD_TMUX_REAL") != "1", reason="needs real tmux; set TRAILHEAD_TMUX_REAL=1")
def test_real_tmux_attach_then_detach_leaves_session(tmp_path):
    sock = f"ranger-smoke-{uuid.uuid4().hex[:8]}"
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    base = ["tmux", "-L", sock]
    argv = tmux_attach_argv("smoke")
    argv[argv.index("-L") + 1] = sock
    try:
        subprocess.run(base + ["new-session", "-d", "-s", "hub-smoke", "sleep 60"], check=True, env=env)
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe(argv[0], argv, env)
        time.sleep(1.0)
        os.write(fd, b"\x02d")
        deadline = time.time() + 5
        done = False
        while time.time() < deadline and not done:
            if select.select([fd], [], [], 0.2)[0]:
                try:
                    if not os.read(fd, 4096):
                        done = True
                except OSError:
                    done = True
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                done = True
        assert done, "client did not detach"
        listed = subprocess.run(
            base + ["list-sessions", "-F", "#{session_name}"], capture_output=True, text=True, env=env
        )
        assert "hub-smoke" in listed.stdout.split()
    finally:
        subprocess.run(base + ["kill-server"], env=env, capture_output=True)
