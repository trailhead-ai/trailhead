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


def _section(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _dispatch_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, DISPATCH_HEADING, REVIEW_HEADING)


def _dispatch_expects_section() -> str:
    """Narrower than `_dispatch_section` — bounded to just the 'The agent
    expects:' list, excluding the model-escalation prose and the trailing
    `Returns:` line that follow it in the same step."""
    return _section(_dispatch_section(), DISPATCH_EXPECTS_START, DISPATCH_EXPECTS_END)


def _review_table_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, REVIEW_HEADING, NEXT_TASK_HEADING)


def _report_format_section() -> str:
    text = EXECUTOR_AGENT.read_text()
    return _section(text, REPORT_FORMAT_HEADING, RULES_HEADING)


def _controller_head_section() -> str:
    """Narrower than `_report_format_section` — bounded to just the
    controller-facing head fence, excluding the durable tail. A pin scoped
    only to the wider report-format section stays green if its line is
    relocated into the durable tail, which is exactly the gap this section
    exists to close."""
    text = EXECUTOR_AGENT.read_text()
    return _section(text, CONTROLLER_HEAD_HEADING, DURABLE_TAIL_HEADING)


def _step7_section() -> str:
    text = EXECUTOR_AGENT.read_text()
    return _section(text, STEP7_HEADING, STEP8_HEADING)


def _what_you_check_section() -> str:
    text = DRIFT_GATE_AGENT.read_text()
    return _section(text, WHAT_YOU_CHECK_HEADING, WHAT_YOU_DO_NOT_CHECK_HEADING)


def _drift_gate_rules_section() -> str:
    text = DRIFT_GATE_AGENT.read_text()
    return _section(text, DRIFT_GATE_RULES_START, DRIFT_GATE_RULES_END)


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
        _review_table_section(),
        "execute.md#4",
        "confirm mutation evidence was reported for every test-contract item",
        "A Small slice skips the formal drift-gate dispatch entirely, so the "
        "inline review the table prescribes must itself name the "
        "mutation-evidence check — otherwise an unevidenced DONE on a Small "
        "slice reaches the metrics row and the run close unchecked.",
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


def test_field_schema_fixture_ships():
    assert FIELD_SCHEMA_FIXTURE.exists(), (
        f"Expected the canonical mutation-evidence field schema fixture at {FIELD_SCHEMA_FIXTURE}"
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
        _drift_gate_rules_section(),
        "drift-gate.md#rules",
        "every test-contract item carries mutation evidence",
        "The verdict block's `PASS` definition is the gate's operative "
        "decision table — a slice with zero mutation evidence must not "
        "satisfy it, so `PASS` must require mutation evidence explicitly, "
        "not rely on the separate 'What you check' list a reader of the "
        "Rules block alone would never see.",
    )


def test_rules_block_drift_fires_regardless_of_claimed_status():
    _pin_in(
        _drift_gate_rules_section(),
        "drift-gate.md#rules",
        "a test-contract item carries no mutation evidence, regardless of the claimed status",
        "The verdict block's `DRIFT` definition must enumerate the "
        "no-mutation-evidence case explicitly and status-agnostically, "
        "mirroring the 'What you check' list, so a gate reading only the "
        "Rules block still returns DRIFT rather than PASS.",
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
