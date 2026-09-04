#!/usr/bin/env python3
"""Contents-block gate: does a document's table of contents still match it?

A long shared document opens with a marker-fenced contents block so a reader can
reach the section that binds them without loading the whole file. That aid is
only worth having while it is accurate — a block naming a section the document
no longer has sends the reader somewhere that is not there, which is worse than
no block at all. This gate binds the two halves of the document to each other.

It asserts no phrase. Every heading may be reworded freely and the gate stays
green, provided the block is reworded with it; the only failure is disagreement.

Usage:
    toc_gate.py <markdown-path> [<markdown-path> ...]

Block shape:
    <!-- toc:start -->
    **Contents**

    - A level-2 section
      - A level-3 section under it
    <!-- toc:end -->

Entries are plain bullets, never headings: an entry written as a heading would
become a section of the document it is describing. A `##` section is a bullet at
indent 0, a `###` section a bullet at indent 2; the sequence must match the
document's own headings exactly, in order.

Headings and markers inside fenced code blocks or HTML comments are template
text and illustrations, not sections — they are excluded from both halves. An
unterminated fence makes every heading below it unclassifiable, so the gate
fails closed rather than guessing which side of it they fall on.

Exit codes:
    0  clean — block and headings agree
    1  drift — they disagree (prints a `reason:` line per disagreement)
    2  error — fail-closed: path missing or unreadable, no contents block, an
       unterminated contents block or code fence, or an empty block. NEVER
       exits 0 when it could not actually certify the document.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TOC_START = "<!-- toc:start -->"
TOC_END = "<!-- toc:end -->"

_HEADING_RE = re.compile(r"^(#{2,3}) (.+)$")
_ENTRY_RE = re.compile(r"^( *)- (.+)$")
_INDENT_PER_LEVEL = 2


class GateError(Exception):
    """The document could not be certified either way."""


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _live_lines(text: str) -> list[tuple[int, str, bool]]:
    """Classify every line as live prose or fenced/commented-out text.

    Returns (line-number, line, is_live). Lines split on "\\n" only: U+2028 and
    friends end a line for some renderers but are not line breaks here, so a
    heading forged after one is never seen as a heading.
    """
    out: list[tuple[int, str, bool]] = []
    fenced = False
    commented = False
    for lineno, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if not commented and stripped.startswith("```"):
            fenced = not fenced
            out.append((lineno, line, False))
            continue
        live = not fenced and not commented
        if live and "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
            commented = True
        elif commented and "-->" in line:
            commented = False
            live = False
        out.append((lineno, line, live))
    if fenced:
        raise GateError("unterminated code fence — cannot classify the headings below it")
    if commented:
        raise GateError("unterminated HTML comment — cannot classify the lines below it")
    return out


def headings(lines: list[tuple[int, str, bool]]) -> list[tuple[int, str]]:
    """The document's own sections, as (level, title)."""
    found = []
    for _lineno, line, live in lines:
        if not live:
            continue
        m = _HEADING_RE.match(line)
        if m:
            found.append((len(m.group(1)), m.group(2).strip()))
    return found


def entries(lines: list[tuple[int, str, bool]]) -> list[tuple[int, str]]:
    """The contents block's entries, as (level, title)."""
    start = end = None
    for lineno, line, live in lines:
        if not live:
            continue
        if line.strip() == TOC_START and start is None:
            start = lineno
        elif line.strip() == TOC_END and start is not None and end is None:
            end = lineno
    if start is None:
        raise GateError(f"no contents block — expected a {TOC_START} marker in live prose")
    if end is None:
        raise GateError(f"unterminated contents block — {TOC_START} with no {TOC_END}")

    found = []
    for lineno, line, live in lines:
        if not (start < lineno < end and live):
            continue
        m = _ENTRY_RE.match(line)
        if m:
            indent = len(m.group(1))
            found.append((2 + indent // _INDENT_PER_LEVEL, m.group(2).strip()))
    if not found:
        raise GateError("empty contents block — it names no sections")
    return found


def disagreements(want: list[tuple[int, str]], got: list[tuple[int, str]]) -> list[str]:
    """Every way the block and the headings fail to line up, most specific first."""
    reasons = []
    want_titles = [t for _, t in want]
    got_titles = [t for _, t in got]
    for level, title in want:
        if title not in got_titles:
            reasons.append(f"section {title!r} (h{level}) has no entry in the contents block")
    for level, title in got:
        if title not in want_titles:
            reasons.append(f"entry {title!r} names no section in the document")
    if not reasons:
        for i, (w, g) in enumerate(zip(want, got)):
            if w == g:
                continue
            if w[1] != g[1]:
                reasons.append(
                    f"entry {i + 1} is {g[1]!r} but section {i + 1} is {w[1]!r} — "
                    "the block is out of order"
                )
            else:
                reasons.append(
                    f"entry {g[1]!r} is nested as a level-{g[0]} section but the "
                    f"document declares it at level {w[0]}"
                )
    return reasons


def check(path: Path) -> list[str]:
    """Reasons this document fails. Empty means clean. Raises GateError to fail closed."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GateError(f"path does not exist: {path}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise GateError(f"cannot read {path}: {e}") from None
    lines = _live_lines(text)
    return disagreements(headings(lines), entries(lines))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    errored = drifted = False
    for path in args.paths:
        try:
            reasons = check(path)
        except GateError as e:
            _err(f"{path}: error: {e}")
            errored = True
            continue
        for reason in reasons:
            _err(f"{path}: reason: {reason}")
            drifted = True
    if errored:
        _err("failing closed — a document could not be certified either way")
        return 2
    return 1 if drifted else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
