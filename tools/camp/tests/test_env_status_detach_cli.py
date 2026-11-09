"""Test contract: `camp env status`, `camp env detach` and the `attached:` line of `camp env ls`,
end to end through camp's CLI entry point, against the stub-ssh rig in `env_ssh_stub.py`.

All three read only the process table. Every test has its own tag namespace and free high ports,
and the rig ends whatever it started.

- Status from a different working directory names the attachment and its ports.
- A second copy of the CLI under a directory whose name has a space starts an attachment; status and
  detach from the normal entry point recognise it, and detach ends it and frees its ports.
- Status reads the live forward: once the forward is killed outside camp, nothing is attached.
- Nothing attached: status and detach say `nothing attached`, exit 0.
- Detach frees every port of a forward that ignores SIGTERM; it names a port a foreign listener still
  holds and exits 1.
- A forward in another namespace, or a process that only mentions the tag, is neither reported nor
  ended by status, detach or ls.
- `ls` leads with the attached line when attached; with `ps` unavailable it warns and lists, while
  status exits 1 with the reason.
- Status and detach take no arguments.
- A relay (a local attachment) is named `local/<group>/<name>` by status from another directory, is ended
  by detach with its ports freed, is found and ended across a camp copy at a path with a space, and once
  detached (as a forward is) status and detach say `nothing attached`.
- A forward or relay that died (SIGKILL, or SIGTERM) after serving traffic leaves nothing attached: status
  says `nothing attached` and `ls` prints no `attached:` line, whichever the kind; a second attach of the
  same target then succeeds with no detach, starts a new process and holds each standard port, which
  answers through it. With the process still alive the same second attach is refused instead.
- Opt-in, against a real sshd named by `CAMP_ENV_TEST_SSH_DEST`: ssh run with camp's own forward argv, its
  link silenced, exits within the 75 s bound and leaves every `-L` port free.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from .env_ssh_stub import AttachRig, ask, attached_rig, free_ports, local_rig, port_is_free, wait_for

_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "camp"
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


@pytest.fixture
def rig(tmp_path: Path):
    r = AttachRig(tmp_path)
    try:
        yield r
    finally:
        r.close()


@pytest.fixture
def procs():
    started: list[subprocess.Popen] = []
    try:
        yield started
    finally:
        for proc in started:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def ports_of(env) -> str:
    return " ".join(str(p) for p in env.standard_ports)


def status_line(env) -> str:
    return f"attached: perseus/{env.group}/{env.name} on ports {ports_of(env)}; its health was not checked\n"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --- status ---------------------------------------------------------------------------------------


def test_status_from_another_directory_names_the_attachment_and_its_ports(rig: AttachRig, tmp_path: Path) -> None:
    env = attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = rig.camp("env", "status", cwd=elsewhere)

    assert result.returncode == 0, result.stderr
    # design doc: "attached: perseus/levr/main on ports 5173 8080 8443; its health was not checked"
    assert result.stdout == status_line(env)
    assert result.stderr == ""


def test_status_reads_the_live_forward_not_a_record_of_the_attach(rig: AttachRig) -> None:
    env = attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0
    assert rig.camp("env", "status").stdout == status_line(env)
    (pid,) = rig.tagged_pids()

    os.kill(pid, signal.SIGKILL)
    wait_for(lambda: not rig.tagged_pids())

    result = rig.camp("env", "status")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "nothing attached\n"


def test_status_with_nothing_attached_says_so_and_exits_0(rig: AttachRig) -> None:
    result = rig.camp("env", "status")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "nothing attached\n"
    assert result.stderr == ""


def test_status_lists_each_attachment_on_its_own_line(rig: AttachRig) -> None:
    env = attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0
    rig.decoy_forward("abc123def456", host="perseus", group="levr", name="other")

    result = rig.camp("env", "status")

    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert status_line(env).rstrip("\n") in lines
    assert any(line.startswith("attached: perseus/levr/other on ports ") for line in lines)


def test_status_says_so_on_stderr_and_exits_1_when_the_process_table_cannot_be_read(rig: AttachRig) -> None:
    rig.break_ps_after(0)

    result = rig.camp("env", "status")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("camp env: cannot tell what is attached: ps exited ")
    assert "Traceback" not in result.stderr


# --- detach ---------------------------------------------------------------------------------------


def test_detach_ends_the_forward_and_frees_its_ports(rig: AttachRig) -> None:
    env = attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0

    result = rig.camp("env", "detach")

    assert result.returncode == 0, result.stderr
    # design doc: "detached perseus/levr/main; ports 5173 8080 8443 are free"
    assert result.stdout == f"detached perseus/levr/main; ports {ports_of(env)} are free\n"
    assert rig.tagged_pids() == []
    assert all(port_is_free(p) for p in env.standard_ports)


def test_detach_with_nothing_attached_says_so_and_exits_0(rig: AttachRig) -> None:
    result = rig.camp("env", "detach")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "nothing attached\n"
    assert result.stderr == ""


def test_detach_frees_every_port_of_a_forward_that_ignores_sigterm(rig: AttachRig) -> None:
    env = attached_rig(rig)
    rig.configure(ignore_term=True)
    assert rig.attach("perseus/levr/main").returncode == 0
    (pid,) = rig.tagged_pids()

    result = rig.camp("env", "detach")

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"detached perseus/levr/main; ports {ports_of(env)} are free\n"
    wait_for(lambda: not alive(pid) or rig.tagged_pids() == [])
    assert rig.tagged_pids() == []
    assert all(port_is_free(p) for p in env.standard_ports)


def test_detach_names_a_port_a_foreign_listener_still_holds_and_exits_1(rig: AttachRig) -> None:
    env = attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0
    held = env.standard_ports[1]
    foreign = socket.socket(socket.AF_INET6)
    try:
        try:
            foreign.bind(("::1", held))
        except OSError:
            pytest.skip("no IPv6 loopback to hold the port on")
        foreign.listen()

        started = time.monotonic()
        result = rig.camp("env", "detach", timeout=15)
        elapsed = time.monotonic() - started
    finally:
        foreign.close()

    assert elapsed < 8
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith(f"camp env: port {held} is still held by ")
    assert f"(pid {os.getpid()})" in result.stderr
    assert rig.tagged_pids() == []


def test_detach_waits_for_a_port_that_frees_within_three_seconds(rig: AttachRig) -> None:
    env = attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0
    held = env.standard_ports[1]
    foreign = socket.socket(socket.AF_INET6)
    try:
        foreign.bind(("::1", held))
    except OSError:
        foreign.close()
        pytest.skip("no IPv6 loopback to hold the port on")
    foreign.listen()
    release = threading.Timer(1.0, foreign.close)
    release.start()
    try:
        result = rig.camp("env", "detach")
    finally:
        release.cancel()
        release.join()
        foreign.close()

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"detached perseus/levr/main; ports {ports_of(env)} are free\n"


def test_detach_ends_and_reports_every_attachment(rig: AttachRig) -> None:
    env = attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0
    other = rig.decoy_forward("abc123def456", host="perseus", group="levr", name="other")

    result = rig.camp("env", "detach")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert f"detached perseus/levr/main; ports {ports_of(env)} are free" in lines
    assert any(line.startswith("detached perseus/levr/other; ports ") for line in lines)
    assert rig.tagged_pids() == []
    other.wait(timeout=5)


def _in_process_detach(monkeypatch, capsys, survivors: list[int]):
    from camp.cli import env as cli_env
    from camp.env.attachment import Attachment

    found = [Attachment(4242, "perseus", "levr", "main", ((8080, 18080),), "abc123def456")]
    monkeypatch.setattr(cli_env, "attachments", lambda: found)
    monkeypatch.setattr(cli_env, "end", lambda given: survivors)
    monkeypatch.setattr(cli_env, "port_holder", lambda port: None)
    code = 0
    try:
        cli_env._cmd_env_cli(["detach"])
    except SystemExit as exit_:
        code = exit_.code
    out = capsys.readouterr()
    return code, out.out, out.err


def test_detach_names_a_forward_that_survived_and_exits_1(monkeypatch, capsys) -> None:
    code, out, err = _in_process_detach(monkeypatch, capsys, survivors=[4242])

    assert code == 1
    assert out == ""
    assert err == "camp env: could not end perseus/levr/main (pid 4242)\n"


def test_detach_reports_detached_when_nothing_survives(monkeypatch, capsys) -> None:
    code, out, err = _in_process_detach(monkeypatch, capsys, survivors=[])

    assert code == 0
    assert out == "detached perseus/levr/main; ports 8080 are free\n"


def test_detach_exits_1_when_a_forward_cannot_be_ended(rig: AttachRig) -> None:
    attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0
    rig.break_ps_after(int((rig.root / "ps.count").read_text()) + 1)

    result = rig.camp("env", "detach")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "camp env: cannot end what is attached (ps exited 1: ps: simulated failure); nothing was signalled\n"
    )
    assert len(rig.tagged_pids()) == 1


def test_detach_does_not_claim_nothing_was_signalled_when_the_table_fails_after_the_signal(rig: AttachRig) -> None:
    attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0
    rig.break_ps_after(int((rig.root / "ps.count").read_text()) + 2)

    result = rig.camp("env", "detach")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "camp env: cannot end what is attached (ps exited 1: ps: simulated failure);"
        " the forward may already have ended; run camp env status\n"
    )


def test_detach_says_so_and_exits_1_when_the_process_table_cannot_be_read(rig: AttachRig) -> None:
    rig.break_ps_after(0)

    result = rig.camp("env", "detach")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("camp env: cannot tell what is attached: ps exited ")


@pytest.mark.parametrize("verb", ["status", "detach"])
def test_status_and_detach_take_no_argument(rig: AttachRig, verb: str) -> None:
    result = rig.camp("env", verb, "extra")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith(f"camp env {verb}: ")


# --- recognition ----------------------------------------------------------------------------------


def start_lookalike(rig: AttachRig, procs: list, kind: str) -> subprocess.Popen:
    """A process that is not an attachment of this namespace: another namespace's forward, a
    non-ssh command that carries every ssh forward word including the tag, or an ssh without -N."""
    standard, published = free_ports(2)
    forward = f"127.0.0.1:{standard}:127.0.0.1:{published}"
    sleeper = "__import__('time').sleep(600)"
    if kind == "other-namespace":
        argv = ["ssh", "-c", sleeper, "-N", "-o", "Tag=other-ns-zz:abc123def456:perseus/levr/x", "-L", forward]
    elif kind == "mentions-tag":
        argv = ["grep", "-c", sleeper, "-N", "-o", f"Tag={rig.ns}:abc123def456:perseus/levr/x", "-L", forward]
    else:
        argv = ["ssh", "-c", sleeper, "-o", f"Tag={rig.ns}:abc123def456:perseus/levr/x", "-L", forward]
    proc = subprocess.Popen(
        argv, executable=sys.executable, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(proc)
    return proc


LOOKALIKES = ["other-namespace", "mentions-tag", "no-dash-n"]


@pytest.mark.parametrize("kind", LOOKALIKES)
def test_status_does_not_report_a_lookalike(rig: AttachRig, procs: list, kind: str) -> None:
    start_lookalike(rig, procs, kind)

    result = rig.camp("env", "status")

    assert result.stdout == "nothing attached\n"


@pytest.mark.parametrize("kind", LOOKALIKES)
def test_detach_does_not_end_a_lookalike(rig: AttachRig, procs: list, kind: str) -> None:
    lookalike = start_lookalike(rig, procs, kind)

    result = rig.camp("env", "detach")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "nothing attached\n"
    time.sleep(0.2)
    assert lookalike.poll() is None


@pytest.mark.parametrize("kind", LOOKALIKES)
def test_ls_does_not_report_a_lookalike(rig: AttachRig, procs: list, kind: str) -> None:
    start_lookalike(rig, procs, kind)

    result = rig.camp("env", "ls")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "this machine\n  no environments\n"


def test_detach_ends_the_namespaces_forward_and_leaves_another_namespaces_alone(rig: AttachRig, procs: list) -> None:
    attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0
    other = start_lookalike(rig, procs, "other-namespace")

    result = rig.camp("env", "detach")

    assert result.returncode == 0, result.stderr
    assert rig.tagged_pids() == []
    assert other.poll() is None


# --- ls -------------------------------------------------------------------------------------------


def test_ls_leads_with_the_attached_line_when_attached(rig: AttachRig) -> None:
    env = attached_rig(rig)
    before = rig.camp("env", "ls")
    assert rig.attach("perseus/levr/main").returncode == 0

    result = rig.camp("env", "ls")

    assert result.returncode == 0, result.stderr
    assert result.stdout == status_line(env) + before.stdout
    assert before.stdout.startswith("this machine\n")
    assert "attached:" not in before.stdout


def test_ls_warns_on_stderr_and_still_lists_when_the_process_table_cannot_be_read(rig: AttachRig) -> None:
    attached_rig(rig)
    rig.break_ps_after(0)

    result = rig.camp("env", "ls")

    assert result.returncode == 0
    assert result.stdout.startswith("this machine\n")
    assert "perseus\n" in result.stdout
    assert result.stderr.startswith("camp env: could not tell whether anything is attached: ps exited ")


# --- wiring ---------------------------------------------------------------------------------------


def test_an_attachment_made_by_a_camp_copy_at_another_path_is_found_and_ended(  # inert-gate: allow seam smoke
    rig: AttachRig, tmp_path: Path
) -> None:
    env = attached_rig(rig)
    home = tmp_path / "a copy with spaces"
    shutil.copytree(_PLUGIN_DIR, home / "plugin", ignore=shutil.ignore_patterns("__pycache__"))
    other_cli = home / "plugin" / "cli" / "camp"

    made = subprocess.run(
        [sys.executable, str(other_cli), "env", "attach", "perseus/levr/main"],
        capture_output=True, text=True, cwd=str(home), timeout=60,
        env={**rig.environ(), "TRAILHEAD_ROOT": str(_REPO_ROOT)},
    )
    assert made.returncode == 0, made.stderr

    seen = rig.camp("env", "status")
    assert seen.stdout == status_line(env)

    ended = rig.camp("env", "detach")
    assert ended.returncode == 0, ended.stderr
    assert ended.stdout == f"detached perseus/levr/main; ports {ports_of(env)} are free\n"
    assert rig.tagged_pids() == []
    assert all(port_is_free(p) for p in env.standard_ports)


def test_detach_after_real_traffic_leaves_the_ports_free_and_attach_works_again_at_once(rig: AttachRig) -> None:
    env = attached_rig(rig)
    assert rig.attach("perseus/levr/main").returncode == 0
    for port in env.standard_ports:
        assert ask(port).startswith("main-")

    detached = rig.camp("env", "detach")

    assert detached.returncode == 0, detached.stderr
    assert detached.stdout == f"detached perseus/levr/main; ports {ports_of(env)} are free\n"
    strict = socket.socket()
    try:
        strict.bind(("127.0.0.1", env.standard_ports[0]))
    except OSError:
        pass
    else:
        pytest.skip("traffic through the forward left no TIME_WAIT on this kernel")
    finally:
        strict.close()
    again = rig.attach("perseus/levr/main")
    assert again.returncode == 0, again.stderr
    assert again.stdout == f"attached perseus/levr/main on ports {ports_of(env)}; its health was not checked\n"


# --- an attachment on this machine: a relay ---------------------------------------------------------


def local_status_line(env) -> str:
    return f"attached: local/{env.group}/{env.name} on ports {ports_of(env)}; its health was not checked\n"


def test_status_from_another_directory_names_a_relay_as_local_group_name(rig: AttachRig, tmp_path: Path) -> None:
    env = local_rig(rig)
    assert rig.attach("local/main").returncode == 0
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = rig.camp("env", "status", cwd=elsewhere)

    assert result.returncode == 0, result.stderr
    assert result.stdout == local_status_line(env)


def test_detach_ends_a_relay_and_its_ports_bind_free(rig: AttachRig) -> None:
    env = local_rig(rig)
    assert rig.attach("local/main").returncode == 0
    for port in env.standard_ports:
        assert ask(port).startswith("main-")

    result = rig.camp("env", "detach")

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"detached local/levr/main; ports {ports_of(env)} are free\n"
    assert rig.tagged_pids() == []
    assert all(port_is_free(p) for p in env.standard_ports)


def test_a_relay_made_by_a_camp_copy_at_a_path_with_a_space_is_found_by_status_and_ended_by_detach(
    rig: AttachRig, tmp_path: Path
) -> None:
    env = local_rig(rig)
    home = tmp_path / "a copy with spaces"
    shutil.copytree(_PLUGIN_DIR, home / "plugin", ignore=shutil.ignore_patterns("__pycache__"))
    other_cli = home / "plugin" / "cli" / "camp"

    made = subprocess.run(
        [sys.executable, str(other_cli), "env", "attach", "local/main"],
        capture_output=True, text=True, cwd=str(home), timeout=60,
        env={**rig.environ(), "TRAILHEAD_ROOT": str(_REPO_ROOT)},
    )
    assert made.returncode == 0, made.stderr

    assert rig.camp("env", "status").stdout == local_status_line(env)
    ended = rig.camp("env", "detach")
    assert ended.returncode == 0, ended.stderr
    assert ended.stdout == f"detached local/levr/main; ports {ports_of(env)} are free\n"
    assert rig.tagged_pids() == []
    assert all(port_is_free(p) for p in env.standard_ports)


@pytest.mark.parametrize("kind", ["relay", "forward"])
def test_after_a_detach_status_and_detach_say_nothing_attached_for_either_kind(rig: AttachRig, kind: str) -> None:
    if kind == "relay":
        local_rig(rig)
        target = "local/main"
    else:
        attached_rig(rig)
        target = "perseus/levr/main"
    assert rig.attach(target).returncode == 0
    assert rig.camp("env", "detach").returncode == 0

    status = rig.camp("env", "status")
    again = rig.camp("env", "detach")

    assert (status.returncode, status.stdout) == (0, "nothing attached\n")
    assert (again.returncode, again.stdout) == (0, "nothing attached\n")


# --- a forward or relay that died ---------------------------------------------------------------------

DEATHS = [
    pytest.param("perseus/levr/main", signal.SIGKILL, id="forward-sigkill"),
    pytest.param("perseus/levr/main", signal.SIGTERM, id="forward-sigterm"),
    pytest.param("local/main", signal.SIGKILL, id="relay-sigkill"),
    pytest.param("local/main", signal.SIGTERM, id="relay-sigterm"),
]
KINDS = [pytest.param("perseus/levr/main", id="forward"), pytest.param("local/main", id="relay")]


def attach_serving(rig: AttachRig, target: str):
    """Attach *target* and carry one connection through each standard port, so each port's closing
    side is left in TIME_WAIT; returns the environment and the attachment's one pid."""
    env = local_rig(rig) if target.startswith("local/") else attached_rig(rig)
    done = rig.attach(target)
    assert done.returncode == 0, done.stderr
    assert [ask(p) for p in env.standard_ports] == ["main-0", "main-1", "main-2"]
    (pid,) = rig.tagged_pids()
    return env, pid


