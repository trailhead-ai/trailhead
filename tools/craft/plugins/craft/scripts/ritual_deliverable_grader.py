#!/usr/bin/env python3
"""Ritual deliverable grader — two independent verdicts over one captured
ritual deliverable, for AC6 and AC7 of
`spec/record-mentions-in-agent-output-are-reachable`.

Usage:
    cat deliverable.txt | ritual_deliverable_grader.py --record <kind/slug> \\
        --command <exact next-command text>

    cat deliverable.txt | ritual_deliverable_grader.py --record <kind/slug> --exempt

The captured deliverable text arrives on stdin. `--record` names the record
the ritual acted on (`kind/slug`, exactly as it would appear as a markdown
link's visible text per the reader plugin's `## Record links` rule).
`--command` names the exact text of the single next command the outcome is
expected to determine; omit it only with `--exempt`, which marks this
outcome as one of AC7's exempt cases — a genuine branch, or a by-design
zero-command close (`task/every-pinned-ritual-links-the-record-it-acted-on-
and-prints-its-one-next-command-on-its-own-line`, `## U3 resolved`, Finding
1) — where no single next command is fabricated to satisfy the shape.

## record-link predicate

`link` if the deliverable contains a markdown link (`[text](target)`) whose
visible text is exactly `--record`'s value. `bare` otherwise — covering a
bare (unlinked) mention of the identifier, no mention at all, and a link
that targets some *other* identifier: a grader that matched any link at all
would be the false-positive mirror of the previous slice's false-negative
grader, so only a link naming the acted-on record counts.

## next-command predicate

`exempt` whenever `--exempt` is passed, regardless of whether any command
text is present — the third verdict Task 1 pinned, never a failure.

Otherwise, `own-line` if some line of the deliverable, stripped of
surrounding whitespace and (if present) one wrapping pair of backticks,
equals `--command`'s value exactly. `embedded` if the command text occurs
somewhere in the deliverable but never alone on its own line this way.
`absent` if the command text does not occur anywhere at all.

## exempt-observation predicate

An `exempt` outcome still passes, but it must never report a verdict about a
deliverable nobody looked at (per the active lesson `a-classification-that-
routes-a-site-around-the-predicate-makes-that-site-unmeasurable`). Printed
only when `--exempt` is passed, alongside the unchanged `next-command:
exempt` line — it never moves the `exempt` verdict or the exit code.

If `--command` was also supplied, this reports the same shape the non-exempt
path would have computed — `own-line`, `embedded`, or `absent` — so an
exempt site whose deliverable actually contains command-shaped text is
observed, not silently passed. If `--command` was omitted (the only
argument shape AC7's genuine branches and by-design zero-command closes
actually have), this reports `not-observed` and says why: there is nothing
to observe a shape against.

Exit codes:
    0  both predicates pass — record-link is `link` and next-command is
       `own-line` or `exempt`. An `exempt-observation:` line (see below) may
       report `embedded` or `absent` on a 0 exit — it is an additive
       observation, never a third predicate the exit code weighs.
    1  at least one predicate fails — record-link is `bare`, or
       next-command is `embedded` or `absent`
    2  could not grade — fail-closed: empty stdin (`reason-code:
       empty-stdin`), non-UTF-8 stdin (`reason-code: invalid-utf8-stdin`),
       `--command` omitted without `--exempt`
       (`reason-code: missing-command`), or a `--command` that is empty or
       only whitespace (`reason-code: empty-command`) — an empty command
       would make the own-line test match any blank line, reporting a pass
       at a deliverable naming no command at all. NEVER exits 0 or 1 without having
       actually read a gradable deliverable. A missing required argument
       (e.g. `--record`) exits 2 via argparse's own usage error instead,
       with no `reason:`/`reason-code:` pair — argparse owns that path
       before this module's own error handling ever runs.
"""

from __future__ import annotations

import argparse
import re
import sys

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

_EMPTY_STDIN_REASON_CODE = "empty-stdin"
_INVALID_UTF8_STDIN_REASON_CODE = "invalid-utf8-stdin"
_MISSING_COMMAND_REASON_CODE = "missing-command"
_EMPTY_COMMAND_REASON_CODE = "empty-command"


def _err(msg: str) -> None:
    print(f"ritual-deliverable-grader: {msg}", file=sys.stderr)


def record_link_verdict(text: str, record: str) -> str:
    """`link` if `record` appears as a markdown link's visible text, else `bare`."""
    for match in _LINK_RE.finditer(text):
        if match.group(1) == record:
            return "link"
    return "bare"


def _command_shape(text: str, command: str) -> str:
    """`own-line`, `embedded`, or `absent` — where `command` occurs in `text`."""
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) >= 2 and stripped[0] == "`" and stripped[-1] == "`":
            stripped = stripped[1:-1]
        if stripped == command:
            return "own-line"
    if command in text:
        return "embedded"
    return "absent"


def next_command_verdict(text: str, command: str | None, *, exempt: bool) -> str:
    """`exempt`, `own-line`, `embedded`, or `absent` — see module docstring."""
    if exempt:
        return "exempt"
    assert command is not None
    return _command_shape(text, command)


def exempt_observation(text: str, command: str | None) -> str:
    """What an exempt outcome's deliverable actually shows, so `exempt` never
    reports a verdict about an artifact nobody looked at. `own-line`,
    `embedded`, or `absent` — same vocabulary as the non-exempt shape — when a
    `--command` was supplied to compare against; `not-observed` with a reason
    when it was not, since there is nothing to observe a shape against."""
    if command is None:
        return "not-observed (no --command was supplied to compare against)"
    return _command_shape(text, command)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ritual_deliverable_grader.py", description=__doc__
    )
    parser.add_argument("--record", required=True, help="kind/slug acted on")
    parser.add_argument(
        "--command", default=None, help="exact next-command text, required unless --exempt"
    )
    parser.add_argument(
        "--exempt",
        action="store_true",
        help="this outcome is one of AC7's exempt branching/zero-command cases",
    )
    args = parser.parse_args(argv)

    if not args.exempt and args.command is None:
        _err("reason: --command is required unless --exempt is set")
        _err(f"reason-code: {_MISSING_COMMAND_REASON_CODE}")
        return 2

    if args.command is not None and not args.command.strip():
        _err("reason: --command is empty or only whitespace, so no command shape")
        _err("reason: can be observed — an empty command matches every blank line")
        _err(f"reason-code: {_EMPTY_COMMAND_REASON_CODE}")
        return 2

    raw = sys.stdin.buffer.read()
    if not raw:
        _err("reason: deliverable on stdin is empty")
        _err(f"reason-code: {_EMPTY_STDIN_REASON_CODE}")
        return 2

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        _err(f"reason: deliverable on stdin is not valid UTF-8: {e}")
        _err(f"reason-code: {_INVALID_UTF8_STDIN_REASON_CODE}")
        return 2

    if not text.strip():
        _err("reason: deliverable on stdin is empty")
        _err(f"reason-code: {_EMPTY_STDIN_REASON_CODE}")
        return 2

    link_verdict = record_link_verdict(text, args.record)
    command_verdict = next_command_verdict(text, args.command, exempt=args.exempt)

    print(f"record-link: {link_verdict}")
    print(f"next-command: {command_verdict}")
    if args.exempt:
        print(f"exempt-observation: {exempt_observation(text, args.command)}")

    ok = link_verdict == "link" and command_verdict in ("own-line", "exempt")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
