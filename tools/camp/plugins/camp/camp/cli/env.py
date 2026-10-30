"""``camp env`` — the verb for dev environments running on this machine and on opted-in hosts.

``camp env ls`` reads this machine, then each host ``env_hosts`` names, one after another, and
prints each machine's block as soon as its read finishes. It ignores the current camp group.

``camp env attach TARGET`` puts one remote environment on this machine's localhost: one background
``ssh`` forward holding each of the environment's standard ports on 127.0.0.1. It checks, in order,
that nothing is attached, resolves the target, refuses a stopped or port-less environment and a
held standard port, starts the forward, and finds it again by its fresh attach id in the process
table. Once the forward has been started, every way out (a failed start, a forward that is not
found, an exception, an interrupt) ends the forward carrying that id and no other.

``camp env status`` and ``camp env detach`` read only the process table: status prints one
``attached:`` line per recognised forward, detach ends them and waits for their ports to bind
free. ``camp env ls`` opens with the same lines.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import uuid
from typing import NoReturn

from ..env.attachment import (
    UNSEEN_HOLDER,
    Attachment,
    Unverifiable,
    UnverifiableAfterSignal,
    Untaggable,
    attachments,
    end,
    port_holder,
    tag_for,
)
from ..env.contract import Environment
from ..env.read import (
    DOCKER_NOT_INSTALLED,
    DOCKER_NOT_RUNNING,
    ENVIRONMENTS,
    MachineInventory,
)
from ..printable import printable_line
from .parser import CampParser

_USAGE = "usage: camp env ls | attach TARGET | status | detach"
_ATTACH_USAGE = "usage: camp env attach TARGET"

#: Seconds the forward may take to appear in the process table after ``ssh -f`` returned.
SETTLE_SECONDS = 3.0
#: Seconds detach waits for the ports of the forwards it ended to bind free again.
PORTS_FREE_SECONDS = 3.0
#: Seconds ``ssh -f`` may run beyond its connect timeout before the attach gives up on it.
START_MARGIN_SECONDS = 20.0
_KEEP_ALIVE_NOTE = "a forward whose link has died ends on its own within about 45s"
_SETTLE_POLL_SECONDS = 0.05


def _render_block(heading: str, inventory: MachineInventory) -> str:
    lines = [heading]
    if inventory.kind == ENVIRONMENTS:
        if not inventory.environments:
            lines.append("  no environments")
        else:
            rows = sorted(inventory.environments, key=lambda e: (e.group, e.name))
            group_width = max(len(e.group) for e in rows)
            name_width = max(len(e.name) for e in rows)
            for e in rows:
                lines.append(f"  {e.group:<{group_width}}  {e.name:<{name_width}}  {e.state}")
    elif inventory.kind == DOCKER_NOT_INSTALLED:
        lines.append("  Docker is not installed")
    elif inventory.kind == DOCKER_NOT_RUNNING:
        lines.append("  Docker is not running")
    else:
        lines.append(f"  unreadable: {inventory.reason}")
    return "\n".join(lines) + "\n"


def _emit(heading: str, inventory: MachineInventory) -> None:
    sys.stdout.write(_render_block(heading, inventory))
    sys.stdout.flush()


def _hosts_file():
    import trailhead.paths as _paths

    return _paths.config_dir("camp") / "hosts.toml"


def _load_machines():
    """The hosts file's ``(hosts, env_hosts, self_name, connect_timeout)``; exits 1 when it is unusable."""
    from ..host.config import (
        HostConfigError,
        connect_timeout_seconds,
        env_host_names,
        load_hosts,
        self_host_name,
    )

    try:
        return load_hosts(), env_host_names(), self_host_name(), connect_timeout_seconds()
    except HostConfigError as error:
        print(f"camp env: {error}", file=sys.stderr)
        sys.exit(1)
    except (OSError, UnicodeDecodeError, RecursionError) as error:
        print(f"camp env: {_hosts_file()}: cannot read file — {type(error).__name__}", file=sys.stderr)
        sys.exit(1)


def _cmd_env_ls(args: list[str]) -> None:
    from ..env.read import read_local, read_remote

    CampParser(verb="env ls").parse_args(args)

    hosts, opted_in, self_name, connect_timeout = _load_machines()

    try:
        for found in attachments():
            print(f"attached: {_summary_of(found)}", flush=True)
    except (Unverifiable, Untaggable) as error:
        print(f"camp env: could not tell whether anything is attached: {error}", file=sys.stderr, flush=True)

    _emit(
        f"this machine ({self_name})" if self_name else "this machine",
        read_local(),
    )
    for name in opted_in:
        if name == self_name:
            continue
        _emit(name, read_remote(name, hosts[name], connect_timeout=connect_timeout))

    left_out = [name for name in hosts if name != self_name and name not in opted_in]
    if left_out:
        hosts_file = _hosts_file()
        print(
            # Host-table keys are not charset-checked by the loader, so they pass the
            # same printable-only reduction as far-side text before reaching the terminal.
            f"not opted in: {', '.join(printable_line(name) for name in left_out)} "
            f"(add to env_hosts in {printable_line(str(hosts_file), limit=4096)})",
            flush=True,
        )


