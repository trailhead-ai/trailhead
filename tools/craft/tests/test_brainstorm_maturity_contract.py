"""Brainstorm's framing step (step 1, "Frame") resolves a maturity level for
every repository the work touches, before grilling (step 2) begins — per
`spec/project-maturity-levels-calibrate-craft-s-standard-of-care` AC3/AC3b.

These tests bind the skill's prose to something executable — the real
`maturity_resolve.py` resolver run as a subprocess against real fixture
agent-instruction bodies — never to a copy of the skill's own wording, mirroring
`test_slice_candidate_set_contract.py`'s established pattern for this repo.

The second half of this module (below the "Write path" banner) covers
brainstorm's spec-write step (step "6a. Write the Spec"): it fills the spec
template's `## Maturity` section from step 1's resolution and certifies the
drafted body through `maturity_stamp.py` before `lore record create` runs —
per `task/brainstorm-stamps-the-spec-and-the-template-carries-the-section`.
Those tests bind to the real `maturity_stamp.py`, `candidate_set.py`, and
`covers_gate.py` scripts run as subprocesses against real fixture spec
bodies, never to a copy of the skill's own wording.
"""

from __future__ import annotations

import re
import subprocess
import sys
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
# The reader's own suite owns its script path, so the two suites can never
# drift onto different files.
from test_maturity_stamp import STAMP

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
BRAINSTORM_SKILL = CRAFT / "skills" / "brainstorm" / "SKILL.md"
SCRIPTS_DIR = CRAFT / "scripts"
CANDIDATE_SET = SCRIPTS_DIR / "candidate_set.py"
COVERS_GATE = SCRIPTS_DIR / "covers_gate.py"
SPEC_TEMPLATE = CRAFT / "templates" / "spec.md"


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


# ===========================================================================
# Write path — step "6a. Write the Spec" fills `## Maturity` and certifies it
# through `maturity_stamp.py` before `lore record create` runs.
# ===========================================================================

sys.path.insert(0, str(SCRIPTS_DIR))
import maturity_stamp  # noqa: E402

# Scanned from the reader's own module namespace rather than hand-listed here:
# a hardcoded copy of this list is exactly the staleness this reader's tenth
# reason-code (`member-name-too-long`) already exposed once — a new eleventh
# code must fail this test until documented, not silently pass a frozen list.
_STAMP_REASON_CODES = sorted(
    value
    for name, value in vars(maturity_stamp).items()
    if name.endswith("_REASON_CODE")
)


def _write_step() -> str:
    return _step("### 6a. Write the Spec")


def _run_script(
    script: Path, body: str, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *(extra_args or [])],
        input=body.encode("utf-8"),
        capture_output=True,
    )


# ---- 9. the spec template's `## Maturity` section, filled in, round-trips
#         through the real stamp reader at exit 0 ----------------------------


def _render_template_with_maturity_entries(entries: dict[str, str]) -> str:
    """Render `templates/spec.md` with the given `{member: level}` entries filled
    into its `## Maturity` section — the same shape brainstorm's write step
    produces from step 1's resolution."""
    text = SPEC_TEMPLATE.read_text(encoding="utf-8")
    marker = "## Acceptance Criteria"
    idx = text.index(marker)
    bullet_lines = "\n".join(f"- {name}: {level}" for name, level in entries.items())
    return text[:idx] + bullet_lines + "\n\n" + text[idx:]


def test_template_rendered_with_two_repository_maturity_section_accepted_at_exit_0():
    body = _render_template_with_maturity_entries(
        {"trailhead": "production", "lookout": "prototype"}
    )
    result = _run_script(STAMP, body)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert result.stdout.decode("utf-8") == "maturity: lookout=prototype, trailhead=production\n"


# ---- 5. the write step instructs writing the section, by heading, keyed by
#         camp member name ---------------------------------------------------


def test_write_step_instructs_writing_the_maturity_section_keyed_by_member_name():
    step = _write_step()
    assert "## Maturity" in step, (
        "step 6a must instruct writing the `## Maturity` section by its heading"
    )
    assert re.search(r"camp member name", step, re.IGNORECASE), (
        "step 6a must name the camp member name as the section's key"
    )


