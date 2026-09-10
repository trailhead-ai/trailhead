"""Host declaration loader for camp.

Loads the remote-host declaration source from
trailhead.paths.config_dir("camp")/hosts.toml — the bare camp config
directory, the same call cli/common.py's `_groups_dir()` makes before
appending "groups". A hosts file is that directory's first sibling to the
group-config tree.

Schema:
  self_name = "<name>"       # optional; this host's own declared name
  [hosts.<name>]
  ssh = "<ssh destination>"   # optional; defaults to the table key <name>
  camp_bin = "<path or bare command>"  # optional; defaults to "camp"

``self_name`` is reserved as a top-level scalar precisely so it cannot
collide with a per-table remote-host entry under ``[hosts.<name>]`` — the
per-table loader's strict-key allowlist is scoped to keys *inside* a host
table, not to the document's top level. The local machine is never declared
under ``[hosts.<name>]`` — camp still names no remote host of its own there;
``self_name`` is how a host learns its own name.
"""
from __future__ import annotations

import re
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

# `\Z` (not `$`) anchors the END OF STRING: `$` also matches just before a
# trailing newline, which would let a name like "valid\n" slip through and
# defeat the control-character guarantee — see camp.group.resolve's
# _VALID_GROUP_RE, the same precedent.
#
# The leading character is restricted to `[a-z0-9]` (no hyphen) because a
# later slice interpolates this value into an ssh command line: a
# leading-hyphen name (e.g. "-oProxyCommand=...") would be argv injection
# against that command, not just a cosmetic identifier issue.
_VALID_HOST_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\Z")


class HostConfigError(Exception):
    """Raised when hosts.toml exists but is malformed.

    The message always includes the file path and the failing reason.
    """


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


def self_host_name(env: dict[str, str] | None = None) -> str | None:
    """Return this host's own declared name, or None if never declared.

    Reads the reserved top-level scalar ``self_name`` from
    ``config_dir("camp")/hosts.toml``. No file, or a file with no
    ``self_name`` key, returns None — a host that never declares a name is a
    supported configuration. A key that is present but malformed raises
    `HostConfigError`.

    A declared name is not verified for uniqueness across the operator's
    hosts — this is a local, peer-independent read with no network and no
    peer to compare against. Two hosts both declaring the same name make
    every ownership comparison return "mine" on both, silently. That limit
    is inherent to this read path, not a defect in it; detecting an actual
    collision belongs to the first code that has both names in hand.

    Args:
        env: Override os.environ for path resolution (for hermetic tests).
             Defaults to os.environ.

    Raises:
        HostConfigError: If hosts.toml cannot be read, is malformed TOML, or
            is nested deeply enough to exhaust the parser's recursion limit
            (tomllib parses nested inline tables and arrays recursively), or
            `self_name` is present but not a non-empty string in camp's
            strict identifier charset (``^[a-z0-9][a-z0-9-]*$``).
    """
    import trailhead.paths as _paths

    path = _paths.config_dir("camp", env=env) / "hosts.toml"

    if not path.is_file():
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise HostConfigError(f"{path}: cannot read file — {e}") from e

    try:
        raw: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise HostConfigError(f"{path}: TOML parse error — {e}") from e
    except RecursionError as e:
        # tomllib is pure Python and parses nested inline tables (and nested
        # arrays) recursively, so a hosts.toml with a few thousand levels of
        # nesting exhausts the interpreter's recursion limit instead of
        # raising TOMLDecodeError. Scoped to this one call (not a broader
        # `except Exception` around the function) so it cannot also swallow
        # the HostConfigError this function raises itself below.
        raise HostConfigError(f"{path}: TOML parse error — {e}") from e

    if "self_name" not in raw:
        return None

    name = raw["self_name"]

    if not isinstance(name, str):
        raise HostConfigError(
            f"{path}: field 'self_name' must be a string, got {type(name).__name__}"
        )
    if not name:
        raise HostConfigError(f"{path}: field 'self_name' must not be empty")
    if not _VALID_HOST_NAME_RE.match(name):
        raise HostConfigError(
            f"{path}: field 'self_name' {name!r} must start with a lowercase "
            "letter or digit and contain only lowercase letters, digits, "
            "and hyphens (^[a-z0-9][a-z0-9-]*$)"
        )

    return name
