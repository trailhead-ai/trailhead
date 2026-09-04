"""`/craft:slice` derives its candidate set and its termination decision from
the real `candidate_set.py` gate, per step 4 and step 6 of `slice/SKILL.md`.

These tests bind the skill's prose to something executable — the real gate
script run as a subprocess against real fixture spec bodies, and the
document-position of the tokens the prose claims to key on — never to a copy
of the skill's own wording. A test asserting the skill file merely contains a
phrase is not acceptable here, so every extraction below is fed through the
real script or checked against real gate output.
"""

from __future__ import annotations

import re
from pathlib import Path

# The gate runner, its stdout token parser, and the fixture bodies are the
# same ones the gate's own suite drives, so a change to either the invocation
# or the token block reaches both suites from one place.
from test_candidate_set import (
    FULL_COVERAGE_WITH_LEGACY_LINE,
    MALFORMED_TOKEN,
    UNDECLARED_COVERAGE,
    ZERO_CRITERIA_SPEC,
    _run,
    _tokens,
)

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SLICE_SKILL = CRAFT / "skills" / "slice" / "SKILL.md"

_NINE_CRITERIA_HEADING = "\n".join(
    f"- **AC{n}.** A fixture criterion." for n in range(1, 10)
)


def _skill_text() -> str:
    return SLICE_SKILL.read_text(encoding="utf-8")