def test_write_step_stamps_repositories_the_work_touches_not_every_enumerated_member():
    """AC5 and the template both scope the stamp to 'every repository this work
    touches' — a possible subset of step 1's enumeration, which resolves a level
    for every camp-workspace member regardless of whether the work touches it.
    Stamping the full enumeration reimports exactly the production-ceremony-on-
    a-prototype failure this spec exists to prevent (AC8 rates an unattributable
    finding at the highest stamped level)."""
    step = _write_step()
    assert re.search(r"repositor(?:y|ies) this work touches", step, re.IGNORECASE), (
        "step 6a must scope the stamp to the repositories this work touches, "
        f"matching AC5's own wording: {step!r}"
    )
    assert "step 1's enumeration reached" not in step, (
        "step 6a must not instruct stamping every repository step 1's enumeration "
        f"reached — that over-stamps relative to AC5: {step!r}"
    )


def test_template_names_the_same_scope_as_the_write_step():
    """The template's own `## Maturity` comment already says 'this work touches' —
    the write step's instruction must agree with it, not merely with AC5."""
    template_text = SPEC_TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r"repositor(?:y|ies) this work touches", template_text, re.IGNORECASE), (
        "fixture assumption: templates/spec.md's Maturity comment scopes to "
        "repositories this work touches"
    )


# ---- 6. the write step instructs certifying via the real reader before
#         `lore record create`, and refusing on a non-zero exit -------------


def test_write_step_instructs_certifying_via_maturity_stamp_before_create():
    step = _write_step()
    assert "maturity_stamp.py" in step, (
        "step 6a must instruct piping the drafted body through maturity_stamp.py"
    )
    assert re.search(r"non-zero exit", step, re.IGNORECASE), (
        "step 6a must name the non-zero-exit refusal case explicitly"
    )
    refusal_clause_match = re.search(r"non-zero exit[^.]*\.", step, re.IGNORECASE)
    assert refusal_clause_match, "step 6a must have a non-zero-exit clause terminated by '.'"
    assert re.search(r"refus", refusal_clause_match.group(0), re.IGNORECASE), (
        "the non-zero-exit clause must instruct refusing the write, not proceeding: "
        f"{refusal_clause_match.group(0)!r}"
    )


def test_write_step_names_no_exception_to_the_non_zero_exit_refusal():
    """A non-zero exit always refuses the write. There is no longer a
    sanctioned exception — `empty-section` and `unresolved-enumeration` are
    now two distinct, both-refusing codes, so no reason-code lets a create
    proceed on a non-zero exit."""
    step = _write_step()
    assert "sanctioned exception" not in step.lower(), (
        "step 6a must not carry a sanctioned-exception clause — every "
        f"non-zero exit refuses the write: {step!r}"
    )
    refusal_clause_match = re.search(r"non-zero exit[^.]*\.", step, re.IGNORECASE)
    assert refusal_clause_match, "step 6a must have a non-zero-exit clause terminated by '.'"
    assert "exception" not in refusal_clause_match.group(0).lower(), (
        "the non-zero-exit clause itself must not name an exception: "
        f"{refusal_clause_match.group(0)!r}"
    )


def test_maturity_certify_precedes_the_record_create_it_guards():
    step = _write_step()
    certify_at = step.index("maturity_stamp.py")
    create_at = step.index("lore record create --kind spec")
    assert certify_at < create_at, (
        "the maturity_stamp.py certify instruction must appear before the "
        "`lore record create --kind spec` call it guards within step 6a"
    )


# ---- 7. every reason-code the reader can emit gets its own named remedy ----


def test_write_step_names_a_distinct_remedy_for_every_stamp_reason_code():
    step = _write_step()
    clauses: dict[str, str] = {}
    for code in _STAMP_REASON_CODES:
        # Scoped to the bullet line naming this reason-code, from just past its
        # backticked token through the terminating '.' — captured as its own
        # group so the code token itself (always distinct) never masks two
        # codes sharing identical remedy prose.
        pattern = re.escape(f"`{code}`") + r"\s*—\s*([^.]*\.)"
        match = re.search(pattern, step)
        assert match, f"step 6a must name a remedy for reason-code `{code}`"
        clauses[code] = match.group(1)
    # A shared boilerplate sentence copy-pasted under all eight codes would
    # satisfy the loop above without translating any of them individually —
    # guard against that by requiring eight *distinct* remedy clauses.
    assert len(set(clauses.values())) == len(_STAMP_REASON_CODES), (
        f"each reason-code must get its own distinct remedy clause, not a shared "
        f"boilerplate line: {clauses!r}"
    )