def end_with(rig: AttachRig, pid: int, sig: int) -> None:
    os.kill(pid, sig)
    wait_for(lambda: not alive(pid) and not rig.tagged_pids())


def attached_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("attached:")]


def attached_shown(target: str) -> str:
    return "local/levr/main" if target.startswith("local/") else target


@pytest.mark.parametrize("target, sig", DEATHS)
def test_status_says_nothing_attached_once_the_forward_or_relay_has_died(
    rig: AttachRig, target: str, sig: int
) -> None:
    _, pid = attach_serving(rig, target)
    end_with(rig, pid, sig)

    result = rig.camp("env", "status")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "nothing attached\n"


@pytest.mark.parametrize("target, sig", DEATHS)
def test_ls_prints_no_attached_line_once_the_forward_or_relay_has_died(
    rig: AttachRig, target: str, sig: int
) -> None:
    _, pid = attach_serving(rig, target)
    assert attached_lines(rig.camp("env", "ls").stdout) != []
    end_with(rig, pid, sig)

    result = rig.camp("env", "ls")

    assert result.returncode == 0, result.stderr
    assert attached_lines(result.stdout) == []


@pytest.mark.parametrize("target, sig", DEATHS)
def test_a_fresh_attach_of_the_same_target_needs_no_detach_and_holds_the_standard_ports(
    rig: AttachRig, target: str, sig: int
) -> None:
    env, pid = attach_serving(rig, target)
    end_with(rig, pid, sig)

    done = rig.attach(target)

    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        f"attached {attached_shown(target)} on ports {ports_of(env)}; its health was not checked\n"
    )
    (fresh,) = rig.tagged_pids()
    assert fresh != pid
    assert [ask(p) for p in env.standard_ports] == ["main-0", "main-1", "main-2"]
    assert rig.camp("env", "status").stdout == (
        f"attached: {attached_shown(target)} on ports {ports_of(env)}; its health was not checked\n"
    )


