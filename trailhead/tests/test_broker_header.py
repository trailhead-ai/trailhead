"""trailhead broker-header: exchanges a session's broker credentials at Outpost's token endpoint."""

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from trailhead import broker_header, cli, outpost_lifecycle
from trailhead.paths import state_dir

CONTRACT = json.loads((Path(__file__).parent / "fixtures" / "ranger-contract.json").read_text())
SECRET = "s3cret-do-not-leak"
TOKEN = "tok-issued-123"
CLIENT_ID = "client-abc"


class _Stub:
    """A loopback token endpoint whose answer is chosen per test."""

    def __init__(self):
        self.requests = []
        self.mode = "ok"
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=()):
                self.send_response(code)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                stub.requests.append(("GET", self.path, b""))
                self._send(200, b"{}")

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                stub.requests.append(("POST", self.path, raw))
                echo = f"bad {SECRET} {TOKEN}".encode()
                if stub.mode == "ok":
                    body = json.dumps({"access_token": TOKEN, "token_type": "Bearer", "expires_in": 3600})
                    self._send(200, body.encode(), [("Content-Type", "application/json")])
                elif stub.mode == "401":
                    self._send(401, echo)
                elif stub.mode == "204":
                    self._send(204)
                elif stub.mode == "500":
                    self._send(500, echo)
                elif stub.mode == "not-json":
                    self._send(200, echo)
                elif stub.mode == "no-token":
                    self._send(200, json.dumps({"detail": echo.decode()}).encode())
                elif stub.mode == "bad-token":
                    self._send(200, json.dumps({"access_token": "a b\r\nX-Evil: 1"}).encode())
                elif stub.mode == "spaced-token":
                    self._send(200, json.dumps({"access_token": "two words"}).encode())
                elif stub.mode == "redirect":
                    self._send(302, b"", [("Location", "/elsewhere")])
                elif stub.mode == "hang":
                    time.sleep(3)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub(monkeypatch):
    s = _Stub()
    monkeypatch.setattr(outpost_lifecycle, "DAEMON_PORT", s.port)
    yield s
    s.close()


def _fence(tmp_path):
    root = tmp_path.resolve() / "fence"
    return {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_STATE_HOME": str(root / "state"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "OUTPOST_STATE_DIR": str(root / "outpost-state"),
        "PATH": os.environ.get("PATH", ""),
    }


def _credential(tmp_path, session="sess-1", body=None):
    d = tmp_path / "outpost-state" / "sessions" / session
    d.mkdir(parents=True, exist_ok=True)
    f = d / "broker-client.json"
    f.write_text(body if body is not None else json.dumps({"client_id": CLIENT_ID, "client_secret": SECRET}))
    return f


def _run(monkeypatch, capsys, env, credential):
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.argv", ["trailhead", "broker-header", "--credential-file", str(credential)])
    rc = cli.main()
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _failure_file(env, session):
    return state_dir("trailhead", env=env) / "broker-header" / f"{session}.failure"