def display_name(host: str, group: str, name: str) -> str:
    """An environment in its fully qualified form, ``<host>/<group>/<name>``."""
    return f"{host}/{group}/{name}"


def display_ports(ports: "tuple[tuple[int, int], ...]") -> str:
    """The standard ports of ``(standard, published)`` pairs, ascending, space separated."""
    return " ".join(str(standard) for standard, _ in sorted(ports))


def attached_summary(host: str, group: str, name: str, ports: "tuple[tuple[int, int], ...]") -> str:
    """``<host>/<group>/<name> on ports <p> <p>; its health was not checked`` — what attach prints
    after ``attached `` and what status prints after ``attached: ``."""
    return f"{display_name(host, group, name)} on ports {display_ports(ports)}; its health was not checked"


def _summary_of(found: Attachment) -> str:
    return attached_summary(found.host, found.group, found.name, found.ports)


def _refuse(message: str) -> NoReturn:
    print(f"camp env: {message}", file=sys.stderr)
    sys.exit(1)


def _port_refusal(port: int, holder: str) -> str:
    if holder == UNSEEN_HOLDER:
        return (
            f"port {port} is held by a process camp cannot see (another user's, or one lsof cannot name);"
            " stop it, then attach again"
        )
    return f"port {port} is held by {holder}; stop it, then attach again"


def _end_own(attach_id: str) -> "tuple[bool, str | None]":
    """End the forward carrying *attach_id* and no other. Returns ``(ended, problem)``: whether a
    forward was found and ended, and why it could not be (``None`` when nothing is left running)."""
    try:
        mine = [a for a in attachments() if a.attach_id == attach_id]
        if not mine:
            return False, None
        if end(mine):
            return False, "the forward it started could not be ended; run camp env detach"
        return True, None
    except Unverifiable as error:
        return False, f"the forward it started could not be ended ({error}); run camp env detach"


def _exit_interrupted(ended: bool, problem: "str | None") -> NoReturn:
    print(
        "camp env: attach interrupted"
        + (f"; {problem}" if problem else "; the forward it started was ended" if ended else ""),
        file=sys.stderr,
    )
    sys.exit(130)


def _end_own_or_exit(attach_id: str) -> "tuple[bool, str | None]":
    """``_end_own``, except that an interrupt during it exits 130, saying the forward may still be running."""
    try:
        return _end_own(attach_id)
    except KeyboardInterrupt:
        _exit_interrupted(False, "the forward it started may still be running; run camp env detach")


def _fail_after_start(reason: str, attach_id: str) -> NoReturn:
    _, problem = _end_own_or_exit(attach_id)
    _refuse(f"attach failed: {reason}" + (f"; {problem}" if problem else ""))


_NOISE_PREFIXES = ("** ", "Warning:", "debug")


def _reason_line(stderr: str) -> str:
    """The first line of ssh's stderr that is not a warning or debug line, else the first line."""
    lines = stderr.split("\n")
    for line in lines:
        if line.strip() and not line.startswith(_NOISE_PREFIXES):
            return line
    return lines[0]


def _start_forward(argv: list[str], timeout: float) -> "str | None":
    """Run ``ssh -f``; None when it exited 0, else why not. Its error text goes to a temporary file."""
    with tempfile.TemporaryFile() as errors:
        try:
            done = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors, timeout=timeout
            )
        except FileNotFoundError:
            return "ssh is not installed"
        except subprocess.TimeoutExpired:
            return f"ssh did not start the forward within {timeout:g}s"
        if done.returncode == 0:
            return None
        errors.seek(0)
        said = printable_line(_reason_line(errors.read().decode("utf-8", "replace"))).strip()
        return f"ssh could not start the forward: {said or f'ssh exited {done.returncode} without a reason'}"


def _wait_for_forward(attach_id: str) -> "Attachment | None":
    deadline = time.monotonic() + SETTLE_SECONDS
    while True:
        for found in attachments():
            if found.attach_id == attach_id:
                return found
        if time.monotonic() >= deadline:
            return None
        time.sleep(_SETTLE_POLL_SECONDS)


