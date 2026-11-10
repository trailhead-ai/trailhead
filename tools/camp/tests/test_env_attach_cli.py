"""Test contract: `camp env attach` end to end through camp's CLI entry point, against the stub-ssh rig.

Every machine answer comes from the rig in `env_ssh_stub.py` (a stub `ssh` that really binds and
relays the `-L` ports and backgrounds itself under `-f`, a stub `docker`, a counting `ps`); every
test has its own tag namespace and free high ports, and the rig ends whatever it started.

- Rig: its backgrounded process shows argv[0] `ssh`, holds /dev/null on descriptors 0, 1 and 2, and relays.
- Attaching a running remote environment answers on each standard port with its upstream; the success line.
- A stopped environment, or one publishing no ports, is refused and no forward is started.
- A port held on 127.0.0.1 or only on ::1 is refused naming port and holder; nothing started, ports free.
- A successful attach returns in a few seconds.
- Bind-first-then-fail, ssh that cannot start (escape sequence sanitised), die-after-fork: fail, nothing held.
- Attach while another attachment exists is refused before any host is read.
- Interrupt while a bare name's machines are being read (SIGINT to the process group, and to the pid
  alone) exits 130 within about 2 s saying `camp env: interrupted`, leaves no read child and starts no forward.
- Interrupt after the forward started ends it; an unreadable process table says it could not be ended.
- Failure cleanup ends only this attach's forward.
- Bare name, HOST/NAME and HOST/GROUP/NAME reach the same environment; a resolution refusal exits 1.
- Usage errors; an invalid tag namespace.
- Local attach (a relay, not an ssh forward): `local/NAME`, `<self_name>/NAME` and a bare name found only here
  answer on each standard port; a stopped or port-less environment, Docker not running, a port held before
  attach, and a port taken between the probe and the bind are refused with nothing left running; a relay
  blocks a remote attach (no dead-link note) and a forward blocks a local one (with it); a name found here and
  on a host is ambiguous; an environment gone before the relay starts still attaches and closes connections;
  an interrupt, a failure and a relay hidden from the process table all end the relay camp started.
- The attach lock (a race, run as concurrent camp processes against the rig): two simultaneous attaches of
  any kind and any environment end with exactly one attachment; the loser is refused with the
  already-attached message naming the winner, started no forward or relay and holds none of its ports. A
  lone attach prints what it always did, with no waiting line, and leaves `env-attach.lock` empty. A held
  lock prints the waiting line once; past the bound the attach refuses and starts nothing, released
  within the bound it attaches. An unopenable lock file refuses naming path and reason. An interrupt
  while waiting exits 130 and starts nothing. In each, the winner's forward is left running.
"""

from __future__ import annotations

import os
import re
import select
import signal
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from .env_ssh_stub import AttachRig, ask, attached_rig, local_rig, port_is_free, wait_for  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
USAGE = "usage: camp env attach TARGET\n"


@pytest.fixture
def rig(tmp_path: Path):
    r = AttachRig(tmp_path)
    try:
        yield r
    finally:
        r.close()


def success_line(host: str, env, ) -> str:
    ports = " ".join(str(p) for p in env.standard_ports)
    return f"attached {host}/{env.group}/{env.name} on ports {ports}; its health was not checked\n"


def assert_nothing_held(rig: AttachRig, env) -> None:
    assert rig.tagged_pids() == []
    assert all(port_is_free(p) for p in env.standard_ports)


# --- the rig's own behaviour --------------------------------------------------------------------


def run_stub_forward(rig: AttachRig, env, tag: str, sink) -> None:
    links = [arg for s_, p_ in env.ports for arg in ("-L", f"127.0.0.1:{s_}:127.0.0.1:{p_}")]
    done = subprocess.run(
        ["ssh", "-f", "-N", "-o", "ExitOnForwardFailure=yes", "-o", f"Tag={tag}", *links, "--", "tduffield@perseus"],
        env=rig.environ(),
        stdin=sink,
        stdout=sink,
        stderr=sink,
        timeout=20,
    )
    assert done.returncode == 0


def test_rig_forward_shows_argv0_ssh_in_ps(rig: AttachRig) -> None:
    env = rig.environment("main")
    with tempfile.TemporaryFile() as sink:
        run_stub_forward(rig, env, f"{rig.ns}:abc:perseus/levr/main", sink)
    (pid,) = rig.wait_tagged()

    command = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout

    assert command.split()[0] == "ssh"
    assert "-N" in command.split()


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/<pid>/fd to read a process's descriptors")
def test_rig_forward_points_descriptors_0_1_2_at_dev_null_not_what_it_inherited(rig: AttachRig) -> None:
    env = rig.environment("main")
    with tempfile.TemporaryFile() as sink:
        run_stub_forward(rig, env, f"{rig.ns}:abc:perseus/levr/main", sink)
        (pid,) = rig.wait_tagged()
        inherited = os.readlink(f"/proc/{os.getpid()}/fd/{sink.fileno()}")

        held = [os.readlink(f"/proc/{pid}/fd/{fd}") for fd in (0, 1, 2)]

    assert held == ["/dev/null"] * 3
    assert inherited != "/dev/null"


def test_rig_works_when_its_root_directory_name_contains_a_space(tmp_path: Path) -> None:
    spaced = AttachRig(tmp_path / "a root with spaces")
    try:
        env = attached_rig(spaced, "main")

        done = spaced.attach("perseus/levr/main")

        assert done.returncode == 0, done.stderr
        assert [ask(p) for p in env.standard_ports] == ["main-0", "main-1", "main-2"]
        assert spaced.camp("env", "status").stdout.startswith("attached: perseus/levr/main")
    finally:
        spaced.close()


def test_rig_forward_relays_each_port_to_its_own_upstream(rig: AttachRig) -> None:
    env = rig.environment("main")
    with tempfile.TemporaryFile() as sink:
        run_stub_forward(rig, env, f"{rig.ns}:abc:perseus/levr/main", sink)
    rig.wait_tagged()

    assert [ask(p) for p in env.standard_ports] == ["main-0", "main-1", "main-2"]


# --- attaching ----------------------------------------------------------------------------------


