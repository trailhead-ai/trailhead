#!/usr/bin/env python3
"""Wording grader — one verdict line over one captured model output.

Usage:
    cat capture.txt | wording_grader.py --check criterion-mention --criteria spec.md
    cat capture.txt | wording_grader.py --check criterion-mention --criteria spec.md --drop-value-line
    cat capture.txt | wording_grader.py --check covers-field
    cat capture.txt | wording_grader.py --check reviewer-report
    cat capture.txt | wording_grader.py --check severity-name --reports reports-lenses.md
    cat capture.txt | wording_grader.py --check reviewer-name --reports reports-lenses.md
    cat capture.txt | wording_grader.py --check calibration-level --reports reports-calibrated.md
    cat capture.txt | wording_grader.py --check code-review-name --reports reports-code-review.md
    cat capture.txt | wording_grader.py --check persisted-levels
    cat capture.txt | wording_grader.py --check review-name --reports reports-spec-review.md
    cat capture.txt | wording_grader.py --check outcome-word --reports reports-spec-review.md
    cat capture.txt | wording_grader.py --check finding-id --reports reports-spec-review.md
    cat capture.txt | wording_grader.py --check finding-id --saved-detail
    cat capture.txt | wording_grader.py --check criterion-mention --criteria spec.md --saved-detail
    cat capture.txt | wording_grader.py --check stamp-form
    cat capture.txt | wording_grader.py --check override-mapping --override C2=accepted-as-risk:<reason>

The capture arrives on stdin. The grammar of `criterion-mention`, `covers-field`
and `reviewer-report` is the one the ritual-output-wording eval case fixes in its
`expected.md`, read together with that file's dated amendment; the grammar of
`severity-name`, `reviewer-name`, `calibration-level` and `persisted-levels` is the one the
finding-presentation-wording case fixes in its `expected.md`.

`criterion-mention` reads every acceptance-criterion number in the capture and
says whether each is a criterion's own words followed by its number in round
brackets. Verdicts: `text-with-number`, `nickname`, `bare`, `no-mention`.
`--criteria` names the spec whose `**AC<n>.**` lines give each criterion's own
words. `--drop-value-line` reads the capture without its last non-blank line when
that line contains a criterion number, for a capture that ends in the drafted
covered-criteria value.

`covers-field` reads the last non-blank line, minus one leading `**Covers:**`
label, against the identifier-list grammar the covers gate certifies (imported
from `covers_gate`, not retyped). Verdicts: `gate-shape`, `off-shape`, `absent`.

`reviewer-report` checks the reviewer-report template's shape: the findings heading, at
least one severity-tagged finding line, the confidence heading and text after
it. Verdicts: `template-shape`, `off-shape`; an `off-shape` verdict names the
missing element on stderr as `missing: <element>`.

`severity-name` and `reviewer-name` read the capture's reader-facing text: its
logical lines without any fenced block and without a line that quotes a reviewer
report verbatim. `--reports` names a canned report file (repeatable) that supplies
those quotations. Verdicts: `plain`, `internal`, `none`. `internal` when a
severity level or reviewer role is named in the working vocabulary, else `plain`
when a plain reading appears, else `none`.

`calibration-level` reads the same reader-facing text and says whether a severity word
(`critical`, `criticals`, `important`, `minor`, any case, whole word) shares a logical line
with a calibration word (`prototype`, `early`, `production`, `maturity`, `level`, `waive`,
`waived`, `waiver`, `stand-down`, `downgrade`, `downgraded`, `rate`, `rated`). Verdicts:
`plain`, `internal`, `none`. `internal` on such a line, else `plain` when the whole word
`prototype` appears in reader-facing text, else `none`. The grammar is the dated extension
at the end of the finding-presentation-wording case's `expected.md`.

`code-review-name` reads the same reader-facing text and says how the capture names the code
reviewer. Verdicts: `plain`, `internal`, `none`. `internal` when the whole phrase `code reviewer`,
`code-reviewer` (or the plural, any case) appears, else `plain` when the whole phrase `code review` or
`code-review` appears, else `none`. A whole phrase is not adjacent to a letter, a digit or a hyphen.
It takes `--reports` like the other report checks. The grammar is the "Grammar of `code-review-name`"
section of the finding-presentation-wording case's `expected.md`.

`persisted-levels` reads only the first fenced block that holds a `## Council Review`
line, for the three italic level labels and a Critical list that is not empty or
`none`. Verdicts: `kept`, `changed`, `missing`. It reads no `--reports`.

`review-name`, `outcome-word` and `finding-id` read the same reader-facing text. `review-name` says how the capture
first names the spec review: `plain` when "spec review" comes first and "gauntlet" appears exactly once, inside round
brackets; `internal` otherwise; `none` when the review is not named (a `/craft:gauntlet` command token is not a
naming). `outcome-word` says how a finding's outcome is shown: `internal` for a ritual term (`resolved`, `revise`,
`accepted-as-risk`, `disputed`, `answered`) in backticks, the hyphenated `accepted-as-risk`, or `resolved` or `revise`
on a line that carries a finding id; else `plain` when "fixed in the spec" or "needs a rewrite" appears; else `none`.
`finding-id` says whether every finding id (`C<n>`) is in round brackets after text, or is the id in a reply form
("dispute C2:", "accept C2 as a risk", "answer C2:"): `bracketed`; `bare` otherwise; `none` when no id appears. The
grammar is the plain-words extension of the finding-presentation-wording case's `expected.md`.

`--saved-detail` applies `criterion-mention` and `finding-id` to the saved review section's detail instead of the
capture's reader-facing text: the first fenced block holding a `## Gauntlet` line, minus its fences, heading, marker
bullet and stamp bullet. `finding-id` then reads no `--reports`.

`stamp-form` reads the stamp bullet of that saved section. Verdicts: `kept` when every finding id in it is followed by
a backticked ritual term, `changed` when one is not or none appears, `missing` when there is no saved section or no
stamp bullet. `override-mapping` reads the same stamp against each `--override C<n>=<term>:<reason>` (repeatable):
`mapped` when each staged id carries its term and its reason and no other id carries an override term, else
`misapplied`.

stdout carries exactly the line `<check>: <verdict>`.

Exit codes:
    0  a passing verdict (text-with-number, template-shape, gate-shape, plain, kept, bracketed, mapped)
    1  any other verdict
    2  could not grade: nothing on stdout, a `reason-code:` line on stderr —
       bad-arguments, missing-check, unknown-check, option-not-applicable,
       missing-criteria, missing-override, criteria-unreadable, criteria-not-utf8,
       no-criteria-found, duplicate-criterion, missing-reports,
       reports-unreadable, reports-not-utf8, criteria-too-large,
       reports-too-large, empty-input, input-too-large, not-utf8. Zero bytes of stdin is `empty-input`;
       input that is only whitespace is a capture and is graded.

Stdin, each `--criteria` file and each `--reports` file are capped at MAX_INPUT_BYTES: a
file over the cap is never read past it and could not be graded (`criteria-too-large`,
`reports-too-large`). Every pattern is linear and none nests one
repeat inside another; the scans that need a window walk the line by index.
A range wider than MAX_RANGE_SPAN is not expanded: it can name no criterion the
criteria file defines and counts as no earlier mention.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from covers_gate import parse_covers  # noqa: E402

MAX_INPUT_BYTES = 262144
MAX_RANGE_SPAN = 10000
WINDOW_WORDS = 60
SHARE_THRESHOLD = 0.5

VERDICT_WORDS = {
    "criterion-mention": ("text-with-number", "nickname", "bare", "no-mention"),
    "reviewer-report": ("template-shape", "off-shape"),
    "covers-field": ("gate-shape", "off-shape", "absent"),
    "severity-name": ("plain", "internal", "none"),
    "reviewer-name": ("plain", "internal", "none"),
    "persisted-levels": ("kept", "changed", "missing"),
    "calibration-level": ("plain", "internal", "none"),
    "code-review-name": ("plain", "internal", "none"),
    "review-name": ("plain", "internal", "none"),
    "outcome-word": ("plain", "internal", "none"),
    "finding-id": ("bracketed", "bare", "none"),
    "stamp-form": ("kept", "changed", "missing"),
    "override-mapping": ("mapped", "misapplied"),
}
_REPORT_CHECKS = (
    "severity-name",
    "reviewer-name",
    "calibration-level",
    "code-review-name",
    "review-name",
    "outcome-word",
    "finding-id",
)
_SAVED_DETAIL_CHECKS = ("criterion-mention", "finding-id")
PASSING = frozenset({"text-with-number", "template-shape", "gate-shape", "plain", "kept", "bracketed", "mapped"})

_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])AC(\d+)")
_DIGITS_RE = re.compile(r"(\d+)")
_LIST_CONNECTOR_RE = re.compile(r"\s*(?:,\s*and\s+|,\s*|&\s*|/\s*|\+\s*)|\s+(?:and|or)\s+")
_RANGE_CONNECTOR_RE = re.compile(r"\s*[-–—]\s*|\s+(?:to|through)\s+")
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_WORD_RE = re.compile(r"[a-z]+")
_MARKUP_RE = re.compile(r"[*_`]")
_EMPHASIS_RE = re.compile(r"[*_]")
_LIST_ITEM_RE = re.compile(r"[ \t]*(?:[-*+]|\d+[.)])[ ]")
_CRITERION_LINE_RE = re.compile(r"[ \t]*(?:[-*+] )?[ \t]*\*\*AC(\d+)\.\*\*(.*)")
_FINDING_TAG_RE = re.compile(r"[ \t]*[-*]?[ \t]*(?:\*\*)?\[(?:Critical|Important|Minor)\]")
_COVERS_LABEL_RE = re.compile(r"\*\*Covers:\*\* *")

# A whole word is not adjacent to a letter, a digit or a hyphen; `_` is deleted before matching.
_BEFORE = r"(?<![^\W_]|-)"
_AFTER = r"(?![^\W_]|-)"
_WS_RE = re.compile(r"\s+")
_FINDING_WORDS = r"(?:findings?|issues?)"
_COUNT = r"[(\[]\d+[)\]]"
_SEVERITY_PATTERNS = (
    re.compile(rf"{_BEFORE}Criticals?{_AFTER}"),
    re.compile(rf"{_BEFORE}critical {_FINDING_WORDS}{_AFTER}"),
    re.compile(rf"{_BEFORE}(?:Important|Minor){_AFTER} *(?:[:\])]|{_COUNT})"),
    re.compile(rf"(?<=\d ){_BEFORE}(?:Important|Minor){_AFTER}"),
    re.compile(rf"{_BEFORE}(?:Important|Minor) {_FINDING_WORDS}{_AFTER}"),
)
_LEVEL_ONLY_RE = re.compile(rf"(?:Important|Minor)(?: *{_COUNT})?")
_GROUP_LABEL_RE = re.compile(rf"(?:Important|Minor){_AFTER} *[-\u2013\u2014(]")
_PLAIN_SEVERITY_RE = re.compile(
    rf"{_BEFORE}(?:needs your decision|worth fixing|nothing to decide){_AFTER}", re.IGNORECASE
)
_CALIBRATION_SEVERITY_RE = re.compile(rf"{_BEFORE}(?:criticals?|important|minor){_AFTER}", re.IGNORECASE)
_CALIBRATION_WORD_RE = re.compile(
    rf"{_BEFORE}(?:prototype|early|production|maturity|level|waive|waived|waiver|stand-down|downgrade|downgraded"
    rf"|rate|rated){_AFTER}",
    re.IGNORECASE,
)
_RESOLVED_LEVEL_RE = re.compile(rf"{_BEFORE}prototype{_AFTER}", re.IGNORECASE)
_INTERNAL_REVIEWER_PATTERNS = (
    re.compile(
        rf"{_BEFORE}(?:builder|breaker|attacker|advocate|premise-attacker|consistency-auditor|divergence-prober){_AFTER}",
        re.IGNORECASE,
    ),
    re.compile(rf"{_BEFORE}(?:Builder|Reliability|Advocate|Premise|Consistency|Divergence){_AFTER}"),
    re.compile(rf"{_BEFORE}fact[ -]verification{_AFTER}", re.IGNORECASE),
)
_PLAIN_REVIEWER_RE = re.compile(
    rf"{_BEFORE}(?:buildability|failure cases|right problem|self-consistency|left undecided|ease of use|fact check"
    rf"|security){_AFTER}",
    re.IGNORECASE,
)
_INTERNAL_CODE_REVIEW_RE = re.compile(rf"{_BEFORE}code[ -]reviewers?{_AFTER}", re.IGNORECASE)
_PLAIN_CODE_REVIEW_RE = re.compile(rf"{_BEFORE}code[ -]review{_AFTER}", re.IGNORECASE)
_SPEC_REVIEW_RE = re.compile(rf"{_BEFORE}spec review{_AFTER}", re.IGNORECASE)
_GAUNTLET_RE = re.compile(rf"(?<![/:]){_BEFORE}gauntlet{_AFTER}", re.IGNORECASE)
_RITUAL_TERMS = "resolved|revise|accepted-as-risk|disputed|answered"
_BACKTICKED_TERM_RE = re.compile(rf"`(?:{_RITUAL_TERMS})(?![A-Za-z-])[^`]*`")
_HYPHENATED_TERM_RE = re.compile(rf"{_BEFORE}accepted-as-risk{_AFTER}")
_ID_LINE_TERM_RE = re.compile(rf"{_BEFORE}(?:resolved|revise){_AFTER}", re.IGNORECASE)
_PLAIN_OUTCOME_RE = re.compile(rf"{_BEFORE}(?:fixed in the spec|needs a rewrite){_AFTER}", re.IGNORECASE)
_FINDING_ID_RE = re.compile(r"(?<![A-Za-z0-9])C(\d+)(?![A-Za-z0-9])")
_ID_LIST = r"C\d+(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*)C\d+)*"
_BRACKETED_IDS_RE = re.compile(rf"\(\s*({_ID_LIST})\s*\)")
_REPLY_FORM_RES = (
    re.compile(r"\b(?:dispute|answer)\s+(C\d+)\s*:", re.IGNORECASE),
    re.compile(r"\baccept\s+(C\d+)\s+as a risk\b", re.IGNORECASE),
)
_STAMP_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])C(\d+) `([a-z-]+)`")
_STAMP_TERM_RE = re.compile(rf" `(?:{_RITUAL_TERMS})`")
_OVERRIDE_TERMS = ("accepted-as-risk", "disputed", "answered")
_OVERRIDE_ARG_RE = re.compile(rf"C(\d+)=({'|'.join(_OVERRIDE_TERMS)}):(.*\S.*)")
_SAVED_HEADING = "## Gauntlet"
_BULLET_RE = re.compile(r"[-*+] ")
_FINDING_TAGS = ("[Critical]", "[Important]", "[Minor]")
_QUOTE_MIN_CHARS = 30
_LABELS = ("*Critical:*", "*Important:*", "*Minor:*")
_NONE_ENTRY_RE = re.compile(r"none\.?", re.IGNORECASE)

_STRIPPED_TAIL = " \t.,;:!?\"'-–—"
_TERMINATORS = ".!?;"


class CouldNotGrade(Exception):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


# --- reading a capture -----------------------------------------------------------------------


def _split_lines(text: str) -> list[str]:
    return _LINE_BREAK_RE.split(text)


def _logical_lines(physical: list[str]) -> list[str]:
    """Group physical lines into logical lines, then split table rows into cells and delete markup.

    A table separator row needs no case of its own: its cells hold only `-`, `:` and spaces, so they
    carry no criterion number and grade as nothing.
    """
    grouped: list[str] = []
    joinable = False
    for raw in physical:
        stripped = raw.strip()
        if not stripped:
            joinable = False
            continue
        starts_own = (
            raw.startswith("#")
            or stripped.startswith(("```", "~~~", "|"))
            or _LIST_ITEM_RE.match(raw) is not None
        )
        if joinable and not starts_own:
            grouped[-1] += " " + stripped
        else:
            grouped.append(stripped)
        joinable = True

    lines: list[str] = []
    for line in grouped:
        if line.startswith("|"):
            for cell in line.split("|"):
                if not cell.strip():
                    continue
                lines.append(_MARKUP_RE.sub("", cell))
        else:
            lines.append(_MARKUP_RE.sub("", line))
    return lines


# --- criterion-mention -----------------------------------------------------------------------


def _fold(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def read_criteria(text: str) -> dict[int, list[str]]:
    """Map criterion number to its own words: the distinct words of four or more letters."""
    bodies: dict[int, list[str]] = {}
    current: int | None = None
    for line in _split_lines(text):
        found = _CRITERION_LINE_RE.match(line)
        if found:
            current = int(found.group(1))
            if current in bodies:
                raise CouldNotGrade("duplicate-criterion", f"criterion AC{current} is defined twice")
            bodies[current] = [found.group(2)]
        elif not line.strip():
            current = None
        elif current is not None:
            bodies[current].append(line)
    return {
        number: sorted({w for w in _WORD_RE.findall(" ".join(parts).lower()) if len(w) >= 4})
        for number, parts in bodies.items()
    }


def _words_before(line: str, end: int) -> list[str]:
    """The last WINDOW_WORDS words of the line's last sentence before `end`, walking backward."""
    i = end - 1
    while i >= 0 and line[i] in _STRIPPED_TAIL:
        i -= 1
    words: list[str] = []
    run_end = -1
    while i >= 0 and len(words) < WINDOW_WORDS:
        ch = line[i]
        is_letter = "a" <= ch <= "z" or "A" <= ch <= "Z"
        if is_letter:
            if run_end < 0:
                run_end = i + 1
        else:
            if run_end >= 0:
                words.append(line[i + 1 : run_end].lower())
                run_end = -1
                if len(words) == WINDOW_WORDS:
                    break
            if ch in _TERMINATORS and i + 1 < end and line[i + 1] == " ":
                break
        i -= 1
    if run_end >= 0 and len(words) < WINDOW_WORDS:
        words.append(line[i + 1 : run_end].lower())
    return words