@pytest.mark.parametrize("target", KINDS)
def test_with_the_forward_or_relay_alive_the_same_second_attach_is_refused(rig: AttachRig, target: str) -> None:
    env, pid = attach_serving(rig, target)

    done = rig.attach(target)

    note = " (a forward whose link has died ends on its own within about 45s)" if target != "local/main" else ""
    assert done.returncode == 1
    assert done.stderr == (
        f"camp env: {attached_shown(target)} is attached; run camp env detach before attaching another{note}\n"
    )
    assert rig.tagged_pids() == [pid]
    assert [ask(p) for p in env.standard_ports] == ["main-0", "main-1", "main-2"]


# --- real ssh: a silent link ends the forward on its own ------------------------------------------------

SSH_DEST_ENV = "CAMP_ENV_TEST_SSH_DEST"
SSH_CONFIG_ENV = "CAMP_ENV_TEST_SSH_CONFIG"
SSH_PROBE_SECONDS_ENV = "CAMP_ENV_TEST_SSH_PROBE_SECONDS"
SILENT_LINK_BOUND_SECONDS = 75.0

#: A ProxyCommand that carries ssh's bytes to the sshd until the flag file appears, then keeps reading
#: both sides and throws what it reads away without closing either: a link that has gone silent.
BLACK_HOLE_RELAY = """\
import os, socket, sys, threading
host, port, flag = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sock = socket.create_connection((host, port))
def pump(read, write):
    while True:
        data = read()
        if not data:
            os._exit(0)
        if not os.path.exists(flag):
            write(data)
def to_stdout(data):
    while data:
        data = data[os.write(1, data):]
threading.Thread(target=pump, args=(lambda: sock.recv(65536), to_stdout), daemon=True).start()
pump(lambda: os.read(0, 65536), sock.sendall)
"""


