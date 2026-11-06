"""`python -m camp.env.relay`: the same-machine stand-in for an ssh forward.

The relay runs as a real subprocess against real loopback upstreams on free high ports. Nothing
here binds a real standard port.
"""

from __future__ import annotations

import errno
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "camp"
sys.path.insert(0, str(PACKAGE_ROOT))

from camp.env import relay as relay_module  # noqa: E402

from .env_ssh_stub import free_ports  # noqa: E402


def can_bind(port: int) -> bool:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


class EchoUpstream:
    """Reads a request to end-of-stream, then answers `<label>:<request>` and closes."""

    def __init__(self, label: str):
        self.label = label
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._answer, args=(client,), daemon=True).start()

    def _answer(self, client: socket.socket) -> None:
        try:
            data = b""
            while chunk := client.recv(65536):
                data += chunk
            if data:
                client.sendall(self.label.encode() + b":" + data)
        except OSError:
            pass
        finally:
            client.close()

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def upstreams():
    made: list[EchoUpstream] = []

    def make(count: int = 1) -> list[EchoUpstream]:
        base = len(made)
        made.extend([EchoUpstream(f"u{base + i}") for i in range(count)])
        return made[base:]

    yield make
    for each in made:
        each.close()


@pytest.fixture
def procs():
    started: list[subprocess.Popen] = []
    yield started
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for stream in (proc.stdout, proc.stderr):
            if stream:
                stream.close()


def argv_for(pairs, *, extra=(), listen="127.0.0.1", target="127.0.0.1", dash_n=True):
    argv = [sys.executable, "-m", "camp.env.relay"]
    if dash_n:
        argv.append("-N")
    argv += ["-o", "Tag=test", *extra]
    for port, up in pairs:
        argv += ["-L", f"{listen}:{port}:{target}:{up}"]
    return argv


def launch(procs, argv, env_extra=None) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(PACKAGE_ROOT), **(env_extra or {})}
    proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    procs.append(proc)
    return proc


def start(procs, pairs, env_extra=None, **kw) -> subprocess.Popen:
    proc = launch(procs, argv_for(pairs, **kw), env_extra)
    assert proc.stdout.readline() == b"ready\n", proc.stderr.read()
    return proc


def request(port: int, payload: bytes, timeout: float = 10) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while data := sock.recv(65536):
            chunks.append(data)
        return b"".join(chunks)


def closed_without_reply(port: int, payload: bytes) -> bool:
    """True when the relay closed this connection without sending anything back.

    The relay closes the client with the payload unread, so the client sees that close
    as a reset at whichever step it is on: the send (broken pipe), the half-close
    (ENOTCONN on Linux, when the reset lands first) or the read (connection reset).
    """
    try:
        return request(port, payload) == b""
    except (ConnectionResetError, BrokenPipeError):
        return True
    except OSError as error:
        if error.errno == errno.ENOTCONN:
            return True
        raise


def test_bytes_to_a_standard_port_reach_its_upstream_and_the_reply_returns(procs, upstreams):
    (up,) = upstreams(1)
    (port,) = free_ports(1)
    start(procs, [(port, up.port)])
    assert request(port, b"hello") == b"u0:hello"


def test_two_ports_in_one_relay_each_reach_their_own_upstream(procs, upstreams):
    a, b = upstreams(2)
    pa, pb = free_ports(2)
    start(procs, [(pa, a.port), (pb, b.port)])
    assert request(pa, b"x") == b"u0:x"
    assert request(pb, b"y") == b"u1:y"


def test_ready_is_printed_only_after_every_port_is_bound(procs, upstreams):
    a, b = upstreams(2)
    pa, pb = free_ports(2)
    proc = launch(procs, argv_for([(pa, a.port), (pb, b.port)]))
    assert proc.stdout.readline() == b"ready\n"
    for port in (pa, pb):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        assert not can_bind(port)


def test_one_standard_port_already_held_fails_naming_it_and_frees_the_other(procs, upstreams):
    a, b = upstreams(2)
    pa, pb = free_ports(2)
    holder = socket.socket()
    holder.bind(("127.0.0.1", pb))
    holder.listen(1)
    try:
        proc = launch(procs, argv_for([(pa, a.port), (pb, b.port)]))
        assert proc.wait(timeout=10) == 1
        assert f"port {pb}:" in proc.stderr.read().decode()
        assert proc.stdout.read() == b""
        assert can_bind(pa)
    finally:
        holder.close()


