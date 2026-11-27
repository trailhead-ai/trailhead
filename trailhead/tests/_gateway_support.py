"""Shared fixtures for tests of the Outpost gateway commands.

``StubOutpost`` is a stand-in for the Outpost routes the gateway
commands call, served on the unix socket Outpost serves them on (``<state>/gateway/gateway.sock``); ``run_under_harness`` runs a shell command as a child of a fake
harness process, so the command finds a real harness ancestor and a real session
key; ``run_gateway_runner`` uses it to run ``_gateway_runner.py`` and returns what
the command printed. The ``*_cmd`` builders are the shell commands that run the two
test entry points, shared with the end-to-end test.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from trailhead.harness.process import format_session_key, harness_process

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_RUNNER = Path(__file__).with_name("_gateway_runner.py")
REGISTER_RUNNER = Path(__file__).with_name("_register_runner.py")

CLIENT_ID = "client-id-SEEDED-1f3a"
CLIENT_SECRET = "client-secret-SEEDED-9c7e"
TOKEN = "access-token-SEEDED-5b2d"
REGISTRATION_AUTHORITY = "registration-authority-SEEDED-7d4e"


def free_port() -> int:
    """A loopback port nothing is listening on (once this returns)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def gateway_socket_path(state: Path) -> Path:
    """Where the client looks for Outpost's gateway socket when Outpost's state folder is *state*."""
    return state / "gateway" / "gateway.sock"


def _socket_folder(state: Path) -> Path:
    folder = gateway_socket_path(state).parent
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    return folder


@contextmanager
def silent_socket(state: Path) -> Iterator[Path]:
    """The gateway socket of *state*, accepting connections (the kernel completes them) and never answering one."""
    _socket_folder(state)
    path = gateway_socket_path(state)
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(str(path))
        s.listen(16)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)


def stale_socket(state: Path) -> Path:
    """A socket file at the gateway path that nothing listens on, as a stopped Outpost leaves behind."""
    _socket_folder(state)
    path = gateway_socket_path(state)
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(str(path))
    return path


@contextmanager
def denied_socket_folder(state: Path) -> Iterator[Path]:
    """The gateway socket of *state*, in a folder this process is denied (mode 0000) until the block ends.

    Root passes a folder's mode check, so a test using this skips when run as root."""
    path = gateway_socket_path(state)
    path.parent.mkdir(mode=0o700, parents=True)
    path.parent.chmod(0o000)
    try:
        yield path
    finally:
        path.parent.chmod(0o700)


@contextmanager
def tcp_squatter() -> Iterator[socket.socket]:
    """A TCP listener on the port Outpost's cockpit uses, as a squatting process would hold it; nothing it is sent is
    read, but ``accept`` (half a second's wait) shows whether anything connected."""
    import pytest

    from trailhead.outpost_lifecycle import DAEMON_PORT

    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", DAEMON_PORT))
        except OSError:
            pytest.skip(f"127.0.0.1:{DAEMON_PORT} is in use on this host, so a squatter cannot be set up")
        s.listen(8)
        s.settimeout(0.5)
        yield s


def short_state_dir() -> Path:
    """A fresh folder whose gateway socket path fits in a socket address (about 100 bytes)."""
    return Path(tempfile.mkdtemp(prefix="gw-"))


def runner_env(
    outpost_state: Path, *, trailhead_state: Path | None = None, extra: dict[str, str] | None = None
) -> dict[str, str]:
    """The environment a test entry point runs in: Outpost's state dir, and this checkout importable."""
    env = {**os.environ, "OUTPOST_STATE_DIR": str(outpost_state), "PYTHONPATH": str(REPO_ROOT)}
    if trailhead_state is not None:
        env["TRAILHEAD_STATE_DIR"] = str(trailhead_state)
    env.update(extra or {})
    return env


def gateway_runner_cmd(env_dump: Path, argv: list[str]) -> str:
    """Shell command running the gateway command, dumping its environment to *env_dump*."""
    return shlex.join([sys.executable, str(GATEWAY_RUNNER), str(env_dump), *argv])


def register_runner_cmd(argv: list[str]) -> str:
    """Shell command running the ``trailhead`` CLI."""
    return shlex.join([sys.executable, str(REGISTER_RUNNER), *argv])


