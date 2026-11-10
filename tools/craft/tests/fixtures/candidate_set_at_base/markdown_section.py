#!/usr/bin/env python3
"""Locate, excise and position-check one `## <heading>` section of a body.

A section runs from its heading to the next unmasked ATX heading of any level
(or the end of the document), the same extent lore exempts from a ready spec's
body freeze. The heading is matched case-insensitively at line start, exactly
once outside fenced code blocks and HTML comments (`covers_gate`'s masker); a
second occurrence raises `covers_gate.DuplicateHeadingError`.

Public API (every function takes the heading and the reason code a duplicate
heading carries):
    section_range(lines, masked, heading, duplicate_code)
                                  -> (heading index, exclusive end index) | None
    split_body(body)              -> (lines, masked)
    excise_section(body, heading, duplicate_code) -> str
    has_section(body, heading, duplicate_code)    -> bool
    section_is_last(body, heading, duplicate_code) -> bool
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
    _find_unique_heading,
    _mask_fenced_lines,
)

_ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|\Z)")


def split_body(body: str) -> tuple[list[str], list[bool]]:
    lines = _COMMONMARK_LINE_RE.split(body)
    return lines, _mask_fenced_lines(lines)


def section_range(
    lines: list[str], masked: list[bool], heading: str, duplicate_code: str
) -> tuple[int, int] | None:
    """(heading index, exclusive end index) of the section, or None."""
    heading_re = re.compile(f"^{re.escape(heading)}$", re.IGNORECASE)
    after = _find_unique_heading(lines, masked, heading_re, heading, duplicate_code)
    if after is None:
        return None
    end = len(lines)
    for j in range(after, len(lines)):
        if not masked[j] and _ATX_HEADING_RE.match(lines[j]):
            end = j
            break
    return after - 1, end


def excise_section(body: str, heading: str, duplicate_code: str) -> str:
    """The body without the section, lines joined by LF and trailing blank
    lines dropped (never trailing whitespace within an authored line), so a
    section appended at the end leaves the rest equal to the body it was
    appended to."""
    lines, masked = split_body(body)
    located = section_range(lines, masked, heading, duplicate_code)
    if located is not None:
        start, end = located
        lines = lines[:start] + lines[end:]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def has_section(body: str, heading: str, duplicate_code: str) -> bool:
    lines, masked = split_body(body)
    return section_range(lines, masked, heading, duplicate_code) is not None


def section_is_last(body: str, heading: str, duplicate_code: str) -> bool:
    """True when the section is absent or only blank lines follow it."""
    lines, masked = split_body(body)
    located = section_range(lines, masked, heading, duplicate_code)
    return located is None or all(not line.strip() for line in lines[located[1]:])
