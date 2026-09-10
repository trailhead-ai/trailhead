"""This host's own declared name for camp — read from
``config_dir("camp")/hosts.toml`` under the reserved top-level scalar key
``self_name``.

``hosts.toml`` is deliberately the same file the sibling remote-host
declaration loader (also `camp.host.config`) reads its per-table
``[hosts.<name>]`` entries from. ``self_name`` is reserved as a top-level
scalar precisely so it cannot collide with a per-table remote-host entry —
that loader's strict-key allowlist is scoped to keys *inside* a host table,
not to the document's top level.

Absence at every level is a supported configuration, not an error: no file,
or a file with no ``self_name`` key, means this host has never declared a
name, and `self_host_name` returns ``None``. A key that IS present but
malformed — non-string, empty, or outside the strict identifier charset — is
an error: raised as `HostConfigError`, carrying the file path and reason,
never as a raw traceback.

The name is validated against camp's strict identifier charset (the same
``\\Z``-anchored pattern as `camp.group.resolve.validate_group_name`, not the
permissive workspace-slug validator) because a later slice interpolates this
value into an ssh command line — validating at the declaration site is what
keeps that from being a shell-injection surface. ``\\Z`` (not ``$``) anchors
the true end of string; ``$`` also matches just before a trailing newline,
which would let ``"valid\\n"`` slip through undetected.

A declared name is not verified for uniqueness across the operator's hosts —
this is a local, peer-independent read with no network and no peer to compare
against. Two hosts both declaring the same name make every ownership
comparison return "mine" on both, silently. That limit is inherent to this
read path, not a defect in it; detecting an actual collision belongs to the
first code that has both names in hand.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any


class HostConfigError(Exception):
    """Raised when hosts.toml exists but is malformed.

    The message always includes the file path and the failing reason.
    """


# `\Z` (not `$`) anchors the END OF STRING: `$` also matches just before a
# trailing newline, which would let a name like "valid\n" slip through and
# defeat the control-character guarantee — see camp.group.resolve's
# _VALID_GROUP_RE, the same precedent.
_VALID_HOST_NAME_RE = re.compile(r"^[a-z0-9-]+\Z")


def self_host_name(env: dict[str, str] | None = None) -> str | None:
    """Return this host's own declared name, or None if never declared.

    Reads the reserved top-level scalar ``self_name`` from
    ``config_dir("camp")/hosts.toml``. No file, or a file with no
    ``self_name`` key, returns None — a host that never declares a name is a
    supported configuration. A key that is present but malformed raises
    `HostConfigError`.

    Args:
        env: Override os.environ for path resolution (for hermetic tests).
             Defaults to os.environ.

    Raises:
        HostConfigError: If hosts.toml is malformed TOML, or `self_name` is
            present but not a non-empty string in camp's strict identifier
            charset (``^[a-z0-9-]+$``).
    """
    import trailhead.paths as _paths

    path = _paths.config_dir("camp", env=env) / "hosts.toml"

    if not path.is_file():
        return None

    try:
        raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
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
            f"{path}: field 'self_name' {name!r} must contain only lowercase "
            "letters, digits, and hyphens (^[a-z0-9-]+$)"
        )

    return name
