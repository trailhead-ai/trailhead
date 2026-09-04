#!/usr/bin/env python3
"""Wrap prose — the greedy filler whose oracle is `wrap_gate.py`.

`wrap_gate.py` certifies that a document's prose is wrapped at one
consistent column; this is the tool a contributor runs when the gate
reddens. It reflows every prose block to the column budget and rewrites
everything else byte-for-byte. Its contract is defined against the gate
rather than against a wording: for any input, the formatter's output exits
`wrap_gate.py` clean at the same column, and running the formatter on its
own output changes nothing.

**An inline code span is atomic and is never broken across lines.** This is
the reason a plain `textwrap.fill` is not sufficient: `textwrap` splits on
whitespace, and craft's prose carries backtick spans containing literal
spaces — copy-and-run commands and credential-scrub patterns a reader
applies verbatim. The formatter fills over the very tokenizer the gate
measures with (`wrap_gate._tokenize`), so a span survives whole; a fill unit
is a maximal non-whitespace run with any embedded span kept intact, which
also means punctuation glued to a span with no space — `` `spec`). `` — is
never pulled apart into two units that a plain space-join would then
wrongly re-separate. A span longer than the budget is emitted on its own
line rather than split — the gate exempts a line whose content (past its
own list-marker or block-quote prefix, if any) is a single unit that
exceeds the budget on its own, and the greedy fill below produces that
shape without any special case: a unit is always placed even when it alone
exceeds what remains of the line.

Byte-preserved, never reflowed: masked lines (fenced blocks, HTML comments,
and a `---`-delimited YAML frontmatter block at the very top of the file),
ATX headings, table rows, and a line ending in a hard line break —
reflowing across a hard break changes what the document renders, so it
ends its wrapped run rather than being joined to its neighbours in either
direction.

Structure preserved across a reflow: list and block-quote markers with
their hanging indent, blank-line separation between blocks, and the file's
trailing newline. A list item's continuation lines fill to the budget at
their indented width — the marker (or quote prefix) becomes a literal
prefix on every produced line, exactly as `wrap_gate.py` measures it (no
separate list-marker accounting on the gate's side, so none is needed
here either).

Usage:
    wrap_prose.py [--column N] <markdown-path> [<markdown-path> ...]

Rewrites each path in place. Exit codes mirror the sibling gates: 0 on
success, 2 (fail-closed) if a path is missing, unreadable, or not valid
UTF-8.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# Every structural decision the formatter shares with its oracle is imported
# from the gate rather than restated here — the block-boundary patterns, the
# atomic-unit tokenizer, the hard-break test, and the masking-plus-
# classification pass. A second definition of any of them could drift, and a
# formatter that disagrees with the gate about a boundary or a unit emits
# output the gate reddens on.
from wrap_gate import (  # noqa: E402
    DEFAULT_COLUMN,
    _BLOCKQUOTE_RE,
    _continues_same_block,
    _is_hard_break,
    _LIST_MARKER_RE,
    _strip_blockquote_prefix,
    _tokenize,
    split_and_classify,
)


class FormatError(Exception):
    """The document could not be read or written."""


def _detect_prefix(first_line: str) -> tuple[str, str, str]:
    """Return (kind, initial_prefix, continuation_prefix) for the paragraph
    whose first physical line is `first_line`. `kind` is 'blockquote',
    'list', or 'plain'."""
    m = _BLOCKQUOTE_RE.match(first_line)
    if m:
        prefix = m.group(1)
        return ("blockquote", prefix, prefix)
    m = _LIST_MARKER_RE.match(first_line)
    if m:
        initial_prefix = m.group(0)
        return ("list", initial_prefix, " " * len(initial_prefix))
    return ("plain", "", "")


def _strip_prefix(line: str, kind: str) -> str:
    """Remove this paragraph's structural prefix from one physical line, so
    its remaining words can be re-tokenized and re-filled."""
    if kind == "blockquote":
        return _strip_blockquote_prefix(line)
    if kind == "list":
        m = _LIST_MARKER_RE.match(line)
        if m:
            return line[m.end() :]
        return line.lstrip(" ")
    return line


def _fill_tokens(
    tokens: list[str], column: int, initial_prefix: str, continuation_prefix: str
) -> list[str]:
    """Greedy-fill `tokens` into lines no wider than `column`, each carrying
    its structural prefix as literal text — the same measurement the gate
    applies. A token that alone (with its prefix) exceeds the column still
    gets its own line rather than being split."""
    lines: list[str] = []
    prefix = initial_prefix
    current: list[str] = []
    cur_len = len(prefix)
    for token in tokens:
        add_len = len(token) if not current else len(token) + 1
        if current and cur_len + add_len > column:
            lines.append(prefix + " ".join(current))
            prefix = continuation_prefix
            current = [token]
            cur_len = len(prefix) + len(token)
        else:
            current.append(token)
            cur_len += add_len
    if current:
        lines.append(prefix + " ".join(current))
    return lines


def _fill_paragraph(lines: list[str], column: int) -> list[str]:
    kind, initial_prefix, continuation_prefix = _detect_prefix(lines[0])
    tokens: list[str] = []
    for line in lines:
        tokens.extend(_tokenize(_strip_prefix(line, kind)))
    if not tokens:
        return list(lines)
    return _fill_tokens(tokens, column, initial_prefix, continuation_prefix)


def _segment_block(block_lines: list[str]) -> list[tuple[str, list[str]]]:
    """Split one gate-defined block (a maximal run of prose lines) into
    segments: ('verbatim', [line]) for a line ending in a hard line break,
    and ('fill', [lines]) for a run of lines to be greedy-filled together as
    one paragraph. A line that opens a new list item (other than the
    block's very first line), or that changes block-quote nesting depth
    from its predecessor, always starts a fresh 'fill' segment — reusing
    `wrap_gate._continues_same_block` rather than restating its notion of a
    boundary, so sibling list items and different-depth block quotes are
    never merged into each other or into their neighbour."""
    segments: list[tuple[str, list[str]]] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            segments.append(("fill", list(current)))
            current.clear()

    for index, line in enumerate(block_lines):
        if _is_hard_break(line):
            flush()
            segments.append(("verbatim", [line]))
            continue
        if index > 0 and not _continues_same_block(block_lines[index - 1], line):
            flush()
        current.append(line)
    flush()
    return segments


def _reflow_block(block_lines: list[str], column: int) -> list[str]:
    out: list[str] = []
    for kind, seg_lines in _segment_block(block_lines):
        if kind == "verbatim":
            out.extend(seg_lines)
        else:
            out.extend(_fill_paragraph(seg_lines, column))
    return out


def format_text(text: str, column: int = DEFAULT_COLUMN) -> str:
    """Reflow `text`'s prose blocks to `column`; return the reflowed text.
    Every non-prose line (masked, heading, table, blank) is passed through
    unchanged, so this is the library entry point both the CLI and the
    formatter's own tests call directly."""
    lines, kinds = split_and_classify(text)
    n = len(lines)

    out: list[str] = []
    i = 0
    while i < n:
        if kinds[i] != "prose":
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < n and kinds[j] == "prose":
            j += 1
        out.extend(_reflow_block(lines[i:j], column))
        i = j
    return "\n".join(out)


def format_path(path: Path, column: int = DEFAULT_COLUMN) -> bool:
    """Reflow `path` in place. Returns True if its content changed."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FormatError(f"path does not exist: {path}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise FormatError(f"cannot read {path}: {e}") from None

    new_text = format_text(text, column)
    if new_text == text:
        return False
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        raise FormatError(f"cannot write {path}: {e}") from None
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--column", type=int, default=DEFAULT_COLUMN)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    errored = False
    for path in args.paths:
        try:
            format_path(path, args.column)
        except FormatError as e:
            print(f"{path}: error: {e}", file=sys.stderr)
            errored = True
    return 2 if errored else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
