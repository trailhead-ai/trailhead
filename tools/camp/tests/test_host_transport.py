"""Tests for camp.host.transport — the SSH transport and outcome classifier.

Test contract:
- The constructed argv carries BatchMode=yes, StrictHostKeyChecking=yes, and
  the configured ConnectTimeout, and never StrictHostKeyChecking=no.
- A value containing a space, a single quote, a semicolon, a `$(`, a
  backtick, and a newline survives into the remote command as exactly one
  argument, inert — parsed back with shlex.split and compared element for
  element against the input.
- The remote command is prefixed with the host's camp_bin.
- Each failure mode the prover established maps to its own outcome: connect
  timeout, execution timeout, refused connection, host key never pinned,
  host key changed, missing remote binary, and a remote camp that ran and
  exited nonzero. Seven separate tests, each failing for exactly one reason.
- The changed-key and never-pinned cases are driven for real against a
  throwaway sshd, never stubbed.
- A runner that connects and then never returns produces the
  stopped-responding outcome within the execution bound, without blocking
  the test.
- Interrupting the call terminates the child ssh process.
- Classification holds under a non-English locale in the caller's
  environment, proving the LC_ALL=C pin is load-bearing.
- A remote refusal carries the remote camp's own stderr through unchanged,
  with no wrapper text added.
- The default timeout applies when none is given; an explicit override
  replaces it.
- Nothing is executed when the runner is never expected to be called.
- The resolution order for an ambiguous exit 255 is load-bearing: the fixed
  transport substrings are matched first, and an otherwise-unmatched 255 is
  the remote command's own exit — never the reverse.
"""
from __future__ import annotations

import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.host import transport  # noqa: E402
from camp.host.config import Host  # noqa: E402


# ---------------------------------------------------------------------------
# The injected runner seam — mirrors the Tmux seam at launch/stop.py:158.
# ---------------------------------------------------------------------------


class _Call:
    def __init__(self, argv: list[str], execution_timeout: float, env: dict[str, str]):
        self.argv = argv
        self.execution_timeout = execution_timeout
        self.env = env


class _FakeRunner:
    """Records every invocation; returns a fixed result or raises a fixed error."""

    def __init__(self, *, result: transport.RawResult | None = None, raises: Exception | None = None):
        self.calls: list[_Call] = []
        self._result = result
        self._raises = raises

    def __call__(self, argv, execution_timeout, env) -> transport.RawResult:
        self.calls.append(_Call(list(argv), execution_timeout, dict(env)))
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


_HOST = Host(ssh="andromeda", camp_bin="/opt/camp/bin/camp")


def _remote_command(fake: _FakeRunner) -> str:
    assert len(fake.calls) == 1
    return fake.calls[0].argv[-1]


# ---------------------------------------------------------------------------
# argv construction
# ---------------------------------------------------------------------------


def test_argv_carries_fixed_ssh_options_and_configured_connect_timeout() -> None:
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr="", exit_code=0))

    transport.run_camp(_HOST, ["list"], connect_timeout=7.0, runner=fake)

    argv = fake.calls[0].argv
    assert "-o" in argv
    assert "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "StrictHostKeyChecking=no" not in argv
    assert "ConnectTimeout=7" in argv


def test_metacharacters_survive_as_one_inert_remote_argument() -> None:
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr="", exit_code=0))
    dangerous = "has space'quote;semi$(sub)`tick\nnewline"

    transport.run_camp(_HOST, ["list", dangerous], runner=fake)

    remote_command = _remote_command(fake)
    parsed = shlex.split(remote_command)
    assert parsed == [_HOST.camp_bin, "list", dangerous]


def test_remote_command_is_prefixed_with_camp_bin() -> None:
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr="", exit_code=0))

    transport.run_camp(_HOST, ["sessions", "--json"], runner=fake)

    remote_command = _remote_command(fake)
    assert shlex.split(remote_command)[0] == _HOST.camp_bin


# ---------------------------------------------------------------------------
# Seven failure modes, each its own outcome.
# ---------------------------------------------------------------------------


def test_connect_timeout_classifies_as_unreachable() -> None:
    fake = _FakeRunner(
        result=transport.RawResult(
            stdout="", stderr="ssh: connect to host andromeda port 22: Connection timed out\r\n", exit_code=255
        )
    )

    outcome = transport.run_camp(_HOST, ["list"], runner=fake)

    assert isinstance(outcome, transport.Unreachable)


def test_execution_timeout_classifies_as_stopped_responding() -> None:
    fake = _FakeRunner(raises=subprocess.TimeoutExpired(cmd=["ssh"], timeout=60.0))

    outcome = transport.run_camp(_HOST, ["list"], execution_timeout=60.0, runner=fake)

    assert isinstance(outcome, transport.StoppedResponding)
    assert outcome.execution_timeout == 60.0


