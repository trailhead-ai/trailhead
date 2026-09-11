"""Tests for camp.host.transport.stream_camp — the binary streaming sibling
of run_camp.

Test contract:
- a payload containing every byte value, including NUL, CR, LF and
  0x80-0xFF, arrives at the remote command's stdin byte-identical.
- a producer that exits non-zero fails the invocation and names the
  producer, never silently sending a truncated stream.
- a remote command that exits non-zero classifies as RemoteRefusal carrying
  its exit code and stderr, exactly as run_camp does for the same input.
- an unreachable host classifies as Unreachable, and a changed host key as
  IdentityChanged — the same classifier, not a second copy of it.
- the assembled ssh argv carries BatchMode=yes, StrictHostKeyChecking=yes, a
  ConnectTimeout, and the two keepalive options, and carries no -t.
- a connection that dies without closing mid-stream is detected and
  classified within a bounded time rather than hanging, the bound derived
  from the keepalive settings.
- the remote command is quoted through quote_and_join, so a slug or group
  name carrying shell metacharacters reaches the far side uninterpreted.
"""
from __future__ import annotations

import io
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.host import transport  # noqa: E402
from camp.host.config import Host  # noqa: E402

_HOST = Host(ssh="andromeda", camp_bin="/opt/camp/bin/camp")


# ---------------------------------------------------------------------------
# In-memory fake child, for the tests that only exercise classification and
# argv assembly — mirrors the _FakeRunner pattern in test_host_transport.py.
# ---------------------------------------------------------------------------


class _RecordingSink:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        self.chunks.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.closed = True

    @property
    def data(self) -> bytes:
        return b"".join(self.chunks)


class _FakeChild:
    def __init__(self, *, exit_code: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = exit_code
        self.stdin = _RecordingSink()
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self) -> None:
        pass


def _fake_spawn(calls: list, child: _FakeChild):
    def _spawn(argv, env):
        calls.append((list(argv), dict(env)))
        return child

    return _spawn


def _quick_producer(payload: bytes = b"", *, exit_code: int = 0) -> subprocess.Popen:
    script = (
        "import sys; sys.stdout.buffer.write(%r); sys.exit(%d)" % (payload, exit_code)
    )
    return subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE
    )


# ---------------------------------------------------------------------------
# 1. Byte fidelity — driven against a real local no-pty child process, per
# the assumption-prover's measured method, never a real ssh.
# ---------------------------------------------------------------------------


def test_default_stream_spawner_carries_bytes_verbatim_in_both_directions() -> None:
    payload = bytes(range(256)) + b"\x00\r\n" * 5 + b"\r\r\n\n\x00\x00"

    child = transport.default_stream_spawner(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
        os.environ,
    )
    child.stdin.write(payload)
    child.stdin.close()
    stdout = child.stdout.read()
    child.wait()

    assert stdout == payload


