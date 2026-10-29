"""Resolve what ``camp env attach`` was given to one environment, or to a refusal.

``parse_target(text)`` reads ``NAME``, ``HOST/NAME`` or ``HOST/GROUP/NAME`` into a
:class:`Target`. Group, name and host are each camp's identifier (``[a-z0-9][a-z0-9-]*``). Text that is none of
the three raises :class:`TargetError`; the CLI maps that to the ``camp env`` usage line and exit 1
(its message says which part was wrong, and for a host says what a host name may be). The hosts
file does not hold a ``[hosts.<name>]`` key to that rule (it checks ``self_name`` and the
``env_hosts`` entries, not the keys), so a declared host whose key falls outside it cannot be
attached in any form, and ``env_hosts`` could not name it either.

``resolve(target, *, hosts, opted_in, self_name, read_local, read_remote)`` returns
:class:`Resolved` (the host name and the one :class:`Environment`) or :class:`Refused`.

* ``hosts`` is the declared hosts mapping as ``host.config.load_hosts`` gives it; ``opted_in`` is
  ``env_host_names()``; ``self_name`` is ``self_host_name()`` (or ``None``).
* ``read_local()`` and ``read_remote(host_name)`` each return a ``read.MachineInventory``.
  ``read_remote`` takes the declared host's name; the caller binds the host, timeout and runner.
* A host form reads that declared host alone. A bare name reads this machine and the ``opted_in``
  hosts other than ``self_name`` (this machine, already read), never any other declared host.
* ``hosts_file`` is how an undeclared-host refusal names the hosts file; the CLI passes the path.

``Refused.message`` is the whole text, one or more lines, without the ``camp env: `` prefix: the
CLI adds it. Every reason that came from a machine passes through ``printable_line``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..host.config import Host
from ..printable import printable_line
from .contract import Environment, is_identifier
from .read import (
    DOCKER_NOT_INSTALLED,
    DOCKER_NOT_RUNNING,
    ENVIRONMENTS,
    UNREADABLE,
    MachineInventory,
)

LOCAL = "local"
THIS_MACHINE = "this machine"
_NOT_YET = "attaching an environment on this machine is not supported yet"
_HOST_RULE = "the host is not a valid host name: host names must be lowercase letters, digits and -, starting with a letter or digit"
_LS_POINTER = "run camp env ls to see what is running"


class TargetError(Exception):
    """The text is not ``NAME``, ``HOST/NAME`` or ``HOST/GROUP/NAME``; ``str()`` says which part."""


@dataclass(frozen=True)
class Target:
    host: str | None
    group: str | None
    name: str


@dataclass(frozen=True)
class Resolved:
    host: str
    environment: Environment


@dataclass(frozen=True)
class Refused:
    message: str


def parse_target(text: str) -> Target:
    """Parse ``NAME``, ``HOST/NAME`` or ``HOST/GROUP/NAME``; raise :class:`TargetError` otherwise."""
    parts = text.split("/")
    if len(parts) > 3:
        raise TargetError("expected NAME, HOST/NAME or HOST/GROUP/NAME")
    for label, part in zip(_labels(len(parts)), parts):
        if not part:
            raise TargetError(f"the {label} is empty")
        if not is_identifier(part):
            if label == "host":
                raise TargetError(_HOST_RULE)
            raise TargetError(f"the {label} is not a valid identifier")
    if len(parts) == 1:
        return Target(None, None, parts[0])
    if len(parts) == 2:
        return Target(parts[0], None, parts[1])
    return Target(parts[0], parts[1], parts[2])


def _labels(count: int) -> tuple[str, ...]:
    return {1: ("name",), 2: ("host", "name"), 3: ("host", "group", "name")}[count]


def _words(names: Sequence[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} or {names[-1]}"


def _and(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _label(machine: str) -> str:
    return THIS_MACHINE if machine == LOCAL else machine


def _docker_note(machine: str, inventory: MachineInventory) -> str | None:
    if inventory.kind == DOCKER_NOT_RUNNING:
        return f"Docker is not running on {_label(machine)}"
    if inventory.kind == DOCKER_NOT_INSTALLED:
        return f"Docker is not installed on {_label(machine)}"
    return None


def _reason(inventory: MachineInventory) -> str:
    return printable_line(inventory.reason).strip()


def _matches(inventory: MachineInventory, target: Target) -> list[Environment]:
    return [
        env
        for env in inventory.environments
        if env.name == target.name and (target.group is None or env.group == target.group)
    ]


def _shortest_forms(machine: str, matches: Sequence[Environment], name: str) -> list[str]:
    if len(matches) > 1:
        return [f"{machine}/{env.group}/{name}" for env in matches]
    return [f"{machine}/{name}"]


def _is_this_machine(host: str, self_name: str | None) -> bool:
    return host == LOCAL or (self_name is not None and host == self_name)


def _qualified(target: Target) -> str:
    return target.name if target.group is None else f"{target.group}/{target.name}"


def _resolve_host(
    target: Target,
    *,
    hosts: Mapping[str, Host],
    self_name: str | None,
    read_remote: Callable[[str], MachineInventory],
    hosts_file: str,
) -> Resolved | Refused:
    host = target.host
    assert host is not None
    if _is_this_machine(host, self_name):
        return Refused(_NOT_YET)
    if host not in hosts:
        declared = ", ".join(printable_line(name) for name in hosts) or "none"
        shown_file = printable_line(hosts_file, limit=4096)
        return Refused(f"no host named {host} in {shown_file}; declared: {declared}")
    inventory = read_remote(host)
    if inventory.kind == UNREADABLE:
        return Refused(f"{host} could not be read: {_reason(inventory)}")
    note = _docker_note(host, inventory)
    if note is not None:
        return Refused(note)
    matches = _matches(inventory, target)
    if not matches:
        return Refused(f"no environment named {_qualified(target)} on {host}; {_LS_POINTER}")
    if len(matches) > 1:
        forms = _shortest_forms(host, matches, target.name)
        return Refused(
            f"{target.name} matches more than one environment on {host}: {', '.join(forms)};"
            " attach one of those"
        )
    return Resolved(host, matches[0])


def _resolve_bare(
    target: Target,
    *,
    hosts: Mapping[str, Host],
    opted_in: Sequence[str],
    self_name: str | None,
    read_local: Callable[[], MachineInventory],
    read_remote: Callable[[str], MachineInventory],
) -> Resolved | Refused:
    name = target.name
    remote = [host for host in opted_in if host != self_name]
    read: list[tuple[str, MachineInventory]] = [(LOCAL, read_local())]
    read.extend((host, read_remote(host)) for host in remote)

    unread = [(machine, inv) for machine, inv in read if inv.kind == UNREADABLE]
    found = [(machine, _matches(inv, target)) for machine, inv in read if inv.kind == ENVIRONMENTS]
    found = [(machine, matches) for machine, matches in found if matches]

    if unread:
        causes = _and([f"{_label(m)} could not be read ({_reason(inv)})" for m, inv in unread])
        forms = [f"{m}/{name}" for m, _ in found if m != LOCAL]
        forms += [f"{m}/{name}" for m, _ in unread if m != LOCAL and f"{m}/{name}" not in forms]
        forms = forms or [f"{m}/{name}" for m in remote]
        offer = f"name the host, as {_words(forms)}" if forms else f"name a host, as <host>/{name}"
        return Refused(f"{name} cannot be resolved while {causes}; {offer}")

    total = sum(len(matches) for _, matches in found)
    if total == 0:
        return Refused(_not_found_bare(name, read, hosts, remote, self_name))
    if total == 1:
        machine, matches = found[0]
        if machine == LOCAL:
            return Refused(_NOT_YET)
        return Resolved(machine, matches[0])

    forms: list[str] = []
    for machine, matches in found:
        shown = _shortest_forms(machine, matches, name)
        if machine == LOCAL:
            shown = [f"{form} (this machine; cannot be attached yet)" for form in shown]
        forms.extend(shown)
    return Refused(f"{name} matches more than one environment: {', '.join(forms)}; attach one of those")


def _not_found_bare(
    name: str,
    read: Sequence[tuple[str, MachineInventory]],
    hosts: Mapping[str, Host],
    remote: Sequence[str],
    self_name: str | None,
) -> str:
    machines = [_label(machine) for machine, _ in read]
    notes = [note for machine, inv in read if (note := _docker_note(machine, inv))]
    skipped = [h for h in hosts if h not in remote and h != self_name]
    text = f"no environment named {name} on {_words(machines)}"
    for note in notes:
        text += f"; {note}"
    if skipped:
        text += f" (not read: {', '.join(printable_line(h) for h in skipped)}; name one as <host>/{name})"
    return f"{text}; {_LS_POINTER}"


def resolve(
    target: Target,
    *,
    hosts: Mapping[str, Host],
    opted_in: Sequence[str],
    self_name: str | None,
    read_local: Callable[[], MachineInventory],
    read_remote: Callable[[str], MachineInventory],
    hosts_file: str = "the hosts file",
) -> Resolved | Refused:
    """Resolve ``target`` to one environment, reading only the machines the target needs."""
    if target.host is not None:
        return _resolve_host(
            target, hosts=hosts, self_name=self_name, read_remote=read_remote, hosts_file=hosts_file
        )
    return _resolve_bare(
        target,
        hosts=hosts,
        opted_in=opted_in,
        self_name=self_name,
        read_local=read_local,
        read_remote=read_remote,
    )