def test_attach_makes_each_standard_port_answer_with_the_upstream_and_names_the_ports(rig: AttachRig) -> None:
    env = attached_rig(rig, "main")
    other = rig.environment("other")
    rig.host_inventory("perseus", env, other)

    done = rig.attach("perseus/levr/main")

    assert done.returncode == 0, done.stderr
    assert done.stdout == success_line("perseus", env)
    assert [ask(p) for p in env.standard_ports] == ["main-0", "main-1", "main-2"]
    assert all(port_is_free(p) for p in other.standard_ports)


def test_a_successful_attach_returns_as_soon_as_the_forward_is_up_not_when_a_timeout_runs_out(rig: AttachRig) -> None:
    from camp.cli.env import START_MARGIN_SECONDS

    attached_rig(rig)

    started = time.monotonic()
    done = rig.attach("perseus/levr/main")
    elapsed = time.monotonic() - started

    assert done.returncode == 0, done.stderr
    # Normal is about a second. The failure this guards is attach lingering until the spawn
    # timeout (connect timeout + START_MARGIN_SECONDS) instead of returning once the forward is
    # up; half the margin leaves several times the normal time for a loaded machine.
    assert elapsed < START_MARGIN_SECONDS / 2


@pytest.mark.parametrize("target", ["main", "perseus/main", "perseus/levr/main"])
def test_every_target_form_reaches_the_same_environment(rig: AttachRig, target: str) -> None:
    env = attached_rig(rig)

    done = rig.attach(target)

    assert done.returncode == 0, done.stderr
    assert done.stdout == success_line("perseus", env)
    (call,) = rig.forwards()
    assert [a for a in call if a.startswith("127.0.0.1:")] == [
        f"127.0.0.1:{s_}:127.0.0.1:{p_}" for s_, p_ in env.ports
    ]


def test_host_group_name_picks_the_group_when_the_name_exists_in_two(rig: AttachRig) -> None:
    levr = rig.environment("main", group="levr")
    acme = rig.environment("main", group="acme")
    rig.declare_hosts("perseus")
    rig.host_inventory("perseus", levr, acme)

    done = rig.attach("perseus/acme/main")

    assert done.returncode == 0, done.stderr
    assert done.stdout == success_line("perseus", acme)
    assert [ask(p) for p in acme.standard_ports] == ["main-0", "main-1", "main-2"]


def test_a_resolution_refusal_exits_1_with_its_message_on_stderr(rig: AttachRig) -> None:
    attached_rig(rig, "main")

    done = rig.attach("nope")

    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr == (
        "camp env: no environment named nope on this machine or perseus;"
        " run camp env ls to see what is running\n"
    )
    assert rig.forwards() == []


def test_a_stopped_environment_is_refused_naming_its_state_and_no_forward_is_started(rig: AttachRig) -> None:
    attached_rig(rig, "main", state="stopped")

    done = rig.attach("perseus/levr/main")

    assert done.returncode == 1
    assert done.stderr == (
        "camp env: perseus/levr/main is stopped;"
        " start it with its project's own tooling, then attach again\n"
    )
    assert rig.forwards() == []


def test_a_running_environment_publishing_no_ports_is_refused(rig: AttachRig) -> None:
    attached_rig(rig, "main", count=0)

    done = rig.attach("perseus/levr/main")

    assert done.returncode == 1
    assert done.stderr == "camp env: perseus/levr/main publishes no ports, so there is nothing to attach\n"
    assert rig.forwards() == []


# --- a held standard port ---------------------------------------------------------------------


def hold(port: int, family: int, address: str) -> socket.socket:
    sock = socket.socket(family)
    sock.bind((address, port))
    sock.listen()
    return sock