def test_byte_payload_arrives_at_remote_stdin_byte_identical(tmp_path: Path) -> None:
    payload = bytes(range(256)) + b"\x00\r\n" * 5 + b"\r\r\n\n\x00\x00"
    output_path = tmp_path / "received.bin"

    def _spawn(argv, env):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys, pathlib; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())",
                str(output_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    producer = _quick_producer(payload)

    outcome = transport.stream_camp(_HOST, ["transfer-receive", "history"], producer, spawn=_spawn)

    assert isinstance(outcome, transport.Answered)
    assert output_path.read_bytes() == payload


# ---------------------------------------------------------------------------
# 2. Producer failure
# ---------------------------------------------------------------------------


def test_producer_nonzero_exit_fails_invocation_and_names_producer() -> None:
    producer = _quick_producer(b"partial-bundle-bytes", exit_code=1)
    calls: list = []
    child = _FakeChild(exit_code=0, stdout=b"", stderr=b"")

    outcome = transport.stream_camp(
        _HOST, ["transfer-receive", "history"], producer, spawn=_fake_spawn(calls, child)
    )

    assert isinstance(outcome, transport.ProducerFailed)
    assert outcome.exit_code == 1


# ---------------------------------------------------------------------------
# 3. Remote refusal, same shape as run_camp
# ---------------------------------------------------------------------------


def test_remote_nonzero_exit_classifies_as_remote_refusal_with_exit_code_and_stderr() -> None:
    producer = _quick_producer(b"")
    calls: list = []
    child = _FakeChild(exit_code=1, stdout=b"", stderr=b"camp transfer-receive: bad bundle\n")

    outcome = transport.stream_camp(
        _HOST, ["transfer-receive", "history"], producer, spawn=_fake_spawn(calls, child)
    )

    assert isinstance(outcome, transport.RemoteRefusal)
    assert outcome.exit_code == 1
    assert outcome.stderr == "camp transfer-receive: bad bundle\n"


# ---------------------------------------------------------------------------
# 4. Shared classifier — Unreachable and IdentityChanged
# ---------------------------------------------------------------------------


def test_unreachable_host_classifies_as_unreachable_via_shared_classifier() -> None:
    producer = _quick_producer(b"")
    calls: list = []
    child = _FakeChild(
        exit_code=255,
        stderr=b"ssh: connect to host andromeda port 22: Connection refused\r\n",
    )

    outcome = transport.stream_camp(
        _HOST, ["transfer-receive", "history"], producer, spawn=_fake_spawn(calls, child)
    )

    assert isinstance(outcome, transport.Unreachable)


def test_changed_host_key_classifies_as_identity_changed_via_shared_classifier() -> None:
    producer = _quick_producer(b"")
    calls: list = []
    child = _FakeChild(
        exit_code=255,
        stderr=b"@@@@@@@@@@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED @@@@@@@@@@@@\n",
    )

    outcome = transport.stream_camp(
        _HOST, ["transfer-receive", "history"], producer, spawn=_fake_spawn(calls, child)
    )

    assert isinstance(outcome, transport.IdentityChanged)
    assert not isinstance(outcome, transport.Unreachable)


# ---------------------------------------------------------------------------
# 5. Argv assembly
# ---------------------------------------------------------------------------


def test_argv_carries_fixed_options_keepalives_and_no_dash_t() -> None:
    producer = _quick_producer(b"")
    calls: list = []
    child = _FakeChild(exit_code=0)

    transport.stream_camp(
        _HOST,
        ["transfer-receive", "history"],
        producer,
        connect_timeout=7.0,
        server_alive_interval=15.0,
        server_alive_count_max=3,
        spawn=_fake_spawn(calls, child),
    )

    argv = calls[0][0]
    assert "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "StrictHostKeyChecking=no" not in argv
    assert "ConnectTimeout=7" in argv
    assert "ServerAliveInterval=15" in argv
    assert "ServerAliveCountMax=3" in argv
    assert "-t" not in argv


# ---------------------------------------------------------------------------
# 6. Bounded-time detection of a dead connection mid-stream
# ---------------------------------------------------------------------------


def test_connection_dying_without_closing_is_classified_within_a_bounded_time() -> None:
    producer = _quick_producer(b"x")

    def _hang_spawn(argv, env):
        return subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    interval, count_max, connect_timeout = 0.05, 2, 0.01
    expected_bound = interval * count_max + connect_timeout

    started = time.monotonic()
    outcome = transport.stream_camp(
        _HOST,
        ["transfer-receive", "history"],
        producer,
        connect_timeout=connect_timeout,
        server_alive_interval=interval,
        server_alive_count_max=count_max,
        spawn=_hang_spawn,
    )
    elapsed = time.monotonic() - started

    assert isinstance(outcome, transport.StoppedResponding)
    assert outcome.execution_timeout == expected_bound
    assert elapsed < 5.0


# ---------------------------------------------------------------------------
# 7. quote_and_join reuse
# ---------------------------------------------------------------------------


def test_remote_command_is_quoted_through_quote_and_join() -> None:
    producer = _quick_producer(b"")
    calls: list = []
    child = _FakeChild(exit_code=0)
    dangerous = "has space'quote;semi$(sub)`tick\nnewline"

    transport.stream_camp(
        _HOST,
        ["transfer-receive", dangerous],
        producer,
        spawn=_fake_spawn(calls, child),
    )

    remote_command = calls[0][0][-1]
    parsed = shlex.split(remote_command)
    assert parsed == [_HOST.camp_bin, "transfer-receive", dangerous]
