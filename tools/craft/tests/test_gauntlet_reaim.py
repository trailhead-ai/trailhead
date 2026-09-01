"""The spec gauntlet re-aimed for the vertical-slice model.

Under the slice model a spec is no longer the last place a determination can be
made: slices are derived from its acceptance criteria, and a coordinator settles
cross-repo interfaces at slice time, not spec time. This file pins the resulting
prose contracts:

  1. The spec template names required interfaces without defining their shape,
     and codifies the deferral vocabulary the slice-model spec used as
     convention only — with an explicit owner slot on the two forms that are
     genuine deferrals, and no false claim of an owner on the form that
     records a decision already made.
  2. The planner's spec-writing checklist fills in Required Interfaces
     alongside the template's other sections, so a planner-written spec
     actually carries the section the template defines.
  3. The divergence prober exempts a named-but-unshaped required interface from
     its findings (a reporting exemption, not a construction one), states the
     jurisdiction transfer explicitly, still flags a commitment the project
     does not control both sides of, and asks its core question in terms of
     slice derivation rather than builder taste — with that derivation question
     wired to its own finding bucket, its own verdict rule, and a slot in the
     output shape.
  4. The consistency auditor enumerates Required Interfaces alongside
     Objectives, Acceptance Criteria, and Non-Goals, its Open Questions rule
     carries an owner-plus-revisit-condition exception, and its coverage
     matrix extends to Required Interfaces.
  5. The council's spec-review preamble names Required Interfaces among the
     sections its lenses fire on, and the Reliability bar that used to read as
     an execution complaint now names a coverage consequence instead — while
     the adr-review bar block, scheduled for removal by a sibling change, not
     this one, is left byte-identical.

Every assertion here is a literal-prose pin, matching the shape the rest of the
gauntlet contract suite uses: a named behavior, a literal fragment, and a
failure message saying why that fragment IS the behavior.
"""

from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
TEMPLATES_DIR = CRAFT / "templates"
AGENTS_DIR = CRAFT / "agents"
SHARED_COUNCIL = CRAFT / "skills" / "_shared" / "council.md"

SPEC_TEMPLATE = TEMPLATES_DIR / "spec.md"
PLANNER = AGENTS_DIR / "planner.md"
DIVERGENCE_PROBER = AGENTS_DIR / "divergence-prober.md"
CONSISTENCY_AUDITOR = AGENTS_DIR / "consistency-auditor.md"
BRAINSTORM_SKILL = CRAFT / "skills" / "brainstorm" / "SKILL.md"

# The three specs the interface-shape jurisdiction transfers to, once a
# required interface is named but not yet defined.
_INTERFACE_RECEIVING_SPECS = (
    "spec/declared-cross-repo-interfaces-and-their-conformance-tests",
    "spec/external-interface-inventory-and-the-interface-test-contract",
    "spec/the-coordinator-posture",
)