def _groups(line: str):
    """Yield (start, end, numbers) for each run of criterion numbers joined by connectors.

    `numbers` is a list of (low, high) inclusive spans, a single number being (n, n).
    """
    pos = 0
    while True:
        token = _TOKEN_RE.search(line, pos)
        if not token:
            return
        start = token.start()
        end = token.end()
        spans = [(int(token.group(1)),) * 2]
        after_plain_number = True
        while True:
            connector = _LIST_CONNECTOR_RE.match(line, end)
            if connector:
                nxt = _TOKEN_RE.match(line, connector.end())
                if nxt:
                    spans.append((int(nxt.group(1)),) * 2)
                    end = nxt.end()
                    after_plain_number = True
                    continue
            connector = _RANGE_CONNECTOR_RE.match(line, end) if after_plain_number else None
            if connector:
                nxt = _TOKEN_RE.match(line, connector.end()) or _DIGITS_RE.match(line, connector.end())
                if nxt:
                    first = spans[-1][1]
                    last = int(nxt.group(1))
                    spans[-1] = (first, last) if first <= last else (first, first)
                    if first > last:
                        spans.append((last, last))
                    end = nxt.end()
                    after_plain_number = False
                    continue
            break
        yield start, end, spans
        pos = end


def _bracketed(line: str, start: int, end: int) -> int | None:
    """Index of the opening bracket when the group sits wholly inside one round-bracket pair."""
    i = start - 1
    while i >= 0 and line[i].isspace():
        i -= 1
    j = end
    while j < len(line) and line[j].isspace():
        j += 1
    if i >= 0 and line[i] == "(" and j < len(line) and line[j] == ")":
        return i
    return None


