"""Slice 2 — the dispatch postmortem writes retrievable lessons.

Phase 5 (flow-out) gains a dispatch postmortem that reads the `## Run Metrics`
block Slice 1 wrote and writes what it learned about dispatch phrasing as
`lesson` records, distinct from the existing domain-lesson session-candidate
capture.

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
LESSON_CONTRACT_FIXTURE = Path(__file__).parent / "fixtures" / "dispatch_lesson_contract.txt"
RUN_TOTAL_FIXTURE = Path(__file__).parent / "fixtures" / "run_total_row_schema.txt"

PHASE5_HEADING = "### Phase 5: Flow-out"
PHASE6_HEADING = "### Phase 6: Close and completion report"


def _phase5_section() -> str:
    text = SHARED_EXECUTE.read_text()
    start = text.index(PHASE5_HEADING)
    end = text.index(PHASE6_HEADING, start)
    return text[start:end]


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


def _run_total_column(name: str) -> str:
    """Return the run-total column token named *name*, read from the canonical fixture."""
    columns = [c.strip() for c in RUN_TOTAL_FIXTURE.read_text().strip().strip("|").split("|")]
    assert name in columns, f"{name!r} is not a run-total column: {columns}"
    return name


# --- postmortem is named and reads the metrics block --------------------------


def test_phase5_names_dispatch_postmortem():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "dispatch postmortem",
        "Phase 5 must name the postmortem step.",
    )


def test_phase5_pins_reads_run_metrics_block():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "reads the `## Run Metrics` block",
        "The postmortem must state that it reads the metrics block Slice 1 wrote.",
    )


@pytest.mark.parametrize(
    "condition",
    [
        "more than one dispatch",
        "a non-`DONE` terminal status",
        "a `DRIFT` verdict",
    ],
)
def test_phase5_pins_each_evidence_condition(condition):
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        condition,
        f"Phase 5 must name the evidence condition {condition!r} the postmortem "
        "uses the metrics block for.",
    )


# --- metrics schema is byte-identical to the canonical fixture ----------------


def test_phase5_metrics_fields_are_byte_identical_to_fixture():
    schema = SCHEMA_FIXTURE.read_text().strip()
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        schema,
        "The field names the postmortem reads must be byte-identical to the "
        "canonical fixture Slice 1 writes against, so writer and reader cannot "
        "drift behind two section-scoped pins.",
    )


# --- write path -----------------------------------------------------------


def test_phase5_pins_lore_record_create_kind_lesson():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "lore record create --kind lesson",
        "The postmortem must write via `lore record create --kind lesson`.",
    )


def test_phase5_pins_dispatch_lesson_label_key():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "--label craft/dispatch-lesson=executor",
        "The postmortem must carry the exact `craft/dispatch-lesson` label key "
        "and its `executor` value.",
    )


def test_phase5_pins_subsystems_label_key():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "--label craft/subsystems=",
        "The postmortem must carry the `craft/subsystems` label key.",
    )


# --- write-failure branch --------------------------------------------------


def test_phase5_pins_write_failure_logs_and_continues():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "logs and continues",
        "A failed lesson write must log and continue, never no-op silently.",
    )


def test_phase5_pins_write_failure_flags_loss_on_the_run_total_row():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        f"flags the loss in the `## Run Metrics` block's run-total `{_run_total_column('Lessons')}` column",
        "A failed lesson write must land in a slot that actually exists — the "
        "run-total row's `Lessons` column — so a lost write cannot render "
        "identically to 'nothing to teach'.",
    )


# --- scrub and citation discipline -----------------------------------------


def test_phase5_pins_lesson_text_runs_through_scrub():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "runs through the credential-pattern scrub before the write",
        "Lesson text is record text and must run through the same scrub as "
        "every other body write.",
    )


def test_phase5_pins_pointer_only_citation_discipline():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "cites a `file:line` or a report pointer rather than inline-quoting",
        "Lesson bodies must follow pointer-only citation discipline, never "
        "inline-quoting executor output.",
    )


def test_phase5_pins_lesson_body_is_piped_on_stdin():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "piping the body in on stdin",
        "`lore record create` has no body flag — prose that omits this "
        "prescribes a command an agent cannot actually run.",
    )


def test_phase5_pins_title_flag_on_the_lesson_write():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "--title '<short imperative summary>'",
        "`lore record create` REQUIRES --title and hard-fails at argparse "
        "without it, so the prescribed command as written could never run and "
        "the producing half of the loop would never fire.",
    )


def test_phase5_pins_title_is_required():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "`--title` is required and the command hard-fails without it",
        "Name why --title is present, the same way the bullet already names "
        "stdin as the body channel.",
    )


def test_phase5_lesson_contract_is_byte_identical_to_fixture():
    contract = LESSON_CONTRACT_FIXTURE.read_text().strip()
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        contract,
        "The kind + label contract that closes the loop must be stated from the "
        "canonical fixture on the write side, so renaming it cannot leave the "
        "producer and the consumer independently green.",
    )


# --- the lesson write lands in the elected vault ----------------------------


def test_phase5_pins_lesson_write_names_the_elected_vault():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "--kind lesson --vault <elected-vault>",
        "An unqualified `lore record create` resolves to the default vault, so "
        "the lesson lands where the claim-time query — which is scoped to the "
        "elected vault — will never find it.",
    )


def test_phase5_pins_absent_subsystem_label_branch_on_the_write():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "the write omits that label and the claim-time query drops its matching term",
        "Nothing writes `craft/subsystems` on a standalone run, so the write "
        "needs a stated branch for the absent case — otherwise it stamps an "
        "invented value or writes nothing retrievable.",
    )


def test_phase5_pins_flag_and_retrieval_outcome_are_carried_to_the_close_write():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "carried forward to the close write as a note in `## End Phases`",
        "The run-total row does not exist until Phase 6, so a crash between the "
        "phases resumes at the first unticked phase with the write-failed flag "
        "and the retrieval outcome gone.",
    )


def test_phase5_pins_untrusted_interpolation_at_the_write():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "both interpolate untrusted text into a literal shell command",
        "`<name>` is vault-sourced and the title is generated prose crafted "
        "repo content can influence; both reach a shell the controller runs.",
    )


def test_phase5_pins_title_sanitization_before_quoting():
    _pin_in(
        _phase5_section(),
        "execute.md#phase5",
        "the title is stripped of single quotes, newlines, backticks, and `$` before it is quoted",
        "A single quote in the title breaks out of the quoted argument and "
        "injects a command into the controller's shell.",
    )