@pytest.fixture
def signals_restored():
    """Puts back the SIGTERM/SIGINT handlers and the wakeup descriptor a Relay built in this process replaces."""
    handlers = {which: signal.getsignal(which) for which in (signal.SIGTERM, signal.SIGINT)}
    wakeup = signal.set_wakeup_fd(-1)
    signal.set_wakeup_fd(wakeup)
    yield
    for which, handler in handlers.items():
        signal.signal(which, handler)
    signal.set_wakeup_fd(wakeup)


def test_a_failed_bind_releases_the_ports_already_held_in_the_same_process(upstreams, signals_restored):
    (up,) = upstreams(1)
    first, second = free_ports(2)
    holder = socket.socket()
    holder.bind(("127.0.0.1", second))
    holder.listen(1)
    try:
        held = relay_module.Relay([(first, up.port), (second, up.port)], 4)
        failure = held.bind()
        assert failure is not None and failure[0] == second
        assert can_bind(first)
    finally:
        holder.close()


def test_every_listener_is_bound_to_loopback_only(upstreams, signals_restored):
    (up,) = upstreams(1)
    (port,) = free_ports(1)
    held = relay_module.Relay([(port, up.port)], 4)
    try:
        assert held.bind() is None
        assert [sock.getsockname()[0] for sock, _ in held.listeners] == ["127.0.0.1"]
    finally:
        held.close_listeners()


def test_stop_closes_every_tracked_connection(signals_restored):
    held = relay_module.Relay([], 4)
    ours, theirs = socket.socketpair()
    try:
        assert held.track(ours)
        held.stop()
        theirs.settimeout(2)
        assert theirs.recv(10) == b""
    finally:
        theirs.close()
        ours.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"listen": "0.0.0.0"},
        {"target": "10.0.0.5"},
        {"extra": ("-x",)},
        {"dash_n": False, "extra": ("-q",)},
    ],
)
def test_a_malformed_argv_exits_2_with_usage_and_binds_nothing(procs, upstreams, kwargs):
    (up,) = upstreams(1)
    (port,) = free_ports(1)
    proc = launch(procs, argv_for([(port, up.port)], **kwargs))
    assert proc.wait(timeout=10) == 2
    assert b"usage" in proc.stderr.read()
    assert proc.stdout.read() == b""
    assert can_bind(port)


@pytest.mark.parametrize(
    "spec",
    [
        "127.0.0.1:1023:127.0.0.1:2000",
        "127.0.0.1:2000:127.0.0.1:1023",
        "127.0.0.1:65536:127.0.0.1:2000",
        "127.0.0.1:abcd:127.0.0.1:2000",
        "127.0.0.1:２０００:127.0.0.1:2000",
        "127.0.0.1:2000:127.0.0.1:",
        "127.0.0.1:2000:127.0.0.1:2000:extra",
        "localhost:2000:127.0.0.1:2000",
    ],
)
def test_an_L_spec_outside_the_grammar_exits_2(procs, spec):
    proc = launch(procs, [sys.executable, "-m", "camp.env.relay", "-N", "-L", spec])
    assert proc.wait(timeout=10) == 2
    assert b"usage" in proc.stderr.read()


def test_no_L_exits_2(procs):
    proc = launch(procs, [sys.executable, "-m", "camp.env.relay", "-N", "-o", "Tag=t"])
    assert proc.wait(timeout=10) == 2
    assert b"usage" in proc.stderr.read()


@pytest.mark.parametrize(
    ("port", "allowed"),
    [(1023, False), (1024, True), (65535, True), (65536, False), (0, False)],
)
def test_the_exported_port_rule_accepts_exactly_1024_to_65535(port, allowed):
    assert relay_module.port_in_range(port) is allowed


def test_an_unreachable_upstream_closes_each_client_and_the_relay_keeps_serving(procs):
    port, dead = free_ports(2)
    proc = start(procs, [(port, dead)])
    assert closed_without_reply(port, b"nobody home")
    assert closed_without_reply(port, b"again")
    assert proc.poll() is None


