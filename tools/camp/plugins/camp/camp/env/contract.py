"""The contract a project's containers follow to be seen as environments, and the reading of Docker's reply.

The contract
------------
A container counts as an environment when all three hold:

* it carries the label ``camp.env.group`` (``LABEL_GROUP``), whose value is the group;
* it carries the label ``camp.env.name`` (``LABEL_NAME``), whose value is the environment's name;
* its container name is ``<group>-env-<name>`` (``container_name``).

Group and name are each camp's identifier: ``^[a-z0-9][a-z0-9-]*\\Z``, matched with ``fullmatch``
so a trailing newline never passes. A container carrying both labels under any other name is
skipped without comment; the name rule is how a project says which of its labelled containers is
the environment and which are helpers beside it.

The inventory command
---------------------
Every machine is read with one Docker command, defined once as ``INVENTORY_ARGV`` (run directly
as a subprocess for this machine) and rendered by ``shlex.join`` into ``INVENTORY_REMOTE_COMMAND``
(one plain command string sent over ssh). The string uses single quotes only, with no backslash and
no control character, so a fish login shell on the far side runs it unchanged. Two ``--filter
label=`` flags are ANDed by Docker, so only containers carrying both labels are listed. ``table``
format makes Docker print a header line (``INVENTORY_HEADER``) even when nothing matches, and each
field is passed through ``json`` so a value carrying a newline, tab or escape arrives as an escape
sequence and can neither span nor forge a line.

Reading a reply
---------------
``parse_inventory`` believes a reply only when its first line is ``INVENTORY_HEADER`` and every
following line decodes as exactly four JSON strings separated by ``|`` (container name, state,
group, name). Anything else raises ``Unreadable`` carrying a one-line, printable reason; a partial
list is never returned. ``has_inventory_header`` lets a caller classify a reply as "has the header
or not" before parsing.

A raw ``|`` survives inside a JSON string, so a record line is never split on ``|``: it is decoded
field by field with ``json.JSONDecoder.raw_decode``, each decoded value required to be a string
(a number, null, object or array, however deeply nested, is unreadable). A group or name carrying a ``|``
therefore stays one field and then fails the identifier charset. DEL (0x7f) and non-ASCII
characters arrive unescaped and also fail the charset. Every Docker state other than ``running``
(created, exited, paused, restarting, dead, removing) reads as ``stopped``.

Untrusted input
---------------
The reply is untrusted text: for a remote host it is whatever the far side printed. The reader
never evaluates, formats or echoes a field's content into the reason (a reason names the line
number and the kind of defect, never the raw value), never raises anything but ``Unreadable``, and
decides nothing from an exit status.

Named residual
--------------
The reply carries no end marker. A far side that truncates its own output cleanly between two
records reads as a shorter list. A dropped connection is ssh's own failure and is caught
elsewhere; this gap is a far side that cuts its own output.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Literal

LABEL_GROUP = "camp.env.group"
LABEL_NAME = "camp.env.name"

INVENTORY_HEADER = "NAMES|STATE|group|name"

_FORMAT = "table " + "|".join(
    [
        "{{json .Names}}",
        "{{json .State}}",
        f'{{{{json (.Label "{LABEL_GROUP}")}}}}',
        f'{{{{json (.Label "{LABEL_NAME}")}}}}',
    ]
)

INVENTORY_ARGV: tuple[str, ...] = (
    "docker",
    "ps",
    "-a",
    "--filter",
    f"label={LABEL_GROUP}",
    "--filter",
    f"label={LABEL_NAME}",
    "--format",
    _FORMAT,
)

INVENTORY_REMOTE_COMMAND: str = shlex.join(INVENTORY_ARGV)

_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_DECODER = json.JSONDecoder()
_FIELD_COUNT = 4


class Unreadable(Exception):
    """A reply that will not be believed; ``str()`` is a one-line, printable reason."""


@dataclass(frozen=True)
class Environment:
    group: str
    name: str
    state: Literal["running", "stopped"]


def container_name(group: str, name: str) -> str:
    return f"{group}-env-{name}"


def has_inventory_header(text: str) -> bool:
    """True when ``text``'s first line is exactly ``INVENTORY_HEADER``."""
    return text.split("\n", 1)[0] == INVENTORY_HEADER


def _decode_record(line: str) -> list[str] | None:
    fields: list[str] = []
    pos = 0
    while True:
        try:
            value, pos = _DECODER.raw_decode(line, pos)
        except (ValueError, RecursionError):
            return None
        if not isinstance(value, str):
            return None
        fields.append(value)
        if len(fields) == _FIELD_COUNT:
            return fields if pos == len(line) else None
        if pos >= len(line) or line[pos] != "|":
            return None
        pos += 1


def _is_identifier(value: str) -> bool:
    return _IDENTIFIER_RE.fullmatch(value) is not None


def parse_inventory(text: str) -> list[Environment]:
    """Parse a reply to the inventory command into environments, or raise ``Unreadable``."""
    if not has_inventory_header(text):
        raise Unreadable("reply does not start with the expected header line")
    lines = text.split("\n")[1:]
    if lines and lines[-1] == "":
        lines.pop()
    environments: list[Environment] = []
    for number, line in enumerate(lines, start=2):
        fields = _decode_record(line)
        if fields is None:
            raise Unreadable(f"line {number}: not four JSON strings separated by '|'")
        container, state, group, name = fields
        if not _is_identifier(group):
            raise Unreadable(f"line {number}: group is not a valid identifier")
        if not _is_identifier(name):
            raise Unreadable(f"line {number}: name is not a valid identifier")
        if container != container_name(group, name):
            continue
        environments.append(Environment(group, name, "running" if state == "running" else "stopped"))
    return environments
