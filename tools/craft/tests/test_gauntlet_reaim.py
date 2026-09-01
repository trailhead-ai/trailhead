"""The spec gauntlet re-aimed for the vertical-slice model.

Under the slice model a spec is no longer the last place a determination can be
made: slices are derived from its acceptance criteria, and a coordinator settles
cross-repo interfaces at slice time, not spec time. This file pins the five
resulting prose contracts:

  1. The spec template names required interfaces without defining their shape,
     and codifies the deferral vocabulary the slice-model spec used as
     convention only.
  2. The divergence prober exempts a named-but-unshaped required interface from
     its findings, states the jurisdiction transfer explicitly, still flags a
     commitment the project does not control both sides of, and asks its core
     question in terms of slice derivation rather than builder taste.
  3. The consistency auditor's Open Questions rule carries an
     owner-plus-revisit-condition exception, and its coverage matrix extends to
     Required Interfaces.
  4. The council's spec-review Reliability bar no longer reads as an execution
     complaint ("so the build will invent one"), while the adr-review bar block
     — scheduled for removal by a sibling change, not this one — is left
     byte-identical.

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
DIVERGENCE_PROBER = AGENTS_DIR / "divergence-prober.md"
CONSISTENCY_AUDITOR = AGENTS_DIR / "consistency-auditor.md"

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
    start = text.index("## Required Interfaces")
    end = text.index("\n## ", start + 1)
    section = text[start:end]
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


# --- 2. divergence prober: re-aimed at slice derivation, interface exemption ---


def test_divergence_prober_exempts_named_but_unshaped_interface():
    text = DIVERGENCE_PROBER.read_text()
    assert "not a divergence finding" in text, (
        "divergence-prober.md must state that a named-but-unshaped required interface "
        "is not a divergence finding — interface shape is settled at slice time, and "
        "pre-locking it at spec time is a cost this pass must not impose"
    )


def test_divergence_prober_names_the_jurisdiction_transfer():
    text = DIVERGENCE_PROBER.read_text()
    assert "jurisdiction transfer" in text, (
        "divergence-prober.md must name the interface-shape exemption as a deliberate "
        "jurisdiction transfer, not an oversight, so a future reader who finds a "
        "divergence probe that no longer pins interfaces can see it was intentional"
    )
    assert any(spec in text for spec in _INTERFACE_RECEIVING_SPECS), (
        "divergence-prober.md must name at least one of the specs the interface-shape "
        "jurisdiction transfers to, by record name, so the transfer is traceable to "
        "where the check now lives"
    )


def test_divergence_prober_still_flags_a_commitment_the_project_does_not_control():
    text = DIVERGENCE_PROBER.read_text()
    assert "does not control" in text, (
        "divergence-prober.md must still flag a commitment whose other side the "
        "project does not control (a published external contract, a production data "
        "shape) — deferral requires being able to change both halves later, and this "
        "residual is what the pass keeps once interface shape itself transfers away"
    )


def test_divergence_prober_frames_the_question_as_slice_derivation():
    text = DIVERGENCE_PROBER.read_text()
    assert "derive a different set of slices" in text, (
        "divergence-prober.md must re-aim its core question: not whether two builders "
        "write different code, but whether two readers derive a different set of "
        "slices, or stop at a different point — that is a coverage finding, not an "
        "execution one"
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


# --- 3. consistency auditor: Open Questions exception, interface coverage ---


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


def test_consistency_auditor_coverage_matrix_extends_to_required_interfaces():
    text = CONSISTENCY_AUDITOR.read_text()
    assert "Required Interfaces" in text, (
        "consistency-auditor.md's coverage matrix must extend to Required Interfaces "
        "— every named interface needs at least one acceptance criterion covering it, "
        "and this pass is the coverage matrix's owner"
    )


# --- 4. council: spec-review reworded, adr-review untouched ---


def test_council_spec_review_bar_no_longer_reads_as_execution_language():
    text = SHARED_COUNCIL.read_text()
    start = text.index("## Per-lens Critical bars — spec review")
    end = text.index("## Per-lens Critical bars — adr review")
    spec_review_block = text[start:end]
    assert "so the build will invent one" not in spec_review_block, (
        "the spec-review Reliability bar must not read 'so the build will invent "
        "one' — that is execution language for a coverage concern; purpose two is "
        "whether the spec leaves the state undetermined, not what a builder will do "
        "about it"
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