def _share(number: int, before: set[str], criteria: dict[int, list[str]]) -> float:
    own = criteria.get(number)
    if not own:
        return 0.0
    return sum(1 for word in own if _fold(word) in before) / len(own)


def _expand(spans: list[tuple[int, int]]) -> list[int] | None:
    numbers: list[int] = []
    for low, high in spans:
        if high - low >= MAX_RANGE_SPAN:
            return None
        numbers.extend(range(low, high + 1))
    return numbers


def grade_criterion_mention(text: str, criteria: dict[int, list[str]], drop_value_line: bool) -> str:
    physical = _split_lines(text)
    if drop_value_line:
        for index in range(len(physical) - 1, -1, -1):
            if physical[index].strip():
                if _TOKEN_RE.search(physical[index]):
                    del physical[index]
                break

    seen: set[int] = set()
    verdict = "no-mention"
    rank = {"no-mention": 0, "text-with-number": 1, "nickname": 2, "bare": 3}
    for line in _logical_lines(physical):
        for start, end, spans in _groups(line):
            numbers = _expand(spans)
            opening = _bracketed(line, start, end)
            if opening is None:
                group = "bare"
            else:
                before = _words_before(line, opening)
                if not before:
                    group = "bare"
                elif numbers is not None and all(n in seen for n in numbers):
                    group = "text-with-number"
                elif numbers is not None and all(
                    _share(n, {_fold(w) for w in before}, criteria) >= SHARE_THRESHOLD for n in numbers
                ):
                    group = "text-with-number"
                else:
                    group = "nickname"
            if numbers is not None:
                seen.update(numbers)
            if rank[group] > rank[verdict]:
                verdict = group
    return verdict


