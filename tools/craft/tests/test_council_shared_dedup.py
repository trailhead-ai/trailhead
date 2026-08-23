"""Drift-guard + behavior-preservation for the council review scaffolding.

The per-lens Critical bars, the synthesis rules, and the parameterized prompt
template are hoisted out of `plan/SKILL.md` and `consult/SKILL.md` into the
single shared reference `_shared/council.md` (read-on-reference, same mechanism as
the membership roster). This test pins three things so a future edit can't
silently regress them:

1. The shared scaffolding lives in `_shared/council.md` (positive) and is not
   duplicated in the two skill bodies (negative).
2. The four per-lens Critical bars appear in `_shared/council.md` BYTE-FOR-BYTE as
   they read before the hoist, and the four disposition option names survive
   byte-for-byte in `plan/SKILL.md` (plan's retained delta — the disposition gate
   stays with planning). Together this proves the compression touched only
   connective prose, never the bars or the disposition set (the spec's
   no-behavior-change non-goal).
3. Each skill keeps only its delta: `plan` retains the disposition gate +
   persistence schema + hard-floor `**build**`; `consult` carries no persistence.

The merged shared file must also be net-leaner than the sum of the two original
council blocks, so a future edit can't re-bloat it back to pure relocation.
"""

from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "skills"
SHARED = SKILLS_DIR / "_shared" / "council.md"
PLAN = SKILLS_DIR / "plan" / "SKILL.md"
CONSULT = SKILLS_DIR / "consult" / "SKILL.md"
GAUNTLET = SKILLS_DIR / "gauntlet" / "SKILL.md"


# --- byte-for-byte bar strings, captured from plan/SKILL.md before the hoist ---
# Each is a contiguous block exactly as it read at HEAD~. If a
# reviewer edits a bar's wording, the assertion below fails.
BUILDER_BARS = """*Builder:*
- Slice ordering creates a dependency that can't be tested
- Architecture choice contradicts a declared axiom in the plan
- Producer slice's contract isn't proven by tests but a consumer slice depends on it
- Plan introduces a new abstraction layer for a single caller (premature)"""

RELIABILITY_BARS = (
    "*Reliability:*\n"
    "- A slice has no test contract, OR test contract is vacuous\n"
    "- New code path's failure mode is invisible — no health check, metric, log, "
    "or other signal — with no substantive reason\n"
    "- Plan removes existing test coverage without replacement\n"
    "- A slice does irreversible work without dry-run / preview / staged rollout\n"
    "- A destructive migration or backfill runs without a gated, replayable console "
    "(the ORM / query layer or migration/backfill console) instead of an ad-hoc one-shot"
)

SECURITY_BARS = """*Security:*
- New authenticated endpoint without named authz check
- New user-supplied input hitting the ORM / query layer without named sanitization
- New log / event / metric containing PII or a user identifier without explicit redaction
- Secret in source / config without using the existing secret-management pattern
- Admin-only behavior exposed to non-admin paths"""

ADVOCATE_BARS = (
    "*Advocate* — dual rule, apply the higher bar for internal admin UX:\n"
    "\n"
    "End-user-facing (the mobile client, the public web surface):\n"
    "- Stuck state with no escape\n"
    "- Primary flow 3+ clicks where 1 is industry-standard\n"
    "- Developer-jargon error messages\n"
    "- Missing empty / error / loading states\n"
    "- A change tested on only one platform but breaks an existing flow on another\n"
    "\n"
    "Internal admin UI — Critical ONLY when at least one holds:\n"
    "- (a) No workaround exists\n"
    "- (b) High-frequency daily workflow with compounding friction "
    "(e.g. 1-click → 10-click for a 50×-daily task)\n"
    "- (c) Feedback ambiguity that propagates bad decisions downstream\n"
    "\n"
    "Otherwise internal-admin findings are Important at most. Admin users tolerate "
    "friction; bikeshedding internal UX is high-cost."
)

ALL_BARS = [BUILDER_BARS, RELIABILITY_BARS, SECURITY_BARS, ADVOCATE_BARS]

DISPOSITION_NAMES = ["resolved", "bounced-back-to-spec", "accepted-as-risk", "disputed"]


# --- positive: shared scaffolding now lives in _shared/council.md ---


def test_bars_byte_for_byte_in_shared():
    text = SHARED.read_text()
    for bars in ALL_BARS:
        assert bars in text, (
            "per-lens Critical bar block drifted or is missing from "
            "_shared/council.md — bars must move BYTE-FOR-BYTE (no-behavior-change)"
        )


