"""``camp env`` — the verb for dev environments running on this machine and on opted-in hosts.

``camp env ls`` reads this machine and each host ``env_hosts`` names all at the same time, and
prints the blocks in a fixed order: this machine, then the hosts in ``env_hosts`` order. A block
prints as soon as its read and every read before it have finished. It ignores the current camp
group. An interrupt ends every ssh and docker the listing started, prints ``camp env: interrupted``
to stderr and exits 130, leaving the blocks already printed as they are; a closed stdout ends them
too and exits quietly (status 1) at the next line it tries to write: an ``attached:`` line, a block
or the ``not opted in:`` line.

``camp env ls HOST`` reads that one machine alone — this machine for ``local`` or ``self_name``
(never over ssh), otherwise a declared host whether or not ``env_hosts`` names it — and prints the
``attached:`` lines, then its block, and no ``not opted in:`` line. An undeclared name, text that
is not a host name and a second argument are refused before anything is printed.

``camp env attach TARGET`` puts one environment on this machine's localhost, holding each of its
standard ports on 127.0.0.1. For an environment on a remote host that is one background ``ssh``
forward; for an environment on this machine (``local/NAME``, ``<self_name>/NAME`` or a bare name
found only here) it is a relay, a background process camp starts itself that carries each connection
to the environment's published port. It refuses at once, reading no machine, when something is
attached; otherwise it resolves the target, then takes the attach lock: an exclusive ``flock`` on
``env-attach.lock`` in camp's state directory that every attach by this user takes. Under the lock
it checks again that nothing is attached, refuses a stopped or port-less environment and a held
standard port, starts the forward or relay, and finds it again by its fresh attach id in the process
table, so two attaches started together end with one attachment and the other is refused before it
starts or binds anything. The lock is released when attach returns, not when the attachment ends. An
attach that finds the lock held says so once on stderr and waits at most the hosts file's connect
timeout plus ``LOCK_WAIT_MARGIN_SECONDS``, then refuses having started nothing; a lock file that
cannot be opened is refused the same way, and an interrupt while waiting exits 130. Once it has been
started, every way out (a failed start, one that is not found, an exception, an interrupt) ends the
forward or relay carrying that id and no other; for a relay it also ends the process handle it
started, found in the table or not. A standard port taken between the probe and the relay's bind is
refused naming its holder.

A bare name reads this machine and every opted-in host at the same time. An interrupt while attach
is reading machines (a bare name or ``HOST/NAME``) ends every ssh and docker started for the reads,
prints ``camp env: interrupted`` to stderr and exits 130, before any forward exists.

``camp env status`` and ``camp env detach`` read only the process table: status prints one
``attached:`` line per recognised forward or relay (a relay is named ``local/<group>/<name>``),
detach ends them and waits for their ports to bind free. ``camp env ls`` opens with the same lines.
"""
from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from typing import NoReturn

from ..env.attachment import (
    end_spawned,
    UNSEEN_HOLDER,
    Attachment,
    Unverifiable,
    UnverifiableAfterSignal,
    Untaggable,
    attachments,
    end,
    port_holder,
    start_relay,
    tag_for,
)
from ..env.contract import Environment
from ..env.lock import LockBusy, LockUnavailable, attach_lock, attach_lock_path
from ..env.read import (
    DOCKER_NOT_INSTALLED,
    DOCKER_NOT_RUNNING,
    ENVIRONMENTS,
    MachineInventory,
)
from ..printable import printable_line
from .parser import CampParser

_USAGE = "usage: camp env ls [HOST] | attach TARGET | status | detach"
_LS_USAGE = "usage: camp env ls [HOST]"
_ATTACH_USAGE = "usage: camp env attach TARGET"