def _cmd_env_attach(args: list[str]) -> None:
    from ..env.read import read_local, read_remote
    from ..env.resolve import Refused, TargetError, parse_target, resolve
    from ..host.transport import local_forward_argv

    parser = CampParser(verb="env attach")
    parser.add_argument("target", nargs="*")
    targets = parser.parse_args(args).target
    if len(targets) != 1:
        print(_ATTACH_USAGE, file=sys.stderr)
        sys.exit(1)
    try:
        target = parse_target(targets[0])
    except TargetError as error:
        print(f"camp env: {error}\n{_ATTACH_USAGE}", file=sys.stderr)
        sys.exit(1)

    try:
        current = attachments()
    except (Unverifiable, Untaggable) as error:
        prefix = "could not tell whether anything is attached: " if isinstance(error, Unverifiable) else ""
        _refuse(f"{prefix}{error}")
    if current:
        first = current[0]
        _refuse(
            f"{display_name(first.host, first.group, first.name)} is attached;"
            f" run camp env detach before attaching another ({_KEEP_ALIVE_NOTE})"
        )

    hosts, opted_in, self_name, connect_timeout = _load_machines()
    hosts_file = _hosts_file()
    outcome = resolve(
        target,
        hosts=hosts,
        opted_in=opted_in,
        self_name=self_name,
        read_local=read_local,
        read_remote=lambda name: read_remote(name, hosts[name], connect_timeout=connect_timeout),
        hosts_file=str(hosts_file),
    )
    if isinstance(outcome, Refused):
        _refuse(outcome.message)

    environment: Environment = outcome.environment
    shown = display_name(outcome.host, environment.group, environment.name)
    if environment.state != "running":
        _refuse(f"{shown} is {environment.state}; start it with its project's own tooling, then attach again")
    if not environment.ports:
        _refuse(f"{shown} publishes no ports, so there is nothing to attach")
    for standard, _ in environment.ports:
        holder = port_holder(standard)
        if holder is not None:
            _refuse(_port_refusal(standard, holder))

    attach_id = uuid.uuid4().hex[:12]
    tag = tag_for(outcome.host, environment.group, environment.name, attach_id)
    argv = local_forward_argv(hosts[outcome.host], environment.ports, tag, connect_timeout)

    try:
        started = _start_forward(argv, connect_timeout + START_MARGIN_SECONDS)
        if started is not None:
            _fail_after_start(started, attach_id)
        found = _wait_for_forward(attach_id)
        if found is None:
            _fail_after_start("ssh started the forward but it is not running", attach_id)
    except Unverifiable as error:
        _fail_after_start(f"could not tell whether the forward started ({error})", attach_id)
    except KeyboardInterrupt:
        _exit_interrupted(*_end_own_or_exit(attach_id))
    except Exception:
        _end_own_or_exit(attach_id)
        raise
    print(f"attached {attached_summary(outcome.host, environment.group, environment.name, environment.ports)}")


def _read_attachments() -> "list[Attachment]":
    try:
        return attachments()
    except (Unverifiable, Untaggable) as error:
        _refuse(f"cannot tell what is attached: {error}")


def _cmd_env_status(args: list[str]) -> None:
    CampParser(verb="env status").parse_args(args)
    found = _read_attachments()
    if not found:
        print("nothing attached")
        return
    for each in found:
        print(f"attached: {_summary_of(each)}")


def _wait_for_free(port: int) -> "str | None":
    """None once *port* binds free, else its holder after ``PORTS_FREE_SECONDS``."""
    deadline = time.monotonic() + PORTS_FREE_SECONDS
    while True:
        holder = port_holder(port)
        if holder is None or time.monotonic() >= deadline:
            return holder
        time.sleep(_SETTLE_POLL_SECONDS)


def _cmd_env_detach(args: list[str]) -> None:
    CampParser(verb="env detach").parse_args(args)
    found = _read_attachments()
    if not found:
        print("nothing attached")
        return
    try:
        surviving = end(found)
    except UnverifiableAfterSignal as error:
        _refuse(f"cannot end what is attached ({error}); the forward may already have ended; run camp env status")
    except Unverifiable as error:
        _refuse(f"cannot end what is attached ({error}); nothing was signalled")
    if surviving:
        by_pid = {a.pid: a for a in found}
        shown = ", ".join(
            f"{display_name(by_pid[pid].host, by_pid[pid].group, by_pid[pid].name)} (pid {pid})" for pid in surviving
        )
        _refuse(f"could not end {shown}")
    for each in found:
        for standard, _ in each.ports:
            holder = _wait_for_free(standard)
            if holder is not None:
                _refuse(f"port {standard} is still held by {holder}")
    for each in found:
        print(f"detached {display_name(each.host, each.group, each.name)}; ports {display_ports(each.ports)} are free")


def _cmd_env_cli(args: list[str]) -> None:
    """``camp env <subcommand>`` — ``ls``, ``attach``, ``status`` and ``detach``; anything else
    prints the usage line."""
    subcommands = {
        "ls": _cmd_env_ls,
        "attach": _cmd_env_attach,
        "status": _cmd_env_status,
        "detach": _cmd_env_detach,
    }
    run = subcommands.get(args[0]) if args else None
    if run is None:
        print(_USAGE, file=sys.stderr)
        sys.exit(1)
    run(args[1:])
