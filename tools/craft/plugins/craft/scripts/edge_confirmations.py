#!/usr/bin/env python3
"""Prototype edge-confirmation renderer — resolves whether brainstorm's
edge-checklist maturity-sensitive dimensions should reach the operator as
interrogation branches or as confirmations carrying a default answer.

Usage:
    <maturity block> | edge_confirmations.py

The input is a bare `## Maturity` block — the same
`- <camp member name>: <level>` grammar `maturity_stamp.py`'s own
`parse_entries` already parses, wrapped in its heading and nothing else.
Brainstorm's grill step pipes it the same block its framing step already
resolved; this script does not re-resolve anything.

The grammar is parsed by importing `parse_entries` and `StampError` from
`maturity_stamp.py` — never re-derived — exactly as `maturity_bars.py`
already does.

When every entry resolves `prototype`, the four maturity-sensitive
dimensions of brainstorm's edge checklist (`SKILL.md`'s "2. Grill for
Clarity" bullets — Reversibility, Migration / backfill, Failure
visibility, Blast radius) are suppressed into one confirmation line
apiece. Each line names the dimension and states the concrete default it
assumes, not a bare "defaulted" label, so the operator can judge whether
to reopen it without re-deriving what the default meant. When any entry
resolves to a level other than `prototype`, nothing is suppressed: the
block states so explicitly and every branch opens normally, exactly as it
does today with no renderer in the loop at all.

No refusal ever writes the offending value a `StampError` may carry to
stdout or stderr — the stamp is vault-writable content read by an agent
deciding what to do next. This script's own `RenderError` carries only a
`reason_code`; it is structurally unable to hold the offending text at
all, mirroring `maturity_bars.py`'s `RenderError`.

Stdout on success (exit 0), the block, exactly once.

Exit codes:
    0  resolved — the block is printed, exactly once.
    2  fail-closed — nothing is printed on stdout, and stderr names a
       stable `reason-code:` — either this script's own `empty-stdin` /
       `invalid-utf8-stdin` (parsed here, since `parse_entries` itself
       never raises `empty-stdin`), or any reason-code
       `maturity_stamp.py`'s `parse_entries` raises (section-absent,
       empty-section, malformed-entry, invalid-level, duplicate-member,
       duplicate-section, unresolved-enumeration).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from maturity_stamp import StampError, parse_entries  # noqa: E402

_PROTOTYPE_LEVEL = "prototype"

_EMPTY_STDIN_REASON_CODE = "empty-stdin"
_INVALID_UTF8_STDIN_REASON_CODE = "invalid-utf8-stdin"

# The four maturity-sensitive dimensions of brainstorm's edge checklist
# (`skills/brainstorm/SKILL.md`'s "2. Grill for Clarity" bullets), each
# paired with the concrete default this renderer assumes at `prototype` —
# the spec's own definition of that level (a flag day is acceptable, state
# is disposable, consumers are the operator alone).
_DEFAULTS = (
    (
        "Reversibility",
        "assumed acceptable to flag-day; prototype state is disposable, "
        "so no rollback path is needed.",
    ),
    (
        "Migration / backfill",
        "assumed none; prototype state is disposable, so there is nothing "
        "to migrate.",
    ),
    (
        "Failure visibility",
        "assumed operator-only; the only consumer is the operator running "
        "this themselves.",
    ),
    (
        "Blast radius",
        "assumed confined to the operator; there are no other consumers "
        "to affect.",
    ),
)


class RenderError(Exception):
    """A fail-closed outcome: `reason_code` is always set. Never carries
    the offending text a `StampError` may have named — this script never
    forwards that text to its own caller."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _err(msg: str) -> None:
    print(f"edge-confirmations: {msg}", file=sys.stderr)


def _all_prototype(entries: dict[str, str]) -> bool:
    return bool(entries) and all(level == _PROTOTYPE_LEVEL for level in entries.values())


def render(entries: dict[str, str]) -> str:
    if _all_prototype(entries):
        lines = ["maturity: prototype — edge checklist confirmed, not interrogated"]
        for dimension, default in _DEFAULTS:
            lines.append(f"- {dimension}: {default}")
        return "\n".join(lines) + "\n"

    return (
        "maturity: not every entry is prototype — no dimension suppressed, "
        "every edge branch opens normally.\n"
    )


def resolve_entries(text: str) -> dict[str, str]:
    try:
        return parse_entries(text)
    except StampError as e:
        raise RenderError(e.reason_code) from e


def main(argv: list[str]) -> int:
    del argv  # no flags — the whole interface is stdin

    raw = sys.stdin.buffer.read()
    if not raw:
        _err(f"reason-code: {_EMPTY_STDIN_REASON_CODE}")
        return 2

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _err(f"reason-code: {_INVALID_UTF8_STDIN_REASON_CODE}")
        return 2

    try:
        entries = resolve_entries(text)
    except RenderError as e:
        _err(f"reason-code: {e.reason_code}")
        return 2

    sys.stdout.write(render(entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
