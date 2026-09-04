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
the left margin and a `###` section an indented one — the documents carry two
heading levels, so any indent means "nested" and the exact width, spaces or
tabs, does not matter. The sequence must match the document's own headings
exactly, in order and in count: two sections may share a title, but then the
block names that title twice.

Structure is read with `covers_gate._mask_fenced_lines`, the masker the sibling
gates already share, so all three agree on what counts as a fence, an HTML
comment, and a line ending. Headings and markers inside either are template
text and illustrations, not sections. A fence or comment still open at end of
file makes every line below it unclassifiable, so the gate fails closed rather
than guessing which side of it they fall on.

Exit codes:
    0  clean — block and headings agree
    1  drift — they disagree (prints a `reason:` line per disagreement)
    2  error — fail-closed: path missing or unreadable, no contents block, more
       than one contents block, an unterminated contents block, an unterminated
       fence or comment, or an empty block. NEVER exits 0 when it could not
       actually certify the document.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from covers_gate import _COMMONMARK_LINE_RE, _mask_fenced_lines  # noqa: E402

TOC_START = "<!-- toc:start -->"
TOC_END = "<!-- toc:end -->"

# Up to three leading spaces still render as a heading, and a closing run of
# `#` is decoration rather than part of the title.
_HEADING_RE = re.compile(r"^ {0,3}(#{2,3})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_ENTRY_RE = re.compile(r"^([ \t]*)- (.+?)[ \t]*$")

# A marker line stands in as ordinary text while the shared masker runs, so the
# masker reports whether it sits inside a fence rather than that it is itself a
# comment. It closes nothing, so substituting it changes no other line's state.
_MARKER_STANDIN = "toc-marker-standin"
_EOF_PROBE = "toc-eof-probe"


class GateError(Exception):
    """The document could not be certified either way."""


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _read_structure(text: str) -> tuple[list[str], list[bool], list[bool]]:
    """Split into lines and mask them twice: as written, and with the markers
    standing in as prose so the masker speaks to whether they are fenced."""
    lines = _COMMONMARK_LINE_RE.split(text)
    probe = [_MARKER_STANDIN if ln.strip() in (TOC_START, TOC_END) else ln for ln in lines]
    if _mask_fenced_lines(probe + [_EOF_PROBE])[-1]:
        raise GateError("unterminated code fence or HTML comment at end of file")
    return lines, _mask_fenced_lines(lines), _mask_fenced_lines(probe)


def headings(lines: list[str], masked: list[bool]) -> list[tuple[int, str]]:
    """The document's own sections, as (level, title)."""
    found = []
    for line, hidden in zip(lines, masked):
        if hidden:
            continue
        m = _HEADING_RE.match(line)
        if m:
            found.append((len(m.group(1)), m.group(2).strip()))
    return found


def entries(
    lines: list[str], masked: list[bool], marker_masked: list[bool]
) -> list[tuple[int, str]]:
    """The contents block's entries, as (level, title)."""
    starts = [i for i, ln in enumerate(lines) if ln.strip() == TOC_START and not marker_masked[i]]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == TOC_END and not marker_masked[i]]
    if not starts:
        raise GateError(f"no contents block — expected a {TOC_START} marker in live prose")
    if len(starts) > 1:
        raise GateError(
            f"a second contents block opens at line {starts[1] + 1} — a document has one"
        )
    start = starts[0]
    after = [i for i in ends if i > start]
    if not after:
        raise GateError(f"unterminated contents block — {TOC_START} with no {TOC_END}")
    end = after[0]

    found = []
    for i in range(start + 1, end):
        if masked[i]:
            continue
        m = _ENTRY_RE.match(lines[i])
        if m:
            found.append((3 if m.group(1) else 2, m.group(2).strip()))
    if not found:
        raise GateError("empty contents block — it names no sections")
    return found


def disagreements(want: list[tuple[int, str]], got: list[tuple[int, str]]) -> list[str]:
    """Every way the block and the headings fail to line up, most specific first.

    Counted, not merely set-compared: a document that grows a second section
    sharing an existing title needs a second entry, and membership alone cannot
    see that.
    """
    reasons = []
    want_counts = Counter(t for _, t in want)
    got_counts = Counter(t for _, t in got)
    for title in dict.fromkeys(t for _, t in want):
        short = want_counts[title] - got_counts[title]
        if short > 0:
            reasons.append(
                f"the document has {want_counts[title]} section(s) titled {title!r} but the "
                f"contents block names it {got_counts[title]} time(s)"
            )
    for title in dict.fromkeys(t for _, t in got):
        if got_counts[title] - want_counts[title] > 0:
            reasons.append(
                f"the contents block names {title!r} {got_counts[title]} time(s) but the "
                f"document has {want_counts[title]} section(s) with that title"
            )
    if reasons:
        return reasons
    # Equal multisets, so equal lengths: a positional walk covers the rest.
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
    lines, masked, marker_masked = _read_structure(text)
    return disagreements(headings(lines, masked), entries(lines, masked, marker_masked))


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
