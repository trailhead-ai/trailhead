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
share, or inside a `---`-delimited YAML frontmatter block at the very top
of the file — see `_mask_frontmatter_lines`), an ATX heading, a table row,
a line whose own longest unit exceeds the budget on its own (an unbreakable
long word, path, URL, or code span — wrapping cannot help a line whose
problem is one token, however much breakable text sits beside it), and a
line ending in a hard line break (two or more trailing spaces, or a
trailing backslash) — reflowing across a hard break changes what the
document renders.

Block structure is measured, not assumed: a "block" is a maximal run of
consecutive prose lines with no blank line, masked line, heading, or table
row between them, further split wherever a line opens a construct its
predecessor was not already inside — a new list-item marker (bullet,
ordinal, or nested), or a block quote at a different nesting depth. Two
sibling list items, or a wrapped item's last continuation line followed by
the next item's marker, sit in the same run of prose lines but are not the
same block, and the under-fill rule never compares across that boundary. A
wrapped list item's continuation lines carry their own hanging indent as
literal characters, so measuring each line's raw length (indent included)
against the same absolute column budget is what "measured at its indented
width" means. A block quote repeats its "> " marker on every line, so when
the successor continues the same quote at the same depth, its marker is
stripped before measuring its first word — otherwise the marker itself,
not the word after it, would be measured as "the next word."

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


def _mask_frontmatter_lines(lines: list[str]) -> list[bool]:
    """Return one boolean per line — True where the line is inside a
    `---`-delimited YAML frontmatter block. Only a `---` on the very first
    line opens frontmatter; a `---` appearing anywhere later in the document
    is an ordinary thematic break, not a delimiter, and it (and everything
    around it) is left unmasked. The block closes on the first subsequent
    line that is exactly `---`; if none exists before EOF, the whole file is
    left unmasked rather than masked to EOF — a mask that ran to EOF would
    silently exempt an entire malformed file from the gate, so an
    unterminated block fails closed instead of masking anything at all."""
    n = len(lines)
    masked = [False] * n
    if n == 0 or lines[0] != "---":
        return masked
    for i in range(1, n):
        if lines[i] == "---":
            for k in range(i + 1):
                masked[k] = True
            return masked
    return masked


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
    characters, treating a valid inline code span (backtick-delimited,
    matched opening/closing run length) anywhere inside the run as atomic
    — its internal spaces never end the run. Punctuation glued directly to
    a span with no space — `` `spec`). `` — therefore stays part of the
    same unit as the span, exactly as `wrap_prose.py`'s fill measures it;
    splitting them here would measure a shorter "next word" than the
    formatter ever produces."""
    tokens: list[str] = []
    i, n = 0, len(line)
    while i < n:
        if line[i].isspace():
            i += 1
            continue
        start = i
        while i < n and not line[i].isspace():
            if line[i] == "`":
                end = _scan_code_span(line, i)
                if end is not None:
                    i = end
                    continue
            i += 1
        tokens.append(line[start:i])
    return tokens


# A list marker (bullet or ordinal) plus its trailing whitespace, with any
# leading indent captured — matched against a whole line, so a nested item
# ("  - foo") carries its indent into the match. Mirrored by
# `wrap_prose.py`'s own copy of this pattern: the two must agree on what
# opens a list item, or a boundary the gate recognizes could be one the
# formatter's own fill segmentation does not, and vice versa.
_LIST_MARKER_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])( +)")

# One or more ">" quote markers, each optionally followed by one space —
# "> " and the nested "> > " both match in full via group(1).
_BLOCKQUOTE_RE = re.compile(r"^(\s*(?:>[ \t]?)+)")


def _starts_new_list_item(line: str) -> bool:
    return bool(_LIST_MARKER_RE.match(line))


def _quote_depth(line: str) -> int:
    """Number of ">" markers this line opens with — 0 for a non-quote
    line. Two lines continue the same block-quote only when their depths
    match; a depth change is a block boundary exactly as a blank line is."""
    m = _BLOCKQUOTE_RE.match(line)
    return m.group(1).count(">") if m else 0


def _strip_blockquote_prefix(line: str) -> str:
    """`line` with its leading block-quote marker(s) removed, so its first
    real word can be measured instead of the marker itself."""
    m = _BLOCKQUOTE_RE.match(line)
    return line[m.end() :] if m else line


def _continues_same_block(line: str, next_line: str) -> bool:
    """Whether `next_line` is a continuation of the same block as `line`,
    for the under-fill rule's purposes. A new list-item marker always
    starts a fresh block — merging it into its predecessor would merge two
    separate list items (or a nested item into its outer one) into one. A
    block-quote depth change is the same kind of boundary: a nested quote,
    or a return to a shallower one, is not a continuation of the line
    before it."""
    if _starts_new_list_item(next_line):
        return False
    return _quote_depth(line) == _quote_depth(next_line)


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
    fenced = _mask_fenced_lines(lines)
    frontmatter = _mask_frontmatter_lines(lines)
    kinds = [
        "masked" if (fenced[i] or frontmatter[i]) else _classify(line)
        for i, line in enumerate(lines)
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
        if (
            not exempt
            and i + 1 < n
            and kinds[i + 1] == "prose"
            and _continues_same_block(line, lines[i + 1])
        ):
            next_line = lines[i + 1]
            depth = _quote_depth(next_line)
            if depth:
                next_line = _strip_blockquote_prefix(next_line)
            next_tokens = _tokenize(next_line)
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
