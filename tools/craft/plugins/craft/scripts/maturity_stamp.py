#!/usr/bin/env python3
"""Maturity stamp reader — reads a spec body on stdin and reports the
per-repository maturity levels its `## Maturity` section declares, failing
closed on every ambiguity rather than guessing.

Usage:
    lore record show spec/<name> | maturity_stamp.py

The spec body arrives on stdin; there is no flag — this reader derives its
result from the document alone, mirroring the sibling gates that already
follow this convention (`covers_gate.py`, `maturity_resolve.py`).

Every primitive here is imported from a sibling script rather than
re-derived: the fenced-block masker and the fail-closed unique-heading
finder come from `covers_gate.py`; the closed level vocabulary and the
offending-value sanitizer come from `maturity_resolve.py`. Note that
`maturity_resolve.py`'s own heading pattern targets `## Project Maturity`
in an agent-instruction file — a different heading in a different document
kind — so this reader anchors on the generic heading finder against its own
`## Maturity` pattern rather than reusing that heading regex.

The entry grammar is one top-level `- <member-name>: <level>` bullet per
repository. `<member-name>` matches `^[A-Za-z0-9._-]+$` and is additionally
never exactly `.` or `..` — a downstream AC7 attribution consumer treats the
member name as a path segment, so those two values are rejected here rather
than read back as a legitimate repository key. `<level>` is drawn from the
closed vocabulary `prototype` / `early` / `production` and nothing else. The
section body runs from the line after the sole unmasked `## Maturity`
heading up to (but not including) the next unmasked top-level `## ` heading,
or the end of the document.

Where a rejection names offending text — a malformed member name or an
out-of-vocabulary level value — that text is repo-authored, untrusted
content on a path that ends in an operator's terminal, so it is sanitized
and length-bounded through `maturity_resolve.py`'s existing sanitizer
before it reaches stderr. That sanitizer closes the steganographic
channels (control characters, Unicode format/bidi controls, variation
selectors) but not the obvious one: a plain English instruction fits
comfortably inside the length bound, and the "treat as untrusted" framing
around an echoed value is prose an agent is asked to honour, not a
mechanical control. This reader is a second call site for that same
open channel — see
`task/the-offending-value-echo-is-an-unclosed-prompt-injection-channel`.

Stdout on success (exit 0), one deterministic line, entries sorted by
member name regardless of the order the section wrote them in:

    maturity: lookout=prototype, trailhead=production

Exit codes:
    0  resolved — every entry in the section parsed cleanly. NEVER exits 0
       without printing a `maturity:` line.
    2  fail-closed — no `maturity:` line is printed, and stderr names a
       stable `reason-code:`:

       empty-stdin            — zero bytes on stdin.
       invalid-utf8-stdin     — stdin does not decode as UTF-8.
       section-absent         — no unmasked `## Maturity` heading at line
                                 start. An unstamped spec is not defaulted
                                 to any level here — reading it at the
                                 repository's current level is the caller's
                                 decision, not this reader's.
       duplicate-section      — a second unmasked `## Maturity` heading
                                 exists — never a silent first-wins.
       empty-section          — the heading exists but its body names zero
                                 entries, distinct from `section-absent` so
                                 a caller never has to infer "declared
                                 nothing" from "declared nothing at all".
                                 This means exactly one thing: the stamp was
                                 left unfilled. It carries no exception —
                                 see unresolved-enumeration below for the
                                 one case that looks similar but isn't.
       unresolved-enumeration — the heading exists, its body names zero
                                 entries, AND the body carries the exact
                                 `<!-- unresolved-enumeration: ... -->`
                                 marker (a single-line HTML comment starting
                                 with that token). Distinguishes "the author
                                 could not enumerate the repositories this
                                 work touches at all" from "the author left
                                 the stamp unfilled" (empty-section) — the
                                 two would otherwise be byte-identical after
                                 masking, since a caller sees only that the
                                 body has zero parseable entries either way.
                                 Still a fail-closed exit: no `maturity:`
                                 line is printed for this code either.
       malformed-entry        — a line in the section body is not a valid
                                 `- <member-name>: <level>` bullet, or its
                                 member name falls outside the safe shape.
       invalid-level          — an entry's level is outside the closed
                                 vocabulary (stderr names the offending
                                 value, sanitized).
       duplicate-member       — the same member name appears twice in the
                                 section, whether or not the two lines
                                 agree on the level.

       A body malformed in more than one way reports exactly one
       reason-code, deterministically: the section-structure check (heading
       uniqueness) runs before any entry is parsed, and entries are then
       scanned in document order, stopping at the first violation found —
       never a set of candidate reasons a caller has to disambiguate.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from covers_gate import (  # noqa: E402
    _COMMONMARK_LINE_RE,
    DuplicateHeadingError,
    _find_unique_heading,
    _mask_fenced_lines,
)
from maturity_resolve import _LEVELS, _sanitize  # noqa: E402

_MATURITY_HEADING = "## Maturity"
_MATURITY_HEADING_RE = re.compile(r"^## Maturity$", re.IGNORECASE)
_MEMBER_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._-]+$")
_ENTRY_RE = re.compile(r"^-\s+([^:]*):\s*(.*)$")
_UNRESOLVED_ENUMERATION_MARKER_RE = re.compile(r"^<!--\s*unresolved-enumeration:.*-->\s*$")

_EMPTY_STDIN_REASON_CODE = "empty-stdin"
_INVALID_UTF8_STDIN_REASON_CODE = "invalid-utf8-stdin"
_SECTION_ABSENT_REASON_CODE = "section-absent"
_DUPLICATE_SECTION_REASON_CODE = "duplicate-section"
_EMPTY_SECTION_REASON_CODE = "empty-section"
_UNRESOLVED_ENUMERATION_REASON_CODE = "unresolved-enumeration"
_MALFORMED_ENTRY_REASON_CODE = "malformed-entry"
_INVALID_LEVEL_REASON_CODE = "invalid-level"
_DUPLICATE_MEMBER_REASON_CODE = "duplicate-member"


def _err(msg: str) -> None:
    print(f"maturity-stamp: {msg}", file=sys.stderr)


class StampError(Exception):
    """A fail-closed outcome: `reason_code` is always set, `offending` is
    the raw (unsanitized) text to report alongside it, or None when the
    reason carries no offending text of its own."""

    def __init__(self, reason_code: str, offending: str | None = None):
        self.reason_code = reason_code
        self.offending = offending
        super().__init__(reason_code)


def _extract_section_lines(text: str) -> tuple[list[str], list[str]]:
    """Return `(body, raw)` for the sole unmasked `## Maturity` heading:
    `body` is everything after the heading line up to (but not including) the
    next unmasked top-level `## ` heading, with masked lines (fenced code,
    HTML comments) omitted — this is what `parse_entries` scans for bullets,
    unchanged from before. `raw` is the same range with masked lines kept, so
    a caller can still observe an HTML comment that `body` makes invisible —
    the one place the unresolved-enumeration marker (necessarily a comment,
    since bare prose under this heading is `malformed-entry`) can be read at
    all. Raises StampError(section-absent) if no such heading exists, or
    StampError(duplicate-section) if a second unmasked occurrence exists."""
    lines = _COMMONMARK_LINE_RE.split(text)
    masked = _mask_fenced_lines(lines)

    try:
        start = _find_unique_heading(
            lines,
            masked,
            _MATURITY_HEADING_RE,
            _MATURITY_HEADING,
            _DUPLICATE_SECTION_REASON_CODE,
        )
    except DuplicateHeadingError as e:
        raise StampError(e.reason_code) from e
    if start is None:
        raise StampError(_SECTION_ABSENT_REASON_CODE)

    body: list[str] = []
    raw: list[str] = []
    n = len(lines)
    i = start
    while i < n:
        if masked[i]:
            raw.append(lines[i])
            i += 1
            continue
        if lines[i].startswith("## "):
            break
        body.append(lines[i])
        raw.append(lines[i])
        i += 1
    return body, raw


def parse_entries(text: str) -> dict[str, str]:
    """Return the section's declared `{member_name: level}` mapping, in
    document order (the caller sorts for display). Raises StampError with
    the appropriate reason-code and offending text on any violation; scans
    the section body in document order and raises on the first violation
    found, so a body malformed in more than one way reports exactly one
    reason-code deterministically.

    A section with zero entries reports one of two reason-codes: if the raw
    section carries the exact `<!-- unresolved-enumeration: ... -->` marker,
    `unresolved-enumeration` — a deliberate statement that no level is
    claimed for any repository. Otherwise `empty-section` — an unfilled
    stamp. The two are not interchangeable: `empty-section` always means the
    stamp was left blank."""
    body_lines, raw_lines = _extract_section_lines(text)

    entries: dict[str, str] = {}
    for line in body_lines:
        if line.strip() == "":
            continue
        m = _ENTRY_RE.match(line)
        if not m:
            raise StampError(_MALFORMED_ENTRY_REASON_CODE, line)
        name = m.group(1).strip()
        level = m.group(2).strip()
        if not _MEMBER_RE.match(name):
            raise StampError(_MALFORMED_ENTRY_REASON_CODE, name)
        if level not in _LEVELS:
            raise StampError(_INVALID_LEVEL_REASON_CODE, level)
        if name in entries:
            raise StampError(_DUPLICATE_MEMBER_REASON_CODE, name)
        entries[name] = level

    if not entries:
        if any(_UNRESOLVED_ENUMERATION_MARKER_RE.match(line) for line in raw_lines):
            raise StampError(_UNRESOLVED_ENUMERATION_REASON_CODE)
        raise StampError(_EMPTY_SECTION_REASON_CODE)

    return entries


def main(argv: list[str]) -> int:
    del argv  # no flags — the whole interface is stdin

    raw = sys.stdin.buffer.read()
    if not raw:
        _err("stdin is empty")
        _err(f"reason-code: {_EMPTY_STDIN_REASON_CODE}")
        return 2

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        _err(f"stdin is not valid UTF-8: {e}")
        _err(f"reason-code: {_INVALID_UTF8_STDIN_REASON_CODE}")
        return 2

    try:
        entries = parse_entries(text)
    except StampError as e:
        _err(f"reason-code: {e.reason_code}")
        if e.offending is not None:
            _err(f"offending-value: {_sanitize(e.offending)}")
        return 2

    stamped = ", ".join(f"{name}={level}" for name, level in sorted(entries.items()))
    print(f"maturity: {stamped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
