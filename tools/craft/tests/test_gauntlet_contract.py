"""The spec gauntlet — adversarial spec review — and the invariant that makes it mandatory.

The gauntlet is the review a spec passes before it freezes: eight parallel passes
(fact verification, premise attack, the four council lenses, an internal-consistency
audit, a plan-divergence probe), adjudicated in the main session, with every Critical
dispositioned by the user.

The user-facing decision was "mandatory, no skip flag" — but a checklist item saying
"don't skip this" is honored only as well as the next agent feels like honoring it.
So the mandate is enforced **structurally** instead, by ownership of a state
transition:

    The `draft` -> `ready` edge on a spec record belongs to the gauntlet, and to
    nothing else in craft.

Brainstorm writes the spec at `draft` and stops. The `planner` agent (which covers
the whole brainstorm -> spec -> plan arc in one isolated dispatch, and therefore
*used* to flip the spec to `ready` itself) leaves it at `draft`. Planning refuses to
slice a `draft` spec. Freezing a spec without review therefore requires deleting the
gauntlet's flip, not merely forgetting a step.

`test_only_gauntlet_freezes_a_spec` is the test that pins that. If it fails, the
gauntlet is optional again, whatever the prose says.
"""

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SKILLS_DIR = CRAFT / "skills"
AGENTS_DIR = CRAFT / "agents"

GAUNTLET = SKILLS_DIR / "gauntlet" / "SKILL.md"
BRAINSTORM = SKILLS_DIR / "brainstorm" / "SKILL.md"
PLAN = SKILLS_DIR / "plan" / "SKILL.md"
SHARED_COUNCIL = SKILLS_DIR / "_shared" / "council.md"
PLANNER = AGENTS_DIR / "planner.md"

# The lore CLI invocation that freezes a spec. Pinned as a literal because the
# whole mandate rests on exactly one file being allowed to contain it.
SPEC_FREEZE = "<spec-id> --status ready"

# The passes the gauntlet dispatches. Kept explicit (not prose-parsed) to avoid
# false positives on ordinary words, mirroring _EXECUTE_DISPATCHED_AGENTS in
# test_craft_skills_registrable.py.
_GAUNTLET_DISPATCHED_AGENTS: list[str] = [
    "premise-attacker",
    "consistency-auditor",
    "divergence-prober",
    "builder",
    "breaker",
    "attacker",
    "advocate",
]

# The disposition vocabulary. `reframed` is the gauntlet's delta over planning's set:
# a spec can fail review by being the wrong spec, an outcome a plan review has no
# analogue for. Dropping it would silently remove the premise pass's only landing
# place — its characteristic (and highest-value) outcome.
_DISPOSITION_NAMES: list[str] = [
    "resolved",
    "reframed",
    "accepted-as-risk",
    "disputed",
]


def test_gauntlet_skill_ships():
    assert GAUNTLET.exists(), f"Expected gauntlet/SKILL.md in {SKILLS_DIR}"


# --- the mandate: exactly one file may freeze a spec ---


def test_only_gauntlet_freezes_a_spec():
    """The `draft` -> `ready` spec flip appears in gauntlet/SKILL.md and NOWHERE else.

    This is the structural form of "mandatory, no skip flag". Any other skill or
    agent carrying the freeze command is a bypass: it can hand planning a spec that
    was never adversarially reviewed.
    """
    assert SPEC_FREEZE in GAUNTLET.read_text(), (
        "gauntlet/SKILL.md must carry the spec-freeze command "
        f"(`lore record update {SPEC_FREEZE}`) — it owns the draft -> ready edge"
    )

    others = [p for p in [*SKILLS_DIR.glob("*/SKILL.md"), *AGENTS_DIR.glob("*.md")] if p != GAUNTLET]
    offenders = [p for p in others if SPEC_FREEZE in p.read_text()]
    assert not offenders, (
        "these craft files can freeze a spec, bypassing the gauntlet: "
        f"{[str(p.relative_to(CRAFT)) for p in offenders]}. The draft -> ready edge "
        "belongs to the gauntlet alone — if a spec can reach `ready` without it, the "
        "review is optional in practice no matter what the prose claims."
    )


def test_brainstorm_hands_off_to_gauntlet_and_does_not_freeze():
    text = BRAINSTORM.read_text()
    assert "/craft:gauntlet" in text, (
        "brainstorm's exit gate must hand off to /craft:gauntlet — it is the next step "
        "after the spec is written, and brainstorm no longer freezes the spec itself"
    )
    assert "Do not flip the spec to `ready` yourself" in text, (
        "brainstorm must explicitly disclaim the ready-flip; without the disclaimer an "
        "agent mid-brainstorm will helpfully freeze the spec on its own"
    )


def test_planner_agent_does_not_freeze_the_spec():
    """The planner agent runs the whole brainstorm -> plan arc in one isolated dispatch.

    It cannot run the gauntlet: it has no `Agent` tool to dispatch the eight passes
    (its `tools:` line is pinned in test_agents_registrable.py), and there is no user
    in its context to disposition Criticals. So it must leave the spec at `draft` and
    say so — it must not quietly freeze it.
    """
    text = PLANNER.read_text()
    assert "leave the spec at `status: draft`" in text, (
        "planner must leave the spec at draft — it has no way to run the gauntlet"
    )
    assert "not yet gauntleted" in text, (
        "planner must flag the un-reviewed spec in its returned summary, or the caller "
        "will treat a provisional plan as a reviewed one"
    )


