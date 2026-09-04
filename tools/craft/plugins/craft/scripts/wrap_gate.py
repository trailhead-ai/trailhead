#!/usr/bin/env python3
"""Wrap gate — is a document's prose wrapped at one consistent column?

A reflow tool is only worth having if the tree it produces stays reflowed: an
editor who touches one paragraph and does not re-run the formatter should not
be able to leave the file in a state the formatter itself would never
produce. This gate is that guarantee, expressed as two rules over the
unmasked prose lines of a document:

- **Over budget** — a line longer than the column budget. A bare "no line
  over N" check, though, cannot tell a document wrapped at 100 apart from
  one wrapped at 70 — both pass — so a second rule pins the *lower* bound:
- **Under-filled** — a line followed, without an intervening blank, by
  another line of the same block, whose length plus one space plus that
  successor's first unit would still have fit inside the budget. A line that
  stops short when it did not have to is evidence the file was hand-wrapped
  rather than run through the formatter, and only the under-fill rule can
  see that; a document can satisfy "no line over N" while wrapped at any
  narrower column, or at no column at all.

**The atomic unit is a word or a whole inline code span, never a bare
whitespace token.** Prose here carries backtick spans containing literal
spaces — commands a reader copies and runs verbatim, or credential-scrub
patterns applied literally — and a tokenizer that splits on whitespace alone
would break one of those across two lines. Both rules measure in units, not
words: an inline code span survives as one atomic span from its opening
backtick run to the matching closing run of the same length, exactly as it
renders.

Exempt from both rules, because no wrap can fix them or wrapping them would
change meaning: a masked line (inside a fenced code block or an HTML
comment, via the same `covers_gate._mask_fenced_lines` the sibling gates
share), an ATX heading, a table row, a line whose own longest unit exceeds
the budget on its own (an unbreakable long word, path, URL, or code span —
wrapping cannot help a line whose problem is one token, however much
breakable text sits beside it), and a line ending in a hard line break (two
or more trailing spaces, or a trailing backslash) — reflowing across a hard
break changes what the document renders.

Block structure is measured, not assumed: a "block" is a maximal run of
consecutive prose lines with no blank line, masked line, heading, or table
row between them. A wrapped list item's continuation lines carry their own
hanging indent as literal characters, so measuring each line's raw length
(indent included) against the same absolute column budget is what "measured
at its indented width" means — no separate list-marker accounting is needed.

Usage:
    wrap_gate.py [--column N] <markdown-path> [<markdown-path> ...]

Exit codes:
    0  clean   — every prose line is within budget and fully filled
    1  finding — an over-budget or under-filled line (prints a `reason:`
       line per finding, naming the file, the line number, which rule
       fired, and the `wrap_prose.py` remedy)
    2  error   — fail-closed: a path is missing or unreadable, or its
       content is not valid UTF-8. NEVER exits 0 when it could not actually
       certify the document.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from covers_gate import _COMMONMARK_LINE_RE, _mask_fenced_lines  # noqa: E402

DEFAULT_COLUMN = 100
REMEDY = "run wrap_prose.py to reflow this file"

# Up to three leading spaces still render as a heading (CommonMark).
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+.*)?[ \t]*$")
_HARD_BREAK_RE = re.compile(r"(?: {2,}|\\)$")


class GateError(Exception):
    """The document could not be certified either way."""


class Finding(NamedTuple):
    line: int
    rule: str  # "over-budget" | "under-filled"
    message: str


def _scan_code_span(line: str, start: int) -> int | None:
    """If a valid inline code span opens at `start` (a run of backticks),
    return the index just past its matching closing run of the same length.
    Returns None if no closing run of that exact length exists on the line —
    an unterminated backtick run is not a span, per CommonMark."""
    n = len(line)
    i = start
    while i < n and line[i] == "`":
        i += 1
    run_len = i - start
    j = i
    while j < n:
        k = line.find("`", j)
        if k == -1:
            return None
        m = k
        while m < n and line[m] == "`":
            m += 1
        if m - k == run_len:
            return m
        j = m
    return None


def _tokenize(line: str) -> list[str]:
    """Split a line into its atomic units: a maximal run of non-whitespace
    characters, except that a valid inline code span (backtick-delimited,
    matched opening/closing run length) is kept whole as one unit even
    though it may contain internal spaces."""
    tokens: list[str] = []
    i, n = 0, len(line)
    while i < n:
        if line[i].isspace():
            i += 1
            continue
        if line[i] == "`":
            end = _scan_code_span(line, i)
            if end is not None:
                tokens.append(line[i:end])
                i = end
                continue
        j = i
        while j < n and not line[j].isspace():
            j += 1
        tokens.append(line[i:j])
        i = j
    return tokens


def _strip_code_spans(line: str) -> str:
    """`line` with every valid inline code span replaced by a placeholder
    containing no `|` — used only to keep a pipe inside a code span from
    being mistaken for table syntax."""
    out = []
    i, n = 0, len(line)
    while i < n:
        if line[i] == "`":
            end = _scan_code_span(line, i)
            if end is not None:
                out.append("x" * (end - i))
                i = end
                continue
        out.append(line[i])
        i += 1
    return "".join(out)


def _classify(line: str) -> str:
    """One of 'blank', 'heading', 'table', 'prose' for an unmasked line."""
    if not line.strip():
        return "blank"
    if _HEADING_RE.match(line):
        return "heading"
    if "|" in _strip_code_spans(line):
        return "table"
    return "prose"


def _is_hard_break(line: str) -> bool:
    return bool(_HARD_BREAK_RE.search(line))


def _longest_unit(tokens: list[str]) -> int:
    return max((len(t) for t in tokens), default=0)


def check(path: Path, column: int = DEFAULT_COLUMN) -> list[Finding]:
    """Return every over-budget or under-filled prose line in `path`.

    This is the gate's library entry point: a caller (the formatter's own
    oracle check, a test) gets structured findings without shelling out to
    `main`. Raises GateError to fail closed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GateError(f"path does not exist: {path}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise GateError(f"cannot read {path}: {e}") from None

    lines = _COMMONMARK_LINE_RE.split(text)
    masked = _mask_fenced_lines(lines)
    kinds = [
        "masked" if masked[i] else _classify(line) for i, line in enumerate(lines)
    ]

    findings: list[Finding] = []
    n = len(lines)
    for i in range(n):
        if kinds[i] != "prose":
            continue
        line = lines[i]
        tokens = _tokenize(line)
        exempt = _is_hard_break(line) or _longest_unit(tokens) > column
        if not exempt and len(line) > column:
            over_by = len(line) - column
            findings.append(
                Finding(
                    i + 1,
                    "over-budget",
                    f"{path}: reason: line {i + 1} is over-budget by {over_by} "
                    f"character(s) (column {column}) — {REMEDY}",
                )
            )
        if not exempt and i + 1 < n and kinds[i + 1] == "prose":
            next_tokens = _tokenize(lines[i + 1])
            if next_tokens:
                candidate = len(line) + 1 + len(next_tokens[0])
                if candidate <= column:
                    findings.append(
                        Finding(
                            i + 1,
                            "under-filled",
                            f"{path}: reason: line {i + 1} is under-filled — the next "
                            f"word would still fit within the {column}-column budget "
                            f"— {REMEDY}",
                        )
                    )
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--column", type=int, default=DEFAULT_COLUMN)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    errored = found = False
    for path in args.paths:
        try:
            findings = check(path, args.column)
        except GateError as e:
            print(f"{path}: error: {e}", file=sys.stderr)
            errored = True
            continue
        for finding in findings:
            print(finding.message, file=sys.stderr)
            found = True
    if errored:
        print("failing closed — a document could not be certified either way", file=sys.stderr)
        return 2
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
