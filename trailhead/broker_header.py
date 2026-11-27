"""`trailhead broker-header`: exchange a session's broker credentials for an Authorization header.

Harness-neutral: it reads the credential file Outpost wrote for a session, posts a
client-credentials grant to Outpost's token endpoint over the gateway unix socket (never TCP, so a process
squatting on a port is sent nothing), and prints the header
as the JSON object a harness's header helper expects. Every failure exits non-zero
with nothing on stdout and leaves one secret-free line in trailhead's state directory.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from trailhead.gateway import outpost_http
from trailhead.launch_profile import InvalidBrokerSessionError, validate_broker_session
from trailhead.paths import state_dir

BROKER_TOKEN_PATH = "/broker/token"
# The harness gives its header helper 10 seconds in all and re-runs it on reconnect.
TIMEOUT_SECONDS = 3.0
_TOKEN_RE = re.compile(r"[\x21-\x7e]+")


class _Failure(Exception):
    """A failure named by a fixed category; never carries a secret, token, or body."""

    def __init__(self, category: str, message: str | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.message = message or category


def _read_credential(path: Path) -> tuple[str, str]:
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise _Failure("credential-missing") from None
    except (OSError, UnicodeDecodeError):
        raise _Failure("credential-unreadable") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise _Failure("credential-malformed") from None
    if not isinstance(data, dict):
        raise _Failure("credential-malformed")
    client_id, client_secret = data.get("client_id"), data.get("client_secret")
    if not (isinstance(client_id, str) and client_id and isinstance(client_secret, str) and client_secret):
        raise _Failure("credential-malformed")
    return client_id, client_secret


def _exchange(client_id: str, client_secret: str) -> str:
    try:
        reply = outpost_http.request(
            "POST",
            BROKER_TOKEN_PATH,
            form={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=TIMEOUT_SECONDS,
        )
    except outpost_http.OutpostUnreachable as err:
        raise _Failure("unreachable", f"Outpost is unreachable at {err.path}; is it running?") from None
    except outpost_http.SocketNotReachable as err:
        raise _Failure(
            "not-reachable", f"Outpost's socket at {err.path} is not reachable from here (permission denied)"
        ) from None
    if 300 <= reply.status < 400:
        raise _Failure("redirect-refused")
    if reply.status != 200:
        raise _Failure(f"http-{reply.status}")
    token = reply.body.get("access_token")
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        raise _Failure("malformed-response")
    return token


def _session_id(credential_file: Path) -> str:
    try:
        return validate_broker_session(credential_file.parent.name)
    except InvalidBrokerSessionError:
        return "unknown"


def _record_failure(session_id: str, category: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        directory = state_dir("trailhead") / "broker-header"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{session_id}.failure").write_text(f"{stamp} broker-header {category}\n")
    except OSError:
        pass


def run(credential_file: str) -> tuple[int, str]:
    """Return (exit code, stdout text). Failure leaves stdout empty."""
    path = Path(credential_file)
    try:
        client_id, client_secret = _read_credential(path)
        token = _exchange(client_id, client_secret)
    except _Failure as failure:
        _record_failure(_session_id(path), failure.category)
        return 1, f"trailhead: broker-header: {failure.message}"
    return 0, json.dumps({"Authorization": f"Bearer {token}"})