@pytest.mark.parametrize(
    "family, address", [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")], ids=["ipv4", "ipv6-only"]
)
def test_a_held_standard_port_is_refused_naming_port_and_holder(rig: AttachRig, family: int, address: str) -> None:
    env = attached_rig(rig)
    held_port = env.standard_ports[1]
    listener = hold(held_port, family, address)
    try:
        done = rig.attach("perseus/levr/main")

        assert done.returncode == 1
        assert re.fullmatch(
            rf"camp env: port {held_port} is held by \S+ \(pid {os.getpid()}\); stop it, then attach again\n",
            done.stderr,
        ), done.stderr
        assert rig.forwards() == []
        assert rig.tagged_pids() == []
        assert [port_is_free(p) for p in env.standard_ports] == [True, False, True]
    finally:
        listener.close()
    assert all(port_is_free(p) for p in env.standard_ports)


# --- failures at the start --------------------------------------------------------------------


def test_a_forward_that_binds_its_first_port_and_fails_on_the_second_leaves_everything_free(rig: AttachRig) -> None:
    env = attached_rig(rig)
    rig.configure(bind_first_then_fail=True)

    done = rig.attach("perseus/levr/main")

    second = env.standard_ports[1]
    assert done.returncode == 1
    assert done.stderr == (
        "camp env: attach failed: ssh could not start the forward:"
        f" bind [127.0.0.1]:{second}: Address already in use\n"
    )
    assert len(rig.forwards()) == 1
    assert_nothing_held(rig, env)


def test_ssh_that_cannot_start_fails_with_the_sanitised_line_and_holds_nothing(rig: AttachRig) -> None:
    env = attached_rig(rig)
    rig.configure(fail_start="\x1b[31mssh: connect to host perseus port 22: Connection refused\nsecond line")

    done = rig.attach("perseus/levr/main")

    assert done.returncode == 1
    assert done.stderr == (
        "camp env: attach failed: ssh could not start the forward:"
        " [31mssh: connect to host perseus port 22: Connection refused\n"
    )
    assert_nothing_held(rig, env)


_PQ_WARNING = (
    "** WARNING: connection is not using a post-quantum key exchange algorithm.\n"
    "** This session may be vulnerable to \"store now, decrypt later\" attacks.\n"
    "** The server may need to be upgraded. See https://openssh.com/pq.html\n"
)


@pytest.mark.parametrize(
    "preamble",
    [
        _PQ_WARNING,
        "Warning: Permanently added 'perseus' to the list of known hosts.\n",
        "debug1: Reading configuration data\n",
    ],
    ids=["post-quantum-block", "warning", "debug"],
)
def test_the_reason_skips_ssh_warning_and_debug_lines(rig: AttachRig, preamble: str) -> None:
    env = attached_rig(rig)
    failure = (_FIXTURES / "env_forward_bind_failure_linux_openssh_9_6.txt").read_text()
    bind_line = failure.split("stderr:\n", 1)[1].splitlines()[0]
    rig.configure(fail_start=preamble + bind_line + "\nCould not request local forwarding.")

    done = rig.attach("perseus/levr/main")

    assert done.returncode == 1
    assert done.stderr == f"camp env: attach failed: ssh could not start the forward: {bind_line}\n"
    assert_nothing_held(rig, env)


def test_the_reason_falls_back_to_the_first_line_when_every_line_is_a_warning(rig: AttachRig) -> None:
    attached_rig(rig)
    rig.configure(fail_start="Warning: one\nWarning: two")

    done = rig.attach("perseus/levr/main")

    assert done.stderr == "camp env: attach failed: ssh could not start the forward: Warning: one\n"


def test_an_interrupt_while_ending_a_forward_that_is_not_running_exits_130_without_a_traceback(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from camp.cli import env as env_cli

    attached_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    real = env_cli.attachments
    calls: list[int] = []

    def attachments_once_interrupted():
        calls.append(1)
        if len(calls) == 3:
            raise KeyboardInterrupt
        return real()

    monkeypatch.setattr(env_cli, "attachments", attachments_once_interrupted)
    monkeypatch.setattr(env_cli, "_start_forward", lambda argv, timeout: None)
    monkeypatch.setattr(env_cli, "_wait_for_forward", lambda attach_id: None)

    try:
        env_cli._cmd_env_attach(["perseus/levr/main"])
    except SystemExit as exit_:
        code = exit_.code
    except KeyboardInterrupt:
        pytest.fail("the interrupt escaped as a traceback")
    else:
        code = 0

    assert code == 130
    assert capsys.readouterr().err.startswith("camp env: attach interrupted")


def _unverifiable(message: str):
    from camp.env.attachment import Unverifiable

    return Unverifiable(message)


def _raises(error: BaseException):
    def raiser(*args, **kwargs):
        raise error

    return raiser


@pytest.mark.parametrize(
    "path, patches",
    [
        ("interrupt-during-the-attach", {"_wait_for_forward": _raises(KeyboardInterrupt())}),
        ("unverifiable-handler", {"_wait_for_forward": _raises(_unverifiable("ps broke"))}),
        ("exception-handler", {"_wait_for_forward": _raises(RuntimeError("boom"))}),
        ("ssh-failed-to-start", {"_start_forward": lambda argv, timeout: "ssh could not start the forward"}),
        ("forward-not-running", {"_wait_for_forward": lambda attach_id: None}),
    ],
)
def test_an_interrupt_during_any_cleanup_exits_130_and_says_to_run_detach(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys, path: str, patches: dict
) -> None:
    from camp.cli import env as env_cli

    attached_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(env_cli, "_start_forward", lambda argv, timeout: None)
    for name, replacement in patches.items():
        monkeypatch.setattr(env_cli, name, replacement)
    monkeypatch.setattr(env_cli, "_end_own", _raises(KeyboardInterrupt()))

    try:
        env_cli._cmd_env_attach(["perseus/levr/main"])
    except SystemExit as exit_:
        code = exit_.code
    except KeyboardInterrupt:
        pytest.fail(f"the interrupt escaped as a traceback ({path})")

    assert code == 130
    assert capsys.readouterr().err == (
        "camp env: attach interrupted; the forward it started may still be running; run camp env detach\n"
    )


def test_a_forward_that_dies_just_after_backgrounding_fails_and_holds_nothing(rig: AttachRig) -> None:
    env = attached_rig(rig)
    rig.configure(die_after_fork=True)

    done = rig.attach("perseus/levr/main")

    assert done.returncode == 1
    assert done.stderr == "camp env: attach failed: ssh started the forward but it is not running\n"
    assert_nothing_held(rig, env)


# --- one attachment per machine -----------------------------------------------------------------


def test_attach_while_an_attachment_exists_is_refused_before_any_host_is_read(rig: AttachRig) -> None:
    first = rig.environment("a", group="levr")
    second = rig.environment("b", group="acme")
    rig.declare_hosts("perseus", "andromeda")
    rig.host_inventory("perseus", first)
    rig.host_inventory("andromeda", second)
    assert rig.attach("perseus/levr/a").returncode == 0
    reads = rig.inventory_reads()

    done = rig.attach("andromeda/acme/b")

    assert done.returncode == 1
    assert done.stderr == (
        "camp env: perseus/levr/a is attached; run camp env detach before attaching another"
        " (a forward whose link has died ends on its own within about 45s)\n"
    )
    assert rig.inventory_reads() == reads
    assert len(rig.forwards()) == 1
    assert all(port_is_free(p) for p in second.standard_ports)


def test_an_unreadable_process_table_refuses_with_its_reason_before_any_host_is_read(rig: AttachRig) -> None:
    attached_rig(rig)
    rig.break_ps_after(0)

    done = rig.attach("perseus/levr/main")

    assert done.returncode == 1
    assert done.stderr == (
        "camp env: could not tell whether anything is attached: ps exited 1: ps: simulated failure\n"
    )
    assert rig.inventory_reads() == []
    assert rig.forwards() == []


# --- ending what this attach started ------------------------------------------------------------


def run_interrupted(rig: AttachRig) -> subprocess.Popen:
    proc = rig.popen_camp("env", "attach", "perseus/levr/main")
    rig.wait_backgrounded()
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=30)
    return proc


@pytest.mark.parametrize("ignore_term", [False, True], ids=["term", "ignores-term"])
def test_an_interrupt_after_the_forward_started_leaves_no_forward_running(rig: AttachRig, ignore_term: bool) -> None:
    env = attached_rig(rig)
    rig.configure(parent_delay=30, ignore_term=ignore_term)

    proc = run_interrupted(rig)

    assert proc.returncode == 130
    assert proc.stderr.read() == "camp env: attach interrupted; the forward it started was ended\n"
    assert_nothing_held(rig, env)


def test_an_unexpected_exception_after_the_forward_started_still_ends_the_forward(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from camp.cli import env as env_cli

    env = attached_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)

    def explode(attach_id: str):
        assert rig.wait_tagged()
        raise RuntimeError("boom")

    monkeypatch.setattr(env_cli, "_wait_for_forward", explode)

    with pytest.raises(RuntimeError, match="boom"):
        env_cli._cmd_env_attach(["perseus/levr/main"])

    assert_nothing_held(rig, env)


def test_failure_cleanup_ends_only_this_attachs_forward(rig: AttachRig) -> None:
    env = attached_rig(rig)
    rig.configure(die_after_fork=True, parent_delay=2)
    proc = rig.popen_camp("env", "attach", "perseus/levr/main")
    wait_for(lambda: rig.forwards() != [])
    decoy = rig.decoy_forward("other1")
    wait_for(lambda: rig.tagged_pids() != [])

    proc.wait(timeout=30)

    assert proc.returncode == 1
    assert decoy.poll() is None
    assert rig.tagged_pids() == [decoy.pid]
    assert all(port_is_free(p) for p in env.standard_ports)


def test_a_failed_attach_says_so_when_it_could_not_end_its_forward(rig: AttachRig) -> None:
    attached_rig(rig)
    rig.break_ps_after(2)

    done = rig.attach("perseus/levr/main")

    assert done.returncode == 1
    assert done.stderr == (
        "camp env: attach failed: could not tell whether the forward started"
        " (ps exited 1: ps: simulated failure); the forward it started could not be ended"
        " (ps exited 1: ps: simulated failure); run camp env detach\n"
    )
    assert len(rig.tagged_pids()) == 1


# --- usage and refusals that are not about the environment ----------------------------------------


@pytest.mark.parametrize("args", [[], ["a", "b"]], ids=["no-target", "two-targets"])
def test_attach_without_exactly_one_target_prints_the_usage_line(rig: AttachRig, args: list[str]) -> None:
    done = rig.camp("env", "attach", *args)

    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr == USAGE


def test_a_malformed_target_says_which_part_is_wrong_and_prints_the_usage_line(rig: AttachRig) -> None:
    done = rig.attach("perseus//main")

    assert done.returncode == 1
    assert done.stderr == "camp env: the group is empty\n" + USAGE
    assert rig.inventory_reads() == []


def test_an_invalid_tag_namespace_is_refused(rig: AttachRig) -> None:
    attached_rig(rig)

    done = rig.attach("perseus/levr/main", CAMP_ENV_TAG_NAMESPACE="Not Valid")

    assert done.returncode == 1
    assert done.stderr.startswith("camp env: CAMP_ENV_TAG_NAMESPACE is not a valid tag namespace")
    assert rig.forwards() == []


# --- interrupting the reads a bare name makes ----------------------------------------------------

_SLEEPING_DOCKER = """#!/bin/sh
echo $$ > "$STUB_DIR/pid.docker"
exec sleep 20
"""

_SLEEPING_SSH = """#!/bin/sh
dest=
while [ $# -gt 0 ]; do
  if [ "$1" = "--" ]; then dest=$2; break; fi
  shift
done
echo $$ > "$STUB_DIR/pid.$dest"
exec sleep 20
"""

_READ_CHILDREN = ["docker", "tduffield@andromeda", "tduffield@perseus"]


def _read_pids(rig: AttachRig) -> dict[str, int]:
    found = {}
    for path in rig.root.glob("pid.*"):
        text = path.read_text().strip()
        if text:
            found[path.name.removeprefix("pid.")] = int(text)
    return found


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("whole_group", [True, False], ids=["process-group", "pid-only"])
@pytest.mark.parametrize(
    ("target", "read_children"),
    [("main", _READ_CHILDREN), ("perseus/main", ["tduffield@perseus"])],
    ids=["bare-name", "host-named"],
)
def test_an_interrupt_while_the_reads_a_target_makes_are_running_exits_130_and_leaves_no_read_child(
    rig: AttachRig, whole_group: bool, target: str, read_children: list[str]
) -> None:
    (rig.bin / "sleep").symlink_to(shutil.which("sleep"))
    rig._script("docker", _SLEEPING_DOCKER)
    rig._script("ssh", _SLEEPING_SSH)
    rig.declare_hosts("andromeda", "perseus")
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "plugins" / "camp" / "cli" / "camp"),
         "env", "attach", target],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=rig.environ(),
        cwd=str(rig.root),
        start_new_session=True,
    )
    rig._popens.append(process)
    pids: dict[str, int] = {}
    try:
        deadline = time.monotonic() + 10
        while not set(read_children) <= set(_read_pids(rig)) and time.monotonic() < deadline:
            time.sleep(0.05)
        pids = _read_pids(rig)
        missing = sorted(set(read_children) - set(pids))
        assert not missing, f"reads that never started: {missing}"

        started = time.monotonic()
        (os.killpg if whole_group else os.kill)(process.pid, signal.SIGINT)
        out, err = process.communicate(timeout=10)
        took = time.monotonic() - started
        deadline = time.monotonic() + 2
        while any(_alive(pid) for pid in pids.values()) and time.monotonic() < deadline:
            time.sleep(0.05)
        left = sorted(name for name, pid in pids.items() if _alive(pid))

        assert process.returncode == 130, f"exit {process.returncode}, stderr {err!r}"
        assert err == "camp env: interrupted\n"
        assert took < 2.5, f"camp took {took:.1f}s to exit"
        assert left == [], f"read children still running: {left}"
        assert rig.tagged_pids() == []
    finally:
        for pid in _read_pids(rig).values():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