def test_a_200_prints_the_authorization_header_with_the_issued_token(tmp_path, monkeypatch, capsys, stub):
    env = _fence(tmp_path)
    rc, out, err = _run(monkeypatch, capsys, env, _credential(tmp_path))
    assert rc == 0
    assert json.loads(out) == {"Authorization": f"Bearer {TOKEN}"}
    assert err == ""
    method, path, raw = stub.requests[0]
    assert (method, path) == ("POST", CONTRACT["broker_token_path"])
    form = parse_qs(raw.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_id"] == [CLIENT_ID] and form["client_secret"] == [SECRET]
    assert not _failure_file(env, "sess-1").exists()


def test_it_exchanges_from_a_directory_with_no_hub_configuration(tmp_path, monkeypatch, capsys, stub):
    env = _fence(tmp_path)
    bare = tmp_path / "bare-workspace"
    bare.mkdir()
    monkeypatch.chdir(bare)
    rc, out, _ = _run(monkeypatch, capsys, env, _credential(tmp_path, session="ws-9"))
    assert rc == 0 and json.loads(out) == {"Authorization": f"Bearer {TOKEN}"}


FAILURES = [
    ("401", "http-401"),
    ("500", "http-500"),
    ("204", "http-204"),
    ("not-json", "malformed-response"),
    ("no-token", "malformed-response"),
    ("bad-token", "malformed-response"),
    ("spaced-token", "malformed-response"),
    ("redirect", "redirect-refused"),
]


@pytest.mark.parametrize("mode,category", FAILURES)
def test_a_bad_answer_fails_closed_with_a_secret_free_failure_line(
    tmp_path, monkeypatch, capsys, stub, mode, category
):
    stub.mode = mode
    env = _fence(tmp_path)
    rc, out, err = _run(monkeypatch, capsys, env, _credential(tmp_path))
    assert rc != 0 and out == ""
    line = _failure_file(env, "sess-1").read_text()
    assert category in line and line.count("\n") == 1 and len(line) < 200
    for text in (out, err, line):
        assert SECRET not in text and TOKEN not in text and "X-Evil" not in text
    if mode == "redirect":
        assert [r[1] for r in stub.requests] == [CONTRACT["broker_token_path"]]


def test_a_closed_port_is_unreachable(tmp_path, monkeypatch, capsys):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    monkeypatch.setattr(outpost_lifecycle, "DAEMON_PORT", closed)
    env = _fence(tmp_path)
    rc, out, err = _run(monkeypatch, capsys, env, _credential(tmp_path))
    assert rc != 0 and out == ""
    line = _failure_file(env, "sess-1").read_text()
    assert "unreachable" in line
    assert SECRET not in err + line


def test_a_slow_outpost_gives_up_inside_the_harness_budget(tmp_path, monkeypatch, capsys, stub):
    stub.mode = "hang"
    monkeypatch.setattr(broker_header, "TIMEOUT_SECONDS", 0.3)
    env = _fence(tmp_path)
    started = time.monotonic()
    rc, out, _ = _run(monkeypatch, capsys, env, _credential(tmp_path))
    assert rc != 0 and out == "" and time.monotonic() - started < 2.5
    assert "unreachable" in _failure_file(env, "sess-1").read_text()


def test_the_port_comes_from_trailheads_setting_not_the_environment(tmp_path, monkeypatch, capsys, stub):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    monkeypatch.setattr(outpost_lifecycle, "DAEMON_PORT", closed)
    env = {**_fence(tmp_path), "OUTPOST_URL": f"http://127.0.0.1:{stub.port}",
           "OUTPOST_PORT": str(stub.port)}
    rc, out, _ = _run(monkeypatch, capsys, env, _credential(tmp_path))
    assert rc != 0 and out == "" and stub.requests == []


def test_a_missing_credential_file_fails_without_contacting_outpost(tmp_path, monkeypatch, capsys, stub):
    env = _fence(tmp_path)
    missing = tmp_path / "outpost-state" / "sessions" / "sess-1" / "broker-client.json"
    rc, out, err = _run(monkeypatch, capsys, env, missing)
    assert rc != 0 and out == "" and stub.requests == []
    assert "credential-missing" in _failure_file(env, "sess-1").read_text()


def test_an_unreadable_credential_file_is_reported_as_such(tmp_path, monkeypatch, capsys, stub):
    env = _fence(tmp_path)
    directory = tmp_path / "outpost-state" / "sessions" / "sess-1" / "broker-client.json"
    directory.mkdir(parents=True)
    rc, out, _ = _run(monkeypatch, capsys, env, directory)
    assert rc != 0 and out == ""
    assert "credential-unreadable" in _failure_file(env, "sess-1").read_text()


@pytest.mark.parametrize(
    "body",
    ["not json", "[]", json.dumps({"client_id": CLIENT_ID}), json.dumps({"client_id": "", "client_secret": SECRET}),
     json.dumps({"client_id": CLIENT_ID, "client_secret": 5})],
)
def test_a_malformed_credential_file_fails_and_does_not_echo_its_contents(
    tmp_path, monkeypatch, capsys, stub, body
):
    env = _fence(tmp_path)
    rc, out, err = _run(monkeypatch, capsys, env, _credential(tmp_path, body=body))
    assert rc != 0 and out == "" and stub.requests == []
    line = _failure_file(env, "sess-1").read_text()
    assert "credential-malformed" in line
    assert SECRET not in err + line and body not in err + line


def test_the_failure_line_is_named_for_the_session_dir_and_overwritten(tmp_path, monkeypatch, capsys, stub):
    stub.mode = "401"
    env = _fence(tmp_path)
    cred = _credential(tmp_path, session="sess-1")
    _run(monkeypatch, capsys, env, cred)
    stub.mode = "500"
    _run(monkeypatch, capsys, env, cred)
    line = _failure_file(env, "sess-1").read_text()
    assert "http-500" in line and "http-401" not in line and line.count("\n") == 1


def test_a_credential_outside_a_session_dir_files_its_failure_under_unknown(tmp_path, monkeypatch, capsys, stub):
    stub.mode = "401"
    env = _fence(tmp_path)
    odd = tmp_path / "not a session"
    odd.mkdir()
    f = odd / "broker-client.json"
    f.write_text(json.dumps({"client_id": CLIENT_ID, "client_secret": SECRET}))
    rc, out, _ = _run(monkeypatch, capsys, env, f)
    assert rc != 0 and out == ""
    assert "http-401" in _failure_file(env, "unknown").read_text()


def test_an_unwritable_state_dir_still_fails_closed(tmp_path, monkeypatch, capsys, stub):
    stub.mode = "401"
    env = _fence(tmp_path)
    Path(env["XDG_STATE_HOME"]).parent.mkdir(parents=True, exist_ok=True)
    Path(env["XDG_STATE_HOME"]).write_text("a file where a directory must be")
    rc, out, err = _run(monkeypatch, capsys, env, _credential(tmp_path))
    assert rc != 0 and out == "" and SECRET not in err


def test_the_token_path_constant_matches_the_contract_fixture():  # inert-gate: allow cross-repo contract seam
    assert broker_header.BROKER_TOKEN_PATH == CONTRACT["broker_token_path"]
