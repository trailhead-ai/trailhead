"""Cleaning of text that came from outside docket before it goes into a report.

Record titles and statuses, and the host names camp prints, are written by other people and other
hosts. `clean` makes one such string safe to relay: control characters and line breaks become single
spaces, format, private-use, surrogate, unassigned and invisible characters are dropped, runs of
whitespace collapse, and the result is cut to `MAX_TEXT` characters. Nothing else about the text is
changed except that fence delimiters are defused: every backtick becomes an apostrophe and a run of three or
more tildes becomes hyphens, so cleaned text can never close a code fence a reader draws around it; a report puts every cleaned string under a `data` key so a reader treats it as data.
"""
from __future__ import annotations

import re
import unicodedata

MAX_TEXT = 200

# Letters and symbols that render as blank space: the Hangul fillers and the braille blank.
_INVISIBLE = frozenset(chr(c) for c in (0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800))
_SPACING = {"Cc", "Zl", "Zp", "Zs"}
_DROPPED = {"Cf", "Co", "Cs", "Cn"}
_TILDE_FENCE = re.compile(r"~{3,}")


def clean(value: str, cap: int = MAX_TEXT) -> str:
    out: list[str] = []
    for ch in value:
        category = unicodedata.category(ch)
        if ch in _INVISIBLE or category in _DROPPED:
            continue
        out.append(" " if category in _SPACING else ch)
    text = _TILDE_FENCE.sub(lambda m: "-" * len(m.group()), "".join(out).replace("`", "'"))
    return " ".join(text.split())[:cap]
