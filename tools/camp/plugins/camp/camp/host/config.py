"""Host declaration loader for camp.

Loads the remote-host declaration source from
trailhead.paths.config_dir("camp")/hosts.toml — the bare camp config
directory, the same call cli/common.py's `_groups_dir()` makes before
appending "groups". A hosts file is that directory's first sibling to the
group-config tree.

Schema:
  [hosts.<name>]
  ssh = "<ssh destination>"   # optional; defaults to the table key <name>
  camp_bin = "<path or bare command>"  # optional; defaults to "camp"

The local machine is never declared here — camp still names no host of its
own.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: camp_bin's documented default when a host table omits it — the bare
#: command name. On the operator's own remote host this is not on the
#: non-interactive PATH, which is the deliberate "camp not resolvable on the
#: host" state rather than a bug in this loader.
_DEFAULT_CAMP_BIN = "camp"

# Keys recognized inside [hosts.<name>]. Anything else is a misconfiguration
# and is rejected at load, mirroring the [launch] block's strict allowlist
# precedent at group/config.py:666-670 — a typo fails loudly rather than
# being silently ignored.
_HOST_KEYS = frozenset({"ssh", "camp_bin"})


class HostConfigError(Exception):
    """Raised when hosts.toml exists but is malformed."""


@dataclass(frozen=True)
class Host:
    """One resolved remote-host declaration."""

    ssh: str
    camp_bin: str


def load_hosts() -> dict[str, Host]:
    """Load and validate hosts.toml, returning the declared hosts by name.

    Reads trailhead.paths.config_dir("camp") / "hosts.toml" — CAMP_CONFIG_DIR
    replaces the whole app dir rather than nesting under it, matching
    trailhead/paths.py's per-app override semantics.

    An absent file (or an absent config directory) returns no hosts rather
    than raising — distinct from an empty file, which also returns no hosts.

    Returns:
        A dict of host name -> Host, in declaration order.

    Raises:
        HostConfigError: If the file exists but fails to parse, declares an
            unknown key inside a host table, or gives a non-string `ssh` or
            `camp_bin`. The message always names the file and the failing
            field.
    """
    import trailhead.paths as _paths

    path = _paths.config_dir("camp") / "hosts.toml"

    if not path.is_file():
        return {}

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise HostConfigError(f"{path}: TOML parse error — {e}") from e

    hosts_raw = raw.get("hosts")
    if hosts_raw is None:
        return {}
    if not isinstance(hosts_raw, dict):
        raise HostConfigError(f"{path}: [hosts] must be a table of host tables")

    hosts: dict[str, Host] = {}
    for name, table in hosts_raw.items():
        hosts[name] = _parse_host(table, path=path, name=name)
    return hosts


def _parse_host(raw: Any, *, path: Path, name: str) -> Host:
    if not isinstance(raw, dict):
        raise HostConfigError(f"{path}: [hosts.{name}] must be a table")

    unknown = sorted(set(raw) - _HOST_KEYS)
    if unknown:
        raise HostConfigError(
            f"{path}: [hosts.{name}] has unknown key(s) {', '.join(unknown)} — "
            f"supported keys: {sorted(_HOST_KEYS)}"
        )

    ssh = raw.get("ssh", name)
    if not isinstance(ssh, str):
        raise HostConfigError(
            f"{path}: hosts.{name}.ssh must be a string, got {type(ssh).__name__!r}"
        )
    if ssh.startswith("-"):
        # transport.py places this value directly into the local `ssh`
        # argv, immediately after the fixed -o options — an ssh value
        # beginning with "-" lands in OPTION position rather than as the
        # destination, so e.g. ssh = "-oProxyCommand=<cmd>" is option
        # injection. Refused at load, before it ever reaches the transport.
        raise HostConfigError(
            f"{path}: hosts.{name}.ssh must not begin with '-' — {ssh!r} "
            "would be read as an ssh option, not a destination"
        )

    camp_bin = raw.get("camp_bin", _DEFAULT_CAMP_BIN)
    if not isinstance(camp_bin, str):
        raise HostConfigError(
            f"{path}: hosts.{name}.camp_bin must be a string, got "
            f"{type(camp_bin).__name__!r}"
        )

    return Host(ssh=ssh, camp_bin=camp_bin)
