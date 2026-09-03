#!/usr/bin/env python3
"""Covers gate — certifies a drafted `--covers` identifier list against a
spec's declared acceptance criteria, BEFORE a slice parent record is written.

Usage:
    lore record show spec/<name> | covers_gate.py --covers "AC2, AC5"

The spec body arrives on stdin; the drafted identifier list is a `--covers`
argument composed by the caller from identifiers alone, never from vault
prose. The gate reads no parent record and writes no temp file — it runs
while the parent body is still a string in hand.

The `--covers` grammar is exact and identifier-only: one or more `ACn`
tokens, comma-separated, nothing else. This is what discharges "copies no
criterion prose" — prose cannot enter a field whose grammar admits only
identifiers — so no similarity heuristic appears anywhere in this gate.

One parser, `parse_criteria`, turns a spec body into its ordered list of
criterion identifiers: top-level `- ` bullets under a `## Acceptance
Criteria` heading, each prefixed `**ACn.**`. A `###` sub-heading groups
criteria without being one; a nested sub-bullet qualifies its parent rather
than forming its own criterion. The heading is anchored at line start, so an
inline mid-paragraph mention of `## Acceptance Criteria` does not satisfy it.

Exit codes:
    0  clean   — every covered identifier names a real spec criterion, and
       the `--covers` grammar is valid.
    1  violation — bad grammar, an unknown identifier, or a duplicate
       identifier (prints a `reason:` line to stderr).
    2  error   — fail-closed: empty or non-UTF-8 stdin, no `## Acceptance
       Criteria` heading in the spec body, a spec declaring zero criterion
       identifiers under that heading (the legacy shape, distinct reason
       line), or a missing `--covers` argument. NEVER exits 0 when it could
       not actually certify the drafted list.
"""

from __future__ import annotations

import argparse
import re
import sys

_AC_HEADING = "## Acceptance Criteria"
_CRITERION_RE = re.compile(r"^-\s+\*\*(AC\d+)\.\*\*")
_COVERS_RE = re.compile(r"\AAC\d+(,\ ?AC\d+)*\Z")


def _err(msg: str) -> None:
    print(f"covers-gate: {msg}", file=sys.stderr)


def parse_criteria(spec_body: str) -> list[str]:
    """Return the ordered criterion identifiers declared under the spec's
    top-level `## Acceptance Criteria` heading. Raises ValueError if that
    heading is not present at line start.

    This is the one parser: a `###` sub-heading is skipped as a grouping
    marker, and an indented sub-bullet is skipped as a qualifier of its
    parent criterion rather than a criterion of its own.
    """
    lines = spec_body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == _AC_HEADING:
            start = i + 1
            break
    if start is None:
        raise ValueError(f"spec body has no {_AC_HEADING!r} heading")

    identifiers: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            m = _CRITERION_RE.match(line)
            if m:
                identifiers.append(m.group(1))
    return identifiers


def parse_covers(value: str) -> list[str]:
    """Parse the `--covers` grammar: one or more `ACn` tokens separated by
    `,` or `, `, nothing else. Raises ValueError naming the reason on any
    violation, including a duplicate identifier."""
    if not value:
        raise ValueError("--covers value is empty")
    if not _COVERS_RE.match(value):
        raise ValueError(f"--covers value is not a valid identifier list: {value!r}")
    identifiers = [t.lstrip(" ") for t in value.split(",")]
    seen: set[str] = set()
    for identifier in identifiers:
        if identifier in seen:
            raise ValueError(f"--covers value repeats identifier {identifier!r}")
        seen.add(identifier)
    return identifiers


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Certify a drafted --covers identifier list against a spec's acceptance criteria."
    )
    ap.add_argument("--covers", required=True, help="comma-separated ACn identifiers, e.g. 'AC2, AC5'")
    args = ap.parse_args(argv)

    try:
        spec_body = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError as e:
        _err(f"spec body on stdin is not valid UTF-8: {e}")
        return 2
    if not spec_body.strip():
        _err("spec body on stdin is empty")
        return 2

    try:
        criteria = parse_criteria(spec_body)
    except ValueError as e:
        _err(str(e))
        _err("failing closed — cannot certify membership without a criteria list")
        return 2

    if not criteria:
        _err(
            "reason: spec declares no criterion identifiers under "
            f"{_AC_HEADING!r} — this is the legacy shape a spec predating the "
            "**ACn.** convention carries"
        )
        return 2

    try:
        covers = parse_covers(args.covers)
    except ValueError as e:
        _err(f"reason: {e}")
        return 1

    unknown = [c for c in covers if c not in criteria]
    if unknown:
        _err(f"reason: unknown criterion identifier(s): {', '.join(unknown)}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