# --- covers-field ----------------------------------------------------------------------------


def grade_covers_field(text: str) -> str:
    last = ""
    for line in _split_lines(text):
        if line.strip():
            last = line.strip()
    if not last or not _TOKEN_RE.search(last):
        # A line that is the field label and nothing else holds no value to read.
        if last and _COVERS_LABEL_RE.fullmatch(last):
            return "off-shape"
        return "absent"
    value = _COVERS_LABEL_RE.sub("", last, count=1) if _COVERS_LABEL_RE.match(last) else last
    try:
        parse_covers(value)
    except ValueError:
        return "off-shape"
    return "gate-shape"


# --- reviewer-report -------------------------------------------------------------------------


def reviewer_report_gap(text: str) -> str | None:
    """Name the first element the reviewer-report template needs that the report lacks, or None."""
    lines = _split_lines(text)
    findings = next((i for i, ln in enumerate(lines) if ln.strip() == "## Findings"), None)
    if findings is None:
        return "findings-heading"
    confidence = next((i for i in range(findings + 1, len(lines)) if lines[i].strip() == "## Confidence"), None)
    if confidence is None:
        return "confidence-heading"
    if not any(_FINDING_TAG_RE.match(ln) for ln in lines[findings + 1 : confidence]):
        return "tagged-finding"
    if not any(ln.strip() for ln in lines[confidence + 1 :]):
        return "confidence-text"
    return None