# --- attaching an environment on this machine: a relay --------------------------------------------


def local_line(env) -> str:
    return success_line("local", env)


def connection_is_closed_without_data(port: int) -> bool:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        return sock.recv(1024) == b""


@pytest.mark.parametrize("target", ["local/main", "tomsmac/main", "main"], ids=["local", "self-name", "bare-name"])
def test_attaching_this_machine_makes_each_standard_port_reach_the_published_listener(
    rig: AttachRig, target: str
) -> None:
    env = local_rig(rig)
    rig.declare_hosts(self_name="tomsmac")

    done = rig.attach(target)

    assert done.returncode == 0, done.stderr
    assert done.stdout == local_line(env)
    assert [ask(p) for p in env.standard_ports] == ["main-0", "main-1", "main-2"]
    assert len(rig.tagged_pids()) == 1


def test_a_stopped_environment_on_this_machine_is_refused_and_no_relay_is_started(rig: AttachRig) -> None:
    local_rig(rig, state="stopped")

    done = rig.attach("local/main")

    assert done.returncode == 1
    assert done.stderr == (
        "camp env: local/levr/main is stopped; start it with its project's own tooling, then attach again\n"
    )
    assert rig.tagged_pids() == []


def test_an_environment_on_this_machine_publishing_no_ports_is_refused_and_no_relay_is_started(
    rig: AttachRig,
) -> None:
    local_rig(rig, count=0)

    done = rig.attach("local/main")

    assert done.returncode == 1
    assert done.stderr == "camp env: local/levr/main publishes no ports, so there is nothing to attach\n"
    assert rig.tagged_pids() == []


