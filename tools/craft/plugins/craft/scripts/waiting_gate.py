#!/usr/bin/env python3
"""Waiting gate — certifies a proposed write to a spec's `## Waiting` section
against the body it was composed from.

Usage:
    waiting_gate.py --prior <path> < proposed-body

The proposed spec body arrives on stdin. `--prior` names the JSON document
`lore record show spec/<n> --json` printed when the session read the spec; the
prior body and revision come from its `body` and `revision` fields (see
`gate_plumbing.load_prior`). On success the gate exits 0 and prints exactly one
line, `base: <revision>`, the revision the writer passes to
`lore record update --base`.

The section's entry grammar lives in `waiting.py`, the single parser. This gate
allows no status transition: every history line sets a status, and a new entry
carries exactly one `waiting` line, so an existing entry's history can never
grow through it. It refuses a proposed body that:
    - loses an entry the prior had
    - drops, reorders or edits a prior history line
    - appends any history line to an existing entry
    - changes an existing entry's check, how, changed behaviour or waits on
    - adds an entry whose history is not exactly one `waiting` line
    - carries a `## Waiting` line longer than `MAX_LINE_CHARS`
    - adds an entry whose text is credential-shaped
    - carries a control or format character on any `## Waiting` line that is not
      byte-identical to a line the prior section holds under the same entry
      (whitespace-class characters the parser strips included)
    - adds an entry dated after today
    - carries a fenced code block or HTML comment inside the section
    - differs from the prior anywhere outside `## Waiting`
    - creates the section anywhere but the end of the body

"Today" is the local date, or the ISO date in `CRAFT_WAITING_GATE_TODAY` when
that variable is set (a malformed value cannot certify).

The credential check is the shared credential-pattern heuristic: a tripwire, not a
guarantee, so common token formats outside its list pass.

Known boundaries: a raw `lore record update` skips this gate, and the `base:`
line certifies the caller's `--prior` document, not the vault's current state.

Exit codes:
    0  certified — `base: <revision>` on stdout.
    1  violation — `reason:` names the entry key, the field and the fix, and
       never repeats credential or control-character text. Stable
       `reason-code:` tokens: removed-entry, history-rewritten,
       status-transition, entry-changed, new-entry-not-waiting, field-too-long,
       credential-shaped-text, control-characters, masked-content, future-date,
       change-outside-waiting, section-not-at-end, plus the parser's own codes
       (`waiting.REASON_CODES`).
    2  could not certify — empty-stdin, non-utf8-stdin, stdin-too-large,
       unterminated-masked-region, unreadable-prior, malformed-prior,
       shared-layer-prior, malformed-prior-section, invalid-today.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import markdown_section  # noqa: E402
import waiting  # noqa: E402
from covers_gate import DuplicateHeadingError  # noqa: E402
from criterion_gate import (  # noqa: E402
    _neutralize_control_chars,
    _scrub_credential_shaped,
)
from gate_plumbing import Refusal, emit_refusal, load_prior, read_stdin  # noqa: E402
from observation_gate import _ends_inside_masked_region  # noqa: E402

TODAY_ENV = "CRAFT_WAITING_GATE_TODAY"
MAX_LINE_CHARS = 1000
_ENTRY_ID_RE = re.compile(r"^- \*\*(AC[1-9][0-9]{0,8})\.\*\*")
_FIELD_NAME_RE = re.compile(r"^  - (check|how|changed behaviour|waits on):")
_HISTORY_START_RE = re.compile(r"^  - \d")


def _today() -> date:
    raw = os.environ.get(TODAY_ENV)
    if raw is None:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise Refusal(f"{TODAY_ENV} is not an ISO date (YYYY-MM-DD)", "invalid-today", 2)


def _label(entry: waiting.WaitingEntry) -> str:
    if entry.building_slice is None:
        return f"{entry.criterion_id} (set aside)"
    return f"{entry.criterion_id} under task/{entry.building_slice}"


def _check_status_transitions(old: waiting.WaitingEntry, new: waiting.WaitingEntry) -> None:
    """Every status change is a history line appended to an existing entry.
    This gate allows none; any later writer that may set a status extends the
    checks here."""
    if len(new.history) > len(old.history):
        added = new.history[len(old.history)][1]
        raise Refusal(
            f"entry {_label(old)} gained a '{added}' history line — this gate does not "
            "set statuses; restore the prior history lines",
            "status-transition",
            1,
        )


def _check_existing(old: waiting.WaitingEntry, new: waiting.WaitingEntry) -> None:
    label = _label(old)
    if new.history[: len(old.history)] != old.history:
        raise Refusal(
            f"entry {label} has a history line dropped, reordered or edited — history is "
            "append-only; restore the prior history lines",
            "history-rewritten",
            1,
        )
    _check_status_transitions(old, new)
    for field in ("check", "how", "changed_behaviour", "waits_on"):
        if getattr(old, field) != getattr(new, field):
            name = field.replace("_", " ")
            raise Refusal(
                f"entry {label} has a changed '{name}' — an existing entry's fields are "
                "fixed; restore the prior line",
                "entry-changed",
                1,
            )


def _entry_texts(entry: waiting.WaitingEntry) -> list[tuple[str, str]]:
    texts = [
        ("check", entry.check),
        ("how", entry.how),
        ("waits on", entry.waits_on),
    ]
    texts += [("history words", words) for _, _, words in entry.history]
    return [(name, text) for name, text in texts if text is not None]


def _check_new(entry: waiting.WaitingEntry, today: date) -> None:
    label = _label(entry)
    if len(entry.history) != 1 or entry.history[0][1] != "waiting":
        raise Refusal(
            f"new entry {label} must carry exactly one 'waiting' history line",
            "new-entry-not-waiting",
            1,
        )
    for name, text in _entry_texts(entry):
        if _scrub_credential_shaped(text) != text:
            raise Refusal(
                f"new entry {label} has credential-shaped text in its {name} — remove "
                "the secret and describe it instead",
                "credential-shaped-text",
                1,
            )
    when = entry.history[0][0]
    if date.fromisoformat(when) > today:
        raise Refusal(
            f"new entry {label} is dated after today — date its history line today "
            "or earlier",
            "future-date",
            1,
        )


def _section_has_masked_lines(proposed: str) -> bool:
    lines, masked = markdown_section.split_body(proposed)
    located = markdown_section.section_range(
        lines, masked, waiting.HEADING, waiting.DUPLICATE_HEADING_REASON_CODE
    )
    if located is None:
        return False
    start, end = located
    return any(masked[start + 1 : end])


def _section_lines(body: str) -> list[str]:
    lines, masked = markdown_section.split_body(body)
    located = markdown_section.section_range(
        lines, masked, waiting.HEADING, waiting.DUPLICATE_HEADING_REASON_CODE
    )
    if located is None:
        return []
    start, end = located
    return lines[start + 1 : end]


def _owned_lines(body: str) -> list[tuple[str, str]]:
    """(owner, line) for every section line; the owner is the entry-opening
    line the line sits under, or the line itself when it opens one."""
    owner = ""
    owned = []
    for line in _section_lines(body):
        if line.strip() and not line.startswith(" "):
            owner = line
        owned.append((owner, line))
    return owned


def _line_field(line: str) -> str:
    if _ENTRY_ID_RE.match(line):
        return "entry line"
    m = _FIELD_NAME_RE.match(line)
    if m:
        return m.group(1)
    if _HISTORY_START_RE.match(line):
        return "history words"
    return "line"


def _check_line_lengths(proposed: str) -> None:
    """Bounds the text every later check scrubs or echoes."""
    for owner, line in _owned_lines(proposed):
        if len(line) > MAX_LINE_CHARS:
            m = _ENTRY_ID_RE.match(owner)
            key = m.group(1) if m else "an unkeyed line"
            raise Refusal(
                f"entry {key} has a {_line_field(line)} longer than {MAX_LINE_CHARS} "
                "characters — shorten it",
                "field-too-long",
                1,
            )


def _check_raw_lines(prior_body: str, proposed: str) -> None:
    """The parser strips whitespace, so a separator or other whitespace-class
    control character can sit in a line the parsed values never show. Every
    proposed section line is checked raw unless the prior section carries the
    same line under the same entry-opening line."""
    prior = set(_owned_lines(prior_body))
    for pair in _owned_lines(proposed):
        if pair not in prior and _neutralize_control_chars(pair[1]) != pair[1]:
            raise Refusal(
                "the '## Waiting' section has a control or format character on a line "
                "you added or changed — remove it",
                "control-characters",
                1,
            )


def _certify(prior_body: str, proposed: str) -> None:
    if _ends_inside_masked_region(markdown_section.split_body(proposed)[0]):
        raise Refusal(
            "the proposed body ends inside an open fenced code block or HTML comment — "
            "close it so the '## Waiting' section is fully visible",
            "unterminated-masked-region",
            2,
        )
    try:
        prior_entries = waiting.parse_waiting(prior_body)
    except (waiting.WaitingParseError, DuplicateHeadingError) as e:
        raise Refusal(
            f"the prior body's own '## Waiting' section does not parse: {e} — "
            "repair it with a direct edit before writing again",
            "malformed-prior-section",
            2,
        )
    try:
        _check_line_lengths(proposed)
    except DuplicateHeadingError:
        pass
    try:
        entries = waiting.parse_waiting(proposed)
    except waiting.WaitingParseError as e:
        raise Refusal(str(e), e.code, 1)

    if _section_has_masked_lines(proposed):
        raise Refusal(
            "the '## Waiting' section carries a fenced code block or HTML comment — "
            "remove it; the section holds entries only",
            "masked-content",
            1,
        )

    _check_raw_lines(prior_body, proposed)

    by_key = {e.key: e for e in entries}
    for old in prior_entries:
        new = by_key.get(old.key)
        if new is None:
            raise Refusal(
                f"entry {_label(old)} is missing from the proposed body — entries are "
                "never removed; restore it",
                "removed-entry",
                1,
            )
        _check_existing(old, new)

    prior_keys = {e.key for e in prior_entries}
    today = _today()
    for entry in entries:
        if entry.key not in prior_keys:
            _check_new(entry, today)

    if waiting.excise_section(prior_body) != waiting.excise_section(proposed):
        raise Refusal(
            "the proposed body differs from the prior outside '## Waiting' — change only "
            "that section (a first write appends it at the end of the body)",
            "change-outside-waiting",
            1,
        )
    if not waiting.has_section(prior_body) and not waiting.section_is_last(proposed):
        raise Refusal(
            "a new '## Waiting' section goes at the end of the body — move it below "
            "everything else",
            "section-not-at-end",
            1,
        )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Certify a proposed '## Waiting' write against the body it was composed "
        "from. Exit 0 certified, 1 violation, 2 could not certify."
    )
    ap.add_argument(
        "--prior",
        required=True,
        help="path to the JSON 'lore record show spec/<n> --json' printed when the spec was read",
    )
    args = ap.parse_args(argv)
    try:
        proposed = read_stdin()
        prior_body, revision = load_prior(args.prior)
        _certify(prior_body, proposed)
    except Refusal as r:
        return emit_refusal("waiting-gate", r)
    print(f"base: {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