# --- severity-name and reviewer-name ---------------------------------------------------------


def _fence_run(line: str) -> tuple[str, int] | None:
    """The fence character and run length when the trimmed line begins with three or more of it."""
    stripped = line.strip()
    for char in ("`", "~"):
        if stripped.startswith(char * 3):
            return char, len(stripped) - len(stripped.lstrip(char))
    return None


def _fenced_blocks(physical: list[str]) -> list[tuple[int, int]]:
    """The fenced blocks as (first, last) line indexes, fence lines included; an unclosed one runs to the end."""
    blocks: list[tuple[int, int]] = []
    start = -1
    opening: tuple[str, int] = ("", 0)
    for index, line in enumerate(physical):
        run = _fence_run(line)
        if start < 0:
            if run is not None:
                start, opening = index, run
        elif run is not None and run[0] == opening[0] and run[1] >= opening[1]:
            if line.strip() == run[0] * len(line.strip()):
                blocks.append((start, index))
                start = -1
    if start >= 0:
        blocks.append((start, len(physical) - 1))
    return blocks


def _first_block_holding(physical: list[str], heading: str) -> list[str] | None:
    """The lines of the first fenced block with a line that is `heading` once trimmed, fences included."""
    for first, last in _fenced_blocks(physical):
        block = physical[first : last + 1]
        if any(line.strip() == heading for line in block):
            return block
    return None


def _fenced_flags(physical: list[str]) -> list[bool]:
    flags = [False] * len(physical)
    for first, last in _fenced_blocks(physical):
        for index in range(first, last + 1):
            flags[index] = True
    return flags


def _normalise(text: str, keep_ticks: bool = False) -> str:
    return _WS_RE.sub(" ", (_EMPHASIS_RE if keep_ticks else _MARKUP_RE).sub("", text)).strip()


def _outside_fences(physical: list[str]) -> list[tuple[str, bool]]:
    """The logical lines outside fenced blocks as (text, is_heading) pairs; a block ends the line before it."""
    grouped: list[tuple[str, bool]] = []
    parts: list[str] = []
    heading_line = False
    joinable = False
    def close() -> None:
        if parts:
            grouped.append((" ".join(parts), heading_line))
            parts.clear()

    for raw, fenced in zip(physical, _fenced_flags(physical)):
        stripped = raw.strip()
        if fenced or not stripped:
            close()
            joinable = False
            continue
        is_heading = stripped.startswith("#")
        starts_own = is_heading or stripped.startswith("|") or _LIST_ITEM_RE.match(raw) is not None
        if joinable and not starts_own:
            parts.append(stripped)
        else:
            close()
            parts.append(stripped)
            heading_line = is_heading
        joinable = True
    close()

    lines: list[tuple[str, bool]] = []
    for text, heading in grouped:
        if text.startswith("|"):
            lines.extend((cell, False) for cell in text.split("|") if cell.strip())
        else:
            lines.append((text, heading))
    return lines


def _tagged_text(normalised: str) -> tuple[str, str] | None:
    """(tag, text after the tag) when the line, minus one `>` and a list marker, begins with a finding tag."""
    text = _without_quote_and_list_marker(normalised)
    for tag in _FINDING_TAGS:
        if text.startswith(tag):
            return tag, text[len(tag) :].strip()
    return None


