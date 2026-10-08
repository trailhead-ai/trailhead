"""Tests for camp.host.transport.fetch_camp_bytes — a remote verb's stdout
written verbatim, as bytes, to a caller-supplied binary handle.

Test contract:
- A real local child emitting every byte value, NUL/CR/LF runs, and a
  multi-megabyte payload lands in the handle byte-identical.
- A child emitting more than the byte cap is killed, the outcome is
  TooLarge, and the handle holds no more than the cap.
- The ssh argv carries the fixed options, the host's ssh destination, and a
  shell-quoted camp_bin plus argv, varied across two hosts.
- 255 is Unreachable, 127 is CampNotResolvable, a timeout is
  StoppedResponding with the child killed, any other non-zero exit is a
  RemoteRefusal carrying its stderr text.
"""
from __future__ import annotations

import io
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


class _Spawner:
    """Ignores the ssh argv and runs a local python child instead; records
    the argv it was handed and the child process."""

    def __init__(self, code: str):
        self.code = code
        self.argv: list[str] = []
        self.process: subprocess.Popen | None = None

    def __call__(self, argv, env):
        self.argv = list(argv)
        self.process = subprocess.Popen(
            [sys.executable, "-c", self.code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return self.process


def _fetch(spawner, out, *, max_bytes=1 << 30, execution_timeout=30.0, host=_HOST, argv=("site-export",)):
    return transport.fetch_camp_bytes(
        host, list(argv), out, max_bytes=max_bytes,
        execution_timeout=execution_timeout, spawner=spawner,
    )


def test_every_byte_value_and_multi_megabyte_payload_land_identical(tmp_path):
    code = (
        "import sys\n"
        "out = sys.stdout.buffer\n"
        "blob = bytes(range(256)) + b'\\x00' * 300 + b'\\r' * 7 + b'\\n' * 5 + b'\\r\\n' * 9 + b'\\n\\r' * 4\n"
        "for _ in range(25000): out.write(blob)\n"
        "out.flush()\n"
    )
    blob = bytes(range(256)) + b"\x00" * 300 + b"\r" * 7 + b"\n" * 5 + b"\r\n" * 9 + b"\n\r" * 4
    expected = blob * 25000
    assert len(expected) > 5_000_000
    with open(tmp_path / "out.bin", "wb") as handle:
        outcome = _fetch(_Spawner(code), handle)
    assert isinstance(outcome, transport.Delivered)
    assert outcome.bytes_written == len(expected)
    assert (tmp_path / "out.bin").read_bytes() == expected


def test_output_over_the_cap_kills_child_and_handle_holds_at_most_the_cap():
    code = "import sys, time\nsys.stdout.buffer.write(b'x' * 100000); sys.stdout.buffer.flush(); time.sleep(20)\n"
    spawner = _Spawner(code)
    out = io.BytesIO()
    started = time.monotonic()
    outcome = _fetch(spawner, out, max_bytes=1000)
    assert time.monotonic() - started < 10
    assert outcome == transport.TooLarge(max_bytes=1000)
    assert len(out.getvalue()) <= 1000
    assert spawner.process.poll() is not None


def test_output_exactly_at_the_cap_is_delivered():
    code = "import sys\nsys.stdout.buffer.write(b'y' * 1000)\n"
    out = io.BytesIO()
    outcome = _fetch(_Spawner(code), out, max_bytes=1000)
    assert outcome == transport.Delivered(bytes_written=1000)
    assert out.getvalue() == b"y" * 1000


def test_argv_carries_fixed_options_destination_and_quoted_remote_command_per_host():
    hosts = [
        Host(ssh="andromeda", camp_bin="/opt/camp/bin/camp"),
        Host(ssh="mac.local", camp_bin="/Users/me/my tools/camp"),
    ]
    for host in hosts:
        spawner = _Spawner("pass")
        _fetch(spawner, io.BytesIO(), host=host, argv=("site-export", "--slug=a b"))
        argv = spawner.argv
        assert argv[0] == "ssh"
        assert "BatchMode=yes" in argv
        assert "StrictHostKeyChecking=yes" in argv
        assert any(a.startswith("ConnectTimeout=") for a in argv)
        assert argv[-2] == host.ssh
        assert shlex.split(argv[-1]) == [host.camp_bin, "site-export", "--slug=a b"]


_HARDENING_OPTIONS = ("ForwardAgent=no", "ForwardX11=no", "ClearAllForwardings=yes", "PermitLocalCommand=no", "RequestTTY=no")
_TWO_HOSTS = [
    Host(ssh="andromeda", camp_bin="/opt/camp/bin/camp"),
    Host(ssh="mac.local", camp_bin="/Users/me/my tools/camp"),
]


def _option_values(argv):
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-o"]


def test_fetch_argv_disables_tty_forwarding_and_local_commands_per_host():
    for host in _TWO_HOSTS:
        spawner = _Spawner("pass")
        _fetch(spawner, io.BytesIO(), host=host)
        argv = spawner.argv
        assert "-T" in argv
        for option in _HARDENING_OPTIONS:
            assert option in _option_values(argv), option


def test_fetch_argv_ends_options_with_double_dash_immediately_before_the_destination():
    for host in _TWO_HOSTS:
        spawner = _Spawner("pass")
        _fetch(spawner, io.BytesIO(), host=host)
        argv = spawner.argv
        assert argv[-3:-1] == ["--", host.ssh]


def test_run_camp_argv_is_unchanged_for_the_same_hosts():
    for host in _TWO_HOSTS:
        seen = []

        def runner(argv, timeout, env):
            seen.append(list(argv))
            return transport.RawResult(stdout="", stderr="", exit_code=0)

        transport.run_camp(host, ["list"], connect_timeout=7.0, runner=runner)

        assert seen == [[
            "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=7",
            host.ssh, transport.quote_and_join(host.camp_bin, ["list"]),
        ]]


def test_exit_255_is_unreachable():
    code = "import sys\nsys.stderr.write('ssh: connect to host x: Connection refused')\nsys.exit(255)\n"
    assert isinstance(_fetch(_Spawner(code), io.BytesIO()), transport.Unreachable)


def test_exit_127_is_camp_not_resolvable():
    code = "import sys\nsys.exit(127)\n"
    assert isinstance(_fetch(_Spawner(code), io.BytesIO()), transport.CampNotResolvable)


def test_timeout_is_stopped_responding_and_child_is_killed():
    spawner = _Spawner("import time\ntime.sleep(20)\n")
    started = time.monotonic()
    outcome = _fetch(spawner, io.BytesIO(), execution_timeout=0.5)
    assert time.monotonic() - started < 10
    assert outcome == transport.StoppedResponding(execution_timeout=0.5)
    assert spawner.process.poll() is not None


def test_other_nonzero_exit_is_remote_refusal_with_stderr_text():
    code = "import sys\nsys.stderr.write('site not found: caf\\u00e9')\nsys.exit(4)\n"
    outcome = _fetch(_Spawner(code), io.BytesIO())
    assert isinstance(outcome, transport.RemoteRefusal)
    assert outcome.exit_code == 4
    assert outcome.stderr == "site not found: café"


def test_a_completed_transfer_is_delivered_even_when_the_timer_fires_after_the_child_exited():
    # The child writes its payload and exits 0, but a grandchild keeps stderr open
    # past the execution timeout, so the timer fires after EOF and exit.
    code = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'],"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)\n"
        "sys.stdout.buffer.write(b'payload')\n"
    )
    out = io.BytesIO()

    outcome = _fetch(_Spawner(code), out, execution_timeout=0.5)

    assert outcome == transport.Delivered(bytes_written=7)
    assert out.getvalue() == b"payload"
