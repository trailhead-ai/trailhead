"""Shared fixtures for tests of the Outpost gateway commands.

``StubOutpost`` is a loopback stand-in for the two Outpost routes the gateway
commands call; ``run_gateway_runner`` runs ``_gateway_runner.py`` as a child of a
fake harness process, so the command finds a real harness ancestor and a real
session key, and returns what the command printed.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from trailhead.harness import session_key

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).with_name("_gateway_runner.py")

CLIENT_ID = "client-id-SEEDED-1f3a"
CLIENT_SECRET = "client-secret-SEEDED-9c7e"
TOKEN = "access-token-SEEDED-5b2d"


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str]
    body: str


@dataclass
class StubOutpost:
    """Loopback Outpost: issues TOKEN for the right client, serves ``runs`` for TOKEN.

    ``token_reply`` / ``runs_reply`` — ``(status, body)`` — override the answers
    wholesale; ``body`` may be a dict (sent as JSON) or a str (sent verbatim).
    """

    workspace: str = "trailhead/outpost-gateway"
    runs: list[dict] = field(default_factory=list)
    client_id: str = CLIENT_ID
    client_secret: str = CLIENT_SECRET
    token: str = TOKEN
    token_reply: tuple[int, object] | None = None
    runs_reply: tuple[int, object] | None = None
    token_redirect: str | None = None
    runs_redirect: str | None = None
    requests: list[Seen] = field(default_factory=list)

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

            def _record(self, body: str) -> None:
                stub.requests.append(
                    Seen(self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                )

            def _redirect(self, location: str) -> None:
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode()
                self._record(body)
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
                self._send(200, {"access_token": stub.token, "token_type": "Bearer", "scope": "gateway"})

            def do_GET(self):
                self._record("")
                if stub.runs_redirect is not None and self.path == "/gateway/runs":
                    return self._redirect(stub.runs_redirect)
                if self.path != "/gateway/runs":
                    return self._send(404, {"error": "not_found"})
                if stub.runs_reply is not None:
                    return self._send(*stub.runs_reply)
                if self.headers.get("Authorization") != f"Bearer {stub.token}":
                    return self._send(
                        401, {"reason": "credential_unrecognised", "message": "The bearer token is not recognised."}
                    )
                self._send(200, {"workspace": stub.workspace, "runs": stub.runs})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "StubOutpost":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()


def write_credential(state_dir: Path, key: str, *, client_id: str = CLIENT_ID, client_secret: str = CLIENT_SECRET) -> Path:
    path = state_dir / "sessions" / key / "gateway-client.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"client_id": client_id, "client_secret": client_secret}))
    return path


@dataclass
class RunResult:
    proc: subprocess.CompletedProcess[str]
    key: str
    env_after: dict[str, str] | None


def run_gateway_runner(
    launcher: Path,
    argv: list[str],
    *,
    state_dir: Path,
    port: int,
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
    env = {**os.environ, "OUTPOST_STATE_DIR": str(state_dir), "PYTHONPATH": str(REPO_ROOT)}
    env.update(extra_env or {})
    script = (
        f'read go; "{sys.executable}" "{RUNNER}" {port} "{env_dump}" '
        + " ".join(_q(a) for a in argv)
    )
    proc = subprocess.Popen(
        [str(launcher), "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )
    key = session_key(start_pid=proc.pid)
    assert key is not None, "fake harness did not resolve to a session key"
    if seed is not None:
        seed(key)
    out, err = proc.communicate("go\n", timeout=60)
    done = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    after = json.loads(env_dump.read_text()) if env_dump.exists() else None
    return RunResult(done, key, after)


def _q(arg: str) -> str:
    return "'" + arg.replace("'", "'\\''") + "'"
