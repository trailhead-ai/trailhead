"""Test contract: `camp env ls` end to end through camp's CLI entry point.

Every machine answer comes from stub `docker` and `ssh` executables on a test-only PATH; the hosts
file lives under `CAMP_CONFIG_DIR` in tmp_path. The ssh stub picks its reply by the destination
(the token after `--`), appends every destination it is called with to a log, and records what the
CLI's stdout file already held at that moment.

- One environment locally, no hosts declared: the `this machine` heading and one line, exit 0.
- Many: two local environments and one remote print two blocks in order, sorted, columns aligned.
- Streaming: this machine's block is on stdout while a slower host's read is still running.
- Concurrency: two hosts' ssh reads (and the docker read with an ssh read) are in flight at the same
  moment, proved by stubs that rendezvous; two hosts taking T each finish in under 1.5 T.
- Order: blocks print this machine, then `env_hosts` order, whichever host answers first.
- Failures stay per host: unreadable, timed out and fine hosts read together each show their own outcome.
- No environments: a header-only reply prints `no environments` under that host.
- Unreadable: a reply without the header prints `unreadable: ...` and none of its records, exit 0.
- Timed out: a stub that outlives the read bound prints `unreadable: no answer within ...`.
- Docker missing or not running, locally and remotely, prints those lines and never `unreadable`.
- Opt-in: a declared host absent from `env_hosts` is never contacted and is named on the
  `not opted in:` line; with every declared host opted in, no such line appears.
- Self: `env_hosts` entry and `[hosts.*]` table named as `self_name` cause no ssh call, no second block.
- Refusals: undeclared `env_hosts` entry, extra argument, `--host`, bare `env`, unknown subcommand.
- Interrupt: SIGINT to the CLI's process group, and to its pid alone, mid-listing: exit 130 within a
  couple of seconds, stderr exactly `camp env: interrupted`, no traceback, the block already printed
  is still on stdout, and no stub child is left running.
- Closed output: stdout closed after the first block while a later host is still being read: no
  traceback, no stub child left running, a prompt exit.
- Group independence: the same output from a directory in no group and from inside a group.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"
FIXTURES = Path(__file__).parent / "fixtures"
HEADER = (FIXTURES / "env_inventory_header_docker_engine_29_8_0_linux.txt").read_text()

_RENDEZVOUS = """
rv="$STUB_DIR/rv/$me"
if [ -f "$rv" ]; then
  peer=$(cat "$rv")
  : > "$STUB_DIR/started.$me"
  n=0
  while [ ! -f "$STUB_DIR/started.$peer" ] && [ $n -lt 50 ]; do sleep 0.1; n=$((n+1)); done
  [ -f "$STUB_DIR/started.$peer" ] || : > "$STUB_DIR/never-met.$me"
