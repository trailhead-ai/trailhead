#!/usr/bin/env python3
"""Maturity calibration-block renderer — resolves exactly one maturity
level for a spec under review and emits the calibration block the council
dispatch substitutes.

Usage:
    lore record show spec/<name> | maturity_bars.py [--agent-instruction-file <path>]

The spec body arrives on stdin, mirroring the sibling gates
(`maturity_stamp.py`, `maturity_resolve.py`). `--agent-instruction-file`
names the repository's agent-instruction file (e.g. `CLAUDE.md`), consulted
only when the spec carries no `## Maturity` section at all.

Every primitive here is imported from a sibling script rather than
re-derived: `parse_entries` and `StampError` come from `maturity_stamp.py`
(the stamp grammar), `resolve` and the closed level vocabulary `LEVELS` come
from `maturity_resolve.py` (the agent-instruction-file declaration and its
own `production` default).

Resolution order, and the basis reported alongside the resolved level:

    stamp                  a `## Maturity` section naming exactly one
                            repository — that repository's declared level.
    highest-stamped         a section naming more than one repository — the
                            highest level among them. This is the spec's own
                            sanctioned fallback for a finding no repository
                            can be attributed to; until per-finding
                            attribution ships, every finding under a
                            multi-repository stamp is exactly that, never a
                            general highest-wins rule. The emitted block
                            says so.
    agent-instruction-file  no `## Maturity` section at all (including empty
                            stdin, a legitimate no-spec case rather than a
                            refusal), with `--agent-instruction-file` given —
                            resolved via `maturity_resolve.resolve()` against
                            that file's `## Project Maturity` declaration,
                            whatever `resolve()` itself reports as ITS
                            reason (declared / absent / invalid / ambiguous
                            all read as basis `agent-instruction-file` here,
                            since a file was consulted).
    default                 no section and no `--agent-instruction-file` —
                            `production`.

Every other stamp violation refuses, carrying the reason-code
`maturity_stamp.py` itself raises rather than a second vocabulary. A
missing agent-instruction file, an unreadable one, or a path naming a
directory is a distinct case this renderer owns (the resolver's own
contract punts "no agent-instruction file at all" to its caller): reason-
code `agent-instruction-file-unreadable`. Stdin that does not decode as
UTF-8 is unreadable input rather than an absent section, and refuses with
`maturity_stamp.py`'s own `invalid-utf8-stdin` reason-code: only a spec
that carries no `## Maturity` section falls through to the agent-
instruction file.

No refusal ever writes the offending value carried by a `StampError` to
stdout or stderr: the stamp is vault-writable content and this refusal is
read by an agent deciding what to do next, with no isolation between the
two. See `task/the-offending-value-echo-is-an-unclosed-prompt-injection-
channel` — the same open channel `maturity_stamp.py` itself narrows but does
not close for ITS caller; this renderer's caller gets the reason-code alone.

Stdout on success (exit 0), the calibration block: the resolved level and
its basis, the five maturity-sensitive concerns each rated at the severity
the resolved level maps to (Critical at `production`, Important at `early`,
Minor at `prototype`), and the standing instruction that a mapped concern is
reported at that severity and never filtered out.

Exit codes:
    0  resolved — a calibration block is printed, exactly once.
    2  fail-closed — no calibration block is printed, and stderr names a
       stable `reason-code:` — either a `maturity_stamp.py` reason-code
       (section-absent is not fail-closed here; the other seven are), or
       this renderer's own `agent-instruction-file-unreadable`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from maturity_resolve import LEVELS, resolve  # noqa: E402
from maturity_stamp import StampError, parse_entries  # noqa: E402

_CONCERNS = (
    "backwards compatibility",
    "migration and backfill",
    "rollback and reversibility",
    "production failure visibility",
    "cross-consumer blast radius",
)

_SEVERITY_BY_LEVEL = {
    "production": "Critical",
    "early": "Important",
    "prototype": "Minor",
}

_DEFAULT_LEVEL = "production"

_AGENT_FILE_UNREADABLE_REASON_CODE = "agent-instruction-file-unreadable"
_INVALID_UTF8_STDIN_REASON_CODE = "invalid-utf8-stdin"

_SECTION_ABSENT_REASON_CODE = "section-absent"


def _err(msg: str) -> None:
    print(f"maturity-bars: {msg}", file=sys.stderr)


class RenderError(Exception):
    """A fail-closed outcome: `reason_code` is always set. Never carries the
    offending text a `StampError` may have named — this renderer never
    forwards that text to its own caller."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _highest_level(levels) -> str:
    return max(levels, key=LEVELS.index)


def _resolve_from_agent_instruction_file(path_str: str) -> tuple[str, str]:
    path = Path(path_str)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise RenderError(_AGENT_FILE_UNREADABLE_REASON_CODE) from e
    level, _reason, _offending = resolve(text)
    return level, "agent-instruction-file"


def resolve_level(spec_text: str, agent_instruction_file: str | None) -> tuple[str, str]:
    """Return `(level, basis)` for the spec under review, or raise
    `RenderError` on any fail-closed outcome."""
    try:
        entries = parse_entries(spec_text)
    except StampError as e:
        if e.reason_code != _SECTION_ABSENT_REASON_CODE:
            raise RenderError(e.reason_code) from e
    else:
        if len(entries) == 1:
            return next(iter(entries.values())), "stamp"
        return _highest_level(entries.values()), "highest-stamped"

    if agent_instruction_file is not None:
        return _resolve_from_agent_instruction_file(agent_instruction_file)

    return _DEFAULT_LEVEL, "default"


def render(level: str, basis: str) -> str:
    lines = [f"maturity: {level} (basis: {basis})"]
    if basis == "highest-stamped":
        lines.append(
            "highest-stamped is the sanctioned fallback for a finding no "
            "repository can be attributed to — not a general highest-wins rule."
        )
    lines.append("")
    severity = _SEVERITY_BY_LEVEL[level]
    for concern in _CONCERNS:
        lines.append(f"- {concern}: {severity}")
    lines.append("")
    lines.append(
        "Every concern above is reported at its mapped severity and is "
        "never filtered out."
    )
    lines.append(
        "Where a concern above also appears in your per-lens Critical bars, "
        "the severity above governs — the bars say what to look for, this "
        "block says how severely to rate it."
    )
    if level != _DEFAULT_LEVEL:
        lines.append(
            f"A finding downgraded by this calibration restates the concern "
            f"and the deciding level in its own text (for example "
            f"\"migration and backfill — {_SEVERITY_BY_LEVEL[level]}, "
            f"downgraded by this spec's {level} maturity level\"), so the "
            f"operator can tell a calibrated downgrade from noise and has "
            f"something concrete to override."
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent-instruction-file")
    args = parser.parse_args(argv)

    raw = sys.stdin.buffer.read()

    try:
        try:
            spec_text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise RenderError(_INVALID_UTF8_STDIN_REASON_CODE) from e
        level, basis = resolve_level(spec_text, args.agent_instruction_file)
    except RenderError as e:
        _err(f"reason-code: {e.reason_code}")
        return 2

    sys.stdout.write(render(level, basis))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
