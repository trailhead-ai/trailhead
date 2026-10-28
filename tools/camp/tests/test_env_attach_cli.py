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
- Interrupt after the forward started ends it; an unreadable process table says it could not be ended.
- Failure cleanup ends only this attach's forward.
- Bare name, HOST/NAME and HOST/GROUP/NAME reach the same environment; a resolution refusal exits 1.
- Usage errors; an invalid tag namespace.
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from .env_ssh_stub import AttachRig, ask, can_bind, port_is_free

USAGE = "usage: camp env attach TARGET\n"


@pytest.fixture
def rig(tmp_path: Path):
    r = AttachRig(tmp_path)
    try:
        yield r
    finally:
        r.close()


def attached_rig(rig: AttachRig, name: str = "main", host: str = "perseus", **kw):
    env = rig.environment(name, **kw)
    rig.declare_hosts(host)
    rig.host_inventory(host, env)
    return env


def success_line(host: str, env, ) -> str:
    ports = " ".join(str(p) for p in env.standard_ports)
    return f"attached {host}/{env.group}/{env.name} on ports {ports}; its health was not checked\n"


def wait_for(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")


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


def test_rig_forward_points_descriptors_0_1_2_at_dev_null_not_what_it_inherited(rig: AttachRig) -> None:
    env = rig.environment("main")
    with tempfile.TemporaryFile() as sink:
        run_stub_forward(rig, env, f"{rig.ns}:abc:perseus/levr/main", sink)
        (pid,) = rig.wait_tagged()
        inherited = os.readlink(f"/proc/{os.getpid()}/fd/{sink.fileno()}")

        held = [os.readlink(f"/proc/{pid}/fd/{fd}") for fd in (0, 1, 2)]

    assert held == ["/dev/null"] * 3
    assert inherited != "/dev/null"


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


def test_a_successful_attach_returns_within_a_few_seconds(rig: AttachRig) -> None:
    attached_rig(rig)

    started = time.monotonic()
    done = rig.attach("perseus/levr/main")
    elapsed = time.monotonic() - started

    assert done.returncode == 0, done.stderr
    assert elapsed < 2.5


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
    rig.wait_tagged()
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
    rig.break_ps_after(1)

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
