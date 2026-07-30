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

import re
from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SKILLS_DIR = CRAFT / "skills"
AGENTS_DIR = CRAFT / "agents"
TEMPLATES_DIR = CRAFT / "templates"

GAUNTLET = SKILLS_DIR / "gauntlet" / "SKILL.md"
BRAINSTORM = SKILLS_DIR / "brainstorm" / "SKILL.md"
DISTILL = SKILLS_DIR / "distill" / "SKILL.md"
PLAN = SKILLS_DIR / "plan" / "SKILL.md"
SHARED_COUNCIL = SKILLS_DIR / "_shared" / "council.md"
PLANNER = AGENTS_DIR / "planner.md"

# Any forward advance of a SPEC's status, in any craft prose. Matched as a regex
# rather than a fixed literal because the invariant is about the *transition*, not
# about one phrasing: `--status ready` is the freeze, but `--status planned` carries
# a spec past `ready` to the same effect, so a test that greps only for `ready`
# leaves a second unguarded door (it did — see git history).
_SPEC_ADVANCE_RE = re.compile(r"<spec-id>\s+--status\s+(\w+)")

# States that imply the spec is frozen (gauntlet-passed). `draft` and `superseded`
# are not advances — brainstorm creates at `draft`, and a reframed spec is superseded.
_FROZEN_STATES = {"ready", "planned", "complete"}

# `planned` may be written by a planning path, but ONLY behind this guard — the
# phrase is the behavior, so it is pinned verbatim.
_ADVANCE_GUARD = "only if the spec is already `ready`"

# `complete` is the second licensed advance, and it belongs to exactly one file.
# The distill ritual owns the spec `planned -> complete` edge — `complete` *means*
# distilled — so its write carries its own guard rather than planning's: a spec may
# only complete from `planned`, and only once its whole cluster was dispositioned.
_COMPLETE_ADVANCE_GUARD = "only if the spec is already `planned` and its cluster is dispositioned"


def _craft_prose_files() -> list[Path]:
    """Every markdown surface craft ships — skills (incl. _shared), agents, templates.

    Globbed broadly on purpose: an earlier version scanned only `skills/*/SKILL.md`
    and `agents/*.md`, which left `_shared/`, nested skill docs, and `templates/`
    invisible to the invariant.
    """
    files: list[Path] = []
    for root in (SKILLS_DIR, AGENTS_DIR, TEMPLATES_DIR):
        if root.is_dir():
            files.extend(sorted(root.rglob("*.md")))
    return files

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


def test_gauntlet_owns_the_freeze():
    assert "<spec-id> --status ready" in GAUNTLET.read_text(), (
        "gauntlet/SKILL.md must carry the spec-freeze command "
        "(`lore record update <spec-id> --status ready`) — it owns the draft -> ready edge"
    )


def test_only_gauntlet_freezes_a_spec():
    """No craft file except the gauntlet may advance a spec to `ready`.

    This is the structural form of "mandatory, no skip flag". Any other skill or
    agent carrying the freeze is a bypass: it can hand planning a spec that was
    never adversarially reviewed.
    """
    offenders = [
        p
        for p in _craft_prose_files()
        if p != GAUNTLET and "ready" in _SPEC_ADVANCE_RE.findall(p.read_text())
    ]
    assert not offenders, (
        "these craft files can freeze a spec, bypassing the gauntlet: "
        f"{[str(p.relative_to(CRAFT)) for p in offenders]}. The draft -> ready edge "
        "belongs to the gauntlet alone — if a spec can reach `ready` without it, the "
        "review is optional in practice no matter what the prose claims."
    )


def test_advancing_a_spec_past_ready_is_guarded():
    """`--status planned` is the *second* door to the same room, and it must be barred.

    A planning path that advances `draft` -> `planned` carries the spec past `ready`
    without stopping there — implying a freeze the gauntlet never granted. Planning
    may only advance a spec that is ALREADY `ready`, and every file that writes such
    an advance must say so verbatim.

    This is the finding that a `ready`-only grep missed: the invariant is about the
    transition, not about one word.

    `complete` is carved out for the distill skill alone, which carries its own,
    stricter guard — see `test_only_distill_completes_a_spec`. The carve-out is
    per-file AND per-state: distill advancing a spec to `ready` or `planned` would
    still land here.
    """
    violations = []
    for p in _craft_prose_files():
        if p == GAUNTLET:
            continue
        text = p.read_text()
        advances = {s for s in _SPEC_ADVANCE_RE.findall(text) if s in _FROZEN_STATES}
        if p == DISTILL:
            advances -= {"complete"}
        if advances and _ADVANCE_GUARD not in text:
            violations.append(f"{p.relative_to(CRAFT)}: advances spec to {sorted(advances)}")
    assert not violations, (
        "these craft files advance a spec into a frozen state without the guard "
        f"{_ADVANCE_GUARD!r}:\n  " + "\n  ".join(violations) + "\n"
        "An unguarded advance lets a `draft` spec reach `planned` without ever passing "
        "the gauntlet — the same bypass the freeze rule exists to close."
    )


