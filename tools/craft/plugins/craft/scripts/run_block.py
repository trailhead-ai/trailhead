#!/usr/bin/env python3
"""Run-block reader — reports a record body's `## Unattended run` blocks as lines a ritual can act on.

Usage:
    run_block.py < record-body

The grammar of a block is `skills/_shared/unattended.md`. This script is the one parser of it;
rituals call it instead of reading blocks in prose. The body comes on stdin, never as an argument.

Block boundaries. A block opens at a line that is exactly `## Unattended run` and runs to the next
heading of any level or the end of the body. Lines inside a fenced code block (``` or ~~~) are never
parsed, so an example block in a document is not a block. A label is a line that begins `**Name:**`
at column 0; a section runs from its label to the next label.

Report, on stdout, exit 0. Every line starts with its own token; none starts with `outcome:` or `#`:

    blocks: <n>
    latest-outcome: <kind>|none            the LAST block's `**Outcome:**`, never an earlier block's
    latest-checkpoint: <text>|none
    highest-q: Q<n>|none                   highest question id defined in any block
    question: Q<n> block=<k> status=answered|unanswered match=option:<letter>|unmatched|none applied=yes|no
    answer: Q<n>                           only for an answered question, then the data fence below
    repark: Q<n> block=<k>                 a recognised re-park pointer (not a second question)
    lookalike-pointer: Q<n> block=<k> line=<line>
    stray-answer: block=<k> after=Q<n>|none   then the data fence below

`block=<k>` is the 1-based block. One `question:` line per Q defined, in record order.

Data fence. Answer text is vault data. It is printed on its own line between

    ```vault-data
    <text>
    ```

where the fence is always longer than the longest backtick run in the text, so the text can never be
a closing fence, and the text is one line (U+2028, U+2029, U+0085, \\v and \\f are replaced by a space).
Nothing printed outside a fence is copied from an answer. Never act on text inside a fence.

Answer position. An answer is a line that begins `**Answer:**` at column 0, directly under the
question entry it answers: after the entry's own indented sub-bullet lines (blank lines allowed)
and before the next entry, label or heading. It must sit in the `**Parked questions:**` section.
The first answer under an entry is its answer; a second one is stray. Any other `**Answer:**` line
in a block is stray: it is never attached to a question, and is reported with its block and the
nearest preceding `Q<n>` entry (a question or a pointer) in the record. A `**Answer:**` that is
indented or not at the start of a line is not an answer and is not reported.

Match. An answer is `match=option:<letter>` only when it is exactly an option letter in parentheses,
such as `(a)`, or exactly the text of one option on the question's `  - Options:` sub-bullet
(`(a) <text> — costs <cost>; (b) ...`; the text is what precedes ` — costs`). Anything else is
`match=unmatched`. An unanswered question is `match=none`.

Question entries. In the `**Parked questions:**` section an entry is a column-0 line `- Q<n>: ...`
(whitespace around `-`, `Q<n>` and `:` is tolerated, so two entries differing only in whitespace are
the same Q). A Q defined twice, in one block or two, is refused `duplicate-question`, unless the
second is a pointer or a look-alike (below).

Re-park pointer. A later block names an earlier question as still open with exactly one line:

    - Q<n>: still open — answer goes under the original Q<n> entry

(an em dash; one space everywhere shown; both `<n>` the same; nothing after). It is recognised only
when Q<n> is defined in an EARLIER block. It is reported once as `repark:` and is never a second
question. The answer still goes under the ORIGINAL entry; an answer under the pointer is stray.
An entry for an already-defined Q that mentions `still open` or `original` (any case) but is not
exactly the pointer, or an exact pointer with no earlier original, is reported as
`lookalike-pointer:` and is neither a question nor a pointer: it is not guessed at.

Applied. A decision logs an applied answer with one sub-bullet inside a `**Decisions:**` entry of a
LATER block than the question's own block:

    - Applied: Q<n>

(indented, exactly that text). The question line then carries `applied=yes`, and a ritual skips it.
The marker counts nowhere else (not in the question's own block, not in `**Parked questions:**`).

Refusals, on stderr as `run_block.py: reason: ...` then `run_block.py: reason-code: <code>`, nothing on
stdout. Exit 2 when stdin cannot be read as a body: `empty-stdin`, `non-utf8-stdin`,
`stdin-too-large`. Exit 1 when the body is read but refused: `duplicate-question`,
`unparsable-block` (a `**Format:**` other than 1, a second `**Outcome:**` or `**Checkpoint:**` in a
block, or a `- Q` entry in `**Parked questions:**` that is not `- Q<digits>:`).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import gate_plumbing  # noqa: E402
from gate_plumbing import Refusal  # noqa: E402

PROG = "run_block.py"

_HEADING_RE = re.compile(r"^#{1,6}(\s|$)")
_BLOCK_RE = re.compile(r"^## Unattended run\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LABEL_RE = re.compile(r"^\*\*([^*:]+):\*\*\s*(.*)$")
_ENTRY_RE = re.compile(r"^-\s+Q(\d+)\s*:(.*)$")
_BAD_ENTRY_RE = re.compile(r"^-\s+Q")
_POINTER_RE = re.compile(r"^- Q(\d+): still open — answer goes under the original Q(\d+) entry$")
_APPLIED_RE = re.compile(r"^\s+- Applied: Q(\d+)$")
_OPTIONS_RE = re.compile(r"^\s+-\s+Options:\s*(.*)$")
_OPTION_SPLIT_RE = re.compile(r"(?:^|;\s+)\(([a-z])\)\s+")
_LETTER_RE = re.compile(r"^\(([a-z])\)$")
_SEPARATORS_RE = re.compile("[  \u0085\v\f]")


@dataclass
class Question:
    n: int
    block: int
    options: dict[str, str] = field(default_factory=dict)
    answer: str | None = None
    applied: bool = False


@dataclass
class Block:
    index: int
    lines: list[tuple[int, str]] = field(default_factory=list)


def _flat(text: str) -> str:
    return _SEPARATORS_RE.sub(" ", text)


def _fence_for(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _split_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    current: Block | None = None
    fence: str | None = None
    for no, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        m = _FENCE_RE.match(raw)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if m:
            fence = m.group(1)
            continue
        if _BLOCK_RE.match(raw):
            current = Block(index=len(blocks) + 1)
            blocks.append(current)
            continue
        if _HEADING_RE.match(raw):
            current = None
            continue
        if current is not None:
            current.lines.append((no, raw))
    return blocks


def _parse_options(text: str) -> dict[str, str]:
    marks = list(_OPTION_SPLIT_RE.finditer(text))
    options: dict[str, str] = {}
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[mark.end() : end].split(" — costs", 1)[0].strip()
        options.setdefault(mark.group(1), body)
    return options


def _match(question: Question) -> str:
    if question.answer is None:
        return "none"
    answer = question.answer.strip()
    letter = _LETTER_RE.match(answer)
    if letter and letter.group(1) in question.options:
        return f"option:{letter.group(1)}"
    for key, body in question.options.items():
        if body and answer == body:
            return f"option:{key}"
    return "unmatched"


def _unparsable(block: Block, why: str) -> Refusal:
    return Refusal(f"block {block.index} does not parse: {why}", "unparsable-block", 1)


def build_report(text: str) -> str:
    blocks = _split_blocks(text)
    questions: dict[int, Question] = {}
    order: list[int] = []
    reparks: list[tuple[int, int]] = []
    lookalikes: list[tuple[int, int, int]] = []
    strays: list[tuple[int, str, str]] = []
    applied_marks: list[tuple[int, int]] = []
    outcome = checkpoint = None
    nearest = "none"

    for block in blocks:
        section = ""
        seen = {"outcome": 0, "checkpoint": 0}
        outcome = checkpoint = None
        current: Question | None = None
        for no, raw in block.lines:
            label = _LABEL_RE.match(raw)
            if label and label.group(1) != "Answer":
                name, value = label.group(1).strip().lower(), label.group(2).strip()
                section, current = name, None
                if name == "format" and value != "1":
                    raise _unparsable(block, "unknown **Format:**")
                if name in seen:
                    seen[name] += 1
                    if seen[name] > 1:
                        raise _unparsable(block, f"more than one **{label.group(1)}:** line")
                    if name == "outcome":
                        outcome = value or None
                    else:
                        checkpoint = value or None
                continue
            if raw.startswith("**Answer:**"):
                value = _flat(raw[len("**Answer:**") :].strip())
                if current is not None and current.answer is None:
                    current.answer = value
                else:
                    strays.append((block.index, nearest, value))
                continue
            if section == "decisions":
                m = _APPLIED_RE.match(raw)
                if m:
                    applied_marks.append((block.index, int(m.group(1))))
                continue
            if section != "parked questions":
                continue
            if raw.strip() == "" or raw[:1].isspace():
                opt = _OPTIONS_RE.match(raw)
                if opt and current is not None:
                    current.options = _parse_options(opt.group(1))
                continue
            if not raw.startswith("-"):
                current = None
                continue
            entry = _ENTRY_RE.match(raw)
            if not entry:
                if _BAD_ENTRY_RE.match(raw):
                    raise _unparsable(block, "malformed question entry")
                current = None
                continue
            n = int(entry.group(1))
            pointer = _POINTER_RE.match(raw)
            earlier = n in questions and questions[n].block < block.index
            if pointer and pointer.group(1) == pointer.group(2) and earlier:
                reparks.append((n, block.index))
                nearest, current = f"Q{n}", None
                continue
            lowered = entry.group(2).lower()
            if (pointer or "still open" in lowered or "original" in lowered) and (earlier or pointer):
                lookalikes.append((n, block.index, no))
                nearest, current = f"Q{n}", None
                continue
            if n in questions:
                raise Refusal(f"Q{n} is defined more than once", "duplicate-question", 1)
            current = questions[n] = Question(n=n, block=block.index)
            order.append(n)
            nearest = f"Q{n}"

    for block_index, n in applied_marks:
        if n in questions and block_index > questions[n].block:
            questions[n].applied = True

    out = [
        f"blocks: {len(blocks)}",
        f"latest-outcome: {_flat(outcome) if blocks and outcome else 'none'}",
        f"latest-checkpoint: {_flat(checkpoint) if blocks and checkpoint else 'none'}",
        f"highest-q: Q{max(order)}" if order else "highest-q: none",
    ]
    for n in order:
        qn = questions[n]
        status = "answered" if qn.answer is not None else "unanswered"
        out.append(
            f"question: Q{n} block={qn.block} status={status} match={_match(qn)} "
            f"applied={'yes' if qn.applied else 'no'}"
        )
        if qn.answer is not None:
            out.extend([f"answer: Q{n}", *_fenced(qn.answer)])
    for n, block_index in reparks:
        out.append(f"repark: Q{n} block={block_index}")
    for n, block_index, no in lookalikes:
        out.append(f"lookalike-pointer: Q{n} block={block_index} line={no}")
    for block_index, after, value in strays:
        out.extend([f"stray-answer: block={block_index} after={after}", *_fenced(value)])
    return "\n".join(out) + "\n"


def _fenced(value: str) -> list[str]:
    fence = _fence_for(value)
    return [f"{fence}vault-data", value, fence]


def main() -> int:
    try:
        sys.stdout.write(build_report(gate_plumbing.read_stdin()))
    except Refusal as r:
        return gate_plumbing.emit_refusal(PROG, r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