#: Seconds the forward may take to appear in the process table after ``ssh -f`` returned.
SETTLE_SECONDS = 3.0
#: Seconds detach waits for the ports of the forwards it ended to bind free again.
PORTS_FREE_SECONDS = 3.0
#: Seconds a relay may take to bind every standard port and report ready.
RELAY_READY_SECONDS = 10.0
#: Seconds ``ssh -f`` may run beyond its connect timeout before the attach gives up on it.
START_MARGIN_SECONDS = 20.0
#: Seconds beyond the hosts file's connect timeout that an attach waits for the attach lock: the
#: longest an attach holding it can take to start a forward and find it.
LOCK_WAIT_MARGIN_SECONDS = 30.0
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
    from ..env.read import ProcessSet, read_concurrently, read_local, read_remote
    from ..env.resolve import INVALID, THIS_MACHINE, UNDECLARED, name_host, undeclared_host_message

    parser = CampParser(verb="env ls")
    parser.add_argument("host", nargs="*")
    named = parser.parse_args(args).host
    if len(named) > 1:
        print(_LS_USAGE, file=sys.stderr)
        sys.exit(1)

    hosts, opted_in, self_name, connect_timeout = _load_machines()

    only = None
    if named:
        only = name_host(named[0], hosts=hosts, self_name=self_name)
        if only.kind == INVALID:
            print(f"camp env: {only.problem}\n{_LS_USAGE}", file=sys.stderr)
            sys.exit(1)
        if only.kind == UNDECLARED:
            _refuse(undeclared_host_message(only.name, hosts, str(_hosts_file())))

    processes = ProcessSet()
    this_machine = (
        f"this machine ({self_name})" if self_name else "this machine",
        lambda: read_local(runner=processes.runner),
    )

    def remote(name: str):
        return (
            name,
            lambda: read_remote(name, hosts[name], connect_timeout=connect_timeout, runner=processes.runner),
        )

    if only is None:
        reads = [this_machine, *(remote(name) for name in opted_in if name != self_name)]
    elif only.kind == THIS_MACHINE:
        reads = [this_machine]
    else:
        reads = [remote(only.name)]
    try:
        try:
            for found in attachments():
                print(f"attached: {_summary_of(found)}", flush=True)
        except (Unverifiable, Untaggable) as error:
            print(f"camp env: could not tell whether anything is attached: {error}", file=sys.stderr, flush=True)

        with read_concurrently(reads, processes) as results:
            for heading, inventory in results:
                _emit(heading, inventory)

        if only is None:
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
    except KeyboardInterrupt:
        processes.kill()
        print("camp env: interrupted", file=sys.stderr, flush=True)
        sys.exit(130)
    except BrokenPipeError:
        processes.kill()
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)


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


def _end_own(attach_id: str, noun: str = "forward") -> "tuple[bool, str | None]":
    """End the *noun* (``forward`` or ``relay``) carrying *attach_id* and no other. Returns
    ``(ended, problem)``: whether one was found and ended, and why it could not be (``None`` when
    nothing is left running)."""
    try:
        mine = [a for a in attachments() if a.attach_id == attach_id]
        if not mine:
            return False, None
        if end(mine):
            return False, f"the {noun} it started could not be ended; run camp env detach"
        return True, None
    except Unverifiable as error:
        return False, f"the {noun} it started could not be ended ({error}); run camp env detach"


def _exit_interrupted(ended: bool, problem: "str | None", noun: str = "forward") -> NoReturn:
    print(
        "camp env: attach interrupted"
        + (f"; {problem}" if problem else f"; the {noun} it started was ended" if ended else ""),
        file=sys.stderr,
    )
    sys.exit(130)


def _end_own_or_exit(attach_id: str, noun: str = "forward") -> "tuple[bool, str | None]":
    """``_end_own``, except that an interrupt during it exits 130, saying it may still be running."""
    try:
        return _end_own(attach_id, noun)
    except KeyboardInterrupt:
        _exit_interrupted(False, f"the {noun} it started may still be running; run camp env detach", noun)


def _fail_after_start(reason: str, attach_id: str, noun: str = "forward") -> NoReturn:
    _, problem = _end_own_or_exit(attach_id, noun)
    _refuse(f"attach failed: {reason}" + (f"; {problem}" if problem else ""))


def _noun_of(found: "list[Attachment]") -> str:
    kinds = {a.kind for a in found}
    return next(iter(kinds)) if len(kinds) == 1 else "attachment"


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


_PORT_FAILURE = re.compile(r"port ([0-9]+): ")


def _refuse_relay_start(reason: str) -> NoReturn:
    """Refuse an attach whose relay never became ready (``start_relay`` already ended it). A relay
    that could not bind a port taken since the probe names the port's holder, as the probe does."""
    named = _PORT_FAILURE.match(reason)
    if named:
        holder = port_holder(int(named[1]))
        if holder is not None:
            _refuse(f"attach failed: {_port_refusal(int(named[1]), holder)}")
    _refuse(f"attach failed: {reason}")


def _end_relay(started, attach_id: str) -> "tuple[bool, str | None]":
    """End the relay this process started, through its handle (which also reaps it), then end
    anything still carrying *attach_id* as a backstop. Returns ``(ended, problem)``: whether the
    relay is gone, and what could not be ended (``None`` when nothing is left running). An
    unreadable process table is not a problem once the handle has ended and reaped the relay."""
    try:
        end_spawned(started)
    except KeyboardInterrupt:
        _exit_interrupted(False, "the relay it started may still be running; run camp env detach", "relay")
    try:
        mine = [a for a in attachments() if a.attach_id == attach_id]
        if mine and end(mine):
            return False, "the relay it started could not be ended; run camp env detach"
    except (Unverifiable, KeyboardInterrupt):
        pass
    return True, None


