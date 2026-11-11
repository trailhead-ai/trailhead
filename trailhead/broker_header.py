"""`trailhead broker-header`: exchange a session's broker credentials for an Authorization header.

Harness-neutral: it reads the credential file Outpost wrote for a session, posts a
client-credentials grant to Outpost's loopback token endpoint, and prints the header
as the JSON object a harness's header helper expects. Every failure exits non-zero
with nothing on stdout and leaves one secret-free line in trailhead's state directory.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from trailhead import outpost_lifecycle
from trailhead.launch_profile import InvalidBrokerSessionError, validate_broker_session
from trailhead.paths import state_dir
from trailhead.ranger.outpost_client import OutpostRefusal, _opener

BROKER_TOKEN_PATH = "/broker/token"
# The harness gives its header helper 10 seconds in all and re-runs it on reconnect.
TIMEOUT_SECONDS = 3.0
_MAX_BODY_BYTES = 65536
_TOKEN_RE = re.compile(r"[\x21-\x7e]+")


class _Failure(Exception):
    """A failure named by a fixed category; never carries a secret, token, or body."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


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
    form = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
    ).encode()
    url = f"http://{outpost_lifecycle.DAEMON_HOST}:{outpost_lifecycle.DAEMON_PORT}{BROKER_TOKEN_PATH}"
    try:
        with _opener().open(url, data=form, timeout=TIMEOUT_SECONDS) as resp:
            status = resp.status
            raw = resp.read(_MAX_BODY_BYTES + 1)
    except OutpostRefusal:
        raise _Failure("redirect-refused") from None
    except urllib.error.HTTPError as exc:
        raise _Failure(f"http-{exc.code}") from None
    except OSError:
        raise _Failure("unreachable") from None
    if status != 200:
        raise _Failure(f"http-{status}")
    try:
        token = json.loads(raw[:_MAX_BODY_BYTES]).get("access_token") if len(raw) <= _MAX_BODY_BYTES else None
    except (ValueError, AttributeError):
        token = None
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
        return 1, f"trailhead: broker-header: {failure.category}"
    return 0, json.dumps({"Authorization": f"Bearer {token}"})
