"""Tests for camp.env.read — one machine's environments, read locally or over ssh.

Every test injects the runner; none runs ssh or Docker. Stderr fixtures under
``fixtures/env_read_*`` are real captures (see ``env_read_README.txt`` for host, ssh flavour and which rows are
unmeasured). Where a row is unmeasured the test says so and uses the planned
exit code with the measured Linux stderr.

Test contract:
- Locally: a header-and-records reply gives those environments; a missing docker
  program is Docker not installed; daemon-unreachable stderr is Docker not
  running; any other failure is unreadable carrying the sanitized stderr line; a
  timeout is unreadable `no answer within <bound>s`.
- Remotely: the same reply gives the same environments; the exit status never
  decides (an exit 0 with an empty stdout is classified from stderr); exit 127 is
  Docker not installed; an empty reply is unreadable.
- A reply over 1 MiB is unreadable and is never held whole.
- Each transport failure class is unreadable with its own reason.
- A reply that fails parsing is unreadable with the parser's reason.
- Far-side text reaches the reason as one printable line of at most 200 characters.
- The remote argv is the hardened ssh argv and exactly the contract's command.
"""

from __future__ import annotations

import io
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.env import read  # noqa: E402
from camp.env.contract import (  # noqa: E402
    INVENTORY_ARGV,
    INVENTORY_HEADER,
    INVENTORY_REMOTE_COMMAND,
    Environment,
)
from camp.host import transport  # noqa: E402
from camp.host.config import Host  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
HOST = Host(ssh="tduffield@perseus", camp_bin="camp")

REPLY = (
    f"{INVENTORY_HEADER}\n"
    '"proj-env-dev"|"running"|"proj"|"dev"|"127.0.0.1:28080->8080/tcp"\n'
    '"proj-env-qa"|"exited"|"proj"|"qa"|""\n'
)
EXPECTED = (Environment("proj", "dev", "running", ((8080, 28080),)), Environment("proj", "qa", "stopped"))


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


UNKNOWN_COMMAND = _fixture("env_read_perseus_tailscale_unknown_command.stderr")
DAEMON_MISSING_SOCKET = _fixture("env_read_perseus_tailscale_daemon_missing_socket.stderr")
DAEMON_TCP = _fixture("env_read_perseus_tailscale_daemon_tcp.stderr")
DAEMON_NO_LISTENER = _fixture("env_read_perseus_tailscale_daemon_no_listener.stderr")
MASKED_EXIT_STDERR = _fixture("env_read_perseus_tailscale_masked_exit.stderr")
LOCAL_DAEMON_MISSING_SOCKET = _fixture("env_read_andromeda_local_daemon_missing_socket.stderr")


class FakeRunner:
    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.calls = []

    def __call__(self, argv, execution_timeout, env, max_stdout_bytes):
        self.calls.append((list(argv), execution_timeout, dict(env), max_stdout_bytes))
        if self.raises is not None:
            raise self.raises
        return self.result


def raw(stdout="", stderr="", exit_code=0):
    return transport.RawResult(stdout=stdout, stderr=stderr, exit_code=exit_code)


def local(result=None, raises=None):
    return read.read_local(runner=FakeRunner(result, raises))


def remote(result=None, raises=None, runner=None):
    return read.read_remote(
        "perseus", HOST, connect_timeout=7.0, runner=runner or FakeRunner(result, raises)
    )


def assert_unreadable(inventory, reason=None):
    assert inventory.kind == read.UNREADABLE
    assert inventory.environments == ()
    assert "\n" not in inventory.reason
    if reason is not None:
        assert inventory.reason == reason


# ---------------------------------------------------------------------------
# Locally
# ---------------------------------------------------------------------------


def test_local_reply_gives_its_environments():
    inventory = local(raw(REPLY))
    assert inventory.kind == read.ENVIRONMENTS
    assert inventory.environments == EXPECTED
    assert inventory.reason == ""


def test_local_header_only_is_an_empty_machine_not_a_failure():
    inventory = local(raw(INVENTORY_HEADER + "\n"))
    assert inventory.kind == read.ENVIRONMENTS
    assert inventory.environments == ()


def test_local_runs_the_contract_argv():
    runner = FakeRunner(raw(REPLY))
    read.read_local(runner=runner)
    assert runner.calls[0][0] == list(INVENTORY_ARGV)


def test_local_missing_docker_program_is_docker_not_installed():
    assert local(raises=FileNotFoundError(2, "No such file or directory")).kind == read.DOCKER_NOT_INSTALLED