def _report_quotations(reports_text: str) -> dict[str, list[str]]:
    by_tag: dict[str, list[str]] = {tag: [] for tag in _FINDING_TAGS}
    for text, _ in _outside_fences(_split_lines(reports_text)):
        tagged = _tagged_text(_normalise(text))
        if tagged:
            by_tag[tagged[0]].append(tagged[1])
    return by_tag


def _is_verbatim_quotation(normalised: str, quotations: dict[str, list[str]]) -> bool:
    tagged = _tagged_text(normalised)
    if tagged is None or len(tagged[1]) < _QUOTE_MIN_CHARS:
        return False
    return any(source.startswith(tagged[1]) for source in quotations[tagged[0]])


def _reader_facing(
    text: str, quotations: dict[str, list[str]], keep_ticks: bool = False
) -> list[tuple[str, bool]]:
    """Normalised reader-facing logical lines as (text, is_heading) pairs; `keep_ticks` leaves backticks in."""
    kept = []
    for line, heading in _outside_fences(_split_lines(text)):
        normalised = _normalise(line)
        if not _is_verbatim_quotation(normalised, quotations):
            kept.append((_normalise(line, keep_ticks) if keep_ticks else normalised, heading))
    return kept


def _without_quote_and_list_marker(text: str) -> str:
    """The text minus one leading `>` and then one leading list marker, trimmed."""
    if text.startswith(">"):
        text = text[1:]
    marker = _LIST_ITEM_RE.match(text)
    if marker:
        text = text[marker.end() :]
    return text.strip()


def _is_level_label_line(text: str, heading: bool) -> bool:
    if heading:
        text = text.lstrip("#").strip()
    text = _without_quote_and_list_marker(text)
    return _LEVEL_ONLY_RE.fullmatch(text) is not None or _GROUP_LABEL_RE.match(text) is not None


def grade_severity_name(text: str, quotations: dict[str, list[str]]) -> str:
    lines = _reader_facing(text, quotations)
    for line, heading in lines:
        if any(pattern.search(line) for pattern in _SEVERITY_PATTERNS) or _is_level_label_line(line, heading):
            return "internal"
    return "plain" if any(_PLAIN_SEVERITY_RE.search(line) for line, _ in lines) else "none"


def grade_reviewer_name(text: str, quotations: dict[str, list[str]]) -> str:
    lines = _reader_facing(text, quotations)
    for line, _ in lines:
        if any(pattern.search(line) for pattern in _INTERNAL_REVIEWER_PATTERNS):
            return "internal"
    return "plain" if any(_PLAIN_REVIEWER_RE.search(line) for line, _ in lines) else "none"


def grade_calibration_level(text: str, quotations: dict[str, list[str]]) -> str:
    lines = _reader_facing(text, quotations)
    for line, _ in lines:
        if _CALIBRATION_SEVERITY_RE.search(line) and _CALIBRATION_WORD_RE.search(line):
            return "internal"
    return "plain" if any(_RESOLVED_LEVEL_RE.search(line) for line, _ in lines) else "none"


def grade_code_review_name(text: str, quotations: dict[str, list[str]]) -> str:
    lines = _reader_facing(text, quotations)
    if any(_INTERNAL_CODE_REVIEW_RE.search(line) for line, _ in lines):
        return "internal"
    return "plain" if any(_PLAIN_CODE_REVIEW_RE.search(line) for line, _ in lines) else "none"


# --- review-name, outcome-word and finding-id ------------------------------------------------


def _in_round_brackets(line: str, position: int) -> bool:
    before = line[:position]
    return before.count("(") > before.count(")") and ")" in line[position:]


def grade_review_name(text: str, quotations: dict[str, list[str]]) -> str:
    gauntlets: list[bool] = []
    first: str | None = None
    for line, _ in _reader_facing(text, quotations):
        found = [(m.start(), "spec review") for m in _SPEC_REVIEW_RE.finditer(line)]
        found += [(m.start(), "gauntlet") for m in _GAUNTLET_RE.finditer(line)]
        for position, name in sorted(found):
            if first is None:
                first = name
            if name == "gauntlet":
                gauntlets.append(_in_round_brackets(line, position))
    if first is None:
        return "none"
    return "plain" if first == "spec review" and gauntlets == [True] else "internal"


def grade_outcome_word(text: str, quotations: dict[str, list[str]]) -> str:
    lines = [line for line, _ in _reader_facing(text, quotations, keep_ticks=True)]
    for line in lines:
        if _BACKTICKED_TERM_RE.search(line) or _HYPHENATED_TERM_RE.search(line):
            return "internal"
        if _FINDING_ID_RE.search(line) and _ID_LINE_TERM_RE.search(line):
            return "internal"
    return "plain" if any(_PLAIN_OUTCOME_RE.search(line) for line in lines) else "none"


def _bracketed_id_starts(line: str) -> set[int]:
    """Where the ids that sit in a round-bracket pair after text, or in a reply form, begin."""
    allowed: set[int] = set()
    for group in _BRACKETED_IDS_RE.finditer(line):
        if any(ch.isalpha() for ch in line[: group.start()]):
            allowed.update(m.start() for m in _FINDING_ID_RE.finditer(line, group.start(1), group.end(1)))
    for pattern in _REPLY_FORM_RES:
        allowed.update(m.start(1) for m in pattern.finditer(line))
    return allowed


