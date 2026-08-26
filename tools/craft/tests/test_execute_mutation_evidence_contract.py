"""Mutation-evidence-per-test-contract-item contract.

The executor's report contract gains a mutation-evidence-per-test-contract-item
requirement: for each item in the intent document's test contract, break the
behaviour, observe the test go RED, restore exactly, observe GREEN, and verify
the restore with an empty diff — reported in the controller-facing head. A
contract item with no mutation evidence is not DONE. `execute.md`'s dispatch
template names the requirement so every dispatch carries it, and the
`drift-gate` conformance gate returns DRIFT for a slice reporting DONE on a
contract item carrying no mutation evidence — regardless of the status the
executor actually claimed, so a sanctioned downgrade to DONE_WITH_CONCERNS
cannot route around the check. On a Small slice, where formal drift-gate
review is skipped entirely, the inline review step itself must confirm
mutation evidence before the slice advances.

Every pin here is scoped to the section it guards — extracted by heading (or,
where a section has no heading of its own, by an exact-text boundary), per
[[lesson/mutation-test-a-prose-pin-whose-target-string-occurs-elsewhere-in-the-file]]
— and asserted as a contiguous substring within one physical line, per
[[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]]. Pins over a
line whose exact position (which section, which sub-block) is itself part of
the contract are additionally mutation-checked against relocation and decoy,
not just deletion, per
[[lesson/a-green-prose-contract-suite-is-not-evidence-the-pins-bind]] — a pin
that stays green when its line is moved somewhere the contract forbids is
decorative.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_EXECUTE = CRAFT / "skills" / "_shared" / "execute.md"
EXECUTOR_AGENT = CRAFT / "agents" / "executor.md"
DRIFT_GATE_AGENT = CRAFT / "agents" / "drift-gate.md"

FIELD_SCHEMA_FIXTURE = Path(__file__).parent / "fixtures" / "mutation_evidence_field_schema.txt"

DISPATCH_HEADING = "### 3. Dispatch `executor`"
REVIEW_HEADING = "### 4. Review"
NEXT_TASK_HEADING = "### 5. Update the task graph"

DISPATCH_EXPECTS_START = "The agent expects:"
DISPATCH_EXPECTS_END = "Personal-vault lessons are fenced"

REPORT_FORMAT_HEADING = "## Report format"
RULES_HEADING = "## Rules"

CONTROLLER_HEAD_HEADING = "### Controller-facing head (return this)"
DURABLE_TAIL_HEADING = "### Durable tail"

STEP7_HEADING = "## Step 7: Verify"
STEP8_HEADING = "## Step 8: Commit"

WHAT_YOU_CHECK_HEADING = "## What you check"
WHAT_YOU_DO_NOT_CHECK_HEADING = "## What you do NOT check"

DRIFT_GATE_RULES_START = "Rules:"
DRIFT_GATE_RULES_END = "## What you check"

SMALL_ROW_START = "| **Small**"
MEDIUM_ROW_START = "| **Medium**"

PASS_BULLET_START = "- `PASS`"
DRIFT_BULLET_START = "- `DRIFT`"
BLOCKED_BULLET_START = "- `BLOCKED`"


class SectionBoundaryError(Exception):
    """Raised when a boundary string used to slice out a section can no
    longer be found in the source text — e.g. a paragraph the boundary
    quotes verbatim got reworded elsewhere in the file."""


def _section(text: str, start_heading: str, end_heading: str, *, context: str) -> str:
    try:
        start = text.index(start_heading)
    except ValueError:
        raise SectionBoundaryError(
            f"{context}: start boundary {start_heading!r} not found in the source "
            f"text — this section can no longer be located."
        ) from None
    try:
        end = text.index(end_heading, start)
    except ValueError:
        raise SectionBoundaryError(
            f"{context}: end boundary {end_heading!r} not found in the source "
            f"text — this section can no longer be located."
        ) from None
    return text[start:end]


def _dispatch_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, DISPATCH_HEADING, REVIEW_HEADING, context="execute.md dispatch step")


def _dispatch_expects_section() -> str:
    """Narrower than `_dispatch_section` — bounded to just the 'The agent
    expects:' list, excluding the model-escalation prose and the trailing
    `Returns:` line that follow it in the same step."""
    return _section(
        _dispatch_section(),
        DISPATCH_EXPECTS_START,
        DISPATCH_EXPECTS_END,
        context="execute.md dispatch step's 'The agent expects:' list",
    )


def _review_table_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, REVIEW_HEADING, NEXT_TASK_HEADING, context="execute.md review step")


def _small_row_section() -> str:
    """Narrower than `_review_table_section` — bounded to just the physical
    `| **Small**` table row, excluding the Medium/Large rows and the
    surrounding prose. A pin scoped only to the wider review section stays
    green if its clause is relocated into the Medium row, which is exactly
    the gap this section exists to close: Small is the one path with no
    drift-gate dispatch, so its row is the sole mutation-evidence guard."""
    return _section(
        _review_table_section(),
        SMALL_ROW_START,
        MEDIUM_ROW_START,
        context="execute.md review table's Small row",
    )


def _report_format_section() -> str:
    text = EXECUTOR_AGENT.read_text()
    return _section(text, REPORT_FORMAT_HEADING, RULES_HEADING, context="executor.md report format")


def _controller_head_section() -> str:
    """Narrower than `_report_format_section` — bounded to just the
    controller-facing head fence, excluding the durable tail. A pin scoped
    only to the wider report-format section stays green if its line is
    relocated into the durable tail, which is exactly the gap this section
    exists to close."""
    text = EXECUTOR_AGENT.read_text()
    return _section(
        text, CONTROLLER_HEAD_HEADING, DURABLE_TAIL_HEADING, context="executor.md controller-facing head"
    )


def _step7_section() -> str:
    text = EXECUTOR_AGENT.read_text()
    return _section(text, STEP7_HEADING, STEP8_HEADING, context="executor.md Step 7")


def _what_you_check_section() -> str:
    text = DRIFT_GATE_AGENT.read_text()
    return _section(
        text, WHAT_YOU_CHECK_HEADING, WHAT_YOU_DO_NOT_CHECK_HEADING, context="drift-gate.md 'What you check'"
    )


def _drift_gate_rules_section() -> str:
    text = DRIFT_GATE_AGENT.read_text()
    return _section(text, DRIFT_GATE_RULES_START, DRIFT_GATE_RULES_END, context="drift-gate.md Rules block")


def _pass_bullet_section() -> str:
    """Narrower than `_drift_gate_rules_section` — bounded to just the
    `PASS` bullet, excluding the `DRIFT` and `BLOCKED` bullets. A pin scoped
    only to the wider Rules block stays green if its clause is relocated
    into another bullet, which is exactly the gap this section exists to
    close: a pin must guard the verdict it names."""
    return _section(
        _drift_gate_rules_section(),
        PASS_BULLET_START,
        DRIFT_BULLET_START,
        context="drift-gate.md Rules block's PASS bullet",
    )


def _drift_bullet_section() -> str:
    """Narrower than `_drift_gate_rules_section` — bounded to just the
    `DRIFT` bullet, excluding the `PASS` and `BLOCKED` bullets. See
    `_pass_bullet_section` for why a per-bullet scope matters."""
    return _section(
        _drift_gate_rules_section(),
        DRIFT_BULLET_START,
        BLOCKED_BULLET_START,
        context="drift-gate.md Rules block's DRIFT bullet",
    )


def _pin_in(section_text: str, path_label: str, phrase: str, why: str) -> None:
    """Assert *phrase* appears inside a single physical line of *section_text*,
    and that it occurs exactly once so the pin cannot pass on the wrong
    occurrence."""
    matching_lines = [line for line in section_text.splitlines() if phrase in line]
    if len(matching_lines) == 1:
        return
    if len(matching_lines) > 1:
        pytest.fail(
            f"{path_label}: the pinned span {phrase!r} occurs {len(matching_lines)} "
            f"times in this section — reword the incidental occurrence so the pin "
            f"guards exactly one line. {why}"
        )
    if phrase in " ".join(section_text.split()):
        pytest.fail(
            f"{path_label}: the pinned span {phrase!r} is present but straddles a "
            f"line wrap — keep it on one physical line. {why}"
        )
    pytest.fail(f"{path_label}: missing the pinned span {phrase!r}. {why}")


# --- Contract item A: execute.md's dispatch template names the requirement ---


def test_dispatch_section_pins_mutation_evidence_requirement():
    _pin_in(
        _dispatch_expects_section(),
        "execute.md#3",
        "Mutation evidence per test-contract item",
        "The dispatch template's 'The agent expects:' list must name the "
        "mutation-evidence requirement so every executor dispatch carries it.",
    )


def test_dispatch_section_pins_no_evidence_means_not_done():
    _pin_in(
        _dispatch_expects_section(),
        "execute.md#3",
        "a contract item with no mutation evidence is not DONE",
        "The dispatch template must state the consequence, not just name the "
        "requirement — an unevidenced item withholds DONE.",
    )


def test_review_table_names_mutation_evidence_for_small_slices():
    _pin_in(
        _small_row_section(),
        "execute.md#4",
        "confirm mutation evidence was reported for every test-contract item",
        "A Small slice skips the formal drift-gate dispatch entirely, so the "
        "inline review the table prescribes must itself name the "
        "mutation-evidence check — otherwise an unevidenced DONE on a Small "
        "slice reaches the metrics row and the run close unchecked. This pin "
        "is scoped to the physical Small row (not the whole Review section) "
        "because Small is the one path with no drift-gate run — this row is "
        "the sole mutation-evidence guard on it, and a clause that reads fine "
        "after being relocated to the Medium row leaves Small unguarded.",
    )


# --- executor.md pin: the report contract carries the requirement -----------


def test_report_format_pins_no_evidence_means_not_done():
    _pin_in(
        _report_format_section(),
        "executor.md#report-format",
        "A contract item with no mutation evidence is not DONE",
        "The report contract must state that an item without mutation "
        "evidence cannot be claimed DONE.",
    )


def test_report_format_pins_empty_diff_restore_verification():
    _pin_in(
        _report_format_section(),
        "executor.md#report-format",
        "verify the restore with a diff that comes back empty",
        "The report contract must require the empty-diff confirmation step, "
        "not just RED/GREEN observation.",
    )


def test_report_format_pins_downgrade_does_not_exempt_the_item():
    _pin_in(
        _report_format_section(),
        "executor.md#report-format",
        "Downgrading the status does not exempt the item",
        "A sanctioned downgrade to DONE_WITH_CONCERNS must not read as a way "
        "to route an unevidenced item around the drift-gate's status-agnostic "
        "check — the executor's prose must say so explicitly.",
    )


def test_report_format_field_schema_is_byte_identical_to_fixture():
    schema = FIELD_SCHEMA_FIXTURE.read_text().strip()
    _pin_in(
        _controller_head_section(),
        "executor.md#report-format",
        schema,
        "The `mutation-evidence:` field in the controller-facing head must be "
        "byte-identical to the canonical fixture — not merely a sentence "
        "mentioning the field name — and must live inside the head fence, not "
        "the durable tail: per item it must require the test node id, the "
        "exact mutation applied, RED observed, GREEN observed, and "
        "empty-diff confirmed, plus a sentinel for the zero-item case.",
    )


# --- Contract item B: drift-gate returns DRIFT for missing evidence ---------


def test_what_you_check_pins_drift_regardless_of_claimed_status():
    _pin_in(
        _what_you_check_section(),
        "drift-gate.md#what-you-check",
        "A test-contract item with no mutation evidence is DRIFT regardless of the claimed status",
        "The conformance gate's check must fire on any status claim, not "
        "just a DONE claim — otherwise a sanctioned downgrade to "
        "DONE_WITH_CONCERNS ships an unevidenced item past the gate.",
    )


def test_rules_block_pass_requires_mutation_evidence():
    _pin_in(
        _pass_bullet_section(),
        "drift-gate.md#rules",
        "every test-contract item carries mutation evidence",
        "The verdict block's `PASS` definition is the gate's operative "
        "decision table — a slice with zero mutation evidence must not "
        "satisfy it, so `PASS` must require mutation evidence explicitly, "
        "not rely on the separate 'What you check' list a reader of the "
        "Rules block alone would never see. Scoped to the PASS bullet only "
        "(not the whole Rules block), so relocating this clause into the "
        "DRIFT or BLOCKED bullet — where it no longer guards PASS — fails.",
    )


def test_rules_block_drift_fires_regardless_of_claimed_status():
    _pin_in(
        _drift_bullet_section(),
        "drift-gate.md#rules",
        "a test-contract item carries no mutation evidence, regardless of the claimed status",
        "The verdict block's `DRIFT` definition must enumerate the "
        "no-mutation-evidence case explicitly and status-agnostically, "
        "mirroring the 'What you check' list, so a gate reading only the "
        "Rules block still returns DRIFT rather than PASS. Scoped to the "
        "DRIFT bullet only (not the whole Rules block), so relocating this "
        "clause into the PASS or BLOCKED bullet — where it no longer guards "
        "DRIFT — fails.",
    )


# --- executor.md pin: mutation evidence precedes the commit -----------------


def test_step7_states_mutation_pass_precedes_commit():
    _pin_in(
        _step7_section(),
        "executor.md#step-7",
        "Produce mutation evidence now, before Step 8's commit",
        "The mutation-evidence requirement otherwise lives only in ## Report "
        "format, which sits after Step 8 in the document's own sequential "
        "order — an executor following the steps in order would commit "
        "before producing the evidence and have to amend a GPG-signed "
        "commit when a mutation exposes a defect. Step 7 must say where in "
        "the loop the mutation pass happens.",
    )


# --- drift-gate.md pin: mutation-evidence check names both test-contract shapes ---


def test_what_you_check_pins_both_test_contract_shapes():
    _pin_in(
        _what_you_check_section(),
        "drift-gate.md#what-you-check",
        "for each item in the intent document's `**Test contract:**` (or `## Test contract`)",
        "executor.md's dispatch loop accepts a standalone leaf's test "
        "contract in either the `**Test contract:**` label or the "
        "`## Test contract` heading shape. If the gate's mutation-evidence "
        "check only names the label shape, a record in the heading shape "
        "gives it no items to iterate and check 4 passes vacuously — the "
        "gate must be taught both shapes explicitly.",
    )


# --- test-file infrastructure: _section raises a named, explanatory error ---


def test_section_raises_named_error_when_boundary_missing():
    with pytest.raises(SectionBoundaryError, match=r"nonexistent-boundary.*not found"):
        _section("some prose with a start marker in it", "start", "nonexistent-boundary", context="a test fixture")