def test_the_relay_serves_a_live_upstream_after_an_unreachable_one_failed(procs, upstreams):
    (up,) = upstreams(1)
    good, bad = free_ports(2)
    dead = free_ports(1)[0]
    start(procs, [(good, up.port), (bad, dead)])
    assert closed_without_reply(bad, b"x")
    assert request(good, b"ok") == b"u0:ok"


@pytest.mark.parametrize("which", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_exits_0_within_2s_and_frees_every_port(procs, upstreams, which):
    a, b = upstreams(2)
    pa, pb = free_ports(2)
    proc = start(procs, [(pa, a.port), (pb, b.port)])
    proc.send_signal(which)
    started = time.monotonic()
    assert proc.wait(timeout=2) == 0
    assert time.monotonic() - started < 2
    assert can_bind(pa) and can_bind(pb)


def test_a_sigterm_sent_the_moment_ready_is_read_never_goes_unheard(procs):
    """The relay's wait must not be able to miss a signal that lands just before it starts.

    A signal consumed between the interpreter's pending-signal check and the blocking wait
    left the relay waiting forever in a few percent of runs when many start together, so
    this starts hundreds of them in batches and signals each the moment it says ready.
    """
    batch, batches = 150, 3
    ports = iter(free_ports(batch * batches))
    deaf: list[int] = []
    sent = 0
    for _ in range(batches):
        group = [launch(procs, argv_for([(next(ports), 1025)])) for _ in range(batch)]
        signalled = []
        for proc in group:
            assert proc.stdout.readline() == b"ready\n"
            proc.send_signal(signal.SIGTERM)
            signalled.append((proc, time.monotonic()))
        for proc, at in signalled:
            try:
                proc.wait(timeout=max(at + 2 - time.monotonic(), 0.01))
            except subprocess.TimeoutExpired:
                deaf.append(proc.pid)
            sent += 1
    assert deaf == [], f"{len(deaf)} of {sent} relays ignored a SIGTERM sent right after ready: {deaf}"


def test_a_signal_closes_an_open_relayed_connection(procs, upstreams):
    (up,) = upstreams(1)
    (port,) = free_ports(1)
    proc = start(procs, [(port, up.port)])
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        time.sleep(0.3)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=2) == 0
        client.settimeout(3)
        try:
            assert client.recv(10) == b""
        except ConnectionResetError:
            pass
    finally:
        client.close()


def test_closing_the_parents_read_end_of_stdout_does_not_kill_the_relay(procs, upstreams):
    (up,) = upstreams(1)
    (port,) = free_ports(1)
    proc = start(procs, [(port, up.port)])
    proc.stdout.close()
    proc.stderr.close()
    assert request(port, b"still") == b"u0:still"
    assert request(port, b"here") == b"u0:here"
    assert proc.poll() is None


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/<pid>/fd to read a process's descriptors")
def test_after_ready_descriptors_0_1_and_2_point_at_dev_null(procs, upstreams):
    (up,) = upstreams(1)
    (port,) = free_ports(1)
    proc = start(procs, [(port, up.port)])
    for fd in (0, 1, 2):
        assert os.readlink(f"/proc/{proc.pid}/fd/{fd}") == os.devnull


def test_at_the_cap_a_waiting_connection_is_served_when_one_ends(procs, upstreams):
    (up,) = upstreams(1)
    (port,) = free_ports(1)
    cap = 3
    start(procs, [(port, up.port)], env_extra={relay_module.MAX_CONNECTIONS_ENV: str(cap)})
    held = [socket.create_connection(("127.0.0.1", port), timeout=5) for _ in range(cap)]
    time.sleep(0.5)
    late = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        late.sendall(b"late")
        late.shutdown(socket.SHUT_WR)
        late.settimeout(1)
        with pytest.raises(socket.timeout):
            late.recv(1024)
        held.pop().close()
        late.settimeout(10)
        reply = b""
        while chunk := late.recv(1024):
            reply += chunk
    finally:
        late.close()
        for sock in held:
            sock.close()
    assert reply == b"u0:late"
