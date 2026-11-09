"""GitHub's heading anchors, shared by the tests that check a markdown link lands on a heading."""

from __future__ import annotations

import re

CODE_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def github_slugs(markdown: str) -> set[str]:
    """The anchors GitHub generates for *markdown*'s headings (outside code fences):
    lowercased, punctuation other than hyphens and underscores dropped, spaces
    turned into hyphens, a repeated heading suffixed `-1`, `-2`, …."""
    seen: dict[str, int] = {}
    slugs: set[str] = set()
    for heading in re.findall(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", CODE_FENCE.sub("", markdown), re.MULTILINE):
        base = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = seen.get(base, 0)
        seen[base] = count + 1
        slugs.add(base if count == 0 else f"{base}-{count}")
    return slugs