def test_only_distill_completes_a_spec():
    """`complete` means *distilled* — so exactly one file may write it.

    The same structural mandate as the gauntlet's `ready`: if any other skill can
    flip a spec `complete`, the ritual that gives the status its meaning becomes
    optional, and `complete` degrades into "someone thought this was finished".
    """
    offenders = [
        p
        for p in _craft_prose_files()
        if p != DISTILL and "complete" in _SPEC_ADVANCE_RE.findall(p.read_text())
    ]
    assert not offenders, (
        "these craft files advance a spec to `complete`, bypassing the distill "
        f"ritual: {[str(p.relative_to(CRAFT)) for p in offenders]}. The "
        "planned -> complete edge belongs to distill/SKILL.md alone."
    )


def test_distill_carries_the_complete_advance_behind_its_own_guard():
    assert DISTILL.exists(), f"Expected distill/SKILL.md in {SKILLS_DIR}"
    text = DISTILL.read_text()
    assert "complete" in _SPEC_ADVANCE_RE.findall(text), (
        "distill/SKILL.md must carry the canonical spec-completion command "
        "(`lore record update <spec-id> --status complete`) — it owns the "
        "planned -> complete edge, and the guard test only recognizes that form"
    )
    assert _COMPLETE_ADVANCE_GUARD in text, (
        "distill/SKILL.md must carry its advance guard verbatim: "
        f"{_COMPLETE_ADVANCE_GUARD!r}. Planning's `ready` guard is the wrong one "
        "here — it would license completing a spec that was never planned, and say "
        "nothing about the cluster disposition that gives `complete` its meaning."
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


# --- adr mode: adapted roster, direct freeze, annotation-borne provenance ---

# The `## Reviewing an adr` heading scopes the adr-specific checks below to just
# that section of the file, so a check for "divergence-prober absent" doesn't
# also have to be true of the spec-mode section earlier in the same file.
ADR_SECTION_HEADER = "## Reviewing an adr"

_ADR_MODE_AGENTS: list[str] = [
    "premise-attacker",
    "consistency-auditor",
    "builder",
    "breaker",
    "attacker",
    "advocate",
]

# Mirrors `_SPEC_ADVANCE_RE`: a literal substring only catches the one exact
# spelling this file happens to use, so a differently-phrased adr activation
# elsewhere (extra whitespace, reordered flags) would silently escape the
# "only gauntlet flips it" guard. The gauntlet owns this edge too, and it is
# the ONLY file allowed to carry it.
_ADR_ADVANCE_RE = re.compile(r"<adr-id>\s+--status\s+active")

_ANNOTATION_PROVENANCE_SENTENCE = (
    "Gauntlet provenance for an adr target goes to the record's annotations, "
    "never the body"
)

_DISTILLED_SKIP_SENTENCE = (
    "Distilled (backward) ADRs skip the gauntlet — the distill disposition owns "
    "their flip"
)


def _adr_mode_section(text: str) -> str:
    assert ADR_SECTION_HEADER in text, (
        f"gauntlet/SKILL.md must carry a {ADR_SECTION_HEADER!r} section describing "
        "adr-target mode"
    )
    return text[text.index(ADR_SECTION_HEADER):]


def test_gauntlet_owns_the_adr_freeze():
    assert _ADR_ADVANCE_RE.search(GAUNTLET.read_text()), (
        "gauntlet/SKILL.md must carry the adr-freeze command "
        "(`lore record update <adr-id> --status active`) — it owns the "
        "draft -> active edge, the adr equivalent of the spec `ready` guard"
    )


def test_only_gauntlet_flips_an_adr_active():
    """No craft file except the gauntlet may advance an adr to `active`.

    The same structural mandate as the spec freeze: a bypass here can hand an
    unreviewed decision straight into the immutable, convention-enforced log.
    """
    offenders = [
        p for p in _craft_prose_files()
        if p != GAUNTLET and _ADR_ADVANCE_RE.search(p.read_text())
    ]
    assert not offenders, (
        "these craft files can flip an adr to `active`, bypassing the gauntlet: "
        f"{[str(p.relative_to(CRAFT)) for p in offenders]}"
    )


@pytest.mark.parametrize("agent", _ADR_MODE_AGENTS)
def test_adr_mode_roster_agents_resolve(agent: str):
    adr_section = _adr_mode_section(GAUNTLET.read_text())
    assert agent in adr_section, (
        f"gauntlet/SKILL.md's adr-mode section does not dispatch {agent!r}"
    )
    agent_file = AGENTS_DIR / f"{agent}.md"
    assert agent_file.exists(), f"{agent_file} does not exist — a dispatch must not dead-end"


def test_adr_mode_dispatches_fact_verification_too():
    adr_section = _adr_mode_section(GAUNTLET.read_text())
    assert "Explore" in adr_section, (
        "the adr roster keeps the fact-verification pass via the generic Explore agent"
    )


def test_adr_mode_drops_divergence_prober():
    """The two-implementations method has no analogue for a decision document."""
    text = GAUNTLET.read_text()
    adr_section = _adr_mode_section(text)
    assert "divergence-prober" not in adr_section, (
        "divergence-prober must be absent from the adr roster"
    )
    assert "divergence-prober" in text, (
        "divergence-prober must still be dispatched for the spec roster elsewhere "
        "in the same file"
    )


def test_adr_mode_restates_all_passes_required():
    adr_section = _adr_mode_section(GAUNTLET.read_text()).lower()
    assert "name" in adr_section and "stop" in adr_section, (
        "the 'all passes required — name the missing one and stop' rule must be "
        "restated for the adr roster, not silently inherited from the spec section"
    )


def test_adr_gauntlet_owns_the_flip_directly_with_no_intermediate_state():
    adr_section = _adr_mode_section(GAUNTLET.read_text())
    assert "no intermediate" in adr_section, (
        "the adr-mode section must explain there is no intermediate "
        "frozen-but-inactive state — the adr vocab has no `ready`, so the flip "
        "goes straight to `active`"
    )


def test_adr_provenance_goes_to_annotations_never_body():
    assert _ANNOTATION_PROVENANCE_SENTENCE in GAUNTLET.read_text(), (
        "the annotation-provenance rule must be pinned verbatim in gauntlet/SKILL.md"
    )


def test_distilled_adrs_skip_the_gauntlet():
    assert _DISTILLED_SKIP_SENTENCE in GAUNTLET.read_text(), (
        "the 'distilled ADRs skip the gauntlet' sentence must be pinned verbatim"
    )


_FORWARD_SUPERSESSION_BACK_EDGE = (
    "lore record update <predecessor-adr-id> --status superseded --related adr=<adr-id>"
)


def test_gauntlet_writes_the_predecessor_supersession_back_edge():
    """The forward path must not leave supersession one-directional.

    Distill writes both directions for a backward ADR; a forward ADR authored and
    activated through this gauntlet needs the identical guarantee, or a reader
    landing on the superseded predecessor never finds its successor.
    """
    text = GAUNTLET.read_text()
    assert _FORWARD_SUPERSESSION_BACK_EDGE in text, (
        "gauntlet/SKILL.md must carry the predecessor's `superseded` flip + "
        "`related: adr=` back-edge as part of the adr-activation flip, mirroring "
        "distill's bidirectional supersession write"
    )


def test_gauntlet_supersession_back_edge_is_second_write():
    """Order matters: the successor must exist (active) before the predecessor flips.

    That ordering is the recoverable one, same reasoning as distill's pinned
    internal supersession order.
    """
    adr_section = _adr_mode_section(GAUNTLET.read_text())
    successor_idx = _ADR_ADVANCE_RE.search(adr_section).start()
    back_edge_idx = adr_section.index(_FORWARD_SUPERSESSION_BACK_EDGE)
    assert successor_idx < back_edge_idx, (
        "gauntlet/SKILL.md must write the successor's `--status active` flip "
        "before the predecessor's `superseded` back-edge"
    )


def test_gauntlet_selects_adr_bars_not_spec_or_plan_bars():
    adr_section = _adr_mode_section(GAUNTLET.read_text())
    assert "Per-lens Critical bars — adr review" in adr_section, (
        "gauntlet/SKILL.md's adr-mode section must point the lens dispatch at the "
        "ADR bars, not the spec or plan bars"
    )


def test_adr_review_bars_live_in_shared_council():
    text = SHARED_COUNCIL.read_text()
    assert "Per-lens Critical bars — adr review" in text, (
        "_shared/council.md must carry the adr-review bar set for the gauntlet's "
        "adr-mode lens pass"
    )
    for lens in ("*Builder — adr review:*", "*Reliability — adr review:*",
                 "*Security — adr review:*", "*Advocate — adr review:*"):
        assert lens in text, f"_shared/council.md missing the {lens!r} bar block"