def test_disposition_names_in_shared_or_plan():
    # The disposition option set is part of plan's retained delta (gate lives in
    # plan), so the four names must survive byte-for-byte somewhere in plan.
    text = PLAN.read_text()
    for name in DISPOSITION_NAMES:
        assert f"`{name}" in text, (
            f"disposition option name `{name}` missing from plan/SKILL.md — "
            "the four disposition names are behavior, not prose"
        )


def test_synthesis_rule_in_shared():
    text = SHARED.read_text()
    assert "De-duplicate by issue, not by member" in text
    assert "Auto-downgrade speculative" in text


# --- how a finding shown to a human must read ---
#
# The four elements of the per-finding presentation contract. A report that drops
# any one of them is the failure this contract exists to prevent: findings stated
# in internal shorthand before the plain problem, with the remedy buried mid-
# paragraph. The contract is written once, in the shared file, so every caller
# that reads it inherits the same shape.
PRESENTATION_ELEMENTS = (
    # 1. plain-language headline, and it comes first
    "what goes wrong, and for whom",
    "before any mechanism",
    # 2. mechanism paragraph names the concrete worst case
    "concrete worst case",
    # 3. the remedy gets its own line, under a label callers can rely on
    "on its own line",
    "`Fix:`",
    # 4. internal shorthand is last, and only when it earns its place
    "only after the plain statement",
)

# The subset distinctive enough to mean "this file re-states the contract" rather
# than "this file happens to use ordinary English". Generic phrases make bad
# canaries: an unrelated future sentence would trip them with a misleading message.
PRESENTATION_CANARIES = (
    "what goes wrong, and for whom",
    "concrete worst case",
    "only after the plain statement",
)


def synthesis_section(text):
    """The Synthesis section body — the contract has to live inside it.

    Item 3 of the synthesis list ends "in the shape below", so a contract block
    hoisted above Synthesis would leave that pointer aimed at nothing.
    """
    start = text.index("## Synthesis")
    rest = text.find("\n## ", start + 1)
    return text[start:] if rest == -1 else text[start:rest]


def test_presentation_contract_in_shared_synthesis_section():
    section = synthesis_section(SHARED.read_text())
    for element in PRESENTATION_ELEMENTS:
        assert element in section, (
            f"_shared/council.md's Synthesis section must state {element!r} — the "
            "per-finding presentation contract for findings shown to a human lives "
            "here, in one copy, below the list item that points at it"
        )


def test_presentation_contract_not_duplicated_into_callers():
    for skill in (PLAN, CONSULT, GAUNTLET):
        text = skill.read_text()
        for element in PRESENTATION_CANARIES:
            assert element not in text, (
                f"{skill.parent.name}/SKILL.md copies the presentation contract "
                f"({element!r}) instead of referencing _shared/council.md — two copies "
                "are how the wording drifts apart"
            )


def test_human_facing_callers_bind_to_the_finding_shape():
    """Every skill that presents a consolidated list must point at the shape.

    Restating the "present the consolidated list" instruction without the pointer
    is how a caller silently stops inheriting the contract — the report reads fine
    to its author and reverts to shorthand-first prose for everyone else.
    """
    for skill in (PLAN, CONSULT, GAUNTLET):
        text = skill.read_text()
        assert "How a finding reads" in text, (
            f"{skill.parent.name}/SKILL.md presents a consolidated list to a human but "
            "never points at 'How a finding reads' in _shared/council.md, so it does not "
            "inherit the per-finding shape"
        )


def test_prompt_template_skeleton_in_shared():
    text = SHARED.read_text()
    assert "Output shape" in text
    assert "## Findings" in text
    assert "## Confidence" in text


def test_template_output_calibration_pinned():
    # The output-shape constraints ARE review-calibration behavior — a future edit
    # that loosens "≤2 Critical" to "≤3" or drops the no-speculative rule changes
    # how every council review prioritizes. Pin the binding literals.
    text = SHARED.read_text()
    assert "≤2 Critical" in text, "the ≤2-Critical forced-prioritization cap must stay pinned"
    assert "≤300 words total" in text, "the ≤300-word output budget must stay pinned"
    assert "No speculative Critical" in text, "the no-speculative-Criticals rule must stay pinned"
    assert "REPLACE your usual" in text, (
        "the explicit output-budget override (vs the agents' ~400-600 word default) "
        "must stay pinned — it is the binding instruction, not the agent's stated default"
    )


def test_lens_substitution_documented_in_shared():
    # The <lens> fill instruction now lives ONLY in the shared file.
    # Pin that the shared file both names the token and enumerates its four values,
    # so the dispatcher always knows what to substitute.
    text = SHARED.read_text()
    assert "<lens>" in text
    for lens in ("Builder", "Reliability", "Security", "Advocate"):
        assert lens in text, f"shared file must enumerate the {lens} lens value for <lens>"


