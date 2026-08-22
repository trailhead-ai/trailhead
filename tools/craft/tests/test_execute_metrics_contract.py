"""Slice 1 — per-slice metrics emission contract.

The execute loop's step 5 (the task-graph write the controller already performs
each slice) gains a `## Run Metrics` row, and Phase 6 (close and completion
report) totals those rows and names any dispatch lesson written/consumed.

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
SCHEMA_FIXTURE = Path(__file__).parent / "fixtures" / "run_metrics_schema.txt"

STEP5_HEADING = "### 5. Update the task graph"
STEP6_HEADING = "### 6. Next task"
PHASE6_HEADING = "### Phase 6: Close and completion report"
PHASE_PROGRESS_HEADING = "### Phase progress and resumability"


def _section(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _step5_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, STEP5_HEADING, STEP6_HEADING)


def _phase6_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, PHASE6_HEADING, PHASE_PROGRESS_HEADING)


def _pin_in(section_text: str, path_label: str, phrase: str, why: str) -> None:
    """Assert *phrase* appears inside a single physical line of *section_text*."""
    if any(phrase in line for line in section_text.splitlines()):
        return
    if phrase in " ".join(section_text.split()):
        pytest.fail(
            f"{path_label}: the pinned span {phrase!r} is present but straddles a "
            f"line wrap — keep it on one physical line. {why}"
        )
    pytest.fail(f"{path_label}: missing the pinned span {phrase!r}. {why}")


# --- fixture ships -----------------------------------------------------------


def test_schema_fixture_ships():
    assert SCHEMA_FIXTURE.exists(), f"Expected the canonical Run Metrics schema fixture at {SCHEMA_FIXTURE}"


# --- Step 5: metrics row ------------------------------------------------------


def test_step5_pins_run_metrics_block_name():
    _pin_in(
        _step5_section(),
        "execute.md#5",
        "## Run Metrics",
        "Step 5 must name the block it appends a metrics row to.",
    )


def test_step5_schema_is_byte_identical_to_fixture():
    schema = SCHEMA_FIXTURE.read_text().strip()
    _pin_in(
        _step5_section(),
        "execute.md#5",
        schema,
        "The column set Step 5 writes must be byte-identical to the canonical "
        "fixture, so a later reader is pinned to the same source rather than an "
        "independently-worded restatement.",
    )


@pytest.mark.parametrize(
    "field",
    [
        "change-size band",
        "executor dispatch count including every re-dispatch",
        "terminal status",
        "drift-gate verdict",
        "model used per dispatch",
        "elapsed wall-clock",
    ],
)
def test_step5_pins_each_recorded_field(field):
    _pin_in(
        _step5_section(),
        "execute.md#5",
        field,
        f"Step 5 must name the recorded field {field!r} in its metrics row description.",
    )


def test_step5_pins_credential_scrub():
    _pin_in(
        _step5_section(),
        "execute.md#5",
        "runs through the credential-pattern scrub",
        "The metrics row is record text and must run through the same scrub as "
        "every other body write.",
    )


def test_step5_pins_drift_not_counted_as_redispatch():
    _pin_in(
        _step5_section(),
        "execute.md#5",
        "is never counted as a re-dispatch",
        "Negative pin: a DRIFT verdict is recorded in its own column, not folded "
        "into the dispatch count — the loop prescribes no re-dispatch on DRIFT.",
    )


# --- Phase 6: totals and completion report -----------------------------------


def test_phase6_pins_run_metrics_totals():
    for field in [
        "final run-total row: slices",
        "total dispatches",
        "dispatches-per-slice",
        "end-to-end wall clock",
    ]:
        _pin_in(
            _phase6_section(),
            "execute.md#phase6",
            field,
            f"Phase 6 must name the run-total field {field!r}.",
        )


def test_phase6_pins_lessons_written_and_consumed():
    _pin_in(
        _phase6_section(),
        "execute.md#phase6",
        "lessons written and lessons consumed",
        "The completion report's metrics line must name any dispatch lesson "
        "written and any consumed this run.",
    )


def test_phase6_run_total_row_carries_the_retrieval_outcome():
    _pin_in(
        _phase6_section(),
        "execute.md#phase6",
        "the retrieval outcome (lessons loaded, `empty`, or `error`)",
        "The claim-time retrieval distinguishes an empty result from a caught "
        "error; the run-total row is where that distinction is recorded, so a "
        "malformed query cannot degrade forever into a silent 'no lessons'.",
    )
