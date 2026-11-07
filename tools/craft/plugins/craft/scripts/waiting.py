#!/usr/bin/env python3
"""The single parser for a spec's `## Waiting` section.

Every reader of that section -- the section gate, slice's candidate set, the
loop-end test, and the check-list gather -- MUST call `parse_waiting` here
rather than parse the section itself, so they can never disagree about what an
entry is or whether the section is readable. This module has no CLI.

Entry format (the operator-approved format):

    ## Waiting

    - **AC32.** pending check — task/<building-slice>
      - check: <what to check>
      - how: <how to check it>
      - changed behaviour: yes | no
      - 2026-11-02 waiting: <words that set it>
      - 2026-11-04 failed: <operator's words>

    - **AC17.** set aside
      - waits on: <what it waits on>
      - 2026-11-03 waiting: <words>

An entry's status is the status on its latest (last written) history line:
`waiting`, `passed`, `failed` or `released`. History lines come after the
fields; dates are ISO `YYYY-MM-DD` and must be real calendar dates. A pending
check carries a building slice (`task/<bare-id>`, bare id matching
`[a-z0-9][a-z0-9._-]*`), `check`, `how` and `changed behaviour`; a set-aside
criterion carries no building slice and a `waits on`, and its changed
behaviour is False. Any field not belonging to the entry's kind is refused.

The section starts after the one unmasked `## Waiting` heading (found with
covers_gate's `_find_unique_heading`, case-insensitive, anchored at line start,
exactly as `## Slices` is) and ends at the next unmasked line starting with
`## `. Fenced blocks and HTML comments are masked with covers_gate's
`_mask_fenced_lines` and contribute no entry; a `## Waiting` heading inside a
fence is not the section. A body with no heading has zero entries.

Fail-closed: anything not read unambiguously raises `WaitingParseError`, whose
`.code` is a member of `REASON_CODES`. Silently returning fewer entries is
never an outcome. The refusals are: a duplicate heading; a fence or HTML
comment opened in the section and never closed (it would hide the rest of the
section); an unmarked prose line; a malformed entry or field line; an entry
with no history; an unknown kind or status; a malformed date; a field the kind
requires missing, or one it forbids present, or one given twice; a bad
`changed behaviour`; a bad building slice; a criterion id given twice.
Limits inherited from covers_gate's masker (setext headings, lenient fences,
lookalike headings) apply here exactly as they do to `## Slices`.

Entry text is untrusted vault data and is returned as written: callers must
clean it before display.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date
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

REASON_CODES = frozenset(
    {
        "duplicate-heading",
        "unterminated-fence",
        "unmarked-prose",
        "bad-entry-line",
        "duplicate-entry",
        "missing-history",
        "unknown-kind",
        "unknown-status",
        "malformed-date",
        "missing-check",
        "missing-how",
        "missing-waits-on",
        "missing-changed-behaviour",
        "bad-changed-behaviour",
        "unexpected-field",
        "duplicate-field",
        "bad-building-slice",
    }
)

KINDS = ("pending check", "set aside")
STATUSES = ("waiting", "passed", "failed", "released")

_HEADING = "## Waiting"
_HEADING_RE = re.compile(r"^## Waiting$", re.IGNORECASE)
_ENTRY_RE = re.compile(r"^- \*\*(AC[1-9][0-9]{0,8})\.\*\* (.+)$")
_FIELD_RE = re.compile(r"^  - (check|how|changed behaviour|waits on): ?(.*)$")
_HISTORY_RE = re.compile(r"^  - (\d\S*) (\S+): ?(.*)$")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SLICE_RE = re.compile(r"task/([a-z0-9][a-z0-9._-]*)")
_KIND_SEPARATOR = " — "


class WaitingParseError(ValueError):
    def __init__(self, code: str, message: str):
        assert code in REASON_CODES, code
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class WaitingEntry:
    criterion_id: str
    building_slice: str | None
    kind: str
    check: str | None
    how: str | None
    changed_behaviour: bool
    waits_on: str | None
    history: tuple[tuple[str, str, str], ...]

    @property
    def status(self) -> str:
        return self.history[-1][1]

    @property
    def set_date(self) -> str | None:
        """Date of the latest `waiting` history line, if any."""
        for when, status, _ in reversed(self.history):
            if status == "waiting":
                return when
        return None


def _valid_date(text: str) -> bool:
    if _DATE_RE.fullmatch(text) is None:
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _section_lines(lines: list[str], masked: list[bool], start: int) -> list[str]:
    end = len(lines)
    for i in range(start, len(lines)):
        if not masked[i] and lines[i].startswith("## "):
            end = i
            break
    section = lines[start:end]
    # A sentinel line is masked only if a fence or comment is still open.
    section_masked = _mask_fenced_lines(section + ["sentinel"])
    if section_masked[-1]:
        raise WaitingParseError(
            "unterminated-fence",
            "a fence or HTML comment in the section is never closed",
        )
    return [ln for ln, m in zip(section, section_masked) if not m]


class _Draft:
    def __init__(self, criterion_id: str, kind: str, building_slice: str | None):
        self.criterion_id = criterion_id
        self.kind = kind
        self.building_slice = building_slice
        self.fields: dict[str, str] = {}
        self.history: list[tuple[str, str, str]] = []

    def finish(self) -> WaitingEntry:
        f = self.fields
        pending = self.kind == "pending check"
        required = (
            (("check", "missing-check"), ("how", "missing-how"),
             ("changed behaviour", "missing-changed-behaviour"))
            if pending
            else (("waits on", "missing-waits-on"),)
        )
        for name, code in required:
            if not f.get(name, "").strip():
                raise WaitingParseError(code, f"{self.criterion_id} has no {name}")
        allowed = {n for n, _ in required}
        for name in f:
            if name not in allowed:
                raise WaitingParseError(
                    "unexpected-field",
                    f"{self.criterion_id} is a {self.kind} and cannot carry {name}",
                )
        changed = False
        if pending:
            if f["changed behaviour"] not in ("yes", "no"):
                raise WaitingParseError(
                    "bad-changed-behaviour",
                    f"{self.criterion_id}: changed behaviour must be yes or no",
                )
            changed = f["changed behaviour"] == "yes"
        if not self.history:
            raise WaitingParseError(
                "missing-history", f"{self.criterion_id} has no history line"
            )
        return WaitingEntry(
            criterion_id=self.criterion_id,
            building_slice=self.building_slice,
            kind=self.kind,
            check=f.get("check"),
            how=f.get("how"),
            changed_behaviour=changed,
            waits_on=f.get("waits on"),
            history=tuple(self.history),
        )


def _open_entry(criterion_id: str, rest: str) -> _Draft:
    kind, sep, slice_text = rest.strip().partition(_KIND_SEPARATOR)
    if kind not in KINDS:
        raise WaitingParseError("unknown-kind", f"{criterion_id}: unknown kind {kind!r}")
    if kind == "set aside":
        if sep:
            raise WaitingParseError(
                "bad-building-slice", f"{criterion_id}: a set-aside criterion has no building slice"
            )
        return _Draft(criterion_id, kind, None)
    m = _SLICE_RE.fullmatch(slice_text) if sep else None
    if m is None:
        raise WaitingParseError(
            "bad-building-slice",
            f"{criterion_id}: a pending check needs ` — task/<id>` with a safe id",
        )
    return _Draft(criterion_id, kind, m.group(1))


def parse_waiting(body: str) -> list[WaitingEntry]:
    lines = _COMMONMARK_LINE_RE.split(body)
    masked = _mask_fenced_lines(lines)
    try:
        start = _find_unique_heading(
            lines, masked, _HEADING_RE, _HEADING, "duplicate-heading"
        )
    except DuplicateHeadingError as exc:
        raise WaitingParseError("duplicate-heading", str(exc)) from exc
    if start is None:
        return []

    drafts: list[_Draft] = []
    seen: set[str] = set()
    for raw in _section_lines(lines, masked, start):
        line = raw.rstrip()
        if not line.strip():
            continue
        entry = _ENTRY_RE.match(line)
        if entry:
            cid = entry.group(1)
            if cid in seen:
                raise WaitingParseError("duplicate-entry", f"{cid} appears twice")
            seen.add(cid)
            drafts.append(_open_entry(cid, entry.group(2)))
            continue
        if not line.startswith((" ", "-")):
            raise WaitingParseError("unmarked-prose", f"unmarked line: {line[:60]!r}")
        if not drafts:
            raise WaitingParseError(
                "unmarked-prose" if not line.startswith("-") else "bad-entry-line",
                f"line outside any entry: {line[:60]!r}",
            )
        draft = drafts[-1]
        field = _FIELD_RE.match(line)
        if field:
            if draft.history:
                raise WaitingParseError(
                    "bad-entry-line", f"{draft.criterion_id}: field after history"
                )
            name = field.group(1)
            if name in draft.fields:
                raise WaitingParseError(
                    "duplicate-field", f"{draft.criterion_id}: {name} given twice"
                )
            draft.fields[name] = field.group(2).strip()
            continue
        hist = _HISTORY_RE.match(line)
        if hist:
            when, status, words = hist.groups()
            if not _valid_date(when):
                raise WaitingParseError(
                    "malformed-date", f"{draft.criterion_id}: bad date {when!r}"
                )
            if status not in STATUSES:
                raise WaitingParseError(
                    "unknown-status", f"{draft.criterion_id}: unknown status {status!r}"
                )
            draft.history.append((when, status, words.strip()))
            continue
        raise WaitingParseError(
            "bad-entry-line", f"{draft.criterion_id}: unreadable line {line[:60]!r}"
        )
    return [d.finish() for d in drafts]
