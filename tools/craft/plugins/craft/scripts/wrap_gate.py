#!/usr/bin/env python3
r"""Wrap gate — is a document's prose wrapped at one consistent column?

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

**A table row is a line that belongs to a real CommonMark table block, never
merely a line containing a `|`.** Craft's prose uses `|` as a plain "or"
between short code-quoted alternatives (`` `SHIP` | `FAIL` | `HOLD` ``), and
those lines must be wrapped like any other prose — exempting every line with
a pipe would silently exempt them too. The discriminator: a delimiter row
(a line of dashes and pipes, e.g. `---|---`, optionally colon-flanked for
alignment, per CommonMark) whose immediately preceding unmasked, non-blank,
non-heading line carries an unescaped `|` (its header row) opens a table
block; the header and the delimiter row are both table rows, and every
contiguous line after the delimiter that still carries an unescaped `|` (a
body row) extends the same block, stopping at the first blank line, masked
line, heading, or line with no pipe. A `|` inside an inline code span or
preceded by a backslash (`` \| ``) is not "unescaped" and does not count toward
either the header check or the delimiter's own pipe requirement — an escaped
pipe never turns a prose line into a table header, and a pipe trapped in a
code-quoted cell (`` | `a|b` | c | ``) never breaks a genuine row.

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
# ("  - foo") carries its indent into the match. `wrap_prose.py` imports this
# pattern rather than defining its own: the gate and the formatter must agree
# on what opens a list item, or a boundary the gate recognizes could be one
# the formatter's own fill segmentation does not, and vice versa.
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
    before it. A hard-break successor is a boundary too:
    `wrap_prose.py`'s `_segment_block` always flushes its fill segment
    before a hard-break line, so `line` is never re-filled together with
    it — treating them as the same block here would report a finding the
    formatter is a no-op against."""
    if _starts_new_list_item(next_line):
        return False
    if _is_hard_break(next_line):
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


# A CommonMark table delimiter row: dashes (with optional colon flanks for
# alignment) in one or more pipe-separated cells, with optional leading and
# trailing pipes — e.g. "---|---", "|---|---|", "|:--|--:|". Matched only
# after confirming the line contains an unescaped `|` at all (see
# `_looks_like_delimiter_row`), since the pattern alone also matches a bare
# run of dashes with no pipe — an ordinary thematic break, not a delimiter.
_TABLE_DELIMITER_RE = re.compile(r"^\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?$")


def _has_unescaped_pipe(line: str) -> bool:
    """Whether `line` carries a `|` that could delimit real table cells —
    one that is neither inside an inline code span nor escaped with a
    backslash. Used both to find a table's header/body rows and to confirm
    a candidate delimiter row is not just a bare thematic break."""
    stripped = _strip_code_spans(line).replace("\\|", "")
    return "|" in stripped


def _looks_like_delimiter_row(line: str) -> bool:
    stripped = line.strip()
    return _has_unescaped_pipe(line) and bool(_TABLE_DELIMITER_RE.match(stripped))


def _find_table_lines(lines: list[str], blocked: list[bool]) -> list[bool]:
    """One boolean per line — True where the line belongs to a table block.
    A table opens at a delimiter row whose immediately preceding line is not
    `blocked` (masked, blank, or heading) and itself carries an unescaped
    pipe (the header row); it then extends through every contiguous,
    non-`blocked` line after the delimiter that still carries an unescaped
    pipe (body rows), stopping at the first line that doesn't."""
    n = len(lines)
    table = [False] * n
    i = 1
    while i < n:
        if (
            not blocked[i]
            and not table[i]
            and _looks_like_delimiter_row(lines[i])
            and not blocked[i - 1]
            and not table[i - 1]
            and _has_unescaped_pipe(lines[i - 1])
        ):
            table[i - 1] = True
            table[i] = True
            j = i + 1
            while j < n and not blocked[j] and _has_unescaped_pipe(lines[j]):
                table[j] = True
                j += 1
            i = j
        else:
            i += 1
    return table


def classify_lines(lines: list[str], masked: list[bool]) -> list[str]:
    """One of 'masked', 'blank', 'heading', 'table', 'prose' per line.
    Table detection needs the whole document (see `_find_table_lines`), so
    this classifies every line at once rather than one at a time."""
    kinds: list[str] = []
    for i, line in enumerate(lines):
        if masked[i]:
            kinds.append("masked")
        elif not line.strip():
            kinds.append("blank")
        elif _HEADING_RE.match(line):
            kinds.append("heading")
        else:
            kinds.append("prose")
    # A table row is prose until the whole-document scan says otherwise, and
    # every other kind blocks a table from opening on or extending across it —
    # so the kinds assigned above are exactly the `blocked` mask that scan needs.
    blocked = [kind != "prose" for kind in kinds]
    for i, is_table in enumerate(_find_table_lines(lines, blocked)):
        if is_table:
            kinds[i] = "table"
    return kinds


def split_and_classify(text: str) -> tuple[list[str], list[str]]:
    """Split `text` into physical lines and classify each one. The single
    place that decides what the gate does not look at: a line is masked when
    it sits inside a fenced block or HTML comment (`_mask_fenced_lines`) or
    inside a leading YAML frontmatter block (`_mask_frontmatter_lines`). The
    gate and `wrap_prose.py` both enter here, so neither can mask a region
    the other still governs."""
    lines = _COMMONMARK_LINE_RE.split(text)
    fenced = _mask_fenced_lines(lines)
    frontmatter = _mask_frontmatter_lines(lines)
    masked = [f or fm for f, fm in zip(fenced, frontmatter)]
    return lines, classify_lines(lines, masked)


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

    lines, kinds = split_and_classify(text)

    findings: list[Finding] = []
    n = len(lines)
    for i in range(n):
        if kinds[i] != "prose":
            continue
        line = lines[i]
        tokens = _tokenize(line)
        hard_break = _is_hard_break(line)
        # The unbreakable-unit exemption only applies to the over-budget
        # rule, and only when the line IS that one unit — a line carrying
        # breakable text beside an over-budget unit is one `wrap_prose.py`
        # would still change (isolating the unit, wrapping the rest), so
        # gate-clean would no longer imply formatter-stable if this stayed
        # exempt too.
        over_budget_exempt = hard_break or (len(tokens) == 1 and _longest_unit(tokens) > column)
        under_filled_exempt = hard_break
        if not over_budget_exempt and len(line) > column:
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
            not under_filled_exempt
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