@pytest.mark.parametrize(
    "stderr",
    [LOCAL_DAEMON_MISSING_SOCKET, DAEMON_TCP, DAEMON_NO_LISTENER],
)
def test_local_daemon_unreachable_is_docker_not_running(stderr):
    # The missing-socket row is measured locally with exit 1 (andromeda); the
    # other two are Docker's measured text, run here at the same exit.
    inventory = local(raw(stderr=stderr, exit_code=1))
    assert inventory.kind == read.DOCKER_NOT_RUNNING
    assert inventory.environments == ()


def test_local_other_failure_is_unreadable_with_the_stripped_stderr_line():
    inventory = local(raw(stderr="permission denied while trying to connect\nsecond line\n", exit_code=1))
    assert_unreadable(inventory, "permission denied while trying to connect")


def test_local_timeout_is_unreadable_naming_the_bound():
    inventory = local(raises=subprocess.TimeoutExpired("docker", 30))
    assert_unreadable(inventory, "no answer within 30s")


def test_the_timeout_reason_follows_the_constant(monkeypatch):
    monkeypatch.setattr(read, "READ_TIMEOUT_SECONDS", 5)
    runner = FakeRunner(raises=subprocess.TimeoutExpired("docker", 5))
    inventory = read.read_local(runner=runner)
    assert_unreadable(inventory, "no answer within 5s")
    assert runner.calls[0][1] == 5


def test_the_bound_passed_to_the_runner_is_the_constant_at_call_time():
    runner = FakeRunner(raw(REPLY))
    read.read_local(runner=runner)
    assert runner.calls[0][1] == read.READ_TIMEOUT_SECONDS == 30


def test_local_reply_is_capped_at_one_mebibyte():
    runner = FakeRunner(raw(REPLY))
    read.read_local(runner=runner)
    assert runner.calls[0][3] == 1024 * 1024


# ---------------------------------------------------------------------------
# Remotely
# ---------------------------------------------------------------------------


def test_remote_same_reply_gives_the_same_environments():
    assert remote(raw(REPLY)) == local(raw(REPLY))
    assert remote(raw(REPLY)).environments == EXPECTED


def test_remote_header_with_no_records_is_an_empty_machine():
    inventory = remote(raw(INVENTORY_HEADER + "\n"))
    assert inventory.kind == read.ENVIRONMENTS and inventory.environments == ()


def test_tailscale_exit_0_empty_stdout_unknown_command_is_docker_not_installed():
    assert remote(raw(stderr=UNKNOWN_COMMAND)).kind == read.DOCKER_NOT_INSTALLED


@pytest.mark.parametrize("stderr", ["docker: command not found\n", "sh: 1: docker: not found\n"])
def test_not_found_wording_is_docker_not_installed_in_any_case(stderr):
    assert remote(raw(stderr=stderr)).kind == read.DOCKER_NOT_INSTALLED
    assert remote(raw(stderr=stderr.upper())).kind == read.DOCKER_NOT_INSTALLED


def test_exit_127_is_docker_not_installed_whatever_stderr_says():
    # OpenSSH exit-127 propagation is UNMEASURED; this uses the planned code.
    assert remote(raw(stderr="", exit_code=127)).kind == read.DOCKER_NOT_INSTALLED
    assert remote(raw(stderr="something unrelated\n", exit_code=127)).kind == read.DOCKER_NOT_INSTALLED


@pytest.mark.parametrize(
    "stderr",
    [
        "docker: not found\n",
        "sh: 1: docker: not found\n",
        "docker: command not found\n",
        "bash: line 1: docker: command not found\n",
        "zsh: command not found: docker\n",
        "fish: Unknown command: docker\n",
        "Docker: Command Not Found\n",
    ],
)
def test_each_shells_missing_docker_wording_is_docker_not_installed(stderr):
    assert remote(raw(stderr=stderr)).kind == read.DOCKER_NOT_INSTALLED
    assert local(raw(stderr=stderr)).kind == read.DOCKER_NOT_INSTALLED


def test_a_not_found_that_names_no_docker_is_unreadable_with_that_line():
    # Constructed, not captured: a docker CLI complaining about a missing context.
    line = 'context "x": context not found'
    assert_unreadable(remote(raw(stderr=line + "\n", exit_code=1)), line)


def test_another_missing_command_beside_a_docker_permission_error_is_not_docker_not_installed():
    stderr = (
        "fish: Unknown command: direnv\n"
        "permission denied while trying to connect to the docker API\n"
    )
    assert remote(raw(stderr=stderr)).kind != read.DOCKER_NOT_INSTALLED


def test_a_daemon_phrase_wins_over_a_docker_not_found_line():
    stderr = "docker: not found\nCannot connect to the Docker daemon\n"
    assert remote(raw(stderr=stderr)).kind == read.DOCKER_NOT_RUNNING