def processes_mentioning(needle: str) -> list[int]:
    out = subprocess.run(["ps", "-axwwo", "pid=,command="], capture_output=True, text=True, check=True).stdout
    found = []
    for line in out.splitlines():
        pid, command = line.split(None, 1)
        if needle in command and int(pid) != os.getpid():
            found.append(int(pid))
    return found


@pytest.mark.parametrize("cause", ["no-ssh", "stalled-login"])
def test_the_real_ssh_probe_skips_when_ssh_is_missing_or_the_login_stalls(tmp_path: Path, cause: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    environ = {k: v for k, v in os.environ.items() if k not in (SSH_CONFIG_ENV,)}
    environ[SSH_DEST_ENV] = "someone@stalled.invalid"
    environ["PATH"] = str(bin_dir)
    if cause == "stalled-login":
        stub = bin_dir / "ssh"
        stub.write_text("#!/bin/sh\nexec sleep 30\n")
        stub.chmod(0o755)
        (bin_dir / "sleep").symlink_to(shutil.which("sleep"))
        environ[SSH_PROBE_SECONDS_ENV] = "0.5"

    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-n", "0", "-p", "no:cacheprovider", "-rs",
         f"{Path(__file__)}::test_a_forward_over_a_silent_link_exits_on_its_keep_alive_and_frees_every_port"],
        capture_output=True, text=True, env=environ, cwd=str(tmp_path), timeout=120,
    )

    assert "1 skipped" in done.stdout, done.stdout + done.stderr
    assert "error" not in done.stdout.splitlines()[-1]
    assert ("no ssh on PATH" if cause == "no-ssh" else "did not finish a key login") in done.stdout