# ---- 8. the unresolved-enumeration case is named, not silently completed ---


def test_write_step_names_the_unresolved_enumeration_case():
    step = _write_step()
    assert re.search(r"unresolved-enumeration", step, re.IGNORECASE), (
        "step 6a must name the unresolved-enumeration case (step 1's own term "
        "for when repositories could not be enumerated at all)"
    )
    clause_match = re.search(r"unresolved-enumeration[^.]*\.", step, re.IGNORECASE)
    assert clause_match, "step 6a must have an unresolved-enumeration clause terminated by '.'"
    assert re.search(r"complete", clause_match.group(0), re.IGNORECASE), (
        "the unresolved-enumeration clause must contrast against a stamp that "
        f"reads as complete: {clause_match.group(0)!r}"
    )


# ---- Slices-sibling hard constraint: the template's placement is inert to
#      both spec-reading gates, for a spec that carries a real `## Slices`
#      ledger and one that carries none — a permanent, narrower successor to
#      the assumption-prover's own four-placement probe. -------------------

_HEAD = "# Fixture Spec\n\n## Problem\nSome problem text.\n\n## Objectives\n- Objective one.\n\n"
_AC_SECTION = (
    "## Acceptance Criteria\n\n"
    "- **AC1.** A fixture criterion.\n"
    "- **AC2.** A fixture criterion.\n"
    "- **AC3.** A fixture criterion.\n\n"
)
_SLICES_SECTION = (
    "## Slices\n\n"
    "- **First slice** — a fixture value claim. "
    "(`task/first`, closed 2026-01-01, covers AC1, AC2)\n"
    "- **Second slice** — a fixture value claim. "
    "(`task/second`, closed 2026-01-02, covers AC3)\n\n"
)
_TAIL = "## Non-Goals\nNothing else in scope.\n\n## Related\nn/a\n"
_MATURITY_SECTION = "## Maturity\n\n- trailhead: production\n- lookout: prototype\n\n"

_BASE_WITH_LEDGER = _HEAD + _AC_SECTION + _SLICES_SECTION + _TAIL
_TEMPLATE_PLACEMENT_WITH_LEDGER = _HEAD + _MATURITY_SECTION + _AC_SECTION + _SLICES_SECTION + _TAIL
_BASE_NO_LEDGER = _HEAD + _AC_SECTION + _TAIL
_TEMPLATE_PLACEMENT_NO_LEDGER = _HEAD + _MATURITY_SECTION + _AC_SECTION + _TAIL
# The hazard the prover found: a heading wedged between the `## Slices` heading
# and its first ledger bullet — never the template's own placement, but the
# fixture that proves the inertness assertions below are actually sensitive
# to placement rather than vacuously true.
_HAZARD_MATURITY_NESTED_IN_SLICES = (
    _HEAD
    + _AC_SECTION
    + "## Slices\n\n"
    + _MATURITY_SECTION
    + "- **First slice** — a fixture value claim. "
    "(`task/first`, closed 2026-01-01, covers AC1, AC2)\n"
    "- **Second slice** — a fixture value claim. "
    "(`task/second`, closed 2026-01-02, covers AC3)\n\n"
    + _TAIL
)


def test_template_placement_is_inert_to_candidate_set_with_ledger():
    base = _run_script(CANDIDATE_SET, _BASE_WITH_LEDGER)
    variant = _run_script(CANDIDATE_SET, _TEMPLATE_PLACEMENT_WITH_LEDGER)
    assert variant.returncode == base.returncode
    assert variant.stdout == base.stdout, (base.stdout, variant.stdout)


def test_template_placement_is_inert_to_candidate_set_without_ledger():
    base = _run_script(CANDIDATE_SET, _BASE_NO_LEDGER)
    variant = _run_script(CANDIDATE_SET, _TEMPLATE_PLACEMENT_NO_LEDGER)
    assert variant.returncode == base.returncode
    assert variant.stdout == base.stdout, (base.stdout, variant.stdout)