# Byte-for-byte capture of the adr-review Critical bar block as it reads at the
# time this file was written — from its own heading up to (not including) the
# `## Synthesis` heading that follows it. This block belongs to the gauntlet's
# adr mode, which a sibling change is sequenced to amputate; this pin exists
# ONLY to guard this change's blast radius against touching that block by
# accident, and is expected to be deleted along with the adr-review block
# itself when that amputation lands — it is not a standing invariant to work
# around once the block it guards is gone.
_ADR_REVIEW_BLOCK = "## Per-lens Critical bars — adr review\n\nUsed by the `gauntlet` skill's lens pass in adr mode. An `adr` record has exactly\nfour sections — Context, Decision, Consequences, Alternatives rejected\n(`templates/adr.md`) — and no Problem, Objectives, Acceptance Criteria, or UI\nDirection. The bars below fire on what a decision record can actually get wrong;\nthey do not cite sections it doesn't have.\n\nThe four lenses accept the Decision as framed and review within it — attacking\nwhether the Decision itself is the right one belongs to the `premise-attacker`\npass, not a lens.\n\n*Builder — adr review:*\n- The Decision has no implementable reading — nothing a build could conform to as stated\n- The Decision contradicts a declared project axiom or a prior, not-yet-superseded ADR\n- The Decision depends on a capability that does not exist yet and the record does not name that capability as a dependency the Decision relies on — the defect is the missing name, not the missing capability\n- Alternatives rejected omits an alternative that was clearly live, making the Decision look uncontested when it wasn't\n\n*Reliability — adr review:*\n- The Decision is framed as irreversible (immutable once `active`) but Consequences never names the supersession path for reversing course\n- Consequences omits a cost or constraint the Decision imposes that a later build will discover the hard way\n- Context doesn't establish why the Decision was necessary now — an unforced Decision invites relitigation later\n- Nothing in the record names a condition for when the Decision should be revisited\n\n*Security — adr review:*\n- The Decision introduces or shifts a trust boundary, authz model, or handling of sensitive data that Consequences never names\n- The Decision commits to storing, logging, or transmitting sensitive data without naming its classification or retention\n- The Decision assumes an existing security control still holds without Alternatives rejected having checked it\n\n*Advocate — adr review:*\n- The Decision changes a surface someone downstream will hit, but Consequences names no way they'd discover it happened\n- Consequences describes only system-internal effects with no outcome any downstream reader would notice\n\n"


def _section(text, heading, next_heading_prefix="\n## "):
    start = text.index(heading)
    end = text.index(next_heading_prefix, start + 1)
    return text[start:end]


def _normalize_ws(text):
    return " ".join(text.split())


# --- 1. spec template: Required Interfaces + deferral vocabulary ---


def test_spec_template_has_required_interfaces_section():
    text = SPEC_TEMPLATE.read_text()
    assert "## Required Interfaces" in text, (
        "templates/spec.md must gain a Required Interfaces section — under the slice "
        "model a spec names the boundaries it implies without settling their shape, "
        "which is decided at slice time instead"
    )


def test_required_interfaces_section_states_it_does_not_define_shape():
    text = SPEC_TEMPLATE.read_text()
    section = _section(text, "## Required Interfaces")
    assert "does not define" in section and "shape" in section, (
        "the Required Interfaces section must say explicitly that it names a boundary "
        "without defining its shape — pre-locking shape at spec time is the cost this "
        "section exists to avoid, so the section must disclaim it in its own text"
    )


def test_spec_template_codifies_deferral_vocabulary():
    text = SPEC_TEMPLATE.read_text()
    for form in ("Accepted risk:", "Settled:", "Deferred with revisit conditions:"):
        assert form in text, (
            f"templates/spec.md must codify {form!r} as template contract — the "
            "vertical-slice spec invented and used this form as convention only; "
            "the template is what makes it contract"
        )


def test_open_questions_deferral_forms_carry_an_owner_slot():
    text = SPEC_TEMPLATE.read_text()
    section = _section(text, "## Open Questions / Risks")
    lines = section.splitlines()
    accepted_risk_line = next(
        line for line in lines if "Accepted risk:" in line
    )
    deferred_line = next(
        line for line in lines if "Deferred with revisit conditions:" in line
    )
    assert "Owner:" in accepted_risk_line, (
        "the `Accepted risk:` form must carry an explicit `Owner:` slot — the "
        "consistency auditor's exception requires an item to name both an owner "
        "and a revisit condition, and this form had no owner slot for an author "
        "to fill in"
    )
    assert "Owner:" in deferred_line, (
        "the `Deferred with revisit conditions:` form must carry an explicit "
        "`Owner:` slot — the consistency auditor's exception requires an item to "
        "name both an owner and a revisit condition, and this form had no owner "
        "slot for an author to fill in"
    )