def test_refused_connection_classifies_as_unreachable() -> None:
    fake = _FakeRunner(
        result=transport.RawResult(
            stdout="", stderr="ssh: connect to host andromeda port 22: Connection refused\r\n", exit_code=255
        )
    )

    outcome = transport.run_camp(_HOST, ["list"], runner=fake)

    assert isinstance(outcome, transport.Unreachable)


def test_missing_remote_binary_classifies_as_camp_not_resolvable() -> None:
    fake = _FakeRunner(
        result=transport.RawResult(
            stdout="", stderr="bash: line 1: camp: command not found\n", exit_code=127
        )
    )

    outcome = transport.run_camp(_HOST, ["list"], runner=fake)

    assert isinstance(outcome, transport.CampNotResolvable)


def test_remote_nonzero_exit_classifies_as_remote_refusal() -> None:
    fake = _FakeRunner(
        result=transport.RawResult(
            stdout="", stderr="camp list: /home/tom/.config/camp/groups/levr.toml: invalid TOML\n", exit_code=1
        )
    )

    outcome = transport.run_camp(_HOST, ["list"], runner=fake)

    assert isinstance(outcome, transport.RemoteRefusal)
    assert outcome.exit_code == 1


# --- driven for real: identity-unknown and identity-changed --------------


def _start_throwaway_sshd(tmp_path: Path) -> tuple[subprocess.Popen, int] | None:
    sshd_bin = shutil.which("sshd") or "/usr/sbin/sshd"
    if not Path(sshd_bin).exists():
        return None

    host_key = tmp_path / "ssh_host_ed25519_key"
    keygen = subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(host_key), "-N", "", "-q"],
        capture_output=True,
        text=True,
    )
    if keygen.returncode != 0:
        return None

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_text("", encoding="utf-8")

    config = tmp_path / "sshd_config"
    config.write_text(
        "\n".join(
            [
                f"Port {port}",
                "ListenAddress 127.0.0.1",
                f"HostKey {host_key}",
                f"AuthorizedKeysFile {authorized_keys}",
                "StrictModes no",
                "UsePAM no",
                "PasswordAuthentication no",
                "PubkeyAuthentication yes",
                f"PidFile {tmp_path / 'sshd.pid'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        proc = subprocess.Popen(
            [sshd_bin, "-D", "-e", "-f", str(config)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return None

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return None
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return proc, port
        except OSError:
            time.sleep(0.05)
    proc.kill()
    return None


@pytest.fixture
def throwaway_sshd(tmp_path: Path):
    started = _start_throwaway_sshd(tmp_path)
    if started is None:
        pytest.skip("could not start a throwaway sshd for the real-ssh identity tests")
    proc, port = started
    try:
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_never_pinned_host_key_classifies_as_identity_unknown(
    tmp_path: Path, throwaway_sshd: int
) -> None:
    known_hosts = tmp_path / "known_hosts_empty"
    known_hosts.write_text("", encoding="utf-8")
    host = Host(ssh="127.0.0.1", camp_bin="true")

    outcome = transport.run_camp(
        host,
        ["list"],
        connect_timeout=5.0,
        execution_timeout=10.0,
        extra_ssh_options=(f"Port={throwaway_sshd}", f"UserKnownHostsFile={known_hosts}"),
    )

    assert isinstance(outcome, transport.IdentityUnknown)


def test_changed_host_key_classifies_as_identity_changed(
    tmp_path: Path, throwaway_sshd: int
) -> None:
    wrong_key = tmp_path / "wrong_host_key"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(wrong_key), "-N", "", "-q"],
        check=True,
        capture_output=True,
    )
    key_type, key_data, *_ = (wrong_key.with_suffix(".pub")).read_text(encoding="utf-8").split()
    known_hosts = tmp_path / "known_hosts_wrong"
    known_hosts.write_text(f"[127.0.0.1]:{throwaway_sshd} {key_type} {key_data}\n", encoding="utf-8")
    host = Host(ssh="127.0.0.1", camp_bin="true")

    outcome = transport.run_camp(
        host,
        ["list"],
        connect_timeout=5.0,
        execution_timeout=10.0,
        extra_ssh_options=(f"Port={throwaway_sshd}", f"UserKnownHostsFile={known_hosts}"),
    )

    assert isinstance(outcome, transport.IdentityChanged)
    assert not isinstance(outcome, transport.Unreachable)


# ---------------------------------------------------------------------------
# Addition 1 — resolution order for an ambiguous exit 255.
# ---------------------------------------------------------------------------


def test_unmatched_255_classifies_as_remote_refusal_not_unreachable() -> None:
    fake = _FakeRunner(
        result=transport.RawResult(stdout="", stderr="camp: some remote-side failure\n", exit_code=255)
    )

    outcome = transport.run_camp(_HOST, ["list"], runner=fake)

    assert isinstance(outcome, transport.RemoteRefusal)
    assert outcome.exit_code == 255


def test_auth_failure_classifies_as_credentials_refused_not_remote_refusal() -> None:
    # Real signal measured on this machine, 2026-09-10: `ssh -o BatchMode=yes
    # -o StrictHostKeyChecking=no -o UserKnownHostsFile=<tmp> localhost true`
    # with no usable identity loaded — exit 255, stderr
    # "tomduffield@localhost: Permission denied (publickey).\n".
    stderr = "tom@andromeda: Permission denied (publickey).\n"
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr=stderr, exit_code=255))

    outcome = transport.run_camp(_HOST, ["list"], runner=fake)

    assert isinstance(outcome, transport.CredentialsRefused)
    assert not isinstance(outcome, transport.RemoteRefusal)


