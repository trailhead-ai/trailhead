"""Authored content of a record: its body minus the kind's pipeline-maintained sections.

Pure string functions — no I/O, no vault access. Callers that compare bodies
neutralize them first (``store.neutralize_fences``) and hand the same text to
:func:`authored_content`; neutralization only rewrites ``external-memory`` tag
names, so it never moves a heading, a fence, or a comment.

Lore carries its own copy of the heading and masking rules: plugins stand
alone, so this module must not import craft. A parity test in craft's suite
holds the two copies in agreement about where ``## Slices`` starts.
"""
from __future__ import annotations

import re
from typing import NamedTuple

_LINE_RE = re.compile(r"\r\n|\r|\n")
_FENCE_START_RE = re.compile(r"^(`{3,}|~{3,})")
_HTML_COMMENT_START_RE = re.compile(r"^<!--")
_ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|\Z)")


class Section(NamedTuple):
    heading: str
    repeatable: bool

    @property
    def heading_re(self) -> re.Pattern[str]:
        """The heading line, matched case-insensitively."""
        return re.compile(re.escape(self.heading), re.IGNORECASE)


# A section is a block the craft pipeline writes onto a record after its author
# is done: the body outside it is the author's. ``repeatable=False`` means a
# second unmasked heading is ambiguous, so the section is not exempted at all.
#
# Craft consumers of this table:
#   - ``## Slices`` (spec): the slice ledger, written by ``craft:slice`` /
#     slice close and read by ``candidate_set.py`` and ``covers_gate.py``.
#   - ``## Driver run`` (task): the drive checkpoint, one block appended at
#     each phase boundary by ``craft:drive``.
PIPELINE_SECTIONS: dict[str, tuple[Section, ...]] = {
    "spec": (Section("## Slices", repeatable=False),),
    "task": (Section("## Driver run", repeatable=True),),
}


def _split_lines(body: str) -> list[str]:
    """Lines with their terminators kept, split on CommonMark boundaries only."""
    out: list[str] = []
    pos = 0
    for m in _LINE_RE.finditer(body):
        out.append(body[pos:m.end()])
        pos = m.end()
    if pos < len(body):
        out.append(body[pos:])
    return out


def _content(line: str) -> str:
    return _LINE_RE.sub("", line)


def _mask(lines: list[str]) -> list[bool]:
    """True for each line inside fenced code or an HTML comment (markers included).

    An unterminated comment or fence masks to end of document.
    """
    masked = [False] * len(lines)
    fence_char: str | None = None
    fence_len = 0
    in_comment = False
    for i, raw in enumerate(lines):
        line = _content(raw)
        stripped = line.strip()
        if in_comment:
            masked[i] = True
            if "-->" in line:
                in_comment = False
            continue
        if fence_char is None:
            if _HTML_COMMENT_START_RE.match(stripped):
                masked[i] = True
                if "-->" not in stripped:
                    in_comment = True
                continue
            m = _FENCE_START_RE.match(stripped)
            if m:
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                masked[i] = True
            continue
        masked[i] = True
        m = _FENCE_START_RE.match(stripped)
        if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len:
            fence_char = None
    return masked


def _heading_lines(
    lines: list[str], masked: list[bool], heading_re: re.Pattern[str]
) -> list[int]:
    return [
        i
        for i, raw in enumerate(lines)
        if not masked[i] and heading_re.fullmatch(_content(raw))
    ]


def _removed_lines(kind: str, lines: list[str], masked: list[bool]) -> set[int]:
    removed: set[int] = set()
    for section in PIPELINE_SECTIONS.get(kind, ()):
        starts = _heading_lines(lines, masked, section.heading_re)
        if len(starts) > 1 and not section.repeatable:
            continue
        for start in starts:
            end = len(lines)
            for j in range(start + 1, len(lines)):
                if not masked[j] and _ATX_HEADING_RE.match(_content(lines[j])):
                    end = j
                    break
            removed.update(range(start, end))
    return removed


def authored_content(kind: str, body: str) -> str:
    """Return *body* with *kind*'s pipeline-maintained sections removed.

    A section runs from its heading to the next unmasked ATX heading of any
    level, or to end of document. Kinds without a pipeline section come back
    unchanged. For kinds with one, blank lines at each segment boundary and at
    the end of the document are normalized, so appending a section with or
    without a trailing newline on the prior body yields equal authored content.
    """
    if kind not in PIPELINE_SECTIONS:
        return body
    lines = _split_lines(body)
    removed = _removed_lines(kind, lines, _mask(lines))
    segments: list[list[str]] = []
    run: list[str] = []
    for i, line in enumerate(lines):
        if i in removed:
            if run:
                segments.append(run)
                run = []
        else:
            run.append(line)
    if run:
        segments.append(run)

    out = ""
    eol = ""
    blanks = 0
    for n, seg in enumerate(segments):
        start = 0
        # Blank lines flanking a removed section are the section's separators.
        while n > 0 and start < len(seg) and not seg[start].strip():
            start += 1
        end = len(seg)
        while end > start and not seg[end - 1].strip():
            end -= 1
        if end == start:
            blanks += len(seg)
            continue
        blanks += start
        last = seg[end - 1]
        terminator = _LINE_RE.search(last)
        text = "".join(seg[start:end])
        if terminator:
            text = text[: -len(terminator.group(0))]
        if out:
            out += eol * (2 if blanks else 1)
        out += text
        eol = terminator.group(0) if terminator else ""
        blanks = len(seg) - end
    return out