def _grade_finding_id(lines: list[str]) -> str:
    verdict = "none"
    for line in lines:
        allowed = _bracketed_id_starts(line)
        for found in _FINDING_ID_RE.finditer(line):
            if found.start() in allowed:
                verdict = "bracketed" if verdict == "none" else verdict
            else:
                return "bare"
    return verdict


def grade_finding_id(text: str, quotations: dict[str, list[str]]) -> str:
    return _grade_finding_id([line for line, _ in _reader_facing(text, quotations)])


# --- the saved section: detail, stamp-form and override-mapping ------------------------------


def _saved_parts(text: str) -> tuple[list[str] | None, str]:
    """The stamp bullet's lines (None when absent) and the detail text of the saved section.

    The section is the first fenced block holding a `## Gauntlet` line. Its detail is what remains once the fences,
    the heading, the marker bullet and the stamp bullet are taken out; a bullet runs over the non-blank lines that do
    not open a bullet or a heading of their own.
    """
    block = _first_block_holding(_split_lines(text), _SAVED_HEADING)
    if block is None:
        return None, ""
    body = block[1:]
    stamp: list[str] | None = None
    detail: list[str] = []
    current = "detail"
    for line in body:
        stripped = line.strip()
        if stripped == _SAVED_HEADING:
            current = "detail"
            continue
        opens_own = _BULLET_RE.match(line) is not None or line.startswith("#")
        if not stripped:
            current = "detail"
        elif opens_own:
            label = _MARKUP_RE.sub("", line[2:] if _BULLET_RE.match(line) else line).lstrip().lower()
            if label.startswith("retained review evidence"):
                current = "marker"
            elif label.startswith("adversarial spec review"):
                current = "stamp"
                stamp = []
            else:
                current = "detail"
        if current == "stamp":
            stamp.append(line)
        elif current == "detail":
            detail.append(line)
    return stamp, "\n".join(detail)


def saved_detail(text: str) -> str:
    return _saved_parts(text)[1]


def grade_saved_finding_id(text: str) -> str:
    return _grade_finding_id(_logical_lines(_split_lines(saved_detail(text))))


def _stamp_text(text: str) -> str | None:
    stamp = _saved_parts(text)[0]
    return None if stamp is None else _normalise("\n".join(stamp), keep_ticks=True)


def grade_stamp_form(text: str) -> str:
    stamp = _stamp_text(text)
    if stamp is None:
        return "missing"
    ids = list(_FINDING_ID_RE.finditer(stamp))
    if ids and all(_STAMP_TERM_RE.match(stamp, found.end()) for found in ids):
        return "kept"
    return "changed"


def _fold_reason(text: str) -> str:
    return _WS_RE.sub(" ", text).strip().casefold()


def grade_override_mapping(text: str, staged: list[tuple[str, str, str]]) -> str:
    stamp = _stamp_text(text)
    if stamp is None:
        return "misapplied"
    tokens = list(_STAMP_TOKEN_RE.finditer(stamp))
    segments: dict[str, list[tuple[str, str]]] = {}
    for index, token in enumerate(tokens):
        end = tokens[index + 1].start() if index + 1 < len(tokens) else len(stamp)
        segments.setdefault(f"C{token.group(1)}", []).append((token.group(2), _fold_reason(stamp[token.end() : end])))
    for found, entries in segments.items():
        if found not in {staged_id for staged_id, _, _ in staged} and any(term in _OVERRIDE_TERMS for term, _ in entries):
            return "misapplied"
    for staged_id, term, reason in staged:
        entries = segments.get(staged_id, [])
        if len(entries) != 1 or entries[0][0] != term or _fold_reason(reason) not in entries[0][1]:
            return "misapplied"
    return "mapped"


# --- persisted-levels ------------------------------------------------------------------------


def grade_persisted_levels(text: str) -> str:
    block = _first_block_holding(_split_lines(text), "## Council Review")
    if block is None:
        return "missing"
    entries: dict[str, list[str]] = {}
    current: str | None = None
    for line in block:
        trimmed = line.strip()
        marker = _LIST_ITEM_RE.match(trimmed)
        listed = marker is not None
        body = trimmed[marker.end() :].strip() if marker else trimmed
        label = next((name for name in _LABELS if body.startswith(name)), None)
        if label is not None:
            current = label
            entries.setdefault(label, [])
            rest = body[len(label) :].strip()
            if rest:
                entries[label].append(rest)
        elif listed and current is not None and body:
            entries[current].append(body)
    if any(label not in entries for label in _LABELS):
        return "changed"
    if all(_NONE_ENTRY_RE.fullmatch(entry) for entry in entries["*Critical:*"]):
        return "changed"
    return "kept"


# --- command line ----------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise CouldNotGrade("bad-arguments", message)


