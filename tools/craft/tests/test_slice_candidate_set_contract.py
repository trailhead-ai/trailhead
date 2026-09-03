"""`/craft:slice` derives its candidate set and its termination decision from
the real `candidate_set.py` gate, per step 4 and step 6 of `slice/SKILL.md`.

These tests bind the skill's prose to something executable — the real gate
script run as a subprocess against real fixture spec bodies, and the
document-position of the tokens the prose claims to key on — never to a copy
of the skill's own wording. A test asserting the skill file merely contains a
phrase is not acceptable here (spec Constraints), so every extraction below
is fed through the real script or checked against real gate output.
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
    assert len(templates) == 2, (
        "slice/SKILL.md step 4 must document exactly two ledger-line shapes — "
        f"one with no coverage field and one carrying it; found {templates}"
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


# ---- 6. the zero-identifier fixture takes the fallback and reports the legacy basis ----


def test_zero_identifier_fixture_takes_fallback_and_reports_distinct_legacy_basis():
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

    gate_basis_match = re.search(r"`(termination basis: gate-certified)`", step4)
    legacy_basis_match = re.search(
        r"`(termination basis: legacy prose-match, not gate-certified)`", step4
    )
    assert gate_basis_match, (
        "slice/SKILL.md step 4 must document the gate-certified basis line verbatim"
    )
    assert legacy_basis_match, (
        "slice/SKILL.md step 4 must document the legacy prose-match basis line "
        "verbatim, for the reason-code the zero-identifier fixture fires"
    )
    assert gate_basis_match.group(1) != legacy_basis_match.group(1), (
        "the gate-certified and legacy prose-match basis lines must never be "
        "reported in the same words"
    )