def test_open_questions_section_does_not_claim_settled_carries_an_owner():
    text = SPEC_TEMPLATE.read_text()
    section = _section(text, "## Open Questions / Risks")
    settled_line = next(
        line for line in section.splitlines() if line.strip().startswith("- `Settled:")
        or line.strip().startswith("`Settled:")
    )
    assert "Owner" not in settled_line, (
        "`Settled:` records a decision already made — it has no owner to name and "
        "must not carry an `Owner:` slot, unlike the two genuine deferral forms"
    )
    assert "is not a deferral" in section, (
        "the section must say explicitly that `Settled:` is not a deferral — it "
        "records a decision already made, so claiming it carries an owner and a "
        "revisit condition is the exact false positive the consistency auditor's "
        "both-required exception was built to avoid triggering on template-compliant "
        "prose"
    )


# --- 2. planner: Required Interfaces reaches the producer's checklist ---


def test_planner_fill_in_list_names_required_interfaces():
    text = PLANNER.read_text()
    start = text.index("Fill in:")
    end = text.index("\n", start)
    fill_in_line = text[start:end]
    assert "Required Interfaces" in fill_in_line, (
        "planner.md's 'Fill in:' checklist must name Required Interfaces — "
        "otherwise a planner-written spec never carries the section the "
        "template defines, and the section is inert on that path"
    )
    ac_pos = fill_in_line.index("Acceptance Criteria")
    ri_pos = fill_in_line.index("Required Interfaces")
    ng_pos = fill_in_line.index("Non-Goals")
    assert ac_pos < ri_pos < ng_pos, (
        "Required Interfaces must sit between Acceptance Criteria and Non-Goals "
        "in the checklist, matching the position it occupies in templates/spec.md"
    )


def test_brainstorm_canonical_sections_names_required_interfaces():
    text = BRAINSTORM_SKILL.read_text()
    start = text.index("carries these canonical")
    end = text.index("Then open the", start)
    normalized = _normalize_ws(text[start:end])
    assert "**Required Interfaces**" in normalized, (
        "brainstorm/SKILL.md's canonical-sections paragraph must name "
        "**Required Interfaces** — a spec section a producer's enumeration "
        "doesn't name is a section that producer never fills in, and "
        "brainstorm is the other producer that enumerates the template's "
        "sections alongside planner.md"
    )
    ac_pos = normalized.index("**Acceptance Criteria**")
    ri_pos = normalized.index("**Required Interfaces**")
    ng_pos = normalized.index("**Non-Goals**")
    assert ac_pos < ri_pos < ng_pos, (
        "Required Interfaces must sit between Acceptance Criteria and Non-Goals "
        "in the enumeration, matching the position it occupies in templates/spec.md"
    )


# --- 3. divergence prober: re-aimed at slice derivation, interface exemption ---


def test_divergence_prober_exempts_named_but_unshaped_interface():
    text = DIVERGENCE_PROBER.read_text()
    section = _section(text, "## What you ignore")
    assert "not a divergence finding" in section, (
        "divergence-prober.md's 'What you ignore' bullet must state that a "
        "named-but-unshaped required interface is not a divergence finding — "
        "interface shape is settled at slice time, and pre-locking it at spec "
        "time is a cost this pass must not impose"
    )


def test_divergence_prober_interface_exemption_governs_reporting_not_construction():
    text = DIVERGENCE_PROBER.read_text()
    section = _section(text, "## What you ignore")
    assert "not what you construct" in section or "not what Build A and Build B construct" in section, (
        "the interface-shape exemption must say it governs what gets reported, "
        "not the construction axes in 'Push them apart deliberately' (boundaries, "
        "the contract's shape) — otherwise the pass could read it as telling it "
        "not to push the interface axes apart at all, gutting the axis that "
        "generates its own evidence"
    )


def test_divergence_prober_names_the_jurisdiction_transfer():
    text = DIVERGENCE_PROBER.read_text()
    section = _section(text, "## What you ignore")
    assert "jurisdiction transfer" in section, (
        "divergence-prober.md must name the interface-shape exemption as a deliberate "
        "jurisdiction transfer, not an oversight, so a future reader who finds a "
        "divergence probe that no longer pins interfaces can see it was intentional"
    )
    assert any(spec in section for spec in _INTERFACE_RECEIVING_SPECS), (
        "divergence-prober.md must name at least one of the specs the interface-shape "
        "jurisdiction transfers to, by record name, so the transfer is traceable to "
        "where the check now lives"
    )