def _read_stdin() -> str:
    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if not data:
        raise CouldNotGrade("empty-input", "stdin is empty")
    if len(data) > MAX_INPUT_BYTES:
        raise CouldNotGrade("input-too-large", f"input is over {MAX_INPUT_BYTES} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CouldNotGrade("not-utf8", f"input is not UTF-8: {exc}") from exc


def _read_utf8(path: str, kind: str) -> str:
    """The file's text; `<kind>-unreadable`, `<kind>-too-large` or `<kind>-not-utf8` when it cannot be had."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise CouldNotGrade(f"{kind}-unreadable", f"cannot read {path}: {exc}") from exc
    if len(data) > MAX_INPUT_BYTES:
        raise CouldNotGrade(f"{kind}-too-large", f"{path} is over {MAX_INPUT_BYTES} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CouldNotGrade(f"{kind}-not-utf8", f"{path} is not UTF-8: {exc}") from exc


def _read_criteria(path: str) -> dict[int, list[str]]:
    criteria = read_criteria(_read_utf8(path, "criteria"))
    if not criteria:
        raise CouldNotGrade("no-criteria-found", f"{path} has no `**AC<n>.**` criterion lines")
    return criteria


def _read_reports(paths: list[str] | None) -> dict[str, list[str]]:
    if not paths:
        raise CouldNotGrade("missing-reports", "the naming checks need --reports")
    merged: dict[str, list[str]] = {tag: [] for tag in _FINDING_TAGS}
    for path in paths:
        for tag, quoted in _report_quotations(_read_utf8(path, "reports")).items():
            merged[tag].extend(quoted)
    return merged


def _read_overrides(values: list[str] | None) -> list[tuple[str, str, str]]:
    if not values:
        raise CouldNotGrade("missing-override", "override-mapping needs --override C<n>=<term>:<reason>")
    staged = []
    for value in values:
        found = _OVERRIDE_ARG_RE.fullmatch(value)
        if found is None:
            raise CouldNotGrade("bad-arguments", f"--override {value!r} is not C<n>=<term>:<reason>")
        staged.append((f"C{found.group(1)}", found.group(2), found.group(3)))
    return staged


def run(argv: list[str]) -> tuple[str, str]:
    parser = _Parser(description="Grade one captured output for one wording check.")
    parser.add_argument("--check")
    parser.add_argument("--criteria")
    parser.add_argument("--reports", action="append")
    parser.add_argument("--drop-value-line", action="store_true")
    parser.add_argument("--saved-detail", action="store_true")
    parser.add_argument("--override", action="append")
    args = parser.parse_args(argv)

    if args.check is None:
        raise CouldNotGrade("missing-check", "--check is required")
    if args.check not in VERDICT_WORDS:
        raise CouldNotGrade("unknown-check", f"unknown check {args.check!r}")
    if args.drop_value_line and args.check != "criterion-mention":
        raise CouldNotGrade("option-not-applicable", "--drop-value-line applies to criterion-mention only")
    if args.saved_detail and args.check not in _SAVED_DETAIL_CHECKS:
        raise CouldNotGrade("option-not-applicable", "--saved-detail applies to criterion-mention and finding-id only")
    if args.override and args.check != "override-mapping":
        raise CouldNotGrade("option-not-applicable", "--override applies to override-mapping only")
    staged = _read_overrides(args.override) if args.check == "override-mapping" else None
    criteria = None
    if args.check == "criterion-mention":
        if args.criteria is None:
            raise CouldNotGrade("missing-criteria", "criterion-mention needs --criteria")
        criteria = _read_criteria(args.criteria)
    needs_reports = args.check in _REPORT_CHECKS and not args.saved_detail
    quotations = _read_reports(args.reports) if needs_reports else None
    text = _read_stdin()

    if args.check == "criterion-mention":
        scoped = saved_detail(text) if args.saved_detail else text
        verdict = grade_criterion_mention(scoped, criteria, args.drop_value_line)
        gap = ""
    elif args.check == "finding-id" and args.saved_detail:
        verdict, gap = grade_saved_finding_id(text), ""
    elif args.check == "finding-id":
        verdict, gap = grade_finding_id(text, quotations), ""
    elif args.check == "review-name":
        verdict, gap = grade_review_name(text, quotations), ""
    elif args.check == "outcome-word":
        verdict, gap = grade_outcome_word(text, quotations), ""
    elif args.check == "stamp-form":
        verdict, gap = grade_stamp_form(text), ""
    elif args.check == "override-mapping":
        verdict, gap = grade_override_mapping(text, staged), ""
    elif args.check == "covers-field":
        verdict, gap = grade_covers_field(text), ""
    elif args.check == "severity-name":
        verdict, gap = grade_severity_name(text, quotations), ""
    elif args.check == "reviewer-name":
        verdict, gap = grade_reviewer_name(text, quotations), ""
    elif args.check == "calibration-level":
        verdict, gap = grade_calibration_level(text, quotations), ""
    elif args.check == "code-review-name":
        verdict, gap = grade_code_review_name(text, quotations), ""
    elif args.check == "persisted-levels":
        verdict, gap = grade_persisted_levels(text), ""
    else:
        found = reviewer_report_gap(text)
        verdict, gap = ("template-shape", "") if found is None else ("off-shape", found)
    return f"{args.check}: {verdict}", gap


def main(argv: list[str]) -> int:
    try:
        line, gap = run(argv)
    except CouldNotGrade as exc:
        print(f"wording-grader: {exc.detail}", file=sys.stderr)
        print(f"reason-code: {exc.reason_code}", file=sys.stderr)
        return 2
    print(line)
    if gap:
        print(f"missing: {gap}", file=sys.stderr)
    return 0 if line.split(": ", 1)[1] in PASSING else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
