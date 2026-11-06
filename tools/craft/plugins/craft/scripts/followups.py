#!/usr/bin/env python3
"""The single parser for a spec's `## Follow-ups` section.

A spec body with no `## Follow-ups` heading reads as zero entries. The
section runs from its heading to the next unmasked ATX heading of any level
(or the end of the document), the same extent lore exempts from a ready
spec's body freeze. Fenced code blocks and HTML comments inside it are
masked with the shared `covers_gate` masker; any other non-blank line that is
not part of an entry is an error, never silently skipped.

Entry grammar:

    ## Follow-ups

    - **F3.** Short title
      - status: open | folded into task/<id> | done by task/<id> | dropped: <reason>
      - what: one line saying what is wrong
      - where: `path/a.py`, `path/b.py`
      - found: where it was found
      - from: task/<id>            (optional)

Public API:
    parse_followups(body) -> list[Entry]   entries in document order
    excise_section(body) -> str            the body without the section
    FollowupsError                         a grammar violation; carries a
                                           stable `reason_code`
    DuplicateHeadingError                  a second unmasked heading (re-exported
                                           from `covers_gate`)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
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

HEADING = "## Follow-ups"
DUPLICATE_HEADING_REASON_CODE = "duplicate-followups-heading"

MALFORMED_ENTRY = "malformed-entry"
MALFORMED_TASK_ID = "malformed-task-id"
PATH_OUTSIDE_REPOSITORY = "path-outside-repository"
DROP_WITHOUT_REASON = "drop-without-reason"

_HEADING_RE = re.compile(r"^## Follow-ups$", re.IGNORECASE)
_ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|\Z)")
_ENTRY_RE = re.compile(r"^- \*\*(F[0-9]+)\.\*\* (\S.*)$")
_ENTRY_START_RE = re.compile(r"^- ")
_FIELD_RE = re.compile(r"^ {2,}- ([a-z]+):[ \t]*(.*?)[ \t]*$")
_TASK_ID_RE = re.compile(r"^task/[A-Za-z0-9._-]+$")
_PATH_CHARS_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_WHERE_RE = re.compile(r"^`[^`]*`(?:, `[^`]*`)*$")
_REQUIRED_FIELDS = ("status", "what", "where", "found")
_ALLOWED_FIELDS = (*_REQUIRED_FIELDS, "from")


class FollowupsError(ValueError):
    """A grammar violation in the section. `reason_code` is stable; the
    message names the entry id or the section and the one-line fix."""

    def __init__(self, message: str, reason_code: str):
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    status_kind: str  # "open" | "folded" | "done" | "dropped"
    status_detail: str | None  # "task/<id>" for folded/done, the reason for dropped
    what: str
    where: tuple[str, ...]
    found: str
    from_: str | None


def _split(body: str) -> tuple[list[str], list[bool]]:
    lines = _COMMONMARK_LINE_RE.split(body)
    return lines, _mask_fenced_lines(lines)


def _section_range(lines: list[str], masked: list[bool]) -> tuple[int, int] | None:
    """(heading index, exclusive end index) of the section, or None."""
    after = _find_unique_heading(
        lines, masked, _HEADING_RE, HEADING, DUPLICATE_HEADING_REASON_CODE
    )
    if after is None:
        return None
    end = len(lines)
    for j in range(after, len(lines)):
        if not masked[j] and _ATX_HEADING_RE.match(lines[j]):
            end = j
            break
    return after - 1, end


def excise_section(body: str) -> str:
    """The body without the section, lines joined by LF and trailing
    whitespace dropped, so a section appended at the end leaves the rest
    equal to the body it was appended to."""
    lines, masked = _split(body)
    located = _section_range(lines, masked)
    if located is not None:
        start, end = located
        lines = lines[:start] + lines[end:]
    return "\n".join(lines).rstrip()


def _check_task_id(value: str, entry_id: str, field: str) -> None:
    if not _TASK_ID_RE.match(value):
        raise FollowupsError(
            f"entry {entry_id}: {field} target {value!r} is not a task id — "
            "write it as task/<id> using only letters, digits, '.', '_' and '-'",
            MALFORMED_TASK_ID,
        )


def _parse_status(value: str, entry_id: str) -> tuple[str, str | None]:
    if value == "open":
        return "open", None
    for prefix, kind in (("folded into ", "folded"), ("done by ", "done")):
        if value.startswith(prefix):
            target = value[len(prefix):]
            _check_task_id(target, entry_id, "status")
            return kind, target
    if value == "dropped" or value.startswith("dropped:"):
        reason = value[len("dropped:"):].strip() if value != "dropped" else ""
        if not reason:
            raise FollowupsError(
                f"entry {entry_id}: dropped without a reason — "
                "write 'dropped: <why it will not be done>'",
                DROP_WITHOUT_REASON,
            )
        return "dropped", reason
    raise FollowupsError(
        f"entry {entry_id}: status {value!r} is not recognised — use "
        "'open', 'folded into task/<id>', 'done by task/<id>' or "
        "'dropped: <reason>'",
        MALFORMED_ENTRY,
    )


def _check_path(path: str, entry_id: str) -> None:
    bad = (
        path.startswith("-")
        or any(s in ("", ".", "..") for s in path.split("/"))
        or not _PATH_CHARS_RE.match(path)
    )
    if bad:
        raise FollowupsError(
            f"entry {entry_id}: where path {path!r} is not repository-relative — "
            "use a relative path of letters, digits, '.', '_', '-' and '/', "
            "with no '.', '..' or empty segment and no leading '-'",
            PATH_OUTSIDE_REPOSITORY,
        )


def _build_entry(entry_id: str, title: str, fields: dict[str, str]) -> Entry:
    for key in _REQUIRED_FIELDS:
        if key not in fields:
            raise FollowupsError(
                f"entry {entry_id}: missing the '{key}:' line — add "
                f"'  - {key}: ...' under the entry",
                MALFORMED_ENTRY,
            )
    for key in ("what", "found"):
        if not fields[key]:
            raise FollowupsError(
                f"entry {entry_id}: the '{key}:' line is empty — say it in one line",
                MALFORMED_ENTRY,
            )
    kind, detail = _parse_status(fields["status"], entry_id)
    if not _WHERE_RE.match(fields["where"]):
        raise FollowupsError(
            f"entry {entry_id}: where must be backticked paths separated by "
            "', ' — write it as `path/a.py`, `path/b.py`",
            MALFORMED_ENTRY,
        )
    paths = tuple(p[1:-1] for p in fields["where"].split(", "))
    for path in paths:
        _check_path(path, entry_id)
    origin = fields.get("from")
    if origin is not None:
        _check_task_id(origin, entry_id, "from")
    return Entry(entry_id, title, kind, detail, fields["what"], paths, fields["found"], origin)


def parse_followups(body: str) -> list[Entry]:
    """Entries of the `## Follow-ups` section in document order; none when the
    section is absent. Raises FollowupsError on a grammar violation and
    DuplicateHeadingError on a second unmasked heading."""
    lines, masked = _split(body)
    located = _section_range(lines, masked)
    if located is None:
        return []
    start, end = located
    entries: list[Entry] = []
    current: tuple[str, str] | None = None
    fields: dict[str, str] = {}

    for i in range(start + 1, end):
        if masked[i] or not lines[i].strip():
            continue
        line = lines[i]
        if _ENTRY_START_RE.match(line):
            m = _ENTRY_RE.match(line)
            if not m:
                raise FollowupsError(
                    f"section '{HEADING}' line {i + 1}: entry line {line!r} is "
                    "malformed — start it '- **F<n>.** Title'",
                    MALFORMED_ENTRY,
                )
            if current is not None:
                entries.append(_build_entry(current[0], current[1], fields))
            current, fields = (m.group(1), m.group(2).strip()), {}
            continue
        m = _FIELD_RE.match(line)
        if current is None or not m or m.group(1) not in _ALLOWED_FIELDS:
            raise FollowupsError(
                f"section '{HEADING}' line {i + 1}: {line!r} is not part of an "
                "entry — keep only entries here, each field as '  - key: value'",
                MALFORMED_ENTRY,
            )
        key = m.group(1)
        if key in fields:
            raise FollowupsError(
                f"entry {current[0]}: '{key}:' appears twice — keep one",
                MALFORMED_ENTRY,
            )
        fields[key] = m.group(2)
    if current is not None:
        entries.append(_build_entry(current[0], current[1], fields))
    return entries
