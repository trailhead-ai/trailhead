#!/usr/bin/env python3
"""Wording grader — one verdict line over one captured model output.

Usage:
    cat capture.txt | wording_grader.py --check criterion-mention --criteria spec.md
    cat capture.txt | wording_grader.py --check criterion-mention --criteria spec.md --drop-value-line
    cat capture.txt | wording_grader.py --check covers-field
    cat capture.txt | wording_grader.py --check reviewer-report

The capture arrives on stdin. The grammar each check implements is the one the
ritual-output-wording eval case fixes in its `expected.md`, read together with
that file's dated amendment.

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

stdout carries exactly the line `<check>: <verdict>`.

Exit codes:
    0  a passing verdict (text-with-number, template-shape, gate-shape)
    1  any other verdict
    2  could not grade: nothing on stdout, a `reason-code:` line on stderr —
       bad-arguments, missing-check, unknown-check, option-not-applicable,
       missing-criteria, criteria-unreadable, criteria-not-utf8,
       no-criteria-found, duplicate-criterion, empty-input, input-too-large,
       not-utf8. Zero bytes of stdin is `empty-input`; input that is only
       whitespace is a capture and is graded.

Input is capped at MAX_INPUT_BYTES. Every pattern is linear and none nests one
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
}
PASSING = frozenset({"text-with-number", "template-shape", "gate-shape"})

_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])AC(\d+)")
_DIGITS_RE = re.compile(r"(\d+)")
_LIST_CONNECTOR_RE = re.compile(r"\s*(?:,\s*and\s+|,\s*|&\s*|/\s*|\+\s*)|\s+(?:and|or)\s+")
_RANGE_CONNECTOR_RE = re.compile(r"\s*[-–—]\s*|\s+(?:to|through)\s+")
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_WORD_RE = re.compile(r"[a-z]+")
_MARKUP_RE = re.compile(r"[*_`]")
_LIST_ITEM_RE = re.compile(r"[ \t]*(?:[-*+]|\d+[.)])[ ]")
_CRITERION_LINE_RE = re.compile(r"[ \t]*(?:[-*+] )?[ \t]*\*\*AC(\d+)\.\*\*(.*)")
_FINDING_TAG_RE = re.compile(r"[ \t]*[-*]?[ \t]*(?:\*\*)?\[(?:Critical|Important|Minor)\]")
_COVERS_LABEL_RE = re.compile(r"\*\*Covers:\*\* *")

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


def _read_criteria(path: str) -> dict[int, list[str]]:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise CouldNotGrade("criteria-unreadable", f"cannot read {path}: {exc}") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CouldNotGrade("criteria-not-utf8", f"{path} is not UTF-8: {exc}") from exc
    criteria = read_criteria(text)
    if not criteria:
        raise CouldNotGrade("no-criteria-found", f"{path} has no `**AC<n>.**` criterion lines")
    return criteria


def run(argv: list[str]) -> tuple[str, str]:
    parser = _Parser(description="Grade one captured output for one wording check.")
    parser.add_argument("--check")
    parser.add_argument("--criteria")
    parser.add_argument("--drop-value-line", action="store_true")
    args = parser.parse_args(argv)

    if args.check is None:
        raise CouldNotGrade("missing-check", "--check is required")
    if args.check not in VERDICT_WORDS:
        raise CouldNotGrade("unknown-check", f"unknown check {args.check!r}")
    if args.drop_value_line and args.check != "criterion-mention":
        raise CouldNotGrade("option-not-applicable", "--drop-value-line applies to criterion-mention only")
    criteria = None
    if args.check == "criterion-mention":
        if args.criteria is None:
            raise CouldNotGrade("missing-criteria", "criterion-mention needs --criteria")
        criteria = _read_criteria(args.criteria)
    text = _read_stdin()

    if args.check == "criterion-mention":
        verdict = grade_criterion_mention(text, criteria, args.drop_value_line)
        gap = ""
    elif args.check == "covers-field":
        verdict, gap = grade_covers_field(text), ""
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
