"""Mutation-evidence-per-test-contract-item contract.

The executor's report contract gains a mutation-evidence-per-test-contract-item
requirement: for each item in the intent document's test contract, break the
behaviour, observe the test go RED, restore exactly, observe GREEN, and verify
the restore with an empty diff — reported in the controller-facing head. A
contract item with no mutation evidence is not DONE. `execute.md`'s dispatch
template names the requirement so every dispatch carries it, and the
`drift-gate` conformance gate returns DRIFT for a slice reporting DONE on a
contract item carrying no mutation evidence.

Every pin here is scoped to the section it guards — extracted by heading,
per [[lesson/mutation-test-a-prose-pin-whose-target-string-occurs-elsewhere-in-the-file]]
— and asserted as a contiguous substring within one physical line, per
[[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]].
"""

from __future__ import annotations

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_EXECUTE = CRAFT / "skills" / "_shared" / "execute.md"
EXECUTOR_AGENT = CRAFT / "agents" / "executor.md"
DRIFT_GATE_AGENT = CRAFT / "agents" / "drift-gate.md"

DISPATCH_HEADING = "### 3. Dispatch `executor`"
REVIEW_HEADING = "### 4. Review"

REPORT_FORMAT_HEADING = "## Report format"
RULES_HEADING = "## Rules"

WHAT_YOU_CHECK_HEADING = "## What you check"
WHAT_YOU_DO_NOT_CHECK_HEADING = "## What you do NOT check"


def _section(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _dispatch_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, DISPATCH_HEADING, REVIEW_HEADING)


def _report_format_section() -> str:
    text = EXECUTOR_AGENT.read_text()
    return _section(text, REPORT_FORMAT_HEADING, RULES_HEADING)


def _what_you_check_section() -> str:
    text = DRIFT_GATE_AGENT.read_text()
    return _section(text, WHAT_YOU_CHECK_HEADING, WHAT_YOU_DO_NOT_CHECK_HEADING)


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
        _dispatch_section(),
        "execute.md#3",
        "Mutation evidence per test-contract item",
        "The dispatch template's 'The agent expects:' list must name the "
        "mutation-evidence requirement so every executor dispatch carries it.",
    )


def test_dispatch_section_pins_no_evidence_means_not_done():
    _pin_in(
        _dispatch_section(),
        "execute.md#3",
        "a contract item with no mutation evidence is not DONE",
        "The dispatch template must state the consequence, not just name the "
        "requirement — an unevidenced item withholds DONE.",
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


def test_report_format_pins_mutation_evidence_field():
    _pin_in(
        _report_format_section(),
        "executor.md#report-format",
        "mutation-evidence:",
        "The controller-facing head block must carry a `mutation-evidence:` "
        "field so the evidence is returned to the controller, not only "
        "written to the durable tail.",
    )


# --- Contract item B: drift-gate returns DRIFT for missing evidence ---------


def test_what_you_check_pins_drift_for_missing_mutation_evidence():
    _pin_in(
        _what_you_check_section(),
        "drift-gate.md#what-you-check",
        "A DONE claim on a contract item with no mutation evidence is DRIFT",
        "The conformance gate must return DRIFT — not PASS — for a slice "
        "reporting DONE on a test-contract item that carries no mutation "
        "evidence; this is the case that currently passes.",
    )
