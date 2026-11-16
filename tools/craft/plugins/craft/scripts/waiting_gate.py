#!/usr/bin/env python3
"""Waiting gate — certifies a proposed write to a spec's `## Waiting` section
against the body it was composed from.

Two paths share one set of checks. The command line and `certify` allow no status
transition. `certify_answer` is the in-process path the check-list answer writer
(`checklist_answer.py`) owns: it additionally accepts exactly one new dated
`passed` or `failed` history line appended to one existing waiting pending check,
and refuses new entries.

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
    (answer path only, besides the above: an answer must be the single new line on
    one waiting pending check, `passed` or `failed`, dated the `today` passed in,
    with words for `failed`; a new entry or a body with no answer is refused)
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
       change-outside-waiting, section-not-at-end, the answer-path codes
       (`ANSWER_REASON_CODES`), plus the parser's own codes
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


ANSWER_MISSING = "answer-missing"
ANSWER_ADDS_ENTRY = "answer-adds-entry"
MORE_THAN_ONE_ANSWER = "more-than-one-answer"
ANSWER_NOT_PENDING_CHECK = "answer-not-pending-check"
ENTRY_NOT_WAITING = "entry-not-waiting"
ANSWER_STATUS_INVALID = "answer-status-invalid"
ANSWER_NOT_DATED_TODAY = "answer-not-dated-today"
ANSWER_WORDS_REQUIRED = "answer-words-required"
ANSWER_REASON_CODES = (
    ANSWER_MISSING,
    ANSWER_ADDS_ENTRY,
    MORE_THAN_ONE_ANSWER,
    ANSWER_NOT_PENDING_CHECK,
    ENTRY_NOT_WAITING,
    ANSWER_STATUS_INVALID,
    ANSWER_NOT_DATED_TODAY,
    ANSWER_WORDS_REQUIRED,
)


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


def _check_existing(
    old: waiting.WaitingEntry, new: waiting.WaitingEntry, answering: bool
) -> None:
    label = _label(old)
    if new.history[: len(old.history)] != old.history:
        raise Refusal(
            f"entry {label} has a history line dropped, reordered or edited — history is "
            "append-only; restore the prior history lines",
            "history-rewritten",
            1,
        )
    if not answering:
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


def _check_answers(
    answers: list[tuple[waiting.WaitingEntry, waiting.WaitingEntry]], today: date
) -> None:
    if not answers:
        raise Refusal(
            "the proposed body carries no answer — append one dated 'passed' or 'failed' "
            "line to one waiting pending check",
            ANSWER_MISSING,
            1,
        )
    if len(answers) > 1 or len(answers[0][1].history) - len(answers[0][0].history) > 1:
        raise Refusal(
            "the proposed body answers more than one check or adds more than one line — "
            "answer exactly one check with one line",
            MORE_THAN_ONE_ANSWER,
            1,
        )
    old, new = answers[0]
    label = _label(old)
    if old.kind != "pending check":
        raise Refusal(
            f"entry {label} is not a pending check — only a pending check takes an answer",
            ANSWER_NOT_PENDING_CHECK,
            1,
        )
    if old.status != "waiting":
        raise Refusal(
            f"entry {label} is not waiting (latest status '{old.status}') — only a waiting "
            "check takes an answer",
            ENTRY_NOT_WAITING,
            1,
        )
    when, status, words = new.history[-1]
    if status not in ("passed", "failed"):
        raise Refusal(
            f"entry {label} was given a '{status}' line — an answer is 'passed' or 'failed'",
            ANSWER_STATUS_INVALID,
            1,
        )
    if when != today.isoformat():
        raise Refusal(
            f"the answer to {label} is not dated today — date it {today.isoformat()}",
            ANSWER_NOT_DATED_TODAY,
            1,
        )
    if status == "failed" and not words:
        raise Refusal(
            f"a 'failed' answer to {label} needs words saying what failed",
            ANSWER_WORDS_REQUIRED,
            1,
        )
    if _scrub_credential_shaped(words) != words:
        raise Refusal(
            f"the answer to {label} has credential-shaped text in its history words — "
            "remove the secret and describe it instead",
            "credential-shaped-text",
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


def _certify(prior_body: str, proposed: str, answering: bool, today: date | None) -> None:
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
    answers = []
    for old in prior_entries:
        new = by_key.get(old.key)
        if new is None:
            raise Refusal(
                f"entry {_label(old)} is missing from the proposed body — entries are "
                "never removed; restore it",
                "removed-entry",
                1,
            )
        _check_existing(old, new, answering)
        if len(new.history) > len(old.history):
            answers.append((old, new))

    prior_keys = {e.key for e in prior_entries}
    if answering:
        if any(e.key not in prior_keys for e in entries):
            raise Refusal(
                "the proposed body adds an entry — the answer path only answers an "
                "existing check",
                ANSWER_ADDS_ENTRY,
                1,
            )
        _check_answers(answers, today)
    else:
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


def certify(prior_body: str, proposed: str) -> None:
    """Certify `proposed` against `prior_body`: returns None when every rule above
    holds, raises `gate_plumbing.Refusal` (carrying the reason code and exit code the
    CLI would print) otherwise. The entry point for writers that certify in-process."""
    _certify(prior_body, proposed, False, None)


def certify_answer(prior_body: str, proposed: str, today: date) -> None:
    """Certify `proposed` as one operator answer to `prior_body`: it differs from the
    prior only by one new dated `passed`/`failed` history line appended to one existing
    waiting pending check, dated `today` (the caller computes it once; the clock
    variable is not read here). Returns None when every rule holds, raises
    `gate_plumbing.Refusal` otherwise, with a code from `ANSWER_REASON_CODES` for the
    answer-specific refusals or any code `certify` raises."""
    _certify(prior_body, proposed, True, today)


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
        certify(prior_body, proposed)
    except Refusal as r:
        return emit_refusal("waiting-gate", r)
    print(f"base: {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