def _attach_relay(environment: Environment, attach_id: str, tag: str) -> None:
    """Start the relay for *environment*, find it by *attach_id*, and leave it running. Every way out
    other than success ends the relay through the handle ``start_relay`` returned, then by attach id,
    before saying what happened."""
    try:
        started = start_relay(environment.ports, tag, timeout=RELAY_READY_SECONDS)
    except KeyboardInterrupt:
        _exit_interrupted(False, None, "relay")
    try:
        if isinstance(started, str):
            _refuse_relay_start(started)
        if _wait_for_forward(attach_id) is not None:
            return
        reason = "the relay started but it is not in the process table"
    except Unverifiable as error:
        reason = f"could not tell whether the relay started ({error})"
    except KeyboardInterrupt:
        _exit_interrupted(*(_end_relay(started, attach_id) if type(started) is not str else (False, None)), "relay")
    except Exception:
        if type(started) is not str:
            _end_relay(started, attach_id)
        raise
    _, problem = _end_relay(started, attach_id)
    _refuse(f"attach failed: {reason}" + (f"; {problem}" if problem else ""))


def _attach_forward(hosts, outcome, environment: Environment, attach_id: str, tag: str, timeout: float) -> None:
    from ..host.transport import local_forward_argv

    argv = local_forward_argv(hosts[outcome.host], environment.ports, tag, timeout)
    try:
        started = _start_forward(argv, timeout + START_MARGIN_SECONDS)
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


def _refuse_if_attached() -> None:
    """Refuse, naming it, when a forward or relay is attached (the dead-link note only for a
    forward); refuse when the process table cannot be read."""
    try:
        current = attachments()
    except (Unverifiable, Untaggable) as error:
        prefix = "could not tell whether anything is attached: " if isinstance(error, Unverifiable) else ""
        _refuse(f"{prefix}{error}")
    if current:
        first = current[0]
        note = f" ({_KEEP_ALIVE_NOTE})" if first.kind == "forward" else ""
        _refuse(
            f"{display_name(first.host, first.group, first.name)} is attached;"
            f" run camp env detach before attaching another{note}"
        )


def _say_waiting() -> None:
    print("camp env: another attach is starting; waiting for it to finish", file=sys.stderr, flush=True)


def _take_attach_lock(held: contextlib.ExitStack, connect_timeout: float) -> None:
    """Take the attach lock onto *held*, waiting at most the connect timeout plus
    ``LOCK_WAIT_MARGIN_SECONDS``. Refuses, starting nothing, when the wait runs out or the lock file
    cannot be opened; an interrupt while waiting exits 130."""
    try:
        held.enter_context(
            attach_lock(
                attach_lock_path(), timeout=connect_timeout + LOCK_WAIT_MARGIN_SECONDS, on_wait=_say_waiting
            )
        )
    except LockBusy:
        _refuse("another attach is still starting; wait for it to finish, then try again")
    except LockUnavailable as error:
        _refuse(
            f"cannot take the attach lock at {printable_line(str(error.path))}"
            f" ({printable_line(error.reason)}); nothing was started"
        )
    except KeyboardInterrupt:
        print("camp env: interrupted", file=sys.stderr, flush=True)
        sys.exit(130)


def _cmd_env_attach(args: list[str]) -> None:
    from ..env.read import ProcessSet, read_local, read_remote
    from ..env.resolve import Refused, TargetError, parse_target, resolve

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

    _refuse_if_attached()

    hosts, opted_in, self_name, connect_timeout = _load_machines()
    hosts_file = _hosts_file()
    processes = ProcessSet()
    try:
        outcome = resolve(
            target,
            hosts=hosts,
            opted_in=opted_in,
            self_name=self_name,
            read_local=lambda: read_local(runner=processes.runner),
            read_remote=lambda name: read_remote(
                name, hosts[name], connect_timeout=connect_timeout, runner=processes.runner
            ),
            hosts_file=str(hosts_file),
            processes=processes,
        )
    except KeyboardInterrupt:
        processes.kill()
        print("camp env: interrupted", file=sys.stderr, flush=True)
        sys.exit(130)
    if isinstance(outcome, Refused):
        _refuse(outcome.message)

    with contextlib.ExitStack() as held:
        _take_attach_lock(held, connect_timeout)
        _refuse_if_attached()
        _start_attachment(hosts, outcome, connect_timeout)
    environment: Environment = outcome.environment
    print(f"attached {attached_summary(outcome.host, environment.group, environment.name, environment.ports)}")


def _start_attachment(hosts, outcome, connect_timeout: float) -> None:
    """The part of attach that runs under the attach lock once nothing is found attached: refuse a
    stopped or port-less environment and a held standard port, then start the forward or relay."""
    from ..env.resolve import LOCAL

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
    if outcome.host == LOCAL:
        _attach_relay(environment, attach_id, tag)
    else:
        _attach_forward(hosts, outcome, environment, attach_id, tag, connect_timeout)


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
        _refuse(
            f"cannot end what is attached ({error}); the {_noun_of(found)} may already have ended;"
            " run camp env status"
        )
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
