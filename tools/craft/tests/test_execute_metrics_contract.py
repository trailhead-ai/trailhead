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
RUN_TOTAL_FIXTURE = Path(__file__).parent / "fixtures" / "run_total_row_schema.txt"

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


# --- fixture ships -----------------------------------------------------------


def test_schema_fixture_ships():
    assert SCHEMA_FIXTURE.exists(), f"Expected the canonical Run Metrics schema fixture at {SCHEMA_FIXTURE}"


def test_run_total_row_fixture_ships():
    assert RUN_TOTAL_FIXTURE.exists(), f"Expected the canonical run-total row fixture at {RUN_TOTAL_FIXTURE}"


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


def test_step5_pins_block_is_created_on_the_first_slice():
    _pin_in(
        _step5_section(),
        "execute.md#5",
        "appending the block itself on the first slice if it is not there yet",
        "Nothing else in the document prescribes creating the `## Run Metrics` "
        "block, so 'append one row to the block' would assume a block that does "
        "not exist on the first slice.",
    )


def test_step5_pins_non_completing_slices_still_emit_a_row():
    _pin_in(
        _step5_section(),
        "execute.md#5",
        "a slice that ends `NEEDS_CONTEXT` or `BLOCKED` emits its row too",
        "Step 5 is headed 'after each child task completes' while the status "
        "column enumerates non-completing statuses — say which it is, or the "
        "rows the postmortem most needs are the ones never written.",
    )


def test_step5_pins_parent_body_write_uses_the_diff_append_form():
    _pin_in(
        _step5_section(),
        "execute.md#5",
        "piping a unified diff that **appends** the lines",
        "Step 5's per-slice parent-body write must name the `--diff` append "
        "form: a bare `lore record update` stdin write is a full-body REPLACE "
        "and destroys the record, as two other sections of this document warn.",
    )


def test_phase6_run_total_row_schema_is_byte_identical_to_fixture():
    schema = RUN_TOTAL_FIXTURE.read_text().strip()
    _pin_in(
        _phase6_section(),
        "execute.md#phase6",
        schema,
        "The run-total row is a different shape from the per-slice columns, so "
        "'total the rows into a final run-total row' needs its own stated "
        "column set rather than borrowing one that does not fit.",
    )


def test_phase6_run_total_row_carries_the_lesson_write_outcome():
    _pin_in(
        _phase6_section(),
        "execute.md#phase6",
        "lessons written this run (or `write-failed` when the postmortem lost a write)",
        "A lost lesson write must render differently from 'nothing to teach' — "
        "the run-total row is the slot the postmortem flags it into.",
    )
