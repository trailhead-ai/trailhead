"""The red baselines of the gateway end-to-end runs boot a trailhead and an Outpost from before the gateway socket.

The real boot is host-only (TRAILHEAD_GATEWAY_E2E=1, run on andromeda by the operator through
`scripts/run-e2e-red-then-green.sh` and `scripts/run-e2e-cancel-red-then-green.sh`). What these tests drive,
ungated, is each decision the end-to-end support makes about the checkouts it is pointed at, on fake checkout
trees: how the token recorder sits in front of an Outpost, and how the trailhead under test is aimed at the
Outpost.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from . import _gateway_e2e_support as support
from ._gateway_e2e_support import RealOutpost, step_env, token_recorder

GATEWAY_FILES = {
    "trailhead/__init__.py": "",
    "trailhead/gateway/__init__.py": "",
    "trailhead/gateway/register.py": "PORT = 7313\n",
    "trailhead/gateway/command.py": (
        "def main(argv=None, *, port=7313):\n    return port\n\n\n"
        "def auth_header_main(argv=None, *, port=7313):\n    return port\n"
    ),
}


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def pre_socket_outpost(tmp_path, monkeypatch) -> Path:
    tree = _tree(tmp_path / "outpost-old", {"server/index.ts": ""})
    monkeypatch.setenv("TRAILHEAD_GATEWAY_E2E_OUTPOST", str(tree))
    return tree


@pytest.fixture
def socket_outpost(tmp_path, monkeypatch) -> Path:
    tree = _tree(tmp_path / "outpost-new", {"server/index.ts": "", "server/gateway/socket.ts": ""})
    monkeypatch.setenv("TRAILHEAD_GATEWAY_E2E_OUTPOST", str(tree))
    return tree


@pytest.fixture
def pre_socket_trailhead(tmp_path, monkeypatch) -> Path:
    tree = _tree(
        tmp_path / "trailhead-old",
        {**GATEWAY_FILES, "trailhead/gateway/outpost_http.py": "DAEMON_PORT = 7313\n"},
    )
    monkeypatch.setenv("TRAILHEAD_GATEWAY_E2E_TRAILHEAD", str(tree))
    return tree


@pytest.fixture
def socket_trailhead(tmp_path, monkeypatch) -> Path:
    tree = _tree(
        tmp_path / "trailhead-new",
        {**GATEWAY_FILES, "trailhead/gateway/outpost_http.py": "def socket_path(env=None):\n    return None\n"},
    )
    monkeypatch.setenv("TRAILHEAD_GATEWAY_E2E_TRAILHEAD", str(tree))
    return tree


def _outpost(tmp_path: Path, port: int = 4242) -> RealOutpost:
    return RealOutpost(port, tmp_path / "state", tmp_path / "outpost.log")


def _upstream_that_answers_a_token(handled: list[bytes]) -> tuple[socket.socket, int]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)

    def serve() -> None:
        client, _ = server.accept()
        with client:
            handled.append(client.recv(65536))
            client.sendall(b'HTTP/1.0 200 OK\r\n\r\n{"access_token": "tok-from-outpost"}')

    threading.Thread(target=serve, daemon=True).start()
    return server, server.getsockname()[1]


# --- the recorder in front of the Outpost under test ------------------------------------------------


def test_a_pre_socket_outpost_gets_a_tcp_relay_that_learns_the_tokens_on_its_port(pre_socket_outpost, tmp_path):
    handled: list[bytes] = []
    upstream, upstream_port = _upstream_that_answers_a_token(handled)
    with upstream, token_recorder(_outpost(tmp_path, upstream_port)) as recorder:
        with socket.create_connection(("127.0.0.1", recorder.port), timeout=5) as client:
            client.sendall(b"GET /gateway/runs HTTP/1.0\r\nAuthorization: Bearer tok-from-client\r\n\r\n")
            answer = b""
            while chunk := client.recv(4096):
                answer += chunk
    assert b"tok-from-outpost" in answer, "the relay did not pass the Outpost's answer back"
    assert b"tok-from-client" in handled[0], "the relay did not pass the request on"
    assert recorder.tokens == {"tok-from-client", "tok-from-outpost"}


def test_an_outpost_that_serves_the_socket_gets_the_socket_relay_and_no_tcp_port(socket_outpost, tmp_path):
    recorder = token_recorder(_outpost(tmp_path))
    assert type(recorder) is support.TokenRecorder
    assert recorder.socket_path == support.gateway_socket_path(tmp_path / "state")
    assert recorder.port is None


# --- aiming the trailhead under test at the Outpost -------------------------------------------------


def _run_in(env: dict[str, str], code: str, cwd: Path) -> str:
    done = subprocess.run([sys.executable, "-c", code], env=env, cwd=cwd, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout


SHOW_PORTS = (
    "import json, trailhead.gateway.register as r, trailhead.gateway.command as c;"
    "print(json.dumps([r.PORT, c.main(), c.auth_header_main()]))"
)


def test_a_trailhead_from_before_the_socket_reaches_the_outpost_on_the_recorders_port(
    pre_socket_trailhead, tmp_path
):
    env = step_env(tmp_path, _outpost(tmp_path, 4242), port=5151)
    assert json.loads(_run_in(env, SHOW_PORTS, tmp_path)) == [5151, 5151, 5151]


def test_a_trailhead_from_before_the_socket_reaches_the_outposts_own_port_when_nothing_relays(
    pre_socket_trailhead, tmp_path
):
    env = step_env(tmp_path, _outpost(tmp_path, 4242))
    assert json.loads(_run_in(env, SHOW_PORTS, tmp_path)) == [4242, 4242, 4242]


def test_a_trailhead_with_the_socket_client_is_left_to_find_the_socket_itself(socket_trailhead, tmp_path):
    env = step_env(tmp_path, _outpost(tmp_path, 4242), port=5151)
    assert json.loads(_run_in(env, SHOW_PORTS, tmp_path)) == [7313, 7313, 7313]


class _Built:
    def __init__(self) -> None:
        self.interpreter: str | None = None


@pytest.fixture
def built(monkeypatch) -> _Built:
    seen = _Built()

    def fake_build(*args, **kwargs):
        seen.interpreter = sys.executable
        return Path("shim-bin")

    monkeypatch.setattr(support, "build_gateway_shims", fake_build)
    monkeypatch.setattr(support, "make_trusted_checkout", lambda dest, source: source)
    return seen


def test_shims_pinned_for_a_trailhead_from_before_the_socket_start_an_interpreter_aimed_at_the_port(
    pre_socket_trailhead, built, tmp_path
):
    support.install_shims(tmp_path, _outpost(tmp_path, 4242), port=5151)
    assert built.interpreter != sys.executable
    done = subprocess.run(
        [built.interpreter, "-I", "-S", "-c", SHOW_PORTS], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [5151, 5151, 5151]


def test_shims_pinned_for_a_trailhead_with_the_socket_client_keep_the_real_interpreter(
    socket_trailhead, built, tmp_path
):
    support.install_shims(tmp_path, _outpost(tmp_path, 4242))
    assert built.interpreter == sys.executable