@pytest.mark.parametrize(
    ("error", "strerror"),
    [
        (FileNotFoundError(2, "No such file or directory"), "No such file or directory"),
        (PermissionError(13, "Permission denied"), "Permission denied"),
        (OSError(8, "Exec format error"), "Exec format error"),
    ],
)
def test_ssh_that_cannot_start_is_unreadable_naming_why(error, strerror):
    assert_unreadable(remote(raises=error), f"cannot run ssh: {strerror}")


@pytest.mark.parametrize("stderr", [DAEMON_MISSING_SOCKET, DAEMON_TCP, DAEMON_NO_LISTENER])
def test_tailscale_exit_0_empty_stdout_daemon_text_is_docker_not_running(stderr):
    inventory = remote(raw(stderr=stderr))
    assert inventory.kind == read.DOCKER_NOT_RUNNING
    assert inventory.environments == ()


def test_daemon_text_matches_in_any_case():
    assert remote(raw(stderr=DAEMON_TCP.upper())).kind == read.DOCKER_NOT_RUNNING
    assert remote(raw(stderr=DAEMON_MISSING_SOCKET.upper())).kind == read.DOCKER_NOT_RUNNING


def test_exit_0_with_nothing_at_all_is_an_empty_reply():
    assert_unreadable(remote(raw()), "empty reply")
    assert_unreadable(remote(raw(stderr="\n")), "empty reply")


def test_exit_1_with_daemon_stderr_is_docker_not_running():
    # OpenSSH exit-1 propagation is UNMEASURED; planned code, measured Linux text.
    assert remote(raw(stderr=LOCAL_DAEMON_MISSING_SOCKET, exit_code=1)).kind == read.DOCKER_NOT_RUNNING


def test_exit_1_with_other_stderr_is_unreadable_carrying_that_line():
    assert_unreadable(remote(raw(stderr=MASKED_EXIT_STDERR, exit_code=1)), "boom")


def test_exit_0_with_other_stderr_is_unreadable_carrying_that_line():
    assert_unreadable(remote(raw(stderr=MASKED_EXIT_STDERR)), "boom")


def test_the_exit_code_never_decides_success():
    assert remote(raw(REPLY, exit_code=0)).environments == EXPECTED
    assert remote(raw(REPLY, exit_code=1)).environments == EXPECTED


def test_remote_stdout_without_header_and_no_stderr_is_unreadable_with_the_parsers_reason():
    assert_unreadable(
        remote(raw("hello\n")), "reply does not start with the expected header line"
    )


def test_a_failing_parse_is_unreadable_with_the_parsers_reason_and_no_environments():
    bad = INVENTORY_HEADER + '\n"proj-env-dev"|"running"|"proj"|"dev"|""\nnot a record\n'
    inventory = remote(raw(bad))
    assert_unreadable(inventory, "line 3: not five JSON strings separated by '|'")


def test_a_label_outside_the_charset_makes_the_whole_reply_unreadable():
    bad = INVENTORY_HEADER + '\n"a-env-b"|"running"|"a"|"b"|""\n"x-env-Y"|"running"|"x"|"Y"|""\n'
    inventory = remote(raw(bad))
    assert_unreadable(inventory, "line 3: name is not a valid identifier")


# ---------------------------------------------------------------------------
# Remote argv
# ---------------------------------------------------------------------------


def test_remote_argv_is_hardened_and_carries_exactly_the_contract_command():
    runner = FakeRunner(raw(REPLY))
    remote(runner=runner)
    argv = runner.calls[0][0]
    assert argv[0] == "ssh" and argv[1] == "-T"
    options = [argv[i + 1] for i, part in enumerate(argv) if part == "-o"]
    assert "BatchMode=yes" in options
    assert "StrictHostKeyChecking=yes" in options
    assert "ClearAllForwardings=yes" in options
    assert "ConnectTimeout=7" in options
    assert argv[-3:] == ["--", "tduffield@perseus", INVENTORY_REMOTE_COMMAND]
    assert shlex.split(argv[-1]) == list(INVENTORY_ARGV)


def test_remote_argv_follows_the_connect_timeout_and_the_destination():
    runner = FakeRunner(raw(REPLY))
    read.read_remote("other", Host(ssh="u@other", camp_bin="x"), connect_timeout=3.0, runner=runner)
    argv = runner.calls[0][0]
    assert "ConnectTimeout=3" in argv and argv[-2] == "u@other"