def test_a_forward_over_a_silent_link_exits_on_its_keep_alive_and_frees_every_port(tmp_path: Path) -> None:
    """Run camp's own forward argv under real ssh, through a relay that goes silent, and time its exit.

    Opt-in: set ``CAMP_ENV_TEST_SSH_DEST`` to an ssh destination that accepts key login without a
    prompt and whose host key ssh already trusts (a ``user@host``, or a ``Host`` alias from the file
    named by ``CAMP_ENV_TEST_SSH_CONFIG``, which is then passed as ``ssh -F``). The test is skipped
    when the variable is unset or the destination does not answer. ssh must give up on the silent
    link within 75 s, leaving every ``-L`` port free; an ssh that keeps the forward past that, as
    one run with the keep-alive options dropped or loosened does, fails.
    """
    from camp.host.config import Host
    from camp.host.transport import local_forward_argv

    dest = os.environ.get(SSH_DEST_ENV)
    if not dest:
        pytest.skip(
            f"set {SSH_DEST_ENV} to an ssh destination (user@host, or a Host alias in the file named by "
            f"{SSH_CONFIG_ENV}) that accepts key login without a prompt"
        )
    config = os.environ.get(SSH_CONFIG_ENV)
    config_args = ["-F", config] if config else []
    probe_seconds = float(os.environ.get(SSH_PROBE_SECONDS_ENV, "60"))
    try:
        reach = subprocess.run(
            ["ssh", *config_args, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", dest, "true"],
            capture_output=True, text=True, timeout=probe_seconds, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        pytest.skip(f"{SSH_DEST_ENV}={dest} accepted a connection but did not finish a key login in {probe_seconds:g}s")
    except FileNotFoundError:
        pytest.skip(f"no ssh on PATH to probe {SSH_DEST_ENV}={dest}")
    if reach.returncode != 0:
        pytest.skip(f"{SSH_DEST_ENV}={dest} did not answer a key login: {reach.stderr.strip()}")

    relay = tmp_path / "relay.py"
    relay.write_text(BLACK_HOLE_RELAY)
    flag = tmp_path / "silent"
    log = tmp_path / "ssh.log"
    tag = "camp-env-t" + tmp_path.name.replace("_", "").replace("-", "")[-8:] + ":silentlink:perseus/levr/main"
    ports = free_ports(4)
    links = [(ports[0], ports[2]), (ports[1], ports[3])]
    argv = local_forward_argv(Host(ssh=dest, camp_bin="camp"), links, tag, 10.0)
    proxy = f"ProxyCommand={shlex.quote(sys.executable)} {shlex.quote(str(relay))} %h %p {shlex.quote(str(flag))}"
    argv[1:1] = [*config_args, "-E", str(log), "-o", proxy]
    forwarders: list[int] = []
    try:
        with open(tmp_path / "ssh.err", "w") as err:
            started = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, timeout=60
            )
        assert started.returncode == 0, (tmp_path / "ssh.err").read_text() + log.read_text()
        wait_for(lambda: processes_mentioning(f"Tag={tag}"))
        forwarders = processes_mentioning(f"Tag={tag}")
        assert len(forwarders) == 1, forwarders
        assert not any(port_is_free(p) for p in ports[:2]), "the forward holds its -L ports before the link goes silent"

        flag.touch()
        silenced = time.monotonic()
        deadline = silenced + SILENT_LINK_BOUND_SECONDS
        while processes_mentioning(f"Tag={tag}") and time.monotonic() < deadline:
            time.sleep(0.5)

        exited_after = time.monotonic() - silenced
        assert not processes_mentioning(f"Tag={tag}"), (
            f"ssh still forwarding {SILENT_LINK_BOUND_SECONDS:g}s after its link went silent\n" + log.read_text()
        )
        assert all(port_is_free(p) for p in ports[:2]), f"a -L port stayed held after ssh exited ({exited_after:.0f}s)"
    finally:
        for pid in forwarders + processes_mentioning(str(relay)):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
