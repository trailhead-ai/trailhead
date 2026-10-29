"""A stub ``ssh`` that forwards ports, and the rig that installs it, for ``camp env`` attach tests.

Two halves in one stdlib-only file. Imported (``from .env_ssh_stub import AttachRig``) it is the
rig; copied to ``<rig>/bin/ssh`` under a ``#!<interpreter>`` line it is the ``ssh`` the CLI runs.

The stub serves two kinds of call:

* **No ``-N``: an inventory read.** It logs ``{"dest": ...}`` to ``inventory.log`` and prints
  ``<root>/inventory/<destination>`` (exit 255 with ``Could not resolve hostname`` when absent).
* **``-N``: a forward.** It logs the argument list to ``forwards.log``, honours ``-f``, ``-o`` and
  ``-L``, binds every ``-L`` local port and relays each to its ``-L`` target on 127.0.0.1. With
  ``-f`` it forks into the background and the foreground process exits, and the background
  process re-points its descriptors 0, 1 and 2 at /dev/null, exactly as real ``ssh -f`` does
  (measured on OpenSSH 9.6: anything it would say after backgrounding is lost). It re-executes
  itself with argv[0] ``ssh`` so ``ps`` shows ``ssh <script> <args>`` as a real forward shows
  ``ssh <args>``.

``<root>/stub.json`` (written by ``AttachRig.configure``) switches in failure modes:

* ``fail_start``: exit 255 before binding anything; a string value is the stderr line.
* ``die_after_fork``: background, then exit at once, leaving no forward.
* ``ignore_term``: ignore SIGTERM in the background process.
* ``bind_first_then_fail``: bind the first ``-L`` port, then exit 255 on the second with ssh's own
  bind-failure lines (``fixtures/env_forward_bind_failure_linux_openssh_9_6.txt``).
* ``parent_delay``: seconds the foreground process lingers after forking, so a test can act while
  ``ssh -f`` has not yet returned.

Using the rig from another test file::

    from .env_ssh_stub import AttachRig

    @pytest.fixture
    def rig(tmp_path):
        r = AttachRig(tmp_path)        # fresh tag namespace, free of any real attachment
        try:
            yield r
        finally:
            r.close()                  # kills every process the namespace owns, closes upstreams

    def test_something(rig):
        env = rig.environment("main")                  # a running env with 3 fake upstreams
        rig.declare_hosts("perseus")                   # hosts.toml, perseus opted in
        rig.host_inventory("perseus", env)             # what `ssh perseus` answers to a read
        done = rig.attach("perseus/levr/main")         # `camp env attach ...`; CompletedProcess
        done = rig.camp("env", "status")               # any camp argv, same environment

``attached_rig(rig)`` makes those three configuring calls for one environment on ``perseus``.
``rig.forwards()`` lists the argument lists of forwards started, ``rig.inventory_reads()`` the
destinations read, ``rig.tagged_pids()`` the live processes carrying this rig's namespace.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MARK = "CAMP_ENV_STUB_REEXEC"
INTERPRETER = "CAMP_ENV_STUB_PYTHON"

BIND_FAILURE_TAIL = "channel_setup_fwd_listener_tcpip: cannot listen to port: {port}\nCould not request local forwarding."


# --- the stub ssh ------------------------------------------------------------------------------


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _config() -> dict:
    path = _root() / "stub.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _die(code: int, message: str):
    print(message, file=sys.stderr)
    sys.exit(code)


def _relay(client: socket.socket, target: tuple[str, int]) -> None:
    try:
        upstream = socket.create_connection(target, timeout=5)
    except OSError:
        client.close()
        return

    def pump(source: socket.socket, sink: socket.socket) -> None:
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                sink.sendall(data)
        except OSError:
            pass
        finally:
            try:
                sink.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    back = threading.Thread(target=pump, args=(upstream, client), daemon=True)
    back.start()
    pump(client, upstream)
    back.join()
    client.close()
    upstream.close()


def _destination(args: list[str]) -> str | None:
    if "--" in args and args.index("--") + 1 < len(args):
        return args[args.index("--") + 1]
    return None


def _inventory(args: list[str]) -> None:
    dest = _destination(args)
    with open(_root() / "inventory.log", "a") as log:
        log.write(json.dumps({"dest": dest}) + "\n")
    reply = _root() / "inventory" / (dest or "")
    if dest is None or not reply.is_file():
        _die(255, f"ssh: Could not resolve hostname {dest}")
    sys.stdout.write(reply.read_text())
    sys.exit(0)


def _forward(args: list[str]) -> None:
    fork = False
    options: dict[str, str] = {}
    forwards: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-f":
            fork = True
        elif arg == "-N":
            pass
        elif arg == "-o":
            i += 1
            key, _, value = args[i].partition("=")
            options[key] = value
        elif arg == "-L":
            i += 1
            forwards.append(args[i])
        elif arg == "--":
            break
        i += 1
    with open(_root() / "forwards.log", "a") as log:
        log.write(json.dumps(args) + "\n")

    cfg = _config()
    if cfg.get("fail_start"):
        message = cfg["fail_start"] if isinstance(cfg["fail_start"], str) else "ssh: connect to host: Connection refused"
        _die(255, message)

    listeners: dict[socket.socket, tuple[str, int]] = {}
    for index, spec in enumerate(forwards):
        bind_address, local_port, target_address, target_port = spec.split(":")
        if cfg.get("bind_first_then_fail") and index == 1:
            _die(255, f"bind [{bind_address}]:{local_port}: Address already in use\n" + BIND_FAILURE_TAIL.format(port=local_port))
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_address, int(local_port)))
        except OSError:
            if options.get("ExitOnForwardFailure") == "yes":
                _die(255, f"bind [{bind_address}]:{local_port}: Address already in use\n" + BIND_FAILURE_TAIL.format(port=local_port))
            continue
        sock.listen()
        listeners[sock] = (target_address, int(target_port))

    if fork:
        if os.fork():
            time.sleep(float(cfg.get("parent_delay", 0)))
            os._exit(0)
        os.setsid()
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
    if cfg.get("die_after_fork"):
        os._exit(0)
    if cfg.get("ignore_term"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    while True:
        ready, _, _ = select.select(list(listeners), [], [])
        for sock in ready:
            client, _ = sock.accept()
            threading.Thread(target=_relay, args=(client, listeners[sock]), daemon=True).start()


def main() -> None:
    args = sys.argv[1:]
    if "-N" not in args:
        _inventory(args)
    if MARK not in os.environ:
        os.environ[MARK] = "1"
        os.environ[INTERPRETER] = sys.executable
        os.execv(sys.executable, ["ssh", str(Path(__file__).resolve()), *args])
    _forward(args)


# --- the rig -----------------------------------------------------------------------------------

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_CLI_CAMP = Path(__file__).resolve().parents[1] / "plugins" / "camp" / "cli" / "camp"
_PS_ARGV = ["ps", "-axwwo", "pid=,uid=,command="]

_DOCKER_STUB = """#!/bin/sh
[ -f "$STUB_DIR/docker.stdout" ] && cat "$STUB_DIR/docker.stdout"
exit 0
"""

_PS_STUB = """#!/bin/sh
n=$(cat "$STUB_DIR/ps.count" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$STUB_DIR/ps.count"
limit=$(cat "$STUB_DIR/ps.limit" 2>/dev/null)
if [ -n "$limit" ] && [ "$n" -gt "$limit" ]; then
  echo "ps: simulated failure" >&2
  exit 1
fi
exec "$REAL_PS" "$@"
"""


def free_ports(count: int) -> list[int]:
    socks = [socket.socket() for _ in range(count)]
    for sock in socks:
        sock.bind(("127.0.0.1", 0))
    ports = [sock.getsockname()[1] for sock in socks]
    for sock in socks:
        sock.close()
    return ports


class Upstream:
    """A fake service: answers every connection with its label, then closes."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            try:
                client.sendall(self.label.encode())
            except OSError:
                pass
            client.close()

    def close(self) -> None:
        self.sock.close()


def ask(port: int) -> str:
    """Connect to 127.0.0.1:port and read until the peer closes; the client closes first."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        chunks = []
        while True:
            data = sock.recv(1024)
            if not data:
                return b"".join(chunks).decode()
            chunks.append(data)


def can_bind(port: int, family: int = socket.AF_INET, address: str = "127.0.0.1") -> bool:
    """True when *port* binds without SO_REUSEADDR, as camp's own port check does."""
    sock = socket.socket(family)
    try:
        sock.bind((address, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def port_is_free(port: int) -> bool:
    return can_bind(port) and can_bind(port, socket.AF_INET6, "::1")


@dataclass
class RigEnvironment:
    """One environment as a host's inventory shows it, with fake upstreams behind its ports."""

    group: str
    name: str
    state: str
    ports: list[tuple[int, int]]
    upstreams: list[Upstream] = field(default_factory=list)

    @property
    def standard_ports(self) -> list[int]:
        return [standard for standard, _ in self.ports]

    def record(self) -> str:
        published = ", ".join(f"127.0.0.1:{host}->{standard}/tcp" for standard, host in self.ports)
        docker_state = "running" if self.state == "running" else "exited"
        return f'"{self.group}-env-{self.name}"|"{docker_state}"|"{self.group}"|"{self.name}"|"{published}"\n'


class AttachRig:
    """A hermetic machine for ``camp env attach``: stub ssh/docker/ps, a hosts file, a tag namespace."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "stubs"
        self.bin = self.root / "bin"
        self.bin.mkdir(parents=True)
        (self.root / "inventory").mkdir()
        self.config_dir = tmp_path / "camp-config"
        self.config_dir.mkdir()
        self.state_dir = tmp_path / "camp-state"
        self.ns = "camp-env-t" + uuid.uuid4().hex[:8]
        self._closers: list = []
        self._popens: list[subprocess.Popen] = []
        for tool in ("sh", "cat", "lsof"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        ssh = self.bin / "ssh"
        ssh.write_text(f"#!{sys.executable}\n{Path(__file__).read_text()}")
        ssh.chmod(0o755)
        self._script("ps", _PS_STUB)
        self._script("docker", _DOCKER_STUB)
        self.docker_inventory()

    def _script(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    # -- configuring the machine ---------------------------------------------------------------

    def declare_hosts(self, *names: str, opted_in: tuple[str, ...] | None = None) -> Path:
        """Write hosts.toml declaring each name as ``tduffield@<name>``; *opted_in* defaults to all."""
        opted = names if opted_in is None else opted_in
        text = "env_hosts = [" + ", ".join(f'"{n}"' for n in opted) + "]\n"
        for name in names:
            text += f'\n[hosts.{name}]\nssh = "{self.destination(name)}"\n'
        return self.write_hosts_file(text)

    def write_hosts_file(self, text: str) -> Path:
        path = self.config_dir / "hosts.toml"
        path.write_text(text)
        return path

    @staticmethod
    def destination(host: str) -> str:
        return f"tduffield@{host}"

    def environment(
        self, name: str, *, group: str = "levr", count: int = 3, state: str = "running"
    ) -> RigEnvironment:
        """An environment with *count* ports, each carried to a fake upstream answering ``<name>-<i>``."""
        upstreams = [Upstream(f"{name}-{i}") for i in range(count)] if state == "running" else []
        self._closers += [u.close for u in upstreams]
        standard = sorted(free_ports(count)) if state == "running" else []
        ports = list(zip(standard, [u.port for u in upstreams]))
        return RigEnvironment(group, name, state, ports, upstreams)

    @staticmethod
    def inventory_text(*environments: RigEnvironment) -> str:
        header = (_FIXTURES / "env_inventory_header_docker_engine_29_8_0_linux.txt").read_text()
        return header + "".join(e.record() for e in environments)

    def host_inventory(self, host: str, *environments: RigEnvironment) -> None:
        """What ``ssh tduffield@<host>`` answers to an inventory read."""
        (self.root / "inventory" / self.destination(host)).write_text(self.inventory_text(*environments))

    def docker_inventory(self, *environments: RigEnvironment) -> None:
        """What this machine's own ``docker`` answers (nothing by default)."""
        (self.root / "docker.stdout").write_text(self.inventory_text(*environments))

    def configure(self, **switches) -> None:
        (self.root / "stub.json").write_text(json.dumps(switches))

    def break_ps_after(self, calls: int) -> None:
        """Every ``ps`` call after the first *calls* fails; 0 fails them all."""
        (self.root / "ps.limit").write_text(str(calls))

    # -- observing -----------------------------------------------------------------------------

    def forwards(self) -> list[list[str]]:
        log = self.root / "forwards.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def inventory_reads(self) -> list[str]:
        log = self.root / "inventory.log"
        return [json.loads(line)["dest"] for line in log.read_text().splitlines()] if log.exists() else []

    def tagged_pids(self) -> list[int]:
        """Live processes of this user whose command line carries this rig's tag namespace."""
        return [pid for pid, command in _process_table() if f"Tag={self.ns}:" in command]

    def wait_tagged(self, timeout: float = 10.0) -> list[int]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pids = self.tagged_pids()
            if pids:
                return pids
            time.sleep(0.05)
        raise AssertionError("no forward carrying this rig's namespace appeared")

    # -- running -------------------------------------------------------------------------------

    def environ(self) -> dict[str, str]:
        return {
            "PATH": str(self.bin),
            "HOME": str(self.root),
            "CAMP_CONFIG_DIR": str(self.config_dir),
            "CAMP_STATE_DIR": str(self.state_dir),
            "CAMP_ENV_TAG_NAMESPACE": self.ns,
            "STUB_DIR": str(self.root),
            "REAL_PS": shutil.which("ps"),
        }

    def camp(
        self, *args: str, timeout: float = 60.0, cwd: "Path | None" = None, **environ: str
    ) -> subprocess.CompletedProcess:
        """Run ``camp <args>`` in *cwd* (default: the rig's own directory)."""
        return subprocess.run(
            [sys.executable, str(_CLI_CAMP), *args],
            capture_output=True,
            text=True,
            env={**self.environ(), **environ},
            cwd=str(self.root if cwd is None else cwd),
            timeout=timeout,
        )

    def popen_camp(self, *args: str) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, str(_CLI_CAMP), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.environ(),
            cwd=str(self.root),
        )
        self._popens.append(proc)
        return proc

    def decoy_forward(
        self, attach_id: str, *, host: str = "perseus", group: str = "levr", name: str = "other"
    ) -> subprocess.Popen:
        """An idle process the process table recognises as a forward carrying *attach_id*.

        It holds no port; it lets a test show that something other than the forward under test
        is left alone. Ended by ``close``.
        """
        standard, published = free_ports(2)
        proc = subprocess.Popen(
            [
                "ssh", "-c", "__import__('time').sleep(600)", "-N",
                "-o", f"Tag={self.ns}:{attach_id}:{host}/{group}/{name}",
                "-L", f"127.0.0.1:{standard}:127.0.0.1:{published}",
                "--", self.destination(host),
            ],
            executable=sys.executable,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._popens.append(proc)
        return proc

    def attach(self, target: str, **environ: str) -> subprocess.CompletedProcess:
        return self.camp("env", "attach", target, **environ)

    def close(self) -> None:
        """End everything this rig's namespace owns and every listener it opened."""
        for proc in self._popens:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
        for pid in self.tagged_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for close in self._closers:
            close()


def attached_rig(rig: AttachRig, name: str = "main", host: str = "perseus", **kw) -> RigEnvironment:
    """A running environment *name* that *host* (declared and opted in) answers an inventory read with."""
    env = rig.environment(name, **kw)
    rig.declare_hosts(host)
    rig.host_inventory(host, env)
    return env


def wait_for(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def _process_table() -> list[tuple[int, str]]:
    out = subprocess.run(["ps", "-axwwo", "pid=,uid=,command="], capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        pid, uid, command = line.split(None, 2)
        if int(uid) == os.getuid():
            rows.append((int(pid), command))
    return rows


if __name__ == "__main__":
    main()
