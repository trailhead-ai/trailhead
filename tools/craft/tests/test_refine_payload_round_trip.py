"""Round-trip a promoted refine payload through execute's and drift-gate's contracts.

`_shared/refine.md` Step 4 specifies the exact shape a promoted standalone task body
takes: the bold inline payload labels from `templates/task.md`, the `## Flow-out`
checklist, and (on re-refine) an idempotency count over those labels that must ignore
mid-prose mentions and fenced-code quotes (`_shared/refine.md:358-361`). Three other
contract-test modules pin that those strings exist in the *procedure document*; none
of them constructs an actual promoted body and checks it against the counting rule
downstream consumers (execute's dispatch framing, drift-gate's conformance read, and
refine's own Re-refine idempotency check) actually rely on.

This module builds synthetic bodies the way Step 4/5 specify and exercises the
line-start/fenced-code-aware counter the Re-refine section describes, so the parsing
contract itself — not just its presence in prose — is under test.
"""

import re

PAYLOAD_LABELS = ["**Delivers:**", "**Test contract:**", "**Files:**"]
FLOW_OUT_HEADING = "## Flow-out"
ESCALATION_HEADING = "## Refine — unresolved"


def count_line_anchored(text: str, needle: str) -> int:
    """Count occurrences of `needle` that begin a line, outside fenced code blocks.

    Mirrors the idempotency rule `_shared/refine.md`'s Re-refine section specifies:
    a key counts only where it begins a line and is not inside a fenced code block —
    the same string quoted mid-prose, in backticks, or inside a fence is content, not
    payload structure, and must never move the count.
    """
    in_fence = False
    count = 0
    for line in text.splitlines():
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith(needle):
            count += 1
    return count


def build_promoted_body(*, name_drop_prose: bool = False) -> str:
    prose = "Fix the widget so it stops crashing on empty input."
    if name_drop_prose:
        prose += (
            " Note: this is unrelated to the **Delivers:** field in some other "
            "task, and don't be fooled by a fenced mention either:\n\n"
            "```\n**Files:** not-a-real-payload.py\n```"
        )
    payload = (
        "**Delivers:** the widget no longer crashes on empty input.\n\n"
        "**Test contract:** `test_widget_empty_input` covers the empty-input case.\n\n"
        "**Files:** `widget.py` (modified).\n\n"
        f"{FLOW_OUT_HEADING}\n\n"
        "- [ ] Touched area/subsystem profiles updated with what changed\n"
        "- [ ] Prover-validated assumptions captured as session candidates (durable at flush)\n"
        "- [ ] New decisions / lessons / follow-ups surfaced during the build recorded\n"
    )
    return f"{prose}\n\n{payload}"


def build_escalated_body() -> str:
    prose = "Fix the widget so it stops crashing on empty input."
    escalation = (
        f"{ESCALATION_HEADING}\n\n"
        "**Question:** should empty input raise or return a default value?\n\n"
        "**Evidence gathered:** no existing precedent in this module.\n\n"
        "**Recommended answer:** raise, matching sibling validators.\n"
    )
    return f"{prose}\n\n{escalation}"


def test_promoted_body_counts_each_payload_label_exactly_once():
    body = build_promoted_body()
    for label in PAYLOAD_LABELS:
        assert count_line_anchored(body, label) == 1, (
            f"a cleanly promoted body must count {label!r} exactly once"
        )


def test_mid_prose_and_fenced_mentions_do_not_inflate_the_count():
    body = build_promoted_body(name_drop_prose=True)
    assert count_line_anchored(body, "**Delivers:**") == 1, (
        "a mid-sentence name-drop of **Delivers:** must not move the count — "
        "only a line-start occurrence is payload structure"
    )
    assert count_line_anchored(body, "**Files:**") == 1, (
        "a **Files:** mention inside a fenced code block must not move the count"
    )


def test_promoted_body_has_flow_out_heading_once():
    body = build_promoted_body()
    assert count_line_anchored(body, FLOW_OUT_HEADING) == 1, (
        "a promoted body must carry the `## Flow-out` heading exactly once — "
        "lore's completion guard looks for it"
    )


def test_escalated_body_has_escalation_heading_and_promoted_body_does_not():
    escalated = build_escalated_body()
    promoted = build_promoted_body()
    assert count_line_anchored(escalated, ESCALATION_HEADING) == 1, (
        "an escalated draft must carry `## Refine — unresolved` exactly once"
    )
    assert count_line_anchored(promoted, ESCALATION_HEADING) == 0, (
        "a promoted body must not carry the escalation heading — promotion "
        "removes it, per the Re-refine section"
    )


def test_two_payload_sets_count_as_two_conflict_signal():
    hand_edited = build_promoted_body() + "\n\n" + build_promoted_body()
    assert count_line_anchored(hand_edited, "**Delivers:**") == 2, (
        "a hand-edited body carrying two concatenated payload sets must count "
        "**Delivers:** as 2 — this is the signal Re-refine's conflict branch "
        "keys off to report rather than guess which set is canonical"
    )