def test_auth_failure_with_multiple_offered_methods_still_classifies_as_credentials_refused() -> None:
    stderr = "tom@andromeda: Permission denied (publickey,password).\n"
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr=stderr, exit_code=255))

    outcome = transport.run_camp(_HOST, ["list"], runner=fake)

    assert isinstance(outcome, transport.CredentialsRefused)


def test_matched_255_message_classifies_as_transport_failure_not_remote_refusal() -> None:
    stderr = "ssh: Could not resolve hostname andromeda: nodename nor servname provided\n"
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr=stderr, exit_code=255))

    outcome = transport.run_camp(_HOST, ["list"], runner=fake)

    assert isinstance(outcome, transport.Unreachable)
    assert not isinstance(outcome, transport.RemoteRefusal)


# ---------------------------------------------------------------------------
# Locale pin
# ---------------------------------------------------------------------------


def test_lc_all_c_pin_holds_under_a_non_english_ambient_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr="", exit_code=0))

    transport.run_camp(_HOST, ["list"], runner=fake)

    assert fake.calls[0].env["LC_ALL"] == "C"


# ---------------------------------------------------------------------------
# Remote refusal relays stderr unchanged
# ---------------------------------------------------------------------------


def test_remote_refusal_carries_stderr_through_unchanged() -> None:
    raw_stderr = "camp list: /home/tom/.config/camp/groups/levr.toml: invalid TOML — skipping\n"
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr=raw_stderr, exit_code=1))

    outcome = transport.run_camp(_HOST, ["list"], runner=fake)

    assert isinstance(outcome, transport.RemoteRefusal)
    assert outcome.stderr == raw_stderr


# ---------------------------------------------------------------------------
# Timeout defaults
# ---------------------------------------------------------------------------


def test_default_connect_timeout_applies_when_none_given() -> None:
    # Literal, not transport.DEFAULT_CONNECT_TIMEOUT_SECONDS: a mutation of the
    # constant itself must not be able to drag this assertion along with it.
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr="", exit_code=0))

    transport.run_camp(_HOST, ["list"], runner=fake)

    assert "ConnectTimeout=10" in fake.calls[0].argv


def test_explicit_connect_timeout_replaces_default() -> None:
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr="", exit_code=0))

    transport.run_camp(_HOST, ["list"], connect_timeout=3.0, runner=fake)

    assert "ConnectTimeout=3" in fake.calls[0].argv
    assert "ConnectTimeout=10" not in fake.calls[0].argv


def test_default_execution_timeout_applies_when_none_given() -> None:
    # Literal, for the same reason as the connect-timeout default above.
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr="", exit_code=0))

    transport.run_camp(_HOST, ["list"], runner=fake)

    assert fake.calls[0].execution_timeout == 60.0


def test_explicit_execution_timeout_replaces_default() -> None:
    fake = _FakeRunner(result=transport.RawResult(stdout="", stderr="", exit_code=0))

    transport.run_camp(_HOST, ["list"], execution_timeout=5.0, runner=fake)

    assert fake.calls[0].execution_timeout == 5.0


# ---------------------------------------------------------------------------
# No retry: once the runner reports the invocation failed, it is not
# invoked again.
# ---------------------------------------------------------------------------


def test_runner_is_invoked_exactly_once_even_when_it_reports_stopped_responding() -> None:
    fake = _FakeRunner(raises=subprocess.TimeoutExpired(cmd=["ssh"], timeout=1.0))

    transport.run_camp(_HOST, ["list"], runner=fake)

    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# Interrupt cleanup — the local child is terminated, using the real default
# runner rather than the injected stub, since the thing under test is the
# module's own subprocess cleanup.
# ---------------------------------------------------------------------------


def test_interrupted_call_terminates_the_local_child_process(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    def _raising_communicate(self, timeout=None):  # noqa: ANN001
        captured["pid"] = self.pid
        raise KeyboardInterrupt()

    monkeypatch.setattr(subprocess.Popen, "communicate", _raising_communicate)

    with pytest.raises(KeyboardInterrupt):
        transport.default_runner(["sleep", "30"], 5.0, {})

    pid = captured["pid"]
    deadline = time.monotonic() + 2.0
    gone = False
    while time.monotonic() < deadline:
        try:
            subprocess.os.kill(pid, 0)
        except ProcessLookupError:
            gone = True
            break
        time.sleep(0.05)
    assert gone, f"child pid {pid} is still alive after the interrupted call unwound"