def _step(name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading."""
    text = _skill_text()
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d+\.", rest)
    return rest[: end.start()] if end else rest


def _reason_code(stderr: str) -> str:
    match = re.search(r"reason-code:\s*([a-z0-9-]+)", stderr)
    assert match, f"gate stderr must carry a reason-code token: {stderr!r}"
    return match.group(1)


def _ledger_line_templates(step4: str) -> list[str]:
    blocks = re.findall(r"```\n(.*?)\n```", step4, re.DOTALL)
    return [b.strip() for b in blocks if b.strip().startswith("- **<slice title>**")]


def _render(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace(f"<{key}>", value)
    return out


# ---- 1. seam: the documented ledger example, fed through the real gate ----


def test_documented_covers_ledger_line_derives_the_coverage_the_prose_claims():
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    templates = _ledger_line_templates(step4)
    assert len(templates) == 4, (
        "slice/SKILL.md step 4 must document exactly four ledger-line shapes — "
        "no coverage, covers-only, partial-only, and covers-plus-partial; "
        f"found {templates}"
    )
    covers_shape = templates[1]  # documented second: the fifth-token extension
    rendered = _render(
        covers_shape,
        **{
            "slice title": "The streaming export slice",
            "value claim": "Exports stream instead of buffering in memory",
            "task-id": "the-streaming-export-slice",
            "close-date": "2026-09-03",
            "covers-value": "AC2, AC5",
        },
    )

    spec_body = (
        "# Fixture spec\n\n## Acceptance Criteria\n\n"
        f"{_NINE_CRITERIA_HEADING}\n\n## Slices\n\n{rendered}\n"
    )
    result = _run(spec_body)
    assert result.returncode == 0, result.stderr + result.stdout
    tokens = _tokens(result.stdout)
    assert tokens["covered"] == "AC2, AC5", (
        f"the real gate must derive the coverage the rendered ledger line names: {tokens}"
    )
    assert tokens["candidates"] == "AC1, AC3, AC4, AC6, AC7, AC8, AC9"


# ---- 2. document-order: derivation precedes the point it's claimed to feed ----


def test_gate_pipe_documented_before_the_basis_it_produces_in_step4():
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    pipe_match = re.search(r"candidate_set\.py", step4)
    basis_match = re.search(r"termination basis: gate-certified", step4)
    assert pipe_match, "slice/SKILL.md step 4 must document the candidate_set.py pipe"
    assert basis_match, "slice/SKILL.md step 4 must document the gate-certified basis line"
    assert pipe_match.start() < basis_match.start(), (
        "the candidate_set.py pipe must be documented, by position, before the "
        "gate-certified basis line it produces"
    )


def test_step6_eligibility_check_documented_before_the_termination_write():
    step6 = _step("### 6. Termination — the loop's terminating condition")
    eligibility_match = re.search(r"complete-eligible: yes", step6)
    write_match = re.search(r"--label craft/slice-loop=complete", step6)
    assert eligibility_match, "slice/SKILL.md step 6 must document the eligibility check"
    assert write_match, "slice/SKILL.md step 6 must document the completion label write"
    assert eligibility_match.start() < write_match.start(), (
        "step 6 must document the complete-eligible check, by position, before the "
        "craft/slice-loop=complete write it gates"
    )


# ---- 3. an ineligible union never satisfies the documented completion guard ----


def test_ineligible_full_coverage_fixture_fails_the_documented_completion_guard():
    step6 = _step("### 6. Termination — the loop's terminating condition")
    assert "candidates: none" in step6 and "complete-eligible: yes" in step6, (
        "slice/SKILL.md step 6 must key completion on both tokens together"
    )

    result = _run(FULL_COVERAGE_WITH_LEGACY_LINE)
    assert result.returncode == 0, result.stderr + result.stdout
    tokens = _tokens(result.stdout)
    assert tokens["candidates"] == "none", (
        "fixture must have an empty candidate set to exercise the guard's second half"
    )
    assert tokens["complete-eligible"] == "no", (
        "fixture's legacy ledger line must make the union ineligible: "
        f"{tokens}"
    )
    guard_satisfied = tokens["candidates"] == "none" and tokens["complete-eligible"] == "yes"
    assert not guard_satisfied, (
        "an empty candidate set with an ineligible union must not satisfy the "
        f"documented completion guard: {tokens}"
    )


# ---- 4. referential integrity: documented reason codes match the real gate's ----


def test_skill_documented_reason_codes_are_exactly_the_real_gates_reason_codes():
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    documented = set(re.findall(r"reason-code:\s*([a-z0-9-]+)", step4))

    real = {
        _reason_code(_run(MALFORMED_TOKEN).stderr),
        _reason_code(_run(UNDECLARED_COVERAGE).stderr),
        _reason_code(_run(ZERO_CRITERIA_SPEC).stderr),
    }

    assert documented == real, (
        f"slice/SKILL.md step 4 documents reason codes {documented} but the real "
        f"gate emits {real} — a rename on either side must go red here"
    )


# ---- 5. discrimination: malformed-token is refused, never fallen back on ----


def test_malformed_token_fixture_is_refused_never_fallen_back_on():
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    carveout_match = re.search(
        r"gate exits 2 with\s*`reason-code:\s*([a-z0-9-]+)`", step4
    )
    assert carveout_match, (
        "slice/SKILL.md step 4 must document the exact reason-code the legacy "
        "carve-out keys on"
    )
    carveout_code = carveout_match.group(1)

    malformed_result = _run(MALFORMED_TOKEN)
    zero_result = _run(ZERO_CRITERIA_SPEC)

    # Both fixtures exit 2 — a branch keyed on the exit code alone would pass
    # this test only by accident.
    assert malformed_result.returncode == 2
    assert zero_result.returncode == 2

    malformed_code = _reason_code(malformed_result.stderr)
    zero_code = _reason_code(zero_result.stderr)

    assert zero_code == carveout_code, (
        "the zero-identifier fixture must carry the documented carve-out's "
        f"reason code: got {zero_code!r}, carve-out is {carveout_code!r}"
    )
    assert malformed_code != carveout_code, (
        "the malformed-token fixture must NOT carry the carve-out's reason "
        f"code, or the documented 'on that reason-code, and only on that "
        f"reason-code' rule would wrongly fall back on it: got "
        f"{malformed_code!r}, carve-out is {carveout_code!r}"
    )


# ---- 6. the zero-identifier fixture is the one that fires the documented fallback ----


def test_zero_identifier_fixture_fires_the_documented_carveout_reason_code():
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    carveout_match = re.search(
        r"gate exits 2 with\s*`reason-code:\s*([a-z0-9-]+)`", step4
    )
    assert carveout_match
    carveout_code = carveout_match.group(1)

    zero_result = _run(ZERO_CRITERIA_SPEC)
    assert zero_result.returncode == 2
    assert _reason_code(zero_result.stderr) == carveout_code, (
        "the zero-identifier fixture must be the one that fires the documented "
        "legacy carve-out — this is the fixture reaching the fallback branch"
    )


# ---- 7. partial coverage: a partial ledger line keeps its criterion a candidate ----


def _spec_with_slices_body(*ledger_lines: str) -> str:
    return (
        "# Fixture spec\n\n## Acceptance Criteria\n\n"
        f"{_NINE_CRITERIA_HEADING}\n\n## Slices\n\n" + "\n".join(ledger_lines) + "\n"
    )


def test_documented_partial_only_ledger_line_keeps_its_criterion_a_candidate():
    """The documented partial-only shape, rendered and run through the real
    gate, must land its identifier on `partial:` and keep it a `candidates:`
    member — the end-to-end assertion that step 4's documented reconcile
    shape and the gate agree on one grammar."""
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    templates = _ledger_line_templates(step4)
    partial_only_shape = templates[2]  # documented third: partial-only extension
    rendered = _render(
        partial_only_shape,
        **{
            "slice title": "The streaming export slice",
            "value claim": "Exports stream instead of buffering in memory",
            "task-id": "the-streaming-export-slice",
            "close-date": "2026-09-03",
            "partially-covers-value": "AC7",
        },
    )

    result = _run(_spec_with_slices_body(rendered))
    assert result.returncode == 0, result.stderr + result.stdout
    tokens = _tokens(result.stdout)
    assert tokens["partial"] == "AC7", tokens
    assert tokens["covered"] == "none", tokens
    assert "AC7" in tokens["candidates"].split(", "), (
        f"a partially-covered criterion must remain a candidate: {tokens}"
    )


def test_documented_covers_plus_partial_ledger_line_reports_both_fields_correctly():
    """The documented both-fields shape, rendered and run through the real
    gate, must report the fully covered identifier on `covered:` (and out of
    `candidates:`) and the partially covered identifier on `partial:` (and
    still inside `candidates:`)."""
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    templates = _ledger_line_templates(step4)
    both_fields_shape = templates[3]  # documented fourth: covers-plus-partial
    rendered = _render(
        both_fields_shape,
        **{
            "slice title": "The streaming export slice",
            "value claim": "Exports stream instead of buffering in memory",
            "task-id": "the-streaming-export-slice",
            "close-date": "2026-09-03",
            "covers-value": "AC5",
            "partially-covers-value": "AC2",
        },
    )

    result = _run(_spec_with_slices_body(rendered))
    assert result.returncode == 0, result.stderr + result.stdout
    tokens = _tokens(result.stdout)
    assert tokens["covered"] == "AC5", tokens
    assert tokens["partial"] == "AC2", tokens
    candidates = tokens["candidates"].split(", ")
    assert "AC2" in candidates and "AC5" not in candidates, (
        f"a fully-covered identifier must leave candidates, a partial one must stay: {tokens}"
    )


# ---- 8. regression: the four-field legacy line still parses unchanged -----


def test_four_field_legacy_ledger_line_still_parses_unchanged():
    """The original no-coverage shape must keep working exactly as before —
    the regression guard on a mixed ledger, which this spec's own record is:
    it carries both a legacy slice-1 line and modern covers/partial lines."""
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    templates = _ledger_line_templates(step4)
    legacy_shape = templates[0]  # documented first: the four-field, no-coverage shape
    rendered = _render(
        legacy_shape,
        **{
            "slice title": "An early slice",
            "value claim": "Shipped before the covers field existed",
            "task-id": "an-early-slice",
            "close-date": "2026-01-01",
        },
    )

    result = _run(_spec_with_slices_body(rendered))
    assert result.returncode == 0, result.stderr + result.stdout
    tokens = _tokens(result.stdout)
    assert tokens["covered"] == "none", tokens
    assert tokens["partial"] == "none", tokens
    assert tokens["complete-eligible"] == "no", (
        "a legacy entry (neither coverage field) must still make the union "
        f"ineligible, exactly as before this change: {tokens}"
    )


# ---- 9. the gate's partial: token is surfaced in the printed basis, not merely computed ----


def test_partial_token_documented_as_surfaced_in_the_printed_basis():
    """The real gate must emit a `partial:` line naming a partially covered
    criterion on the documented partial-only ledger shape — the behavioural
    pin for step 4's instruction to surface that token in the printed basis.
    A check that the step's prose merely contains the phrase would pass on
    any wording change that keeps the words, whether or not the gate still
    behaves this way, so this test drives the real gate instead."""
    step4 = _step("### 4. Reconcile the `## Slices` ledger, then derive the candidate set")
    pipe_match = re.search(r"candidate_set\.py", step4)
    assert pipe_match, "slice/SKILL.md step 4 must document the candidate_set.py pipe"

    templates = _ledger_line_templates(step4)
    partial_only_shape = templates[2]
    rendered = _render(
        partial_only_shape,
        **{
            "slice title": "The streaming export slice",
            "value claim": "Exports stream instead of buffering in memory",
            "task-id": "the-streaming-export-slice",
            "close-date": "2026-09-03",
            "partially-covers-value": "AC7",
        },
    )
    result = _run(_spec_with_slices_body(rendered))
    assert result.returncode == 0, result.stderr + result.stdout
    assert re.search(r"^partial: AC7$", result.stdout, re.MULTILINE), (
        f"the real gate must print a partial: line naming the partially "
        f"covered criterion, for the documented basis to surface: {result.stdout!r}"
    )