def test_remote_never_uses_camp_bin():
    runner = FakeRunner(raw(REPLY))
    read.read_remote("p", Host(ssh="p", camp_bin="/opt/camp/bin/camp"), connect_timeout=7, runner=runner)
    assert "/opt/camp/bin/camp" not in " ".join(runner.calls[0][0])


def test_remote_bound_cap_and_locale_are_passed_to_the_runner():
    runner = FakeRunner(raw(REPLY))
    remote(runner=runner)
    _, bound, env, cap = runner.calls[0]
    assert bound == 30 and cap == 1024 * 1024 and env["LC_ALL"] == "C"


# ---------------------------------------------------------------------------
# Transport failures: distinct reasons in the operator's words
# ---------------------------------------------------------------------------

UNREACHABLE = raw(stderr="ssh: Could not resolve hostname perseus: Name or service not known\r\n", exit_code=255)
KEY_UNKNOWN = raw(
    stderr="No ED25519 host key is known for perseus and you have requested strict checking.\n",
    exit_code=255,
)
KEY_CHANGED = raw(stderr="WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\n", exit_code=255)
REFUSED = raw(stderr="tduffield@perseus: Permission denied (publickey).\n", exit_code=255)


def test_unreachable_names_the_transports_cause():
    assert_unreadable(remote(UNREACHABLE), "unreachable (Could not resolve hostname)")


def test_unreachable_cause_follows_the_transport():
    refused = raw(stderr="ssh: connect to host perseus port 22: Connection refused\r\n", exit_code=255)
    assert_unreadable(remote(refused), "unreachable (Connection refused)")


def test_host_key_unknown_names_the_next_step():
    assert_unreadable(
        remote(KEY_UNKNOWN), "the host key is not pinned; run ssh tduffield@perseus once to pin it"
    )


def test_host_key_next_step_names_the_hosts_destination():
    host = Host(ssh="me@elsewhere", camp_bin="c")
    inventory = read.read_remote("o", host, connect_timeout=7, runner=FakeRunner(KEY_UNKNOWN))
    assert "ssh me@elsewhere once" in inventory.reason


def test_host_key_changed_is_its_own_reason():
    inventory = remote(KEY_CHANGED)
    assert inventory.kind == read.UNREADABLE
    assert "changed" in inventory.reason and "tduffield@perseus" in inventory.reason


def test_credentials_refused_is_its_own_reason():
    assert_unreadable(remote(REFUSED), "ssh credentials were refused")


def test_stopped_responding_is_the_timeout_reason():
    inventory = remote(raises=subprocess.TimeoutExpired("ssh", 30))
    assert_unreadable(inventory, "no answer within 30s")


def test_unmatched_255_is_unreadable_with_its_line():
    inventory = remote(raw(stderr="Bad configuration option: Foo\n", exit_code=255))
    assert_unreadable(inventory, "Bad configuration option: Foo")


def test_every_transport_failure_class_has_a_distinct_reason():
    reasons = {
        remote(UNREACHABLE).reason,
        remote(KEY_UNKNOWN).reason,
        remote(KEY_CHANGED).reason,
        remote(REFUSED).reason,
        remote(raises=subprocess.TimeoutExpired("ssh", 30)).reason,
        remote(raw("x" * (1024 * 1024 + 1))).reason,
    }
    assert len(reasons) == 6


# ---------------------------------------------------------------------------
# Size cap
# ---------------------------------------------------------------------------


def test_a_reply_over_one_mebibyte_is_unreadable_reply_too_large():
    assert_unreadable(remote(raw(INVENTORY_HEADER + "\n" + "x" * (1024 * 1024))), "reply too large")


def test_a_reply_of_exactly_one_mebibyte_is_not_too_large():
    body = INVENTORY_HEADER + "\n"
    body += "x" * (1024 * 1024 - len(body))
    assert remote(raw(body)).reason != "reply too large"


