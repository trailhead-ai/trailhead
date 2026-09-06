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
    # Word-boundary matches, not bare substring checks — "early" bare would
    # be satisfiable by "clearly"/"nearly"/"yearly" appearing anywhere in
    # this multi-thousand-character step, without the vocabulary word itself
    # ever being named.
    assert re.search(r"\bprototype\b", frame_step)
    assert re.search(r"\bearly\b", frame_step)
    assert re.search(r"\bproduction\b", frame_step)
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
    # Anchored to the invalid-value clause itself (from the reason token
    # through the terminating '.'), not the whole multi-thousand-character
    # step — an unscoped search would be satisfied by "offending-value" or
    # "valid level" appearing in an unrelated clause elsewhere in step 1.
    clause_match = re.search(r"invalid-value`?\)[^.]*\.", frame_step)
    assert clause_match, (
        "step 1 must have an invalid-value clause terminated by '.'"
    )
    clause = clause_match.group(0)
    assert "offending-value" in clause or "offending value" in clause, (
        "the invalid-value clause must document reporting the offending "
        f"value: {clause!r}"
    )
    assert re.search(r"valid (level|value)", clause, re.IGNORECASE), (
        "the invalid-value clause must document naming the valid levels "
        f"alongside the offending value: {clause!r}"
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


# ---- 7. behaviour is named for a non-zero resolver exit on an existing file ----


def test_frame_step_names_behaviour_on_a_non_zero_resolver_exit():
    """A resolver invocation can exit non-zero (fail-closed) even for a
    repository whose agent-instruction file exists — non-UTF-8 content, or a
    read failure mid-pipe. Step 1 must say what happens then, or a
    repository can silently end up with no level stated at all (AC3/AC3b)."""
    frame_step = _step("### 1. Frame")
    # Scoped to the resolver's OWN non-zero exit (a fail-closed read failure
    # on an existing file), not the neighbouring "leak a non-zero exit"
    # clause about the absence-path guard, which is a different case.
    assert re.search(r"resolver.{0,20}exits? non-zero", frame_step, re.IGNORECASE), (
        "step 1 must name the resolver's own non-zero-exit case explicitly"
    )
    clause_match = re.search(
        r"resolver.{0,20}exits? non-zero[^.]*\.", frame_step, re.IGNORECASE
    )
    assert clause_match, "step 1 must have a non-zero-exit clause terminated by '.'"
    clause = clause_match.group(0)
    assert "production" in clause, (
        f"the non-zero-exit clause must still state a level (production): {clause!r}"
    )


# ---- 8. the unreachable-repository case is named, not silently omitted ----


def test_frame_step_names_the_case_where_repositories_cannot_be_enumerated():
    """Beyond the camp-workspace and single-current-repo cases, a session
    rooted above several repositories with no camp manifest to enumerate
    them must be named explicitly — never silently folded into the
    single-current-repo fallback, which would omit sibling repos this work
    touches."""
    frame_step = _step("### 1. Frame")
    assert re.search(r"neither", frame_step, re.IGNORECASE), (
        "step 1 must name the case where neither the camp-workspace nor the "
        "single-current-repo enumeration applies"
    )
    clause_match = re.search(r"neither[^.]*\.", frame_step, re.IGNORECASE)
    assert clause_match, "step 1 must have a 'neither applies' clause terminated by '.'"
    clause = clause_match.group(0)
    assert re.search(r"explicit|state|cannot", clause, re.IGNORECASE), (
        f"the 'neither applies' clause must require stating the case explicitly: {clause!r}"
    )
