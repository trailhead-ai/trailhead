#!/usr/bin/env python3
"""Reference depth gate — does a document still name a sibling `_shared`
document by its filename?

A shared document that names a sibling by filename creates a second-level
reference: the reader following it lands on a whole other document instead of
the one line of shared prose the pointer actually needed. This gate reddens on
any surviving mention of a sibling document's filename, in any of the forms
prose uses to write it — a parent-relative path, a directory-relative path, or
a bare filename with no directory at all — so a rewrite of the reference is
what turns the gate green, not a rewording that keeps naming the file.

The sibling set for a given document is every other `.md` file in the same
directory. A document naming its own filename is not a finding — a document
does not reference itself — and a filename that names no file present in that
directory is not a finding either: mentioning `README.md` in prose is not
mentioning a sibling document unless a `README.md` actually sits beside the
file being checked.

Structure is read with `covers_gate._mask_fenced_lines`, the masker the
sibling gates already share, so all three agree on what counts as a fence, an
HTML comment, and a line ending. A reference inside a fenced code block or an
HTML comment is template text or an illustration, not a live pointer, and
stays invisible to the matcher.

Usage:
    reference_depth_gate.py <markdown-path> [<markdown-path> ...]

Exit codes:
    0  clean   — no surviving reference to a sibling document
    1  finding — a sibling document is still named (prints a `reason:` line
       naming the file, line number, and matched text per finding)
    2  error   — fail-closed: a path is missing or unreadable, or its content
       is empty or not valid UTF-8. NEVER exits 0 when it could not actually
       certify the document.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from covers_gate import _COMMONMARK_LINE_RE, _mask_fenced_lines  # noqa: E402


class GateError(Exception):
    """The document could not be certified either way."""


def _sibling_stems(path: Path) -> list[str]:
    """Every other `.md` file's stem in `path`'s own directory.

    This is a directory listing, not a fixed reference set: a filename that
    is not actually present beside `path` (a `README.md` mentioned in prose
    with no `README.md` file there) never becomes a sibling to match against.
    """
    return sorted(p.stem for p in path.parent.glob("*.md") if p.name != path.name)


def _reference_pattern(stems: list[str]) -> re.Pattern[str] | None:
    """A pattern matching any sibling stem followed by `.md`, in any
    surrounding path form or none — the character immediately before and
    after the match must not itself be part of a longer filename, so a
    sibling stem embedded inside an unrelated longer name is not a match."""
    if not stems:
        return None
    alts = "|".join(re.escape(s) for s in stems)
    return re.compile(rf"(?<![\w-])(?:{alts})\.md(?![\w-])")


def check(path: Path) -> list[tuple[int, str]]:
    """Return (line_number, matched_text) for each surviving reference to a
    sibling document. Raises GateError to fail closed."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GateError(f"path does not exist: {path}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise GateError(f"cannot read {path}: {e}") from None
    if not text.strip():
        raise GateError(f"{path} is empty")

    pattern = _reference_pattern(_sibling_stems(path))
    if pattern is None:
        return []

    lines = _COMMONMARK_LINE_RE.split(text)
    masked = _mask_fenced_lines(lines)

    findings: list[tuple[int, str]] = []
    for i, (line, hidden) in enumerate(zip(lines, masked)):
        if hidden:
            continue
        for m in pattern.finditer(line):
            findings.append((i + 1, m.group(0)))
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    errored = found = False
    for path in args.paths:
        try:
            findings = check(path)
        except GateError as e:
            print(f"{path}: error: {e}", file=sys.stderr)
            errored = True
            continue
        for line_no, text in findings:
            print(
                f"{path}: reason: line {line_no} references sibling document {text!r}",
                file=sys.stderr,
            )
            found = True
    if errored:
        print("failing closed — a document could not be certified either way", file=sys.stderr)
        return 2
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