def test_the_cap_counts_bytes_not_characters():
    body = "é" * (1024 * 1024 // 2 + 1)
    assert_unreadable(remote(raw(body)), "reply too large")


def test_the_default_runner_stops_reading_a_runaway_reply():
    program = "import sys\nwhile True:\n    sys.stdout.buffer.write(b'x' * 65536)\n    sys.stdout.buffer.flush()\n"
    started = time.monotonic()
    result = transport.bounded_runner([sys.executable, "-c", program], 30, dict(os.environ), 1000)
    assert time.monotonic() - started < 10
    assert len(result.stdout) == 1001


# ---------------------------------------------------------------------------
# Far-side text reaches the reason as one printable line
# ---------------------------------------------------------------------------

HOSTILE = [
    ("esc", "\x1b[31mred\x1b[0m boom\n", "[31mred[0m boom"),
    ("second line", "first\nsecond\n", "first"),
    ("bidi", "ab‮cd\n", "abcd"),
    ("zero width", "ab​cd\n", "abcd"),
    ("line separator", "ab cd\n", "abcd"),
    ("invalid utf8", b"ab\xffcd\n".decode("utf-8", errors="surrogateescape"), "abcd"),
]


@pytest.mark.parametrize("label,stderr,expected", HOSTILE, ids=[h[0] for h in HOSTILE])
@pytest.mark.parametrize("where", ["local", "remote"])
def test_stderr_reaches_the_reason_without_hostile_characters(label, stderr, expected, where):
    result = raw(stderr=stderr, exit_code=1)
    inventory = local(result) if where == "local" else remote(result)
    assert_unreadable(inventory, expected)
    inventory.reason.encode("utf-8")
    io.TextIOWrapper(io.BytesIO(), encoding="utf-8").write(inventory.reason)


def test_a_line_of_only_invisible_characters_is_skipped_for_the_next_one():
    assert_unreadable(remote(raw(stderr="\u200b\x1b\n  real reason\n", exit_code=1)), "real reason")


def test_the_destination_in_a_reason_is_reduced_too():
    host = Host(ssh="me@x\x1b[2J\u202e", camp_bin="c")
    inventory = read.read_remote("o", host, connect_timeout=7, runner=FakeRunner(KEY_UNKNOWN))
    assert inventory.reason == "the host key is not pinned; run ssh me@x[2J once to pin it"


def test_an_exit_255_that_carries_a_complete_valid_reply_is_unreadable():
    assert_unreadable(remote(raw(REPLY, exit_code=255)), "ssh exited 255 without a reason")


def test_an_exit_255_with_a_partial_reply_is_unreadable_with_the_stderr_line():
    # Constructed example: OpenSSH's standard wording for a drop, not a measured capture.
    first_record_only = REPLY.splitlines(keepends=True)[:2]
    dropped = raw(
        "".join(first_record_only),
        stderr="Connection to perseus closed by remote host.\n",
        exit_code=255,
    )
    assert_unreadable(remote(dropped), "Connection to perseus closed by remote host.")


def test_an_exit_255_with_records_and_empty_stderr_uses_the_fallback_reason():
    assert_unreadable(
        remote(raw(REPLY, stderr="", exit_code=255)), "ssh exited 255 without a reason"
    )


def test_a_complete_reply_on_a_non_255_failure_exit_is_still_believed():
    assert remote(raw(REPLY, exit_code=1)).environments == EXPECTED


def test_a_ten_thousand_character_line_is_cut_to_two_hundred():
    inventory = remote(raw(stderr="y" * 10_000 + "\n", exit_code=1))
    assert inventory.reason == "y" * 200


def test_the_cut_also_applies_to_the_parsers_and_transports_reasons():
    inventory = remote(raw(stderr="a" * 500, exit_code=255))
    assert len(inventory.reason) == 200


# ---------------------------------------------------------------------------
# The default runners resolve docker and ssh through PATH at call time
# ---------------------------------------------------------------------------


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def test_default_local_runner_runs_docker_from_path(tmp_path, monkeypatch):
    _stub(tmp_path, "docker", f"printf '%s\\n' '{INVENTORY_HEADER}' '\"s-env-a\"|\"running\"|\"s\"|\"a\"|\"\"'\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert read.read_local().environments == (Environment("s", "a", "running"),)


def test_default_local_runner_without_docker_on_path_is_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert read.read_local().kind == read.DOCKER_NOT_INSTALLED


def test_default_remote_runner_runs_ssh_from_path(tmp_path, monkeypatch):
    log = tmp_path / "ssh.args"
    _stub(
        tmp_path,
        "ssh",
        f"printf '%s\\n' \"$@\" > {log}\nprintf '%s\\n' '{INVENTORY_HEADER}' '\"s-env-a\"|\"exited\"|\"s\"|\"a\"|\"\"'\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    inventory = read.read_remote("perseus", HOST, connect_timeout=7.0)
    assert inventory.environments == (Environment("s", "a", "stopped"),)
    assert INVENTORY_REMOTE_COMMAND in log.read_text().splitlines()


def test_default_remote_runner_enforces_the_bound(tmp_path, monkeypatch):
    _stub(tmp_path, "ssh", "exec sleep 30\n")
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(read, "READ_TIMEOUT_SECONDS", 1)
    started = time.monotonic()
    inventory = read.read_remote("perseus", HOST, connect_timeout=7.0)
    assert time.monotonic() - started < 10
    assert_unreadable(inventory, "no answer within 1s")