def test_template_placement_is_inert_to_covers_gate_with_ledger():
    args = ["--covers", "AC1, AC2, AC3"]
    base = _run_script(COVERS_GATE, _BASE_WITH_LEDGER, args)
    variant = _run_script(COVERS_GATE, _TEMPLATE_PLACEMENT_WITH_LEDGER, args)
    assert variant.returncode == base.returncode
    assert variant.stdout == base.stdout, (base.stdout, variant.stdout)


def test_template_placement_is_inert_to_covers_gate_without_ledger():
    args = ["--covers", "AC1, AC2, AC3"]
    base = _run_script(COVERS_GATE, _BASE_NO_LEDGER, args)
    variant = _run_script(COVERS_GATE, _TEMPLATE_PLACEMENT_NO_LEDGER, args)
    assert variant.returncode == base.returncode
    assert variant.stdout == base.stdout, (base.stdout, variant.stdout)


def test_maturity_nested_inside_slices_diverges_from_the_ledger_positive_control():
    """Positive control for the four tests above: proves they are actually
    sensitive to placement rather than passing vacuously. The hazard placement
    (heading nested between `## Slices` and its first bullet) must NOT be
    inert — `candidate_set.py` regresses `candidates` to the full criteria set
    and reports `complete-eligible: yes` at exit 0, silently losing the
    ledger's two entries."""
    base = _run_script(CANDIDATE_SET, _BASE_WITH_LEDGER)
    hazard = _run_script(CANDIDATE_SET, _HAZARD_MATURITY_NESTED_IN_SLICES)
    assert hazard.returncode == 0, hazard.stderr.decode("utf-8")
    assert hazard.stdout != base.stdout, (
        "the nested-in-Slices hazard placement must diverge from the base body "
        "(silent ledger truncation) — if it doesn't, the inertness tests above "
        "are not actually sensitive to placement"
    )


# ---- the unresolved-enumeration case has its own reason-code, and it still
#      refuses the write like every other non-zero exit -----------------------
#
# Step 6a instructs writing an explicit note when step 1's enumeration could not
# reach the repositories at all, and separately gates `lore record create` on the
# reader exiting 0. `empty-section` used to double as this case's outcome too —
# byte-identical to a stamp the author simply forgot to fill — which is why the
# reader now emits a ninth, distinct reason-code for it. It still refuses: the
# bright line ("a non-zero exit always refuses the write") holds with no
# exception. These bind to the real reader, not to the skill's wording.

_UNRESOLVED_ENUMERATION_BODY = """# X

## Maturity
<!-- unresolved-enumeration: step 1 could not enumerate the repositories this work touches -->

## Acceptance Criteria

- **AC1.** thing
"""


def test_unresolved_enumeration_note_reads_as_its_own_reason_code():
    """A prose sentence under the heading trips `malformed-entry`; the prescribed
    comment-shaped note must trip neither that nor the generic `empty-section` —
    it gets its own code, and still exits non-zero."""
    result = _run_script(STAMP, _UNRESOLVED_ENUMERATION_BODY)
    assert result.returncode == 2
    err = result.stderr.decode("utf-8")
    assert "reason-code: malformed-entry" not in err, err
    assert "reason-code: empty-section" not in err, err
    assert "reason-code: unresolved-enumeration" in err, err


def test_skill_prescribes_a_comment_shaped_unresolved_enumeration_note():
    text = BRAINSTORM_SKILL.read_text()
    section = text[text.index("**Fill `## Maturity`") : text.index("**Certify the drafted body")]
    assert "<!--" in section, (
        "step 6a must prescribe a comment-shaped unresolved-enumeration note — a bare "
        f"sentence under the heading is rejected by the certify step it feeds: {section!r}"
    )


