"""Brainstorm's framing step (step 1, "Frame") resolves a maturity level for
every repository the work touches, before grilling (step 2) begins — per
`spec/project-maturity-levels-calibrate-craft-s-standard-of-care` AC3/AC3b.

These tests bind the skill's prose to something executable — the real
`maturity_resolve.py` resolver run as a subprocess against real fixture
agent-instruction bodies — never to a copy of the skill's own wording, mirroring
`test_slice_candidate_set_contract.py`'s established pattern for this repo.
"""

from __future__ import annotations

import re
from pathlib import Path

# The resolver runner and fixture bodies are the same ones the resolver's own
# suite drives, so a change to either the invocation or the token block reaches
# both suites from one place.
from test_maturity_resolve import (
    INVALID_VALUE_DECLARED,
    NO_SECTION_AT_ALL,
    _lines,
    _run,
)

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
BRAINSTORM_SKILL = CRAFT / "skills" / "brainstorm" / "SKILL.md"


def _skill_text() -> str:
    return BRAINSTORM_SKILL.read_text(encoding="utf-8")


def _step(name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading."""
    text = _skill_text()
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d+\.", rest)
    return rest[: end.start()] if end else rest


# ---- 1. the framing step instructs resolution, before grilling begins ----


def test_frame_step_instructs_maturity_resolution_for_every_touched_repository():
    frame_step = _step("### 1. Frame")
    assert "maturity_resolve.py" in frame_step, (
        "brainstorm's framing step (step 1) must instruct invoking the "
        "maturity resolver"
    )
    assert re.search(r"every repositor(y|ies)", frame_step, re.IGNORECASE), (
        "the framing step must instruct resolution for every repository the "
        "work touches, not merely the current one"
    )


def test_maturity_resolution_is_documented_inside_step_1_before_step_2_heading():
    text = _skill_text()
    step1_start = text.index("### 1. Frame")
    step2_start = text.index("### 2. Grill for Clarity")
    resolver_mention = text.index("maturity_resolve.py")
    assert step1_start < resolver_mention < step2_start, (
        "the maturity resolution instruction must live inside step 1 (Frame), "
        "positioned before step 2 (Grill for Clarity) begins"
    )


# ---- 2. the closed vocabulary is named, and only that vocabulary ----


def test_frame_step_names_the_closed_vocabulary_and_no_other_level():
    frame_step = _step("### 1. Frame")
    assert "prototype" in frame_step
    assert "early" in frame_step
    assert "production" in frame_step
    # No fourth level word slips in beside the closed three.
    for off_vocab in ("spike", "throwaway", "mature", "stable", "beta"):
        assert off_vocab not in frame_step.lower(), (
            f"step 1 must name only prototype/early/production, found "
            f"off-vocabulary word {off_vocab!r}"
        )


# ---- 3. an absent declaration resolves to production ----


def test_absent_declaration_resolves_to_production_via_the_real_resolver():
    result = _run(NO_SECTION_AT_ALL.encode("utf-8"))
    assert result.returncode == 0, result.stderr
    assert "level: production" in _lines(result)
    assert "reason: section-absent" in _lines(result)

    frame_step = _step("### 1. Frame")
    # Scoped to the absent-declaration clause alone (up to its terminating
    # `;`), so a `production` mention belonging to the neighbouring
    # invalid-value clause can never satisfy this — a decoy the unscoped
    # `absent...production` search across the whole step would miss.
    absent_clause_match = re.search(r"section-absent[^;]*;", frame_step, re.IGNORECASE)
    assert absent_clause_match, (
        "step 1 must have a section-absent clause terminated by ';'"
    )
    assert "production" in absent_clause_match.group(0), (
        "step 1 must state, within the absent-declaration clause itself, that "
        f"it resolves to production: {absent_clause_match.group(0)!r}"
    )


# ---- 4. an out-of-vocabulary declaration resolves to production and is reported ----


def test_out_of_vocabulary_declaration_resolves_to_production_via_the_real_resolver():
    result = _run(INVALID_VALUE_DECLARED.encode("utf-8"))
    assert result.returncode == 0, result.stderr
    assert "level: production" in _lines(result)
    assert "reason: invalid-value" in _lines(result)
    assert any(line.startswith("offending-value:") for line in _lines(result)), (
        "the resolver must emit the offending-value line the report reads"
    )


def test_frame_step_documents_reporting_the_offending_value_and_the_valid_ones():
    frame_step = _step("### 1. Frame")
    assert "offending-value" in frame_step or "offending value" in frame_step, (
        "step 1 must document reporting the offending value on an "
        "out-of-vocabulary declaration"
    )
    assert re.search(r"valid (level|value)", frame_step, re.IGNORECASE), (
        "step 1 must document naming the valid levels alongside the offending "
        "value"
    )


# ---- 5. the resolved level is stated per repository, in the session ----


def test_frame_step_requires_stating_the_resolved_level_per_repository_in_session():
    frame_step = _step("### 1. Frame")
    # Step 1 mentions "session" many times elsewhere (the prior-art survey
    # block), so an unscoped "session" search would pass on any of those —
    # scope to one sentence spanning "per repository" through "the session".
    same_sentence = re.search(
        r"per repositor(?:y|ies)[^.]*\bsession\b", frame_step, re.IGNORECASE
    )
    assert same_sentence, (
        "step 1 must require, in one statement, that the resolved level be "
        f"stated per repository in the session: {frame_step!r}"
    )


# ---- 6. the resolver is invoked by the same absolute-path convention ----


def test_resolver_invoked_by_the_same_absolute_path_convention_as_other_gates():
    frame_step = _step("### 1. Frame")
    assert "${CLAUDE_PLUGIN_ROOT}/scripts/maturity_resolve.py" in frame_step, (
        "step 1 must invoke the resolver via the ${CLAUDE_PLUGIN_ROOT}/scripts/ "
        "convention the existing gate invocations (candidate_set.py, "
        "covers_gate.py, criterion_gate.py) already use"
    )

    slice_skill_text = (CRAFT / "skills" / "slice" / "SKILL.md").read_text(encoding="utf-8")
    other_gate_invocations = re.findall(
        r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/\S+\.py",
        slice_skill_text,
    )
    assert other_gate_invocations, "fixture assumption: slice/SKILL.md invokes gates this way"
    # Every existing invocation is piped input, never a bare invocation with
    # flags substituting for stdin — the maturity resolver follows the same
    # "pipe content in" shape rather than a flag-based interface.
    for invocation in other_gate_invocations:
        assert re.search(
            r"\|\s*" + re.escape(invocation), slice_skill_text
        ), f"existing gate invocation {invocation!r} must be piped, not standalone"

    assert re.search(
        r"\|\s*\$\{CLAUDE_PLUGIN_ROOT\}/scripts/maturity_resolve\.py", frame_step
    ), (
        "the maturity resolver invocation must also be piped input, matching "
        "the established gate-invocation shape"
    )