def test_divergence_prober_still_flags_a_commitment_the_project_does_not_control():
    text = DIVERGENCE_PROBER.read_text()
    section = _section(text, "## What you ignore")
    assert "does not control" in section, (
        "divergence-prober.md's exemption bullet must still flag a commitment "
        "whose other side the project does not control (a published external "
        "contract, a production data shape) — deferral requires being able to "
        "change both halves later, and this residual is what the pass keeps once "
        "interface shape itself transfers away"
    )


def test_divergence_prober_frames_the_question_as_slice_derivation():
    text = DIVERGENCE_PROBER.read_text()
    assert "derive a different set of slices" in text, (
        "divergence-prober.md must re-aim its core question: not whether two builders "
        "write different code, but whether two readers derive a different set of "
        "slices, or stop at a different point — that is a coverage finding, not an "
        "execution one"
    )


def test_divergence_prober_derivation_forking_is_a_named_finding_bucket():
    text = DIVERGENCE_PROBER.read_text()
    section = _section(text, "## Judging a divergence", "\n## Every finding")
    assert "Derivation-forking" in section, (
        "the derivation question ('would two readers derive a different set of "
        "slices') must land in its own named finding bucket alongside "
        "Boundary-crossing and Free — otherwise a pass that trips it has nowhere "
        "to file the finding and the buckets keep keying only on the older "
        "cross-boundary question"
    )
    derivation_bucket = _section(section, "**Derivation-forking**", "\n- **")
    assert "load-bearing" in derivation_bucket.lower(), (
        "the Derivation-forking bucket must say a divergence that trips it is "
        "load-bearing on its own, whether or not it also crosses a boundary — "
        "otherwise the primary question stated earlier in this section still "
        "has no consequence for the verdict"
    )


def test_divergence_prober_verdict_ties_underdetermined_to_derivation_forking():
    text = DIVERGENCE_PROBER.read_text()
    verdict_line = next(
        line for line in text.splitlines() if line.strip().startswith("1. **Verdict**")
    )
    assert "derivation-forking" in verdict_line.lower(), (
        "the Verdict line's definition of `underdetermined` must name a "
        "derivation-forking divergence as what drives it there — a spec whose "
        "criteria admit two conformant builds that derive different slice sets "
        "must actually reach the `underdetermined` verdict, not just a bucket "
        "with no route to the output"
    )


def test_divergence_prober_output_shape_has_a_slot_for_derivation_findings():
    text = DIVERGENCE_PROBER.read_text()
    findings_item = next(
        line for line in text.splitlines()
        if line.strip().startswith("3. **Load-bearing divergences**")
    )
    assert "derivation-forking" in findings_item.lower(), (
        "the output shape's findings slot (item 3, Load-bearing divergences) "
        "must itself name the derivation-forking bucket, so a pass that finds "
        "one has a defined place in the report to put it — not just a category "
        "mentioned in the Verdict line with no home in what gets returned"
    )


def test_divergence_prober_severely_underdetermined_bar_reads_against_slice_model():
    text = DIVERGENCE_PROBER.read_text()
    assert "a plan cannot be written from this spec" not in text, (
        "the severely-underdetermined verdict bar must no longer read as 'a plan "
        "cannot be written from this spec' — that phrasing has two referents now "
        "that a plan roots at one slice, not the whole feature"
    )
    assert "roots at one slice" in text or "roots at a" in text, (
        "the severely-underdetermined verdict bar must be restated against the slice "
        "model, naming that a plan roots at one slice rather than the whole spec"
    )


# --- 4. consistency auditor: enumeration, Open Questions exception, coverage ---


def test_consistency_auditor_enumerates_required_interfaces():
    text = CONSISTENCY_AUDITOR.read_text()
    enumerate_line = next(
        line for line in text.splitlines() if line.strip().startswith("Enumerate the spec's")
    )
    assert "Required Interfaces" in enumerate_line, (
        "the cross-matrix step's enumeration line must name Required Interfaces "
        "alongside Objectives, Acceptance Criteria, and Non-Goals — this pass is "
        "explicitly told a missed cell in the matrix is a defect in its own work, "
        "so the Required Interfaces matrix row it builds later needs an "
        "enumeration source here"
    )