def test_skill_names_empty_section_as_meaning_only_an_unfilled_stamp():
    """`empty-section` no longer has a second, sanctioned cause — it means one
    thing, and it always refuses. The unresolved-enumeration case now has its
    own code and is not mentioned in this bullet."""
    text = BRAINSTORM_SKILL.read_text()
    bullet = re.search(r"- `empty-section`[^\n]*(?:\n  [^\n]*)*", text)
    assert bullet, "the `empty-section` remedy bullet must exist"
    assert not re.search(r"unresolved.enumeration", bullet.group(0), re.IGNORECASE), (
        "the `empty-section` remedy must no longer name the unresolved-enumeration "
        f"case — that case now has its own reason-code: {bullet.group(0)!r}"
    )
    assert re.search(r"refus", bullet.group(0), re.IGNORECASE), (
        "the `empty-section` remedy must state that it refuses, with no exception: "
        f"{bullet.group(0)!r}"
    )


def test_skill_names_a_remedy_for_the_unresolved_enumeration_reason_code_that_refuses():
    """The new code gets its own remedy bullet, and that remedy still refuses the
    write — resolving the enumeration (not proceeding on the marked note) is the
    only path back to a create."""
    text = BRAINSTORM_SKILL.read_text()
    bullet = re.search(r"- `unresolved-enumeration`[^\n]*(?:\n  [^\n]*)*", text)
    assert bullet, "an `unresolved-enumeration` remedy bullet must exist"
    assert re.search(r"resolv", bullet.group(0), re.IGNORECASE), (
        "the `unresolved-enumeration` remedy must instruct resolving the "
        f"enumeration before retrying: {bullet.group(0)!r}"
    )


def test_skill_instructs_retaining_the_keep_marked_reminder_comment():
    """The template's keep-marker is only half the mechanism — the step that fills
    the section has to honour it. Without this, the reminder survives by convention
    alone, which is the failure the reminder exists to prevent."""
    text = BRAINSTORM_SKILL.read_text()
    section = text[text.index("**Fill `## Maturity`") : text.index("**Certify the drafted body")]
    assert re.search(r"\bkeep\b", section, re.IGNORECASE), (
        "step 6a must instruct keeping the template's keep-marked reminder comment "
        f"when the section is filled: {section!r}"
    )


# ---- the near-miss heading (trailing/doubled whitespace) is named in the
#      section-absent remedy, not just "add it" -----------------------------


def test_section_absent_remedy_names_the_near_miss_heading_case():
    """A heading with trailing or doubled whitespace (`## Maturity `,
    `##  Maturity`) also exits `section-absent`, because the heading matcher
    requires an exact match. Following the bare "add it" remedy on that body
    produces a *second* heading and `duplicate-section` on retry — the remedy
    must name the near-miss case so an author checks for it first."""
    text = BRAINSTORM_SKILL.read_text()
    bullet = re.search(r"- `section-absent`[^\n]*(?:\n  [^\n]*)*", text)
    assert bullet, "the `section-absent` remedy bullet must exist"
    assert re.search(r"whitespace|near.miss", bullet.group(0), re.IGNORECASE), (
        "the `section-absent` remedy must name the near-miss-heading case (trailing "
        f"or doubled whitespace): {bullet.group(0)!r}"
    )


def test_section_absent_fixture_heading_with_trailing_space_reproduces_the_near_miss():
    """Fixture proof the near-miss case is real: a heading with a trailing space
    exits `section-absent`, not some other code — grounding the remedy-text
    assertion above in actual reader behaviour."""
    body = "# X\n\n## Maturity \n\n- lookout: prototype\n\n## Acceptance Criteria\n"
    result = _run_script(STAMP, body)
    assert result.returncode == 2
    assert "reason-code: section-absent" in result.stderr.decode("utf-8")


# ---- the vanilla single-repo path has a defined stamp key -------------------


# ---- the keep-marked reminder survives the transformation the skill actually
#      prescribes: strip the pre-fill comment, keep the keep-marked one --------


def _apply_skill_prescribed_fill_transformation(section_text: str) -> str:
    """Strip the longer pre-fill comment, keep the keep-marked reminder
    comment — the exact transformation step 6a's own prose prescribes
    ("Keep the template's keep-marked reminder comment in the filled
    section; strip only the longer pre-fill comment above it.")."""
    comments = re.findall(r"<!--.*?-->", section_text, re.DOTALL)
    assert len(comments) == 2, (
        "fixture assumption: the template's Maturity section carries exactly "
        f"two HTML comments (pre-fill + keep-marked): {comments!r}"
    )
    prefill, keep_marked = comments
    assert "keep this line" in keep_marked, (
        "fixture assumption: the second comment is the keep-marked reminder: "
        f"{keep_marked!r}"
    )
    return section_text.replace(prefill, "")


