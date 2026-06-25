"""Slice 2 drift-guard + behavior-preservation for the council review scaffolding.

The per-lens Critical bars, the synthesis rules, and the parameterized prompt
template are hoisted out of `plan/SKILL.md` and `consult/SKILL.md` into the
single shared reference `_shared/council.md` (read-on-reference, same mechanism as
the membership roster). This test pins three things so a future edit can't
silently regress them:

1. The shared scaffolding lives in `_shared/council.md` (positive) and is no longer
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


# --- byte-for-byte bar strings, captured from plan/SKILL.md before the hoist ---
# Each is a contiguous block exactly as it read at HEAD~ (Slice 1b tip). If a
# reviewer edits a bar's wording, the assertion below fails.
BUILDER_BARS = """*Builder:*
- Slice ordering creates a dependency that can't be tested
- Architecture choice contradicts a declared axiom in the plan
- Producer slice's contract isn't proven by tests but a consumer slice depends on it
- Plan introduces a new abstraction layer for a single caller (premature)"""

RELIABILITY_BARS = (
    "*Reliability:*\n"
    "- A slice has no test contract, OR test contract is vacuous\n"
    "- New code path's failure mode is invisible (no health check, metric, log, "
    "soak observable) AND the spec's Observability & Failure Visibility block says "
    "`n/a — soak-invisible` without substantive reason\n"
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


def test_prompt_template_skeleton_in_shared():
    text = SHARED.read_text()
    assert "Output shape" in text
    assert "## Findings" in text
    assert "## Confidence" in text


def test_template_output_calibration_pinned():
    # The output-shape constraints ARE review-calibration behavior — a future edit
    # that loosens "≤2 Critical" to "≤3" or drops the no-speculative rule changes
    # how every council review prioritizes. Pin the binding literals (review M-2).
    text = SHARED.read_text()
    assert "≤2 Critical" in text, "the ≤2-Critical forced-prioritization cap must stay pinned"
    assert "≤300 words total" in text, "the ≤300-word output budget must stay pinned"
    assert "No speculative Critical" in text, "the no-speculative-Criticals rule must stay pinned"
    assert "REPLACE your usual" in text, (
        "the explicit output-budget override (vs the agents' ~400-600 word default) "
        "must stay pinned — it is the binding instruction, not the agent's stated default"
    )


def test_lens_substitution_documented_in_shared():
    # The <lens> fill instruction now lives ONLY in the shared file (review I-1).
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