fi
"""

_DOCKER_STUB = """#!/bin/sh
me=docker
""" + _RENDEZVOUS + """
[ -f "$STUB_DIR/docker.stdout" ] && cat "$STUB_DIR/docker.stdout"
[ -f "$STUB_DIR/docker.stderr" ] && cat "$STUB_DIR/docker.stderr" >&2
[ -f "$STUB_DIR/docker.exit" ] && exit "$(cat "$STUB_DIR/docker.exit")"
exit 0
"""

_SSH_STUB = """#!/bin/sh
dest=
while [ $# -gt 0 ]; do
  if [ "$1" = "--" ]; then dest=$2; break; fi
  shift
done
echo "$dest" >> "$STUB_DIR/ssh.log"
me=$dest
RENDEZVOUS
if [ -n "$STUB_STDOUT_FILE" ]; then cat "$STUB_STDOUT_FILE" > "$STUB_DIR/stdout-at-call.$dest"; fi
reply="$STUB_DIR/ssh/$dest"
[ -f "$reply.delay" ] && sleep "$(cat "$reply.delay")"
[ -f "$reply.sleep" ] && exec sleep "$(cat "$reply.sleep")"
[ -f "$reply.stdout" ] && cat "$reply.stdout"
[ -f "$reply.stderr" ] && cat "$reply.stderr" >&2
[ -f "$reply.exit" ] && exit "$(cat "$reply.exit")"
exit 0
"""


def _records(*rows: tuple[str, ...]) -> str:
    """An inventory reply: (group, name, docker state[, published ports]) per container."""
    return HEADER + "".join(
        f'"{g}-env-{n}"|"{s}"|"{g}"|"{n}"|"{rest[0] if rest else ""}"\n' for g, n, s, *rest in rows
    )


class Rig:
    """A hermetic machine: stub docker/ssh, a hosts file, and a way to run the CLI."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "stubs"
        self.bin = self.root / "bin"
        self.bin.mkdir(parents=True)
        (self.root / "ssh").mkdir()
        (self.root / "rv").mkdir()
        for tool in ("sh", "cat", "sleep", "ps"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        self._script("ssh", _SSH_STUB.replace("RENDEZVOUS", _RENDEZVOUS))
        self.config_dir = tmp_path / "camp-config"
        self.config_dir.mkdir()
        self.state_dir = tmp_path / "camp-state"
        self.stdout_file = tmp_path / "cli-stdout"

    def _script(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def hosts_file(self, text: str) -> Path:
        path = self.config_dir / "hosts.toml"
        path.write_text(text)
        return path

    def docker(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self._script("docker", _DOCKER_STUB)
        (self.root / "docker.stdout").write_text(stdout)
        (self.root / "docker.stderr").write_text(stderr)
        (self.root / "docker.exit").write_text(str(exit_code))

    def ssh_reply(
        self,
        destination: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        sleep: float | None = None,
        delay: float | None = None,
    ) -> None:
        base = self.root / "ssh" / destination
        base.with_name(base.name + ".stdout").write_text(stdout)
        base.with_name(base.name + ".stderr").write_text(stderr)
        base.with_name(base.name + ".exit").write_text(str(exit_code))
        if sleep is not None:
            base.with_name(base.name + ".sleep").write_text(str(sleep))
        if delay is not None:
            base.with_name(base.name + ".delay").write_text(str(delay))

    def rendezvous(self, machine: str, peer: str) -> None:
        """Make ``machine``'s stub (an ssh destination, or ``docker``) wait up to 5 s for ``peer``'s."""
        (self.root / "rv" / machine).write_text(peer)

    def never_met(self) -> list[str]:
        """The machines whose read ran alone: it started and no peer started within the wait."""
        return sorted(p.name.removeprefix("never-met.") for p in self.root.glob("never-met.*"))

    @property
    def ssh_calls(self) -> list[str]:
        log = self.root / "ssh.log"
        return log.read_text().split() if log.exists() else []

    def stdout_at_first_call(self, destination: str) -> str:
        return (self.root / f"stdout-at-call.{destination}").read_text()

    def env(self) -> dict[str, str]:
        return {
            "PATH": str(self.bin),
            "HOME": str(self.root),
            "CAMP_CONFIG_DIR": str(self.config_dir),
            "CAMP_STATE_DIR": str(self.state_dir),
            "STUB_DIR": str(self.root),
            "STUB_STDOUT_FILE": str(self.stdout_file),
        }

    def run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        with self.stdout_file.open("wb") as out:
            completed = subprocess.run(
                [sys.executable, str(_CLI_CAMP), *args],
                stdout=out,
                stderr=subprocess.PIPE,
                text=True,
                env=self.env(),
                cwd=str(cwd) if cwd else str(self.root),
            )
        completed.stdout = self.stdout_file.read_text()
        return completed


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


PERSEUS = "tduffield@perseus"
MACBOOK = "tduffield@toms-macbook-pro"
TWO_PEERS = f"""
[hosts.perseus]
ssh = "{PERSEUS}"

[hosts.toms-macbook-pro]
ssh = "{MACBOOK}"
"""


def test_one_environment_locally_with_no_hosts(rig: Rig) -> None:
    rig.docker(stdout=_records(("levr", "main", "running")))

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "this machine\n  levr  main  running\n"


def test_published_ports_do_not_change_what_ls_prints(rig: Rig) -> None:
    rig.hosts_file('env_hosts = ["perseus"]\n' + TWO_PEERS.split("\n[hosts.toms")[0])
    rig.docker(
        stdout=_records(
            ("levr", "main", "running", "127.0.0.1:28081->8081/tcp, 127.0.0.1:28080->8080/tcp"),
            ("levr", "ben", "exited", ""),
        )
    )
    rig.ssh_reply(PERSEUS, stdout=_records(("levr", "main", "running", "127.0.0.1:3000->3000/tcp")))

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "this machine\n  levr  ben   stopped\n  levr  main  running\nperseus\n  levr  main  running\n"
    )


def test_ports_that_are_not_loopback_tcp_make_that_machine_unreadable_without_the_value(rig: Rig) -> None:
    rig.docker(stdout=_records(("levr", "main", "running", "0.0.0.0:31337->8080/tcp")))

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("this machine\n  unreadable: ")
    assert "31337" not in result.stdout and "levr" not in result.stdout.split("unreadable")[1]


def test_heading_names_this_machine_when_self_name_is_declared(rig: Rig) -> None:
    rig.hosts_file('self_name = "andromeda"\n')
    rig.docker(stdout=_records(("levr", "main", "running")))

    result = rig.run("env", "ls")

    assert result.stdout.splitlines()[0] == "this machine (andromeda)"


def test_many_environments_print_ordered_sorted_aligned_blocks(rig: Rig) -> None:
    rig.hosts_file(
        'self_name = "andromeda"\nenv_hosts = ["perseus"]\n' + TWO_PEERS.split("\n[hosts.toms")[0]
    )
    rig.docker(
        stdout=_records(
            ("levr", "main", "running"),
            ("alpha", "zz", "exited"),
            ("levr", "ben-feature", "paused"),
        )
    )
    rig.ssh_reply(PERSEUS, stdout=_records(("levr", "main", "running")))

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "this machine (andromeda)"
    assert lines[4] == "perseus"
    assert lines[5] == "  levr  main  running"
    local = [re.fullmatch(r"  (\S+)(\s+)(\S+)(\s+)(\S+)", line) for line in lines[1:4]]
    assert all(local), lines
    assert [(m[1], m[3], m[5]) for m in local] == [
        ("alpha", "zz", "stopped"),
        ("levr", "ben-feature", "stopped"),
        ("levr", "main", "running"),
    ]
    assert len({(m.start(3), m.start(5)) for m in local}) == 1, lines


def _hosts(*names: str, self_name: str | None = None) -> str:
    head = f'self_name = "{self_name}"\n' if self_name else ""
    listed = ", ".join(f'"{n}"' for n in names)
    return head + f"env_hosts = [{listed}]\n" + "".join(
        f'\n[hosts.{n}]\nssh = "u@{n}"\n' for n in names
    )


def test_this_machines_block_is_on_stdout_while_a_slower_host_is_still_being_read(rig: Rig) -> None:
    rig.hosts_file(_hosts("slow", self_name="andromeda"))
    rig.docker(stdout=_records(("levr", "main", "running")))
    rig.ssh_reply("u@slow", stdout=HEADER, delay=8)
    process = subprocess.Popen(
        [sys.executable, str(_CLI_CAMP), "env", "ls"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=rig.env(),
        cwd=str(rig.root),
    )
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
    try:
        got = []
        deadline = time.monotonic() + 6
        while len(got) < 2 and time.monotonic() < deadline:
            try:
                got.append(lines.get(timeout=0.2))
            except queue.Empty:
                pass
        still_running = process.poll() is None
    finally:
        process.kill()
        process.wait()

    assert got == ["this machine (andromeda)\n", "  levr  main  running\n"]
    assert still_running, "the run had already finished, so the slow read was not still running"


def test_two_hosts_are_read_at_the_same_moment(rig: Rig) -> None:
    rig.hosts_file(_hosts("one", "two"))
    rig.docker(stdout=HEADER)
    rig.ssh_reply("u@one", stdout=HEADER)
    rig.ssh_reply("u@two", stdout=HEADER)
    rig.rendezvous("u@one", "u@two")
    rig.rendezvous("u@two", "u@one")

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    assert rig.never_met() == [], f"read ran alone, no peer started within 5s: {rig.never_met()}"


def test_this_machines_docker_read_overlaps_a_remote_read(rig: Rig) -> None:
    rig.hosts_file(_hosts("one"))
    rig.docker(stdout=HEADER)
    rig.ssh_reply("u@one", stdout=HEADER)
    rig.rendezvous("docker", "u@one")
    rig.rendezvous("u@one", "docker")

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    assert rig.never_met() == [], f"read ran alone, no peer started within 5s: {rig.never_met()}"


def test_two_hosts_that_each_take_about_t_finish_in_under_one_and_a_half_t(rig: Rig) -> None:
    t = 3.0
    rig.hosts_file(_hosts("one", "two"))
    rig.docker(stdout=HEADER)
    rig.ssh_reply("u@one", stdout=HEADER, delay=t)
    rig.ssh_reply("u@two", stdout=HEADER, delay=t)

    started = time.monotonic()
    result = rig.run("env", "ls")
    elapsed = time.monotonic() - started

    assert result.stdout == "this machine\n  no environments\none\n  no environments\ntwo\n  no environments\n"
    assert elapsed < 1.5 * t, f"two {t}s reads took {elapsed:.1f}s"


def test_blocks_print_this_machine_then_env_hosts_order_whichever_host_answers_first(rig: Rig) -> None:
    rig.hosts_file(_hosts("slow", "fast"))
    rig.docker(stdout=_records(("here", "main", "running")))
    rig.ssh_reply("u@slow", stdout=_records(("far", "slow-one", "running")), delay=1.5)
    rig.ssh_reply("u@fast", stdout=_records(("far", "fast-one", "exited")))

    result = rig.run("env", "ls")

    assert result.stdout == (
        "this machine\n  here  main  running\n"
        "slow\n  far  slow-one  running\n"
        "fast\n  far  fast-one  stopped\n"
    )


def test_each_host_shows_its_own_outcome_when_read_together(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from camp.cli import dispatch
    from camp.env import read

    rig.hosts_file(_hosts("down", "stuck", "fine"))
    rig.docker(stdout=HEADER)
    rig.ssh_reply("u@down", stderr="no route to the thing\n", exit_code=255)
    rig.ssh_reply("u@stuck", sleep=10)
    rig.ssh_reply("u@fine", stdout=_records(("levr", "main", "running")))
    rig.stdout_file.write_text("")
    for key, value in rig.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(read, "READ_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(sys, "argv", ["camp", "env", "ls"])

    dispatch.main()

    assert capsys.readouterr().out == (
        "this machine\n  no environments\n"
        "down\n  unreadable: no route to the thing\n"
        "stuck\n  unreadable: no answer within 1s\n"
        "fine\n  levr  main  running\n"
    )


def test_header_only_reply_prints_no_environments_under_that_host(rig: Rig) -> None:
    rig.hosts_file('env_hosts = ["perseus"]\n' + TWO_PEERS.split("\n[hosts.toms")[0])
    rig.docker(stdout=HEADER)
    rig.ssh_reply(PERSEUS, stdout=HEADER)

    result = rig.run("env", "ls")

    assert result.stdout == "this machine\n  no environments\nperseus\n  no environments\n"


def test_reply_without_the_header_is_unreadable_and_exits_zero(rig: Rig) -> None:
    rig.hosts_file('env_hosts = ["perseus"]\n' + TWO_PEERS.split("\n[hosts.toms")[0])
    rig.docker(stdout=_records(("levr", "main", "running")))
    rig.ssh_reply(PERSEUS, stdout='"sneaky-env-one"|"running"|"sneaky"|"one"|""\n')

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    block = result.stdout.split("perseus\n")[1]
    assert block.startswith("  unreadable: ")
    assert "sneaky" not in result.stdout
    assert block.count("\n") == 1


def test_a_host_that_outlives_the_read_bound_is_unreadable_with_the_timeout(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from camp.cli import dispatch
    from camp.env import read

    rig.hosts_file('env_hosts = ["perseus"]\n' + TWO_PEERS.split("\n[hosts.toms")[0])
    rig.docker(stdout=HEADER)
    rig.ssh_reply(PERSEUS, sleep=10)
    for key, value in rig.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(read, "READ_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(sys, "argv", ["camp", "env", "ls"])

    dispatch.main()

    out = capsys.readouterr().out
    assert out == "this machine\n  no environments\nperseus\n  unreadable: no answer within 1s\n"


@pytest.mark.parametrize(
    ("stderr_fixture", "exit_code", "line"),
    [
        (None, 0, "Docker is not installed"),
        ("env_read_andromeda_local_daemon_missing_socket.stderr", 1, "Docker is not running"),
    ],
)
def test_local_docker_missing_or_not_running(
    rig: Rig, stderr_fixture: str | None, exit_code: int, line: str
) -> None:
    if stderr_fixture:
        rig.docker(stderr=(FIXTURES / stderr_fixture).read_text(), exit_code=exit_code)

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"this machine\n  {line}\n"


@pytest.mark.parametrize(
    ("stderr_fixture", "line"),
    [
        ("env_read_perseus_tailscale_unknown_command.stderr", "Docker is not installed"),
        ("env_read_perseus_tailscale_daemon_tcp.stderr", "Docker is not running"),
    ],
)
def test_remote_docker_missing_or_not_running(rig: Rig, stderr_fixture: str, line: str) -> None:
    rig.hosts_file('env_hosts = ["perseus"]\n' + TWO_PEERS.split("\n[hosts.toms")[0])
    rig.docker(stdout=HEADER)
    rig.ssh_reply(PERSEUS, stderr=(FIXTURES / stderr_fixture).read_text())

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"this machine\n  no environments\nperseus\n  {line}\n"
    assert "unreadable" not in result.stdout


def test_ssh_that_cannot_start_is_unreadable_and_the_run_carries_on(rig: Rig) -> None:
    rig.hosts_file('env_hosts = ["perseus"]\n' + TWO_PEERS)
    rig.docker(stdout=HEADER)
    (rig.bin / "ssh").unlink()

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout.startswith("this machine\n  no environments\nperseus\n  unreadable: cannot run ssh: ")
    assert result.stdout.endswith(
        f"not opted in: toms-macbook-pro (add to env_hosts in {rig.config_dir / 'hosts.toml'})\n"
    )


@pytest.mark.parametrize(
    "content",
    [b"env_hosts = [\xff\xfe]\n", b"x = " + b"[" * 5000 + b"]" * 5000 + b"\n"],
    ids=["invalid-utf8", "deeply-nested"],
)
def test_a_hosts_file_that_cannot_be_read_refuses_without_a_traceback(
    rig: Rig, content: bytes
) -> None:
    hosts = rig.config_dir / "hosts.toml"
    hosts.write_bytes(content)
    rig.docker(stdout=HEADER)

    result = rig.run("env", "ls")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("camp env: ")
    assert str(hosts) in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_a_hosts_file_without_read_permission_refuses_without_a_traceback(rig: Rig) -> None:
    hosts = rig.hosts_file('env_hosts = ["perseus"]\n' + TWO_PEERS)
    rig.docker(stdout=HEADER)
    hosts.chmod(0)

    try:
        result = rig.run("env", "ls")
    finally:
        hosts.chmod(0o600)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("camp env: ")
    assert str(hosts) in result.stderr
    assert "Traceback" not in result.stderr


def test_a_left_out_host_name_carrying_control_characters_is_printed_without_them(rig: Rig) -> None:
    rig.hosts_file(
        f'[hosts."evil\\u001b[2J\\u202ename"]\nssh = "{MACBOOK}"\n'
    )
    rig.docker(stdout=HEADER)

    result = rig.run("env", "ls")

    assert result.returncode == 0, result.stderr
    last = result.stdout.splitlines()[-1]
    assert last.startswith("not opted in: evil[2Jname (add to env_hosts in ")
    assert "\x1b" not in result.stdout and "‮" not in result.stdout


def test_a_declared_host_not_in_env_hosts_is_never_contacted_and_is_named(rig: Rig) -> None:
    hosts = rig.hosts_file('env_hosts = ["perseus"]\n' + TWO_PEERS)
    rig.docker(stdout=HEADER)
    rig.ssh_reply(PERSEUS, stdout=HEADER)

    result = rig.run("env", "ls")

    assert rig.ssh_calls == [PERSEUS]
    assert result.stdout.splitlines()[-1] == (
        f"not opted in: toms-macbook-pro (add to env_hosts in {hosts})"
    )


def test_hosts_left_out_are_named_in_declaration_order(rig: Rig) -> None:
    hosts = rig.hosts_file(TWO_PEERS)
    rig.docker(stdout=HEADER)

    result = rig.run("env", "ls")

    assert rig.ssh_calls == []
    assert result.stdout.splitlines()[-1] == (
        f"not opted in: perseus, toms-macbook-pro (add to env_hosts in {hosts})"
    )


def test_no_not_opted_in_line_when_every_declared_host_is_opted_in(rig: Rig) -> None:
    rig.hosts_file('env_hosts = ["perseus", "toms-macbook-pro"]\n' + TWO_PEERS)
    rig.docker(stdout=HEADER)
    rig.ssh_reply(PERSEUS, stdout=HEADER)
    rig.ssh_reply(MACBOOK, stdout=HEADER)

    result = rig.run("env", "ls")

    assert sorted(rig.ssh_calls) == sorted([PERSEUS, MACBOOK])
    assert "not opted in" not in result.stdout


def test_self_name_is_never_read_over_ssh_or_listed_twice(rig: Rig) -> None:
    rig.hosts_file(
        'self_name = "andromeda"\nenv_hosts = ["andromeda", "perseus"]\n'
        '[hosts.andromeda]\nssh = "tduffield@andromeda"\n'
        + TWO_PEERS.split("\n[hosts.toms")[0]
    )
    rig.docker(stdout=_records(("levr", "main", "running")))
    rig.ssh_reply(PERSEUS, stdout=HEADER)

    result = rig.run("env", "ls")

    assert rig.ssh_calls == [PERSEUS]
    assert result.stdout == (
        "this machine (andromeda)\n  levr  main  running\nperseus\n  no environments\n"
    )


def test_self_declared_but_not_opted_in_is_not_reported_as_left_out(rig: Rig) -> None:
    rig.hosts_file('self_name = "andromeda"\n[hosts.andromeda]\nssh = "tduffield@andromeda"\n')
    rig.docker(stdout=HEADER)

    result = rig.run("env", "ls")

    assert result.stdout == "this machine (andromeda)\n  no environments\n"


def test_env_hosts_naming_an_undeclared_host_refuses_and_lists_nothing(rig: Rig) -> None:
    rig.hosts_file('env_hosts = ["ghost"]\n' + TWO_PEERS)
    rig.docker(stdout=_records(("levr", "main", "running")))

    result = rig.run("env", "ls")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("camp env: ")
    assert "ghost" in result.stderr
    assert "Traceback" not in result.stderr
    assert rig.ssh_calls == []


def test_a_malformed_hosts_file_refuses(rig: Rig) -> None:
    rig.hosts_file("this is = = not toml\n")
    rig.docker(stdout=HEADER)

    result = rig.run("env", "ls")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("camp env: ")


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["extra"], "camp env ls: "),
        (["--bogus"], "camp env ls: "),
        (["--host", "perseus"], "camp env: --host has no meaning here\n"),
    ],
)
def test_ls_refuses_any_argument(rig: Rig, extra: list[str], message: str) -> None:
    rig.docker(stdout=_records(("levr", "main", "running")))

    result = rig.run("env", "ls", *extra)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith(message)
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("args", [[], ["frobnicate"]])
def test_bare_env_and_unknown_subcommand_print_one_usage_line(rig: Rig, args: list[str]) -> None:
    result = rig.run("env", *args)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "usage: camp env ls | attach TARGET | status | detach\n"


def test_camp_help_lists_the_verb(rig: Rig) -> None:
    result = rig.run("help")

    assert "camp env ls" in result.stdout + result.stderr


def test_output_is_the_same_outside_and_inside_a_camp_group(rig: Rig, tmp_path: Path) -> None:
    rig.hosts_file('self_name = "andromeda"\nenv_hosts = ["perseus"]\n' + TWO_PEERS)
    rig.docker(stdout=_records(("levr", "main", "running")))
    rig.ssh_reply(PERSEUS, stdout=_records(("levr", "main", "stopped")))
    repo = tmp_path / "member-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    groups = rig.config_dir / "groups"
    groups.mkdir()
    (groups / "levr.toml").write_text(
        f'[group]\nname = "levr"\n\n[[members]]\nname = "m"\nrepo_root = "{repo}"\n'
    )

    outside = rig.run("env", "ls", cwd=tmp_path)
    inside = rig.run("env", "ls", cwd=repo)

    assert outside.returncode == inside.returncode == 0
    assert outside.stdout == inside.stdout
    assert "this machine (andromeda)" in inside.stdout


_SLEEPING_DOCKER = """#!/bin/sh
echo $$ > "$STUB_DIR/pid.docker"
if [ -f "$STUB_DIR/docker.stdout" ]; then cat "$STUB_DIR/docker.stdout"; fi
if [ -f "$STUB_DIR/docker.sleep" ]; then exec sleep "$(cat "$STUB_DIR/docker.sleep")"; fi
exit 0
"""

_SLEEPING_SSH = """#!/bin/sh
dest=
while [ $# -gt 0 ]; do
  if [ "$1" = "--" ]; then dest=$2; break; fi
  shift
done
echo $$ > "$STUB_DIR/pid.$dest"
if [ "$dest" = "$STUB_SHORT_DEST" ]; then exec sleep 1; fi
exec sleep 20
"""


class _Listing:
    """`camp env ls` in its own session against a stub docker that answers at once and ssh stubs that sleep."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        rig._script("docker", _SLEEPING_DOCKER)
        rig._script("ssh", _SLEEPING_SSH)
        (rig.root / "docker.stdout").write_text(_records(("levr", "main", "running")))
        rig.hosts_file('env_hosts = ["perseus", "toms-macbook-pro"]\n' + TWO_PEERS)
        self.process: subprocess.Popen | None = None

    def start(self, short_dest: str = "") -> subprocess.Popen:
        self.process = subprocess.Popen(
            [sys.executable, str(_CLI_CAMP), "env", "ls"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**self.rig.env(), "STUB_SHORT_DEST": short_dest},
            cwd=str(self.rig.root),
            start_new_session=True,
        )
        return self.process

    def pids(self) -> dict[str, int]:
        found = {}
        for path in self.rig.root.glob("pid.*"):
            if path.read_text().strip():
                found[path.name.removeprefix("pid.")] = int(path.read_text())
        return found

    def wait_for_ssh_children(self) -> None:
        wanted = {PERSEUS, MACBOOK}
        deadline = time.monotonic() + 10
        while not wanted <= set(self.pids()) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert wanted <= set(self.pids()), f"ssh stubs that never started: {wanted - set(self.pids())}"

    def survivors(self) -> list[str]:
        alive = []
        for name, pid in self.pids().items():
            try:
                os.kill(pid, 0)
                alive.append(name)
            except ProcessLookupError:
                pass
        return sorted(alive)

    def survivors_after_grace(self, limit: float = 2.0) -> list[str]:
        deadline = time.monotonic() + limit
        while self.survivors() and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.survivors()

    def finish(self, limit: float = 5.0) -> tuple[str, str, float]:
        started = time.monotonic()
        try:
            out, err = self.process.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate()
            pytest.fail(f"camp env ls was still running {limit}s after it was told to stop")
        return out, err, time.monotonic() - started

    def cleanup(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.communicate()
        for pid in self.pids().values():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("whole_group", [True, False], ids=["process-group", "pid-only"])
def test_an_interrupt_mid_listing_ends_every_child_and_prints_one_line(rig: Rig, whole_group: bool) -> None:
    listing = _Listing(rig)
    process = listing.start()
    try:
        listing.wait_for_ssh_children()
        printed = [process.stdout.readline(), process.stdout.readline()]
        (os.killpg if whole_group else os.kill)(process.pid, signal.SIGINT)
        rest, err, took = listing.finish()
        left = listing.survivors_after_grace()

        assert process.returncode == 130, f"exit {process.returncode}, stderr {err!r}"
        assert err == "camp env: interrupted\n"
        assert printed == ["this machine\n", "  levr  main  running\n"] and rest == ""
        assert took < 3, f"camp took {took:.1f}s to exit"
        assert left == [], f"children still running after the interrupt: {left}"
    finally:
        listing.cleanup()


def test_closing_stdout_while_a_later_host_is_still_read_ends_it_without_a_traceback(rig: Rig) -> None:
    listing = _Listing(rig)
    process = listing.start(short_dest=PERSEUS)
    try:
        listing.wait_for_ssh_children()
        assert process.stdout.readline() == "this machine\n"
        process.stdout.close()
        _, err, took = listing.finish()
        left = listing.survivors_after_grace()

        assert "Traceback" not in err and "BrokenPipe" not in err, err
        assert took < 3, f"camp took {took:.1f}s to exit"
        assert left == [], f"children still running after stdout closed: {left}"
    finally:
        listing.cleanup()