def test_keep_marked_reminder_survives_pre_fill_strip_with_vocabulary_intact():
    """Task 2's contract: the grammar reminder must survive into a rendered
    spec body — a body with the pre-fill comment stripped still tells a later
    hand-editor what it may contain. This observes an actual rendered body,
    not just that a marker string and the word 'keep' each exist somewhere."""
    template_text = SPEC_TEMPLATE.read_text(encoding="utf-8")
    start = template_text.index("## Maturity")
    end = template_text.index("## Acceptance Criteria")
    section = template_text[start:end]

    rendered = _apply_skill_prescribed_fill_transformation(section)

    assert "prototype" in rendered
    assert "early" in rendered
    assert "production" in rendered
    # The pre-fill comment's own distinguishing prose is gone — proof the
    # vocabulary above survived via the keep-marked comment, not a fluke of
    # the pre-fill comment being left in place.
    assert "never invented here" not in rendered


def test_fill_step_defines_the_vanilla_single_repo_stamp_key():
    """Step 1's enumeration has a single-current-repo path outside a camp
    workspace (no camp manifest, no camp member name to key on). Step 6a's
    fill instruction must define what key that entry uses, or a vanilla
    single-repo write has no defined `## Maturity` bullet to produce."""
    text = BRAINSTORM_SKILL.read_text()
    section = text[text.index("**Fill `## Maturity`") : text.index("**Certify the drafted body")]
    assert re.search(r"vanilla", section, re.IGNORECASE), (
        "step 6a must name the vanilla (no camp manifest) single-repo case "
        f"explicitly: {section!r}"
    )
    assert re.search(r"basename|directory name", section, re.IGNORECASE), (
        "step 6a must define the vanilla single-repo stamp key concretely "
        f"(e.g. the repository directory's basename): {section!r}"
    )


# ---- the malformed-entry remedy is actionable even when the offending text
#      is the member name itself, un-representable in the safe grammar -----


def _malformed_entry_bullet() -> str:
    text = BRAINSTORM_SKILL.read_text()
    bullet = re.search(r"- `malformed-entry`[^\n]*(?:\n  [^\n]*)*", text)
    assert bullet, "the `malformed-entry` remedy bullet must exist"
    return bullet.group(0)


def test_malformed_entry_remedy_names_normalization_for_an_unrepresentable_member_name():
    """Camp validates member names only as non-empty strings — no character
    restriction — so a name like `my repo` can never satisfy the reader's
    safe grammar (`^(?!\\.{1,2}$)[A-Za-z0-9._-]+$`) no matter how its shape is
    corrected. 'correct that line's shape' alone is not actionable for that
    case; the remedy must name a real normalization."""
    bullet = _malformed_entry_bullet()
    assert re.search(r"normali[sz]e", bullet, re.IGNORECASE), (
        f"the `malformed-entry` remedy must name normalizing an un-representable "
        f"member name, not just 'correct that line's shape': {bullet!r}"
    )


def test_malformed_entry_remedy_names_collision_detection_for_normalized_keys():
    """A normalization that can map two distinct member names to the same
    safe-grammar key must say how that collision is caught — never a silent
    one-shadows-the-other write."""
    bullet = _malformed_entry_bullet()
    assert re.search(r"collid|collision", bullet, re.IGNORECASE), (
        f"the `malformed-entry` remedy must name collision detection for the "
        f"normalized key: {bullet!r}"
    )


def test_malformed_entry_remedy_normalization_is_a_defined_deterministic_transform():
    """The normalization must be spelled out concretely (which characters are
    replaced, and with what) rather than left to an author's own judgment call
    each time — an undefined 'pick something reasonable' produces a different
    key for the same member name on a later retry."""
    bullet = _malformed_entry_bullet()
    assert re.search(r"\[A-Za-z0-9._-\]", bullet), (
        f"the `malformed-entry` remedy must cite the reader's own safe-grammar "
        f"character class it normalizes into: {bullet!r}"
    )