def test_docker_not_running_on_this_machine_refuses_a_local_target_and_starts_nothing(rig: AttachRig) -> None:
    rig.docker_down()

    done = rig.attach("local/main")

    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr == "camp env: Docker is not running on this machine\n"
    assert rig.tagged_pids() == []


def test_a_standard_port_held_before_a_local_attach_is_refused_and_no_relay_is_started(rig: AttachRig) -> None:
    env = local_rig(rig)
    held_port = env.standard_ports[1]
    listener = hold(held_port, socket.AF_INET, "127.0.0.1")
    try:
        done = rig.attach("local/main")

        assert done.returncode == 1
        assert re.fullmatch(
            rf"camp env: port {held_port} is held by \S+ \(pid {os.getpid()}\); stop it, then attach again\n",
            done.stderr,
        ), done.stderr
        assert rig.tagged_pids() == []
        assert [port_is_free(p) for p in env.standard_ports] == [True, False, True]
    finally:
        listener.close()


def test_a_port_taken_between_the_probe_and_the_bind_names_its_holder_and_leaves_no_relay_and_no_port_held(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from camp.cli import env as env_cli

    env = local_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    held_port = env.standard_ports[1]
    taken: list[socket.socket] = []
    real = env_cli.start_relay

    def start_after_the_port_is_taken(*args, **kwargs):
        taken.append(hold(held_port, socket.AF_INET, "127.0.0.1"))
        return real(*args, **kwargs)

    monkeypatch.setattr(env_cli, "start_relay", start_after_the_port_is_taken)
    try:
        with pytest.raises(SystemExit) as exit_:
            env_cli._cmd_env_attach(["local/main"])

        assert exit_.value.code == 1
        assert re.fullmatch(
            rf"camp env: attach failed: port {held_port} is held by \S+ \(pid {os.getpid()}\);"
            r" stop it, then attach again\n",
            capsys.readouterr().err,
        )
        assert rig.tagged_pids() == []
        assert [port_is_free(p) for p in env.standard_ports] == [True, False, True]
    finally:
        for sock in taken:
            sock.close()
    assert all(port_is_free(p) for p in env.standard_ports)


def test_a_relay_blocks_a_remote_attach_and_its_refusal_has_no_dead_link_note(rig: AttachRig) -> None:
    local = local_rig(rig, "main")
    remote = rig.environment("other")
    rig.declare_hosts("perseus")
    rig.host_inventory("perseus", remote)
    assert rig.attach("local/main").returncode == 0
    reads = rig.inventory_reads()

    done = rig.attach("perseus/levr/other")

    assert done.returncode == 1
    assert done.stderr == "camp env: local/levr/main is attached; run camp env detach before attaching another\n"
    assert rig.inventory_reads() == reads
    assert rig.forwards() == []
    assert all(port_is_free(p) for p in remote.standard_ports)
    assert [ask(p) for p in local.standard_ports] == ["main-0", "main-1", "main-2"]


def test_a_forward_blocks_a_local_attach_and_its_refusal_keeps_the_dead_link_note(rig: AttachRig) -> None:
    remote = attached_rig(rig, "other")
    local = local_rig(rig, "main")
    assert rig.attach("perseus/levr/other").returncode == 0

    done = rig.attach("local/main")

    assert done.returncode == 1
    assert done.stderr == (
        "camp env: perseus/levr/other is attached; run camp env detach before attaching another"
        " (a forward whose link has died ends on its own within about 45s)\n"
    )
    assert len(rig.tagged_pids()) == 1
    assert all(port_is_free(p) for p in local.standard_ports)
    assert [ask(p) for p in remote.standard_ports] == ["other-0", "other-1", "other-2"]


def test_a_name_found_here_and_on_a_host_is_ambiguous_and_offers_local(rig: AttachRig) -> None:
    local = local_rig(rig, "main")
    remote = attached_rig(rig, "main")

    done = rig.attach("main")

    assert done.returncode == 1
    assert done.stderr == (
        "camp env: main matches more than one environment: local/main, perseus/main; attach one of those\n"
    )
    assert rig.tagged_pids() == []
    assert rig.forwards() == []
    assert all(port_is_free(p) for p in local.standard_ports + remote.standard_ports)


def test_an_environment_gone_before_the_relay_starts_still_attaches_and_closes_connections_without_data(
    rig: AttachRig,
) -> None:
    env = local_rig(rig)
    for upstream in env.upstreams:
        upstream.close()

    done = rig.attach("local/main")

    assert done.returncode == 0, done.stderr
    assert done.stdout == local_line(env)
    assert [connection_is_closed_without_data(p) for p in env.standard_ports] == [True, True, True]


def test_an_interrupt_after_the_relay_started_ends_it_names_the_relay_and_exits_130(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from camp.cli import env as env_cli

    env = local_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(env_cli, "_wait_for_forward", _raises(KeyboardInterrupt()))

    with pytest.raises(SystemExit) as exit_:
        env_cli._cmd_env_attach(["local/main"])

    assert exit_.value.code == 130
    assert capsys.readouterr().err == "camp env: attach interrupted; the relay it started was ended\n"
    assert_nothing_held(rig, env)


def test_an_interrupt_the_moment_start_relay_returns_ends_the_relay_and_holds_no_port(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from camp.cli import env as env_cli

    env = local_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    real_start = env_cli.start_relay
    handles: list[subprocess.Popen] = []

    class InterruptedWhenFirstInspected:
        """Stands in for the handle; the first look at what kind of object it is raises Ctrl-C."""

        def __init__(self, process: subprocess.Popen) -> None:
            self._process = process
            self._armed = True

        @property
        def __class__(self):
            if self._armed:
                self._armed = False
                raise KeyboardInterrupt
            return type(self._process)

        def __getattr__(self, name: str):
            return getattr(self._process, name)

    def start_then_hand_back_an_interruptible_handle(*args, **kwargs):
        process = real_start(*args, **kwargs)
        handles.append(process)
        return InterruptedWhenFirstInspected(process)

    monkeypatch.setattr(env_cli, "start_relay", start_then_hand_back_an_interruptible_handle)

    with pytest.raises(SystemExit) as exit_:
        env_cli._cmd_env_attach(["local/main"])

    assert exit_.value.code == 130
    assert capsys.readouterr().err == "camp env: attach interrupted; the relay it started was ended\n"
    (handle,) = handles
    assert handle.returncode is not None
    assert_nothing_held(rig, env)


def _remember_relay_handles(env_cli, monkeypatch) -> "list[subprocess.Popen]":
    handles: list[subprocess.Popen] = []
    real_start = env_cli.start_relay

    def remember(*args, **kwargs):
        started = real_start(*args, **kwargs)
        handles.append(started)
        return started

    monkeypatch.setattr(env_cli, "start_relay", remember)
    return handles


def test_a_relay_attach_that_cannot_read_the_process_table_ends_the_relay_and_says_only_what_happened(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from camp.cli import env as env_cli

    env = local_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    real_attachments = env_cli.attachments
    calls: list[int] = []

    def unreadable_once_the_relay_runs():
        calls.append(1)
        if len(calls) > 2:
            raise _unverifiable("ps broke")
        return real_attachments()

    monkeypatch.setattr(env_cli, "attachments", unreadable_once_the_relay_runs)
    handles = _remember_relay_handles(env_cli, monkeypatch)

    with pytest.raises(SystemExit) as exit_:
        env_cli._cmd_env_attach(["local/main"])

    assert exit_.value.code == 1
    assert capsys.readouterr().err == "camp env: attach failed: could not tell whether the relay started (ps broke)\n"
    (handle,) = handles
    assert handle.returncode is not None
    monkeypatch.setattr(env_cli, "attachments", real_attachments)
    assert_nothing_held(rig, env)


def test_a_relay_attach_that_raises_unexpectedly_ends_the_relay_at_once_and_lets_the_error_out(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from camp.cli import env as env_cli

    env = local_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(env_cli, "_wait_for_forward", _raises(RuntimeError("boom")))
    handles = _remember_relay_handles(env_cli, monkeypatch)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="boom"):
        env_cli._cmd_env_attach(["local/main"])

    assert time.monotonic() - started < 1.5
    (handle,) = handles
    assert handle.returncode is not None
    assert_nothing_held(rig, env)


def test_an_interrupted_relay_attach_returns_at_once_having_ended_the_relay(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from camp.cli import env as env_cli

    env = local_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(env_cli, "_wait_for_forward", _raises(KeyboardInterrupt()))
    handles = _remember_relay_handles(env_cli, monkeypatch)

    started = time.monotonic()
    with pytest.raises(SystemExit) as exit_:
        env_cli._cmd_env_attach(["local/main"])

    assert time.monotonic() - started < 1.5
    assert exit_.value.code == 130
    assert capsys.readouterr().err == "camp env: attach interrupted; the relay it started was ended\n"
    (handle,) = handles
    assert handle.returncode is not None
    assert_nothing_held(rig, env)


def test_an_interrupt_while_the_relay_is_starting_does_not_claim_a_relay_was_ended(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from camp.cli import env as env_cli

    env = local_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(env_cli, "start_relay", _raises(KeyboardInterrupt()))

    with pytest.raises(SystemExit) as exit_:
        env_cli._cmd_env_attach(["local/main"])

    assert exit_.value.code == 130
    assert capsys.readouterr().err == "camp env: attach interrupted\n"
    assert_nothing_held(rig, env)


def test_a_relay_hidden_from_the_process_table_fails_the_attach_and_is_ended_through_its_handle(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from camp.cli import env as env_cli

    env = local_rig(rig)
    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    real_attachments = env_cli.attachments
    monkeypatch.setattr(env_cli, "attachments", lambda: [a for a in real_attachments() if a.kind != "relay"])
    monkeypatch.setattr(env_cli, "SETTLE_SECONDS", 0.3)
    handles: list[subprocess.Popen] = []
    real_start = env_cli.start_relay

    def remember(*args, **kwargs):
        started = real_start(*args, **kwargs)
        handles.append(started)
        return started

    monkeypatch.setattr(env_cli, "start_relay", remember)

    with pytest.raises(SystemExit) as exit_:
        env_cli._cmd_env_attach(["local/main"])

    assert exit_.value.code == 1
    assert capsys.readouterr().err == "camp env: attach failed: the relay started but it is not in the process table\n"
    (handle,) = handles
    assert handle.returncode is not None
    assert_nothing_held(rig, env)


# --- the attach lock ----------------------------------------------------------------------------

WAITING = "camp env: another attach is starting; waiting for it to finish\n"
STILL_STARTING = "camp env: another attach is still starting; wait for it to finish, then try again\n"
FORWARD_NOTE = " (a forward whose link has died ends on its own within about 45s)"


def lock_file(rig: AttachRig) -> Path:
    return rig.state_dir / "env-attach.lock"


def refusal(shown: str, *, note: bool) -> str:
    suffix = FORWARD_NOTE if note else ""
    return f"camp env: {shown} is attached; run camp env detach before attaching another{suffix}\n"


def race(rig: AttachRig, *targets: str) -> "list[tuple[str, int, str, str]]":
    """Start `camp env attach` for each target at once; each target's (target, exit, stdout, stderr)."""
    procs = [(t, rig.popen_camp("env", "attach", t)) for t in targets]
    done = []
    for target, proc in procs:
        out, err = proc.communicate(timeout=60)
        done.append((target, proc.returncode, out, err))
    return done


def refused_as(stderr: str, shown: str, *, note: bool) -> bool:
    """Whether *stderr* is the already-attached refusal naming *shown*, after the waiting line a loser
    that met the held lock prints first."""
    return stderr.removeprefix(WAITING) == refusal(shown, note=note)


def one_winner(done) -> "tuple[tuple, tuple]":
    winners = [d for d in done if d[1] == 0]
    losers = [d for d in done if d[1] != 0]
    assert len(winners) == 1, done
    assert len(losers) == 1, done
    return winners[0], losers[0]


def test_two_simultaneous_attaches_of_one_remote_environment_end_with_one_attachment(rig: AttachRig) -> None:
    env = attached_rig(rig, "main")
    rig.configure(parent_delay=1.5)

    winner, loser = one_winner(race(rig, "perseus/levr/main", "perseus/levr/main"))

    assert winner[2] == success_line("perseus", env)
    assert loser[3] == WAITING + refusal("perseus/levr/main", note=True)
    assert len(rig.forwards()) == 1
    assert len(rig.tagged_pids()) == 1
    status = rig.camp("env", "status").stdout
    assert status.count("attached:") == 1
    assert status.startswith("attached: perseus/levr/main")


def test_the_loser_of_two_remote_attaches_to_different_environments_starts_no_forward_and_holds_no_port(
    rig: AttachRig,
) -> None:
    main = attached_rig(rig, "main")
    other = rig.environment("other")
    rig.host_inventory("perseus", main, other)
    rig.configure(parent_delay=1.5)

    winner, loser = one_winner(race(rig, "perseus/levr/main", "perseus/levr/other"))

    won, lost = (main, other) if winner[0].endswith("main") else (other, main)
    assert winner[2] == success_line("perseus", won)
    assert loser[3] == WAITING + refusal(f"perseus/levr/{won.name}", note=True)
    assert len(rig.forwards()) == 1
    assert len(rig.tagged_pids()) == 1
    assert all(port_is_free(p) for p in lost.standard_ports)
    assert [ask(p) for p in won.standard_ports] == [f"{won.name}-{i}" for i in range(3)]


def test_a_remote_attach_and_a_local_attach_started_together_end_with_one_attachment_naming_the_winner(
    rig: AttachRig,
) -> None:
    remote = attached_rig(rig, "main")
    local = local_rig(rig, "main")
    rig.configure(parent_delay=1.5)
    rig.delay_ps(0.5)

    winner, loser = one_winner(race(rig, "perseus/levr/main", "local/levr/main"))

    remote_won = winner[0].startswith("perseus")
    won, lost = (remote, local) if remote_won else (local, remote)
    assert winner[2] == success_line("perseus" if remote_won else "local", won)
    assert loser[3].startswith(WAITING), loser
    assert refused_as(loser[3], "perseus/levr/main" if remote_won else "local/levr/main", note=remote_won), loser
    assert len(rig.tagged_pids()) == 1
    assert len(rig.forwards()) == (1 if remote_won else 0)
    assert all(port_is_free(p) for p in lost.standard_ports)
    assert [ask(p) for p in won.standard_ports] == ["main-0", "main-1", "main-2"]


@pytest.mark.parametrize("same_environment", [True, False])
def test_two_local_attaches_started_together_end_with_one_relay_and_no_stray_one(
    rig: AttachRig, same_environment: bool
) -> None:
    main = local_rig(rig, "main")
    other = main
    if not same_environment:
        other = rig.environment("other")
        rig.docker_inventory(main, other)
    second = "local/levr/main" if same_environment else "local/levr/other"
    rig.delay_ps(0.5)

    winner, loser = one_winner(race(rig, "local/levr/main", second))

    won, lost = (main, other) if winner[0].endswith("main") else (other, main)
    assert winner[2] == success_line("local", won)
    assert loser[3].startswith(WAITING), loser
    assert refused_as(loser[3], f"local/levr/{won.name}", note=False), loser
    assert len(rig.tagged_pids()) == 1
    if not same_environment:
        assert all(port_is_free(p) for p in lost.standard_ports)
    assert rig.camp("env", "status").stdout.count("attached:") == 1


def test_a_lone_attach_prints_what_it_always_did_leaves_the_lock_file_empty_and_detaches(rig: AttachRig) -> None:
    env = attached_rig(rig)

    done = rig.attach("perseus/levr/main")

    assert (done.returncode, done.stdout, done.stderr) == (0, success_line("perseus", env), "")
    assert lock_file(rig).read_bytes() == b""
    assert rig.camp("env", "status").stdout.startswith("attached: perseus/levr/main")
    assert rig.camp("env", "detach").returncode == 0
    assert rig.tagged_pids() == []


def hold_lock(path: Path):
    """Hold the attach lock at *path* in a thread until the returned function is called."""
    from camp.env.lock import attach_lock

    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with attach_lock(path, timeout=5):
            held.set()
            release.wait(30)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert held.wait(5)

    def let_go() -> None:
        release.set()
        thread.join(5)

    return let_go


def short_connect_timeout(rig: AttachRig) -> None:
    rig.write_hosts_file(
        'env_hosts = ["perseus"]\nconnect_timeout = 1\n\n[hosts.perseus]\nssh = "tduffield@perseus"\n'
    )


def attach_in_process(rig: AttachRig, monkeypatch: pytest.MonkeyPatch, margin: float) -> None:
    from camp.cli import env as env_cli

    for key, value in rig.environ().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(env_cli, "LOCK_WAIT_MARGIN_SECONDS", margin)
    env_cli._cmd_env_attach(["perseus/levr/main"])


def test_an_attach_whose_lock_wait_runs_out_says_so_starts_nothing_and_says_it_waited(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    attached_rig(rig)
    short_connect_timeout(rig)
    let_go = hold_lock(lock_file(rig))
    started = time.monotonic()
    try:
        with pytest.raises(SystemExit) as exit_:
            attach_in_process(rig, monkeypatch, margin=0.3)
    finally:
        let_go()

    assert exit_.value.code == 1
    assert 1.2 < time.monotonic() - started < 5
    assert capsys.readouterr().err == WAITING + STILL_STARTING
    assert rig.forwards() == []
    assert rig.tagged_pids() == []


def test_an_attach_that_gets_the_lock_before_the_bound_attaches_and_says_it_waited(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    env = attached_rig(rig)
    short_connect_timeout(rig)
    let_go = hold_lock(lock_file(rig))
    threading.Timer(0.7, let_go).start()

    attach_in_process(rig, monkeypatch, margin=0.3)

    captured = capsys.readouterr()
    assert captured.out == success_line("perseus", env)
    assert captured.err == WAITING
    assert len(rig.tagged_pids()) == 1


def test_a_lock_wait_that_runs_out_leaves_the_winner_still_attaching_untouched(
    rig: AttachRig, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from camp.cli import env as env_cli

    env = attached_rig(rig)
    short_connect_timeout(rig)
    rig.configure(parent_delay=3.0)
    real = env_cli.attachments
    calls: list[int] = []

    def attachments_then_wait_for_the_winner_to_start():
        found = real()
        calls.append(1)
        if len(calls) == 1:
            wait_for(lambda: len(rig.forwards()) == 1)
        return found

    monkeypatch.setattr(env_cli, "attachments", attachments_then_wait_for_the_winner_to_start)
    winner = rig.popen_camp("env", "attach", "perseus/levr/main")

    with pytest.raises(SystemExit) as exit_:
        attach_in_process(rig, monkeypatch, margin=0.3)

    assert exit_.value.code == 1
    assert capsys.readouterr().err == WAITING + STILL_STARTING
    started = set(rig.tagged_pids())
    out, _ = winner.communicate(timeout=30)
    assert (winner.returncode, out) == (0, success_line("perseus", env))
    assert len(rig.forwards()) == 1
    (still_running,) = rig.tagged_pids()
    assert still_running in started
    monkeypatch.undo()
    assert rig.camp("env", "status").stdout.startswith("attached: perseus/levr/main")


def test_a_lock_file_that_cannot_be_opened_refuses_naming_it_and_starts_nothing(rig: AttachRig, tmp_path: Path) -> None:
    attached_rig(rig)
    blocker = tmp_path / "a-regular-file"
    blocker.write_text("")
    where = blocker / "state"

    done = rig.attach("perseus/levr/main", CAMP_STATE_DIR=str(where))

    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr == (
        f"camp env: cannot take the attach lock at {where / 'env-attach.lock'} (Not a directory);"
        " nothing was started\n"
    )
    assert rig.forwards() == []
    assert rig.tagged_pids() == []


def test_a_fifo_planted_at_the_lock_path_refuses_naming_it_and_starts_nothing(rig: AttachRig, tmp_path: Path) -> None:
    attached_rig(rig)
    state = tmp_path / "state"
    state.mkdir()
    lock = state / "env-attach.lock"
    os.mkfifo(lock, 0o600)

    done = rig.attach("perseus/levr/main", CAMP_STATE_DIR=str(state))

    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr == (
        f"camp env: cannot take the attach lock at {lock} (not a regular file); nothing was started\n"
    )
    assert rig.forwards() == []
    assert rig.tagged_pids() == []


@pytest.mark.parametrize("variable", ["CAMP_STATE_DIR", "XDG_STATE_HOME"])
def test_a_relative_state_directory_refuses_cleanly_and_starts_nothing(rig: AttachRig, variable: str) -> None:
    attached_rig(rig)
    environ = {"CAMP_STATE_DIR": "", "XDG_STATE_HOME": "", variable: "relative/state"}

    done = rig.attach("perseus/levr/main", **environ)

    assert "Traceback" not in done.stderr
    assert done.returncode == 1
    assert done.stdout == ""
    lines = done.stderr.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("camp env: cannot take the attach lock (")
    assert lines[0].endswith("); nothing was started")
    assert variable in lines[0]
    assert rig.forwards() == []
    assert rig.tagged_pids() == []


def test_the_path_in_a_cannot_take_the_lock_refusal_has_its_control_characters_removed(
    rig: AttachRig, tmp_path: Path
) -> None:
    attached_rig(rig)
    blocker = tmp_path / "a\x1b[31mfile"
    blocker.write_text("")

    done = rig.attach("perseus/levr/main", CAMP_STATE_DIR=str(blocker / "state"))

    assert done.returncode == 1
    assert "\x1b" not in done.stderr
    assert done.stderr == (
        f"camp env: cannot take the attach lock at {tmp_path / 'a[31mfile' / 'state' / 'env-attach.lock'}"
        " (Not a directory); nothing was started\n"
    )
    assert rig.forwards() == []


def test_an_interrupt_while_waiting_for_the_lock_exits_130_starts_nothing_and_leaves_the_winner(
    rig: AttachRig,
) -> None:
    env = attached_rig(rig)
    rig.configure(parent_delay=3.0)
    first = rig.popen_camp("env", "attach", "perseus/levr/main")
    second = rig.popen_camp("env", "attach", "perseus/levr/main")
    waiting = None
    deadline = time.monotonic() + 30
    while waiting is None and time.monotonic() < deadline:
        ready, _, _ = select.select([first.stderr, second.stderr], [], [], 0.1)
        waiting = ready[0] if ready else None
    assert waiting is not None, "neither attach reported waiting for the lock"
    loser, winner = (first, second) if waiting is first.stderr else (second, first)
    assert loser.stderr.readline() == WAITING
    wait_for(lambda: rig.tagged_pids())

    loser.send_signal(signal.SIGINT)
    out, err = loser.communicate(timeout=10)

    assert loser.returncode == 130
    assert (out, err) == ("", "camp env: interrupted\n")
    wout, werr = winner.communicate(timeout=30)
    assert (winner.returncode, wout, werr) == (0, success_line("perseus", env), "")
    assert len(rig.forwards()) == 1
    assert len(rig.tagged_pids()) == 1
    assert rig.camp("env", "status").stdout.startswith("attached: perseus/levr/main")
