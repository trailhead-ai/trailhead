"""``camp env`` — the verb for dev environments running on this machine and on opted-in hosts.

``camp env ls`` reads this machine, then each host ``env_hosts`` names, one after another, and
prints each machine's block as soon as its read finishes. It ignores the current camp group.
"""
from __future__ import annotations

import sys

from ..env.read import (
    DOCKER_NOT_INSTALLED,
    DOCKER_NOT_RUNNING,
    ENVIRONMENTS,
    MachineInventory,
)
from ..printable import printable_line
from .parser import CampParser

_USAGE = "usage: camp env ls"


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


def _cmd_env_ls(args: list[str]) -> None:
    import trailhead.paths as _paths

    from ..env.read import read_local, read_remote
    from ..host.config import (
        HostConfigError,
        connect_timeout_seconds,
        env_host_names,
        load_hosts,
        self_host_name,
    )

    CampParser(verb="env ls").parse_args(args)

    try:
        hosts = load_hosts()
        opted_in = env_host_names()
        self_name = self_host_name()
        connect_timeout = connect_timeout_seconds()
    except HostConfigError as error:
        print(f"camp env: {error}", file=sys.stderr)
        sys.exit(1)
    except (OSError, UnicodeDecodeError, RecursionError) as error:
        hosts_file = _paths.config_dir("camp") / "hosts.toml"
        print(f"camp env: {hosts_file}: cannot read file — {type(error).__name__}", file=sys.stderr)
        sys.exit(1)

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
        hosts_file = _paths.config_dir("camp") / "hosts.toml"
        print(
            # Host-table keys are not charset-checked by the loader, so they pass the
            # same printable-only reduction as far-side text before reaching the terminal.
            f"not opted in: {', '.join(printable_line(name) for name in left_out)} "
            f"(add to env_hosts in {printable_line(str(hosts_file), limit=4096)})",
            flush=True,
        )


def _cmd_env_cli(args: list[str]) -> None:
    """``camp env <subcommand>`` — only ``ls`` exists; anything else prints the usage line."""
    if not args or args[0] != "ls":
        print(_USAGE, file=sys.stderr)
        sys.exit(1)
    _cmd_env_ls(args[1:])