def run_under_harness(
    launcher: Path, command: str, *, env: dict[str, str], on_start: Callable[[int], None], stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    """Run shell *command* as a child of the fake harness *launcher*.

    The command is held until *on_start* has been called with the harness's pid, so
    a test can read the harness or seed a file before the command looks for it.
    *stdin* is what the command reads after that.
    """
    proc = subprocess.Popen(
        [str(launcher), "-c", f"read go; {command}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )
    on_start(proc.pid)
    out, err = proc.communicate("go\n" + stdin, timeout=60)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


class Cut:
    """A ``log_steps`` entry for a chunked log: announce a chunk, send part of it, and close the socket."""


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str]
    body: str
    raw: bytes = b""


class _UnixHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_UNIX
    daemon_threads = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "outpost", 0


@contextmanager
def unix_http_server(state: Path, handler: type[BaseHTTPRequestHandler]) -> Iterator[None]:
    """Serve *handler* on the gateway socket of *state*, as Outpost does."""
    _socket_folder(state)
    server = _UnixHTTPServer(str(gateway_socket_path(state)), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()


@dataclass
class StubOutpost:
    """Loopback Outpost: issues TOKEN for the right client, serves ``runs`` for TOKEN.

    ``token_reply`` / ``runs_reply`` / ``start_reply`` / ``view_reply`` — ``(status, body)`` —
    override the answers wholesale; ``body`` may be a dict (sent as JSON) or a str (sent verbatim).
    ``POST /gateway/runs`` answers 202 (or ``start_reply``) and keeps the uploaded tar in
    ``requests``; ``GET /gateway/runs/<id>`` serves ``view``; ``GET /gateway/runs/<id>/log``
    writes ``log_steps`` in order, a ``str`` as it is, a ``threading.Event`` as a wait until it
    is set, then closes the connection (an early close is a dropped stream). ``log_connections``,
    when set, gives each successive log request its own steps (the last repeats); ``view_sequence``
    likewise gives each successive view request its own body; ``log_chunked`` sends the log chunked,
    and a ``Cut`` step ends it mid-chunk. ``log_reply`` answers the log route wholesale (a refusal).
    ``POST /gateway/runs/<id>/cancel`` answers 202 ``completed``/``cancelled`` (or ``cancel_reply``),
    after ``cancel_delay`` seconds.
    ``on_register`` is called with the session key a successful registration answers for (a real Outpost
    writes the session's credential file there); ``register_delay`` and ``token_delay`` hold those two
    answers back that many seconds. With ``token_uses`` set, each exchange issues a fresh token that is
    accepted for that many authorised calls and then refused as ``expiry_reason`` (``credential_expired`` unless set).
    ``state`` is Outpost's state folder (default: the test process's ``OUTPOST_STATE_DIR``); the stub serves
    ``<state>/gateway/gateway.sock``.
    ``release`` is set
    on exit so no waiting step outlives the stub.
    """

    state: Path | None = None
    workspace: str = "trailhead/outpost-gateway"
    runs: list[dict] = field(default_factory=list)
    client_id: str = CLIENT_ID
    client_secret: str = CLIENT_SECRET
    token: str = TOKEN
    token_reply: tuple[int, object] | None = None
    runs_reply: tuple[int, object] | None = None
    token_redirect: str | None = None
    runs_redirect: str | None = None
    authority: str = REGISTRATION_AUTHORITY
    register_reply: tuple[int, object] | None = None
    start_reply: tuple[int, object] | None = None
    view: dict | None = None
    view_reply: tuple[int, object] | None = None
    cancel_reply: tuple[int, object] | None = None
    cancel_delay: float = 0.0
    on_register: Callable[[str], None] | None = None
    register_delay: float = 0.0
    token_delay: float = 0.0
    token_uses: int | None = None
    expiry_reason: str = "credential_expired"
    log_reply: tuple[int, object] | None = None
    log_steps: list = field(default_factory=list)
    log_connections: list[list] | None = None
    view_sequence: list[dict] | None = None
    log_chunked: bool = False
    requests: list[Seen] = field(default_factory=list)
    issued: list[str] = field(default_factory=list)
    uses: dict[str, int] = field(default_factory=dict)
    views_served: int = 0
    release: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status: int, body: object) -> None:
                payload = body if isinstance(body, str) else json.dumps(body)
                raw = payload.encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _record(self, raw: bytes) -> None:
                stub.requests.append(
                    Seen(
                        self.command,
                        self.path,
                        {k.lower(): v for k, v in self.headers.items()},
                        raw.decode(errors="replace"),
                        raw,
                    )
                )

            def _authorised(self) -> bool:
                bearer = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                if stub.token_uses is None and bearer == stub.token:
                    return True
                if stub.token_uses is not None and bearer in stub.issued:
                    stub.uses[bearer] = stub.uses.get(bearer, 0) + 1
                    if stub.uses[bearer] <= stub.token_uses:
                        return True
                    self._send(401, {"reason": stub.expiry_reason, "message": "This access token has expired."})
                    return False
                self._send(401, {"reason": "credential_unrecognised", "message": "The bearer token is not recognised."})
                return False

            def _stream_log(self) -> None:
                logs = sum(1 for q in stub.requests if urlsplit(q.path).path.endswith("/log"))
                steps = stub.log_steps
                if stub.log_connections is not None:
                    steps = stub.log_connections[min(logs - 1, len(stub.log_connections) - 1)]
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                if stub.log_chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for step in steps:
                    if isinstance(step, threading.Event):
                        while not (step.is_set() or stub.release.is_set()):
                            step.wait(0.05)
                        continue
                    if isinstance(step, Cut):
                        self.wfile.write(b"ff\r\npartial")
                        self.wfile.flush()
                        self.close_connection = True
                        return
                    data = step.encode()
                    if stub.log_chunked:
                        data = f"{len(data):x}\r\n".encode() + data + b"\r\n"
                    self.wfile.write(data)
                    self.wfile.flush()
                if stub.log_chunked:
                    self.wfile.write(b"0\r\n\r\n")

            def _redirect(self, location: str) -> None:
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                body = raw.decode(errors="replace")
                self._record(raw)
                if urlsplit(self.path).path == "/gateway/runs":
                    if stub.start_reply is not None:
                        return self._send(*stub.start_reply)
                    if self._authorised():
                        self._send(202, {"id": 7, "state": "queued", "matrix": None, "not_run": 0})
                    return
                parts = urlsplit(self.path).path.split("/")
                if (parts[:3] == ["", "gateway", "runs"] and len(parts) == 5
                        and parts[3].isdigit() and parts[4] == "cancel"):
                    stub.release.wait(stub.cancel_delay)
                    if stub.cancel_reply is not None:
                        return self._send(*stub.cancel_reply)
                    if self._authorised():
                        self._send(202, {"id": int(parts[3]), "state": "completed", "result": "cancelled"})
                    return
                if self.path == "/gateway/sessions":
                    if stub.register_reply is not None:
                        return self._send(*stub.register_reply)
                    if self.headers.get("Authorization") != f"Bearer {stub.authority}":
                        return self._send(
                            401, {"reason": "registration_authority_required", "message": "No authority."}
                        )
                    stub.release.wait(stub.register_delay)
                    sent = json.loads(body)
                    key = f"ws-{sent['pid']}-{sent['start_time']}"
                    if stub.on_register is not None:
                        stub.on_register(key)
                    return self._send(200, {"session_key": key, "workspace": stub.workspace})
                if stub.token_redirect is not None and self.path == "/broker/token":
                    return self._redirect(stub.token_redirect)
                if self.path != "/broker/token":
                    return self._send(404, {"error": "not_found"})
                if stub.token_reply is not None:
                    return self._send(*stub.token_reply)
                if "grant_type=client_credentials" not in body:
                    return self._send(400, {"error": "unsupported_grant_type"})
                expected = base64.b64encode(f"{stub.client_id}:{stub.client_secret}".encode()).decode()
                if self.headers.get("Authorization") != f"Basic {expected}":
                    return self._send(401, {"error": "invalid_client"})
                stub.release.wait(stub.token_delay)
                token = stub.token
                if stub.token_uses is not None:
                    token = f"{stub.token}-{len(stub.issued) + 1}"
                    stub.issued.append(token)
                self._send(200, {"access_token": token, "token_type": "Bearer", "scope": "gateway"})

            def do_GET(self):
                self._record(b"")
                parts = urlsplit(self.path).path.split("/")
                if len(parts) in (4, 5) and parts[:3] == ["", "gateway", "runs"] and parts[3].isdigit():
                    if len(parts) == 5 and parts[4] == "log":
                        if stub.log_reply is not None:
                            return self._send(*stub.log_reply)
                        if stub.view_reply is not None and stub.view_reply[0] == 404:
                            return self._send(*stub.view_reply)
                        if self._authorised():
                            self._stream_log()
                        return
                    if len(parts) == 4:
                        if stub.view_reply is not None:
                            return self._send(*stub.view_reply)
                        if self._authorised():
                            if stub.view_sequence is not None:
                                stub.views_served += 1
                                return self._send(
                                    200, stub.view_sequence[min(stub.views_served - 1, len(stub.view_sequence) - 1)]
                                )
                            self._send(200, stub.view)
                        return
                if stub.runs_redirect is not None and self.path == "/gateway/runs":
                    return self._redirect(stub.runs_redirect)
                if self.path != "/gateway/runs":
                    return self._send(404, {"error": "not_found"})
                if stub.runs_reply is not None:
                    return self._send(*stub.runs_reply)
                if self._authorised():
                    self._send(200, {"workspace": stub.workspace, "runs": stub.runs})

        if self.state is None:
            self.state = Path(os.environ["OUTPOST_STATE_DIR"])
        _socket_folder(self.state)
        self.socket_path = gateway_socket_path(self.state)
        self._server = _UnixHTTPServer(str(self.socket_path), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "StubOutpost":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.release.set()
        self._server.shutdown()
        self._server.server_close()
        self.socket_path.unlink(missing_ok=True)


def write_credential(
    state_dir: Path, key: str, *, client_id: str = CLIENT_ID, client_secret: str = CLIENT_SECRET
) -> Path:
    path = state_dir / "sessions" / key / "gateway-client.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"client_id": client_id, "client_secret": client_secret}))
    path.chmod(0o600)
    return path


def write_authority(state_dir: Path, authority: str = REGISTRATION_AUTHORITY, *, mode: int = 0o600) -> Path:
    """Outpost's registration authority file, written at the mode Outpost writes it (0600) unless *mode* says
    otherwise."""
    path = state_dir / "registration" / "credential"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(authority + "\n")
    path.chmod(mode)
    return path


def await_harness(
    pid: int,
    *,
    resolve: Callable[..., tuple[int, int] | None] = harness_process,
    timeout: float = 5.0,
    interval: float = 0.02,
) -> tuple[int, int]:
    """``(pid, start_time)`` of the fake harness *pid*, once the process table shows it.

    A just-spawned process is not always readable as a harness the instant ``Popen``
    returns, and until it is, the walk can land on an outer session instead; this waits
    for the answer to be *pid* itself rather than reading once.
    """
    deadline = time.monotonic() + timeout
    while True:
        found = resolve(start_pid=pid)
        if found is not None and found[0] == pid:
            return found
        if time.monotonic() >= deadline:
            raise AssertionError("fake harness did not resolve to a session key")
        time.sleep(interval)


@dataclass
class RunResult:
    proc: subprocess.CompletedProcess[str]
    env_after: dict[str, str] | None


def run_gateway_runner(
    launcher: Path,
    argv: list[str],
    *,
    state_dir: Path,
    seed: Callable[[str], None] | None = None,
    extra_env: dict[str, str] | None = None,
    scratch: Path,
) -> RunResult:
    """Run ``_gateway_runner.py <argv>`` as a child of the fake harness *launcher*.

    *seed* is called with the session key the command will derive, before the
    command starts, so a credential file can be written where it will look.
    """
    env_dump = scratch / "env-after.json"
    env_dump.unlink(missing_ok=True)

    def started(pid: int) -> None:
        key = format_session_key(*await_harness(pid))
        if seed is not None:
            seed(key)

    done = run_under_harness(
        launcher,
        gateway_runner_cmd(env_dump, argv),
        env=runner_env(state_dir, extra=extra_env),
        on_start=started,
    )
    after = json.loads(env_dump.read_text()) if env_dump.exists() else None
    return RunResult(done, after)
