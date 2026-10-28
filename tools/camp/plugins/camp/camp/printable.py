"""Reduce text from outside camp to one printable line.

Far-side text (ssh's stderr, Docker's error line, a reply that failed to parse) reaches an
operator's terminal only through :func:`printable_line`. It keeps printable characters and
drops everything that could move the cursor, recolour the screen, reorder the line or hide
characters: control characters (Unicode category Cc, which includes ESC, newline, tab and
DEL), format characters (Cf: bidirectional overrides, zero-width characters), the line and
paragraph separators U+2028 and U+2029, and lone surrogates, which is what an undecodable
byte becomes under ``errors="surrogateescape"``. The result is then cut to ``limit``
characters.

Newlines are removed, not kept, so the result is always one line; a caller that wants a
message's first line takes it before calling. ``camp.host.relay`` keeps newline and tab in
the stderr it relays and has its own filter for that reason.
"""
from __future__ import annotations

import unicodedata

#: The longest a far-side line may be once printed.
LINE_LIMIT = 200

_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def printable_line(text: str, limit: int = LINE_LIMIT) -> str:
    """*text* with every non-printable character removed, cut to *limit* characters."""
    kept: list[str] = []
    for character in text:
        if unicodedata.category(character) in _DROPPED_CATEGORIES:
            continue
        kept.append(character)
        if len(kept) >= limit:
            break
    return "".join(kept)