# --- each skill keeps only its delta ---


def test_plan_retains_disposition_gate_and_persistence():
    text = PLAN.read_text()
    assert "## Council Review" in text, "plan must keep the persistence schema heading"
    assert "*Disposition:*" in text, "plan must keep the disposition gate"
    assert "Hard-floor gate" in text or "hard-floor" in text.lower()
    assert "**build**" in text, "plan must keep the hard-floor build handoff prompt"


def test_plan_keeps_spec_path_substitution():
    text = PLAN.read_text()
    assert "<spec-path>" in text, (
        "plan must supply its <spec-path> context-pointer substitution above the template"
    )


def test_consult_keeps_role_label_note_and_context_pointers():
    text = CONSULT.read_text()
    assert "may strip your role label" in text, (
        "consult must keep its synthesizer-may-strip-role-label note"
    )
    assert "<context-pointers>" in text, "consult must supply its <context-pointers> substitution"


def test_consult_has_no_persistence_schema():
    text = CONSULT.read_text()
    assert "## Council Review" not in text, (
        "consult has no plan file to persist into — no persistence schema allowed"
    )
    assert "*Disposition:*" not in text, "consult has no disposition gate"


# --- both skills still reference the shared file (read-on-reference) ---


def test_skills_reference_shared_council():
    for skill in (PLAN, CONSULT):
        assert "_shared/council.md" in skill.read_text(), (
            f"{skill.parent.name}/SKILL.md must reference _shared/council.md"
        )


# --- the narrative synthesis: prose leads, the consolidated list supports ---
#
# A consolidated list makes the reader reconstruct how the findings relate, which of
# them matter, and why, by reading across rows. The narrative is where that
# reconstruction has already been done for them. Like the per-finding shape above,
# the contract is written once in the shared file so every human-facing caller
# inherits the same three movements.
NARRATIVE_ELEMENTS = (
    # the three movements, in name
    "What the passes found",
    "Whether it holds, and where it came from",
    "What to do about it",
    # the movement-2 carve-out: the interpretive per-pass read is the point, and it
    # is what a blanket "no per-pass roll-up" ban would wrongly forbid
    "interpretive per-pass account, and it is wanted",
    "mechanical roll-up",
    # movement 1 is written in the vocabulary of the thing under review
    "in the reviewed record's own terms",
    # the demotion itself — the list is supporting detail, not the explanation
    "supporting detail",
)

NARRATIVE_CANARIES = (
    "a count wearing a sentence's clothes",
    "Whether it holds, and where it came from",
    "artifact of the lens rather than a defect",
)


def test_narrative_synthesis_contract_in_shared_synthesis_section():
    section = synthesis_section(SHARED.read_text())
    for element in NARRATIVE_ELEMENTS:
        assert element in section, (
            f"_shared/council.md's Synthesis section must state {element!r} — the "
            "narrative-synthesis contract for what a human reads first lives here, "
            "in one copy, alongside the per-finding shape"
        )


def test_narrative_synthesis_not_duplicated_into_callers():
    for skill in (PLAN, CONSULT, GAUNTLET):
        text = skill.read_text()
        for element in NARRATIVE_CANARIES:
            assert element not in text, (
                f"{skill.parent.name}/SKILL.md copies the narrative-synthesis contract "
                f"({element!r}) instead of referencing _shared/council.md — two copies "
                "are how the wording drifts apart"
            )


def test_human_facing_callers_bind_to_the_synthesis_shape():
    """Every skill that presents to a human must point at the narrative shape.

    Without the pointer a caller silently reverts to leading with the list, which
    is the shape this contract exists to replace.
    """
    for skill in (PLAN, CONSULT, GAUNTLET):
        assert "How the synthesis reads" in skill.read_text(), (
            f"{skill.parent.name}/SKILL.md presents findings to a human but never points "
            "at 'How the synthesis reads' in _shared/council.md, so it does not inherit "
            "the narrative shape"
        )


def test_plan_persisted_schema_is_carved_out():
    """plan's `## Council Review` is a file record, not in-session reading.

    Its one-line-per-finding schema is plan's own. Without an explicit carve-out the
    narrative contract reads as governing it too, and a future edit expands a
    persisted record into three paragraphs per council run.
    """
    section = synthesis_section(SHARED.read_text())
    assert "neither the narrative shape nor this contract governs it" in section, (
        "_shared/council.md's Synthesis section must carve plan's persisted "
        "`## Council Review` schema out of BOTH the narrative shape and the "
        "per-finding shape — a carve-out naming only one of them leaves the other "
        "reading as binding on a file record no human reads in session"
    )