def test_planning_refuses_to_slice_a_draft_spec():
    text = PLAN.read_text()
    assert "A `draft` spec is not plannable" in text, (
        "plan/SKILL.md must refuse a draft spec and route to the gauntlet — otherwise "
        "planning is a second path around the review"
    )


# --- dispatch integrity: no pass may dead-end ---


@pytest.mark.parametrize("agent", _GAUNTLET_DISPATCHED_AGENTS)
def test_gauntlet_dispatched_agents_resolve(agent: str):
    """Every agent the gauntlet dispatches is named in the skill AND installed.

    A rename that drops one of the eight passes would otherwise silently shrink the
    review while the skill still claims eight.
    """
    assert agent in GAUNTLET.read_text(), (
        f"gauntlet/SKILL.md does not dispatch {agent!r} — restore the dispatch, or "
        "update _GAUNTLET_DISPATCHED_AGENTS if the pass was intentionally removed"
    )
    agent_file = AGENTS_DIR / f"{agent}.md"
    assert agent_file.exists(), (
        f"gauntlet/SKILL.md dispatches {agent!r} but {agent_file} does not exist — "
        "a dispatch must not dead-end"
    )


def test_gauntlet_dispatches_fact_verification():
    """The fact pass rides the generic Explore agent, not a craft-owned one."""
    assert "Explore" in GAUNTLET.read_text(), (
        "gauntlet/SKILL.md must dispatch Explore for the fact-verification pass"
    )


def test_fact_pass_verifies_sibling_specs_not_just_code():
    """Calibration lock, from the pilot that established the protocol.

    The single highest-severity finding of that run came from a *sibling spec's*
    dependency on a capability the spec under review proposed to delete — invisible
    to a code-only sweep, which would have returned clean. If this instruction is
    ever dropped, the fact pass silently loses its highest-value axis.
    """
    text = GAUNTLET.read_text()
    assert "sibling spec" in text.lower(), (
        "gauntlet/SKILL.md must instruct the fact pass to verify sibling-spec "
        "expectations, not only the codebase"
    )


# --- calibration + severity rules ---


def test_gauntlet_is_mandatory_with_no_skip_flag():
    text = GAUNTLET.read_text()
    assert "There is no skip flag" in text, (
        "the no-skip-flag rule must stay pinned — calibration is tuned via the Critical "
        "bars, not via per-invocation opt-outs (same contract as planning's council review)"
    )


def test_cross_pass_convergence_is_the_severity_signal():
    """The strongest signal the gauntlet produces, and the one an adjudicator loses first."""
    assert "convergence" in GAUNTLET.read_text().lower(), (
        "gauntlet/SKILL.md must keep the cross-pass convergence rule: findings that "
        "independent, mutually-blind passes reached separately outrank single-pass findings"
    )


@pytest.mark.parametrize("name", _DISPOSITION_NAMES)
def test_disposition_names_pinned(name: str):
    assert f"`{name}" in GAUNTLET.read_text(), (
        f"disposition option `{name}` missing from gauntlet/SKILL.md — the disposition "
        "set is behavior, not prose"
    )


def test_reframed_disposition_supersedes_rather_than_freezes():
    """`reframed` is the premise pass's landing place: the spec must NOT freeze."""
    text = GAUNTLET.read_text()
    assert "superseded" in text, (
        "gauntlet/SKILL.md must route a `reframed` Critical to a superseded spec — a "
        "spec whose framing failed review must not reach `ready`"
    )


# --- shared-council reuse: the roster/template/bars are not duplicated ---


def test_gauntlet_reads_the_shared_council_file():
    assert "_shared/council.md" in GAUNTLET.read_text(), (
        "gauntlet/SKILL.md must reference _shared/council.md for the lens pass rather "
        "than redefining the roster (same read-on-reference contract as plan and consult)"
    )


def test_gauntlet_does_not_reinline_council_scaffolding():
    """The roster, prompt template, and bars live in _shared/council.md — one copy."""
    text = GAUNTLET.read_text()
    for fragment in ("≤2 Critical", "≤300 words total", "## Confidence"):
        assert fragment not in text, (
            f"gauntlet/SKILL.md re-inlines council scaffolding ({fragment!r}) that belongs "
            "only in _shared/council.md — duplication is how the two copies drift apart"
        )


def test_spec_review_bars_live_in_shared_council():
    """The lenses need spec-altitude bars; the plan bars fire on slices that don't exist yet."""
    text = SHARED_COUNCIL.read_text()
    assert "Per-lens Critical bars — spec review" in text, (
        "_shared/council.md must carry the spec-review bar set for the gauntlet's lens pass"
    )
    for lens in ("*Builder — spec review:*", "*Reliability — spec review:*",
                 "*Security — spec review:*", "*Advocate — spec review:*"):
        assert lens in text, f"_shared/council.md missing the {lens!r} bar block"


def test_gauntlet_selects_spec_bars_not_plan_bars():
    text = GAUNTLET.read_text()
    assert "Per-lens Critical bars — spec review" in text, (
        "gauntlet/SKILL.md must point the lens dispatch at the SPEC bars — pointing it at "
        "the plan bars yields findings that are all true and all useless ('this slice has "
        "no test contract' — there are no slices)"
    )