def test_consistency_auditor_open_questions_exception():
    text = CONSISTENCY_AUDITOR.read_text()
    assert "owner" in text and "revisit condition" in text, (
        "consistency-auditor.md's Open Questions rule must exempt an item carrying "
        "both an owner and a revisit condition — under the slice loop that is a named, "
        "gated deferral discharged by assumption-prover, not a requirement decided "
        "silently by whoever builds it"
    )
    assert "will get decided" not in text, (
        "the claim that a parked decision 'will get decided — silently, by whoever "
        "builds it' no longer holds unconditionally under the slice loop and must be "
        "removed or qualified by the owner-plus-revisit-condition exception"
    )


def test_consistency_auditor_output_shape_coverage_matrix_extends_to_required_interfaces():
    text = CONSISTENCY_AUDITOR.read_text()
    output_shape = _section(text, "## Output shape", "\n\nQuote the spec verbatim")
    coverage_matrix_item = next(
        line for line in output_shape.splitlines() if line.strip().startswith("2. **Coverage matrix**")
    )
    assert "Required Interfaces" in coverage_matrix_item, (
        "the Coverage matrix item in the Output shape section must itself name "
        "Required Interfaces — a whole-file substring check would still pass if "
        "the second table describing that coverage were deleted, as long as the "
        "words appeared somewhere else in the file"
    )


# --- 5. council: preamble, spec-review reworded, adr-review untouched ---


def test_council_spec_review_preamble_names_required_interfaces():
    text = SHARED_COUNCIL.read_text()
    preamble = _section(text, "## Per-lens Critical bars — spec review", "\n\nThe four lenses")
    assert "Required Interfaces" in preamble, (
        "the spec-review preamble must name Required Interfaces among the "
        "sections the lenses fire on, alongside objectives, acceptance criteria, "
        "non-goals, and constraints — otherwise a lens firing on the new section "
        "has no textual license to do so"
    )


def test_council_spec_review_bar_no_longer_reads_as_execution_language():
    text = SHARED_COUNCIL.read_text()
    spec_review_block = _section(
        text, "## Per-lens Critical bars — spec review", "\n## Per-lens Critical bars — adr review"
    )
    assert "so the build will invent one" not in spec_review_block, (
        "the spec-review Reliability bar must not read 'so the build will invent "
        "one' — that is execution language for a coverage concern; purpose two is "
        "whether the spec leaves the state undetermined, not what a builder will do "
        "about it"
    )


def test_council_spec_review_unspecified_state_bar_names_a_coverage_consequence():
    text = SHARED_COUNCIL.read_text()
    spec_review_block = _section(
        text, "## Per-lens Critical bars — spec review", "\n## Per-lens Critical bars — adr review"
    )
    unspecified_state_bar = next(
        line for line in spec_review_block.splitlines()
        if "certainly reach" in line
    )
    assert "no acceptance criterion" in unspecified_state_bar, (
        "the unspecified-state Reliability bar's trailing clause must name a "
        "coverage consequence — that no acceptance criterion covers the state — "
        "not merely restate that the state's behavior is undefined; a bar that "
        "restates its own premise doesn't say why the gap clears Critical"
    )


def test_council_adr_review_block_is_untouched():
    text = SHARED_COUNCIL.read_text()
    start = text.index("## Per-lens Critical bars — adr review")
    end = text.index("## Synthesis")
    adr_review_block = text[start:end]
    assert adr_review_block == _ADR_REVIEW_BLOCK, (
        "the adr-review Critical bar block must stay byte-identical — a sibling "
        "session is sequenced to amputate the gauntlet's adr mode after this change, "
        "and re-aiming prose about to be deleted is wasted work that risks preserving "
        "bars that should not survive"
    )
