"""The spec gauntlet — adversarial spec review — and the invariant that makes it mandatory.

The gauntlet is the review a spec passes before it advances: eight parallel passes
(fact verification, premise attack, the four council lenses, an internal-consistency
audit, a plan-divergence probe), adjudicated in the main session and delivered to the
operator as one compact recommendation — synthesis, recommended outcome, per-Critical
dispositions — which they accept or override in a single round-trip before anything is
written.

The user-facing decision was "mandatory, no skip flag" — but a checklist item saying
"don't skip this" is honored only as well as the next agent feels like honoring it.
So the mandate is enforced **structurally** instead, by ownership of a state
transition:

    The `draft` -> `ready` edge on a spec record belongs to the gauntlet, and to
    nothing else in craft.

Brainstorm writes the spec at `draft` and stops. The `planner` agent (which covers
the whole brainstorm -> spec -> plan arc in one isolated dispatch, and therefore
*used* to flip the spec to `ready` itself) leaves it at `draft`. Planning refuses to
slice a `draft` spec. Advancing a spec without review therefore requires deleting the
gauntlet's flip, not merely forgetting a step.

`test_only_gauntlet_advances_a_spec` is the test that pins that. If it fails, the
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
PREMISE_ATTACKER = AGENTS_DIR / "premise-attacker.md"

# Any forward advance of a SPEC's status, in any craft prose. Matched as a regex
# rather than a fixed literal because the invariant is about the *transition*, not
# about one phrasing: `--status ready` is the advance, but `--status planned` carries
# a spec past `ready` to the same effect, so a test that greps only for `ready`
# leaves a second unguarded door (it did — see git history).
_SPEC_ADVANCE_RE = re.compile(r"<spec-id>\s+--status\s+(\w+)")

# States that imply the spec is advanced (gauntlet-passed). `draft` and `superseded`
# are not advances — brainstorm creates at `draft`, and a spec carrying a final
# `revise` disposition withholds the advance and stays `draft`, revise round by
# revise round, rather than advancing anywhere.
_ADVANCED_STATES = {"ready", "planned", "complete"}

# `planned` may be written by a planning path, but ONLY behind this guard — the
# phrase is the behavior, so it is pinned verbatim.
_ADVANCE_GUARD = "only if the spec is already `ready`"

# `complete` is the second licensed advance, and it belongs to exactly one file.
# The distill ritual owns the spec `planned -> complete` edge — `complete` *means*
# distilled — so its write carries its own guard rather than planning's: a spec may
# only complete from `planned`, and only once its whole cluster was dispositioned.
_COMPLETE_ADVANCE_GUARD = "only if the spec is closed out and its cluster is dispositioned"


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

# The disposition vocabulary. `revise` is the gauntlet's delta over planning's set:
# a spec can fail review by being the wrong spec, an outcome a plan review has no
# analogue for. Unlike the `reframed` disposition it replaced, `revise` carries a
# mandatory prescription and scope rather than handing back a closed door — dropping
# it would silently remove the premise pass's only landing place, its characteristic
# (and highest-value) outcome. `answered` is the operator's non-terminal override,
# re-adjudicated into one of the two agent-proposable terms above.
_DISPOSITION_NAMES: list[str] = [
    "resolved",
    "revise",
    "accepted-as-risk",
    "disputed",
    "answered",
]

# The subset of `_DISPOSITION_NAMES` that gets its own slot in the adr counts
# annotation. `answered` is excluded on purpose: it is not a final disposition and
# "contributes no term of its own to the grammar" (SKILL.md's provenance-stamp
# rule) — it counts under whichever of `resolved` / `revise` it is re-adjudicated
# to, plus an operator-override marker, never under its own name.
_FINAL_DISPOSITION_NAMES: list[str] = [
    name for name in _DISPOSITION_NAMES if name != "answered"
]


def test_gauntlet_skill_ships():
    assert GAUNTLET.exists(), f"Expected gauntlet/SKILL.md in {SKILLS_DIR}"


# --- the mandate: exactly one file may advance a spec ---


def test_gauntlet_owns_the_advance():
    assert "<spec-id> --status ready" in GAUNTLET.read_text(), (
        "gauntlet/SKILL.md must carry the spec-advance command "
        "(`lore record update <spec-id> --status ready`) — it owns the draft -> ready edge"
    )


def test_only_gauntlet_advances_a_spec():
    """No craft file except the gauntlet may advance a spec to `ready`.

    This is the structural form of "mandatory, no skip flag". Any other skill or
    agent carrying the advance is a bypass: it can hand planning a spec that was
    never adversarially reviewed.
    """
    offenders = [
        p
        for p in _craft_prose_files()
        if p != GAUNTLET and "ready" in _SPEC_ADVANCE_RE.findall(p.read_text())
    ]
    assert not offenders, (
        "these craft files can advance a spec, bypassing the gauntlet: "
        f"{[str(p.relative_to(CRAFT)) for p in offenders]}. The draft -> ready edge "
        "belongs to the gauntlet alone — if a spec can reach `ready` without it, the "
        "review is optional in practice no matter what the prose claims."
    )


def test_advancing_a_spec_past_ready_is_guarded():
    """`--status planned` is the *second* door to the same room, and it must be barred.

    A planning path that advances `draft` -> `planned` carries the spec past `ready`
    without stopping there — implying a advance the gauntlet never granted. Planning
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
        advances = {s for s in _SPEC_ADVANCE_RE.findall(text) if s in _ADVANCED_STATES}
        if p == DISTILL:
            advances -= {"complete"}
        if advances and _ADVANCE_GUARD not in text:
            violations.append(f"{p.relative_to(CRAFT)}: advances spec to {sorted(advances)}")
    assert not violations, (
        "these craft files advance a spec into a advanced state without the guard "
        f"{_ADVANCE_GUARD!r}:\n  " + "\n  ".join(violations) + "\n"
        "An unguarded advance lets a `draft` spec reach `planned` without ever passing "
        "the gauntlet — the same bypass the advance rule exists to close."
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
        "here — it would license completing a spec nobody finished, and say "
        "nothing about the cluster disposition that gives `complete` its meaning."
    )


def test_brainstorm_hands_off_to_gauntlet_and_does_not_advance():
    text = BRAINSTORM.read_text()
    assert "/craft:gauntlet" in text, (
        "brainstorm's exit gate must hand off to /craft:gauntlet — it is the next step "
        "after the spec is written, and brainstorm no longer advances the spec itself"
    )
    assert "Do not flip the spec to `ready` yourself" in text, (
        "brainstorm must explicitly disclaim the ready-flip; without the disclaimer an "
        "agent mid-brainstorm will helpfully advance the spec on its own"
    )


def test_planner_agent_does_not_advance_the_spec():
    """The planner agent runs the whole brainstorm -> plan arc in one isolated dispatch.

    It cannot run the gauntlet: it has no `Agent` tool to dispatch the eight passes
    (its `tools:` line is pinned in test_agents_registrable.py), and there is no user
    in its context to disposition Criticals. So it must leave the spec at `draft` and
    say so — it must not quietly advance it.
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


def test_revise_disposition_withholds_advance_rather_than_superseding():
    """`revise` is the premise pass's landing place: the spec must NOT advance —
    but it is not discarded either. It stays `draft` for another revise round.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert (
        "advances when no Critical carries a final disposition of `revise`"
    ) in step, (
        "gauntlet/SKILL.md must withhold the advance while a `revise` Critical "
        "survives — a spec whose framing failed review must not reach `ready`"
    )
    assert "<spec-id> --status superseded" not in GAUNTLET.read_text(), (
        "a `revise` Critical must not route the spec to `superseded` — it "
        "withholds the advance and starts another revise round instead"
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


# --- adr mode: adapted roster, direct advance, annotation-borne provenance ---

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
# "only distill flips it" guard. Distill owns this edge, on both the forward and
# the backward path, and it is the ONLY file allowed to carry it.
_ADR_ADVANCE_RE = re.compile(r"<adr-id>\s+--status\s+active")

_ANNOTATION_PROVENANCE_SENTENCE = (
    "Gauntlet provenance for an adr target goes to the record's annotations, "
    "never the body"
)

_DISTILLED_SKIP_SENTENCE = (
    "Distilled (backward) ADRs skip the gauntlet — the distill disposition owns "
    "their flip"
)


def _section(text: str, header: str, stop: str | None = None, why: str = "") -> str:
    """One section of gauntlet/SKILL.md, from *header* up to *stop*.

    Every section pin scopes itself this way rather than matching the whole file:
    the skill states the spec-mode and adr-mode rules in near-identical prose, so
    a whole-file substring check passes on the other mode's wording and pins
    nothing. A *stop* of ``None``, or one that does not occur after *header*,
    means "to the end of the text" — the last section of a file or of a slice has
    no following marker to bound it.
    """
    assert header in text, why or f"gauntlet/SKILL.md must carry a {header!r} section"
    start = text.index(header)
    end = -1 if stop is None else text.find(stop, start + 1)
    return text[start:] if end == -1 else text[start:end]


def _adr_mode_section(text: str) -> str:
    return _section(
        text,
        ADR_SECTION_HEADER,
        why=(
            f"gauntlet/SKILL.md must carry a {ADR_SECTION_HEADER!r} section "
            "describing adr-target mode"
        ),
    )


def test_gauntlet_never_flips_an_adr_active():
    """The gauntlet does not own the draft -> active edge any more — distill does.

    Activation moved from review-time (the gauntlet) to completion-time
    (distill, when every derived spec reaches a terminal status). A `--status
    active` invocation anywhere in the gauntlet skill is the old route back.
    """
    assert not _ADR_ADVANCE_RE.search(GAUNTLET.read_text()), (
        "gauntlet/SKILL.md must not carry `lore record update <adr-id> --status "
        "active` in either mode — the gauntlet never advances an adr past `draft`; "
        "distill is the sole writer of that edge now"
    )


def test_only_distill_flips_an_adr_active():
    """No craft file except distill may advance an adr to `active`.

    The same structural mandate the spec advance uses, retargeted: a bypass here
    can hand an unreviewed or premature activation straight into the immutable,
    convention-enforced log.
    """
    offenders = [
        p for p in _craft_prose_files()
        if p != DISTILL and _ADR_ADVANCE_RE.search(p.read_text())
    ]
    assert not offenders, (
        "these craft files can flip an adr to `active`, bypassing distill: "
        f"{[str(p.relative_to(CRAFT)) for p in offenders]}"
    )
    assert _ADR_ADVANCE_RE.search(DISTILL.read_text()), (
        "distill/SKILL.md must carry the adr-activation command "
        "(`lore record update <adr-id> --status active`) — it is the sole writer "
        "of an adr's draft -> active edge now, on both the forward and backward path"
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


def test_gauntlet_supersession_back_edge_is_the_accepted_tails_third_write():
    """Order matters: the accepted edits and provenance land before the predecessor flips.

    The predecessor write comes last in the accepted tail — after the `lesson`
    record and the atomic edits+annotation write — so a crash never leaves the
    predecessor retired ahead of the successor's own accepted content.
    """
    tail = _adr_tail(GAUNTLET.read_text())
    edits_idx = tail.index(_ADR_EDITS_WRITE)
    back_edge_idx = tail.index(_FORWARD_SUPERSESSION_BACK_EDGE)
    assert edits_idx < back_edge_idx, (
        "gauntlet/SKILL.md must write the accepted tail's atomic edits+annotation "
        "write before the predecessor's `superseded` back-edge"
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



# --- resolution: one compact recommendation, accepted or overridden ---

# The resolution step is the only gauntlet output an operator acts on, so its shape
# is behavior. Anchored on the heading so most checks below can be scoped to the
# step: a phrase that happens to appear in the adr-mode section must not satisfy a
# pin about the shared resolution step.
RESOLUTION_HEADER = "### 5. Recommend, then accept"

# The seam between deciding and writing. Named as its own subsection so the two
# halves of the step can be edited independently without either one guessing where
# the other ends.
ACCEPTED_TAIL_ANCHOR = "#### The accepted tail"

# The points where the step hands control to a human. Named — rather than left
# implicit in the prose — so an unattended caller would be a re-route table over
# these names instead of a redesign, the pattern `_shared/execute.md` establishes.
_ESCALATION_POINTS: list[str] = [
    "operator acceptance gate",
    "override round-trip",
    "route-change re-present",
    "failed-write report",
]


def _flat(text: str) -> str:
    """Whitespace-collapsed prose, so a pinned sentence survives a line wrap.

    The contract is the sentence, not where the paragraph happens to break.
    """
    return " ".join(text.split())


def _resolution_step(text: str) -> str:
    """The resolution step's body, from its heading to the next step heading.

    `#### ` subsections inside the step do not terminate it; only the next `### `
    step heading does.
    """
    return _section(
        text,
        RESOLUTION_HEADER,
        "\n### ",
        why=(
            f"gauntlet/SKILL.md must carry a {RESOLUTION_HEADER!r} step — the "
            "adjudicated findings reach the operator as one recommendation, not as "
            "a per-finding interrogation"
        ),
    )


def test_resolution_step_exists():
    _resolution_step(GAUNTLET.read_text())


def test_deliverable_is_compact_and_leads_with_a_narrative_synthesis():
    """The operator's job here is to decide, not to re-derive the review.

    A finding dump makes them re-open the record to judge each item, and so does a
    table doing the work prose should do: either way the reader reconstructs how
    the findings relate by reading across rows. The deliverable therefore leads
    with prose carrying the judgment, and the table supports it.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "target one terminal screen (~40 lines)" in step, (
        "the resolution step must state the compactness target for the default "
        "deliverable — without it the step degrades back into the finding dump it "
        "replaced"
    )
    assert "**The narrative synthesis**" in step, (
        "the deliverable must lead with the narrative synthesis — the part that "
        "carries what the passes found, whether it holds, and what to do about it"
    )
    assert "How the synthesis reads" in step, (
        "the deliverable's first part must bind to the three-movement shape in "
        "_shared/council.md rather than restating it, so gauntlet and the other "
        "human-facing callers cannot drift apart"
    )


def test_synthesis_carries_no_sentence_cap():
    """A five-sentence cap cannot hold three movements.

    The synthesis has to say what the passes found, whether those findings hold and
    where they came from, and what to do about them. A hard sentence cap forces two
    of the three back out into the table — which is the shape being replaced. The
    one-screen budget is what keeps it honest instead.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "at most five sentences" not in step, (
        "the five-sentence cap must be gone — it is too small to carry themes, "
        "validity judgment, and remedy together, and it is what pushed the "
        "explanation down into the table"
    )
    assert "under-consolidated" in step, (
        "with the cap gone, the step must say what an over-long synthesis means: "
        "the finding set was under-consolidated, not the budget too small. Without "
        "it, dropping the cap reads as a licence to grow"
    )


def test_interpretive_per_pass_reading_is_licensed():
    """A blanket ban on the per-pass view would forbid the wrong thing.

    What must stay banned is the mechanical roll-up — "premise raised 2, breaker
    raised 3". What must stay licensed is the adjudicator's interpretive read of
    which lenses were right and which overreached, which is exactly what the
    operator needs to judge the findings without re-deriving them.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "Not a finding count, not a per-pass roll-up" not in step, (
        "the blanket per-pass prohibition must be gone — it forbids the "
        "interpretive per-lens account the synthesis is supposed to carry"
    )


def test_deliverable_pins_the_recommended_outcome_and_the_per_critical_table():
    """The synthesis is one of four parts, and the other three carry the decision.

    The outcome is what the operator accepts; the table is what they override
    against. Left unpinned, either can drift into prose the operator has to parse.
    """
    step = _resolution_step(GAUNTLET.read_text())
    flat = _flat(step)
    assert "The deliverable is these four parts, in this order" in flat, (
        "the resolution step must fix the deliverable's parts and their order — a "
        "deliverable assembled differently each run cannot be scanned"
    )
    assert "minus the table on a run that produced none" in flat, (
        "the part count must reconcile with the zero-Critical run, which presents "
        "no per-Critical table — stated as an unqualified 'exactly four', the "
        "deliverable's own definition is false on the clean-run path"
    )
    assert "supporting detail, not the explanation" in flat, (
        "the per-Critical table must be billed as supporting detail — left "
        "unqualified it re-accumulates the weight the narrative is supposed to "
        "carry, which is the failure this shape replaced"
    )
    assert "**The recommended outcome**, on its own line" in flat, (
        "the outcome — advance, or the round the record continues into — must be "
        "its own line, named — an outcome inferred from prose is one the operator "
        "accepts without reading"
    )
    assert "| id | finding | proposed disposition | proposed edit |" in step, (
        "the per-Critical table must pin its columns — id, finding, proposed "
        "disposition, proposed edit — since those four are what acceptance covers"
    )
    assert "**Important and Minor, compressed**" in flat, (
        "the deliverable's fourth part is the compressed Important/Minor summary"
    )
    assert (
        flat.index("**The narrative synthesis**")
        < flat.index("**The recommended outcome**")
        < flat.index("**The per-Critical table**")
        < flat.index("**Important and Minor, compressed**")
    ), (
        "the four parts must appear in the order the step declares — prose order is "
        "the only thing carrying it"
    )


def test_criticals_carry_stable_presentation_ordered_ids():
    """Ids are the operator's handle for an override and the audit trail's label.

    Assigned late, or renumbered after presenting, and an override lands on a
    different finding than the one the operator named.
    """
    text = _flat(GAUNTLET.read_text())
    assert "`C1`…`Cn`" in text, (
        "gauntlet/SKILL.md must give each Critical a stable id `C1`…`Cn` — the "
        "override syntax and the audit trail both quote them"
    )
    assert "in the order you will present them" in text, (
        "ids must be assigned in presentation order, so an operator scanning the "
        "table can name a row without counting"
    )
    assert "stable for the rest of the run" in text, (
        "ids must be pinned stable across override round-trips — renumbering "
        "mid-run silently retargets an override the operator already gave"
    )


def test_only_resolved_and_revise_are_agent_proposable():
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "The agent proposes **only `resolved` or `revise`**" in step, (
        "the resolution step must restrict agent-proposed dispositions to "
        "`resolved` and `revise` — those are judgments about the document, which "
        "the adjudicator has read in full"
    )


def test_revise_requires_a_prescription_to_be_a_critical():
    """A `revise` with no prescription is the exact defect this disposition exists
    to fix: a verdict that hands back a closed door instead of something actionable.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert (
        "Every `revise` carries a prescription** naming what is wrong, what to "
        "change, and how"
    ) in step, (
        "the resolution step must require every `revise` to carry a prescription "
        "naming what is wrong, what to change, and how"
    )
    assert (
        "A finding that cannot produce a prescription this specific is not a "
        "Critical." in step
    ), (
        "the resolution step must state that a finding unable to produce a "
        "prescription this specific is not a Critical — otherwise `revise` can "
        "still be proposed as a bare verdict"
    )


def test_revise_prescription_declares_a_scope_with_the_downstream_evidence_bar():
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "`record-only`" in step, (
        "the resolution step must define the `record-only` scope"
    )
    assert (
        "the change lands inside the record under review; the finding is its own "
        "evidence" in step
    ), "the `record-only` scope must be defined in these terms"
    assert "`reaches-downstream`" in step, (
        "the resolution step must define the `reaches-downstream` scope"
    )
    assert "downstream evidence bar" in step, (
        "the resolution step must name the downstream evidence bar a "
        "`reaches-downstream` prescription must meet"
    )
    assert (
        "a named, specific alternative that accomplishes the same outcome" in step
    ), "the downstream evidence bar must require a named, specific alternative"
    assert "**Generalised doubt does not meet it.**" in step, (
        "the downstream evidence bar must state its exclusion: generalised doubt "
        "does not meet it"
    )


def test_reaches_downstream_writes_nothing_to_the_named_specs():
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert (
        "A `reaches-downstream` prescription **writes nothing to the named "
        "specs**" in step
    ), (
        "a `reaches-downstream` prescription must be pinned as writing nothing to "
        "the specs it names — re-entry into brainstorming is the operator's act, "
        "not something the gauntlet does on its own write"
    )


def test_per_critical_table_renders_a_revise_row_as_a_prescription_block():
    """A `revise` row's prescription (and scope) cannot fit the one-clause edit
    cell the table defines for `resolved` — so the step must say what a `revise`
    row renders as instead: a compact header line plus an indented prescription
    block, with a `reaches-downstream` block also naming the specs it reaches.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "compact header line" in step, (
        "the per-Critical table section must describe a `revise` row's rendering "
        "as a compact header line — the one-clause edit cell cannot hold a "
        "prescription and its scope"
    )
    assert "indented prescription block" in step, (
        "the compact header line must be followed by an indented prescription "
        "block naming what is wrong, what to change, and how"
    )
    assert "Reaches:" in step, (
        "a `reaches-downstream` row's block must carry a `Reaches:` line naming "
        "the derived specs it invalidates"
    )


# Bounds the worked-example fixture below in gauntlet/SKILL.md. The markers are
# HTML comments so they render as nothing in a rendered skill doc.
_WORKED_EXAMPLE_START = "<!-- worked-example:start -->"
_WORKED_EXAMPLE_END = "<!-- worked-example:end -->"


def _worked_example(text: str) -> str:
    assert _WORKED_EXAMPLE_START in text and _WORKED_EXAMPLE_END in text, (
        "the resolution step must carry a worked-example fixture bounded by "
        f"{_WORKED_EXAMPLE_START!r} / {_WORKED_EXAMPLE_END!r} markers — the "
        "layout claim needs a real fixture to check, not just a description"
    )
    start = text.index(_WORKED_EXAMPLE_START) + len(_WORKED_EXAMPLE_START)
    end = text.index(_WORKED_EXAMPLE_END)
    return text[start:end]


def test_worked_example_renders_three_criticals_with_a_reaches_downstream_row():
    """A real render of 3+ Criticals, one `reaches-downstream`, checked against
    the "one screen per typical run" UI Direction — as a fixture, not a claim.

    The bound: a `resolved` (or operator-override) row is one table line; a
    `revise` row is a header line plus at most two prescription lines (three
    for `reaches-downstream`, which adds the `Reaches:` line). The full
    deliverable's other three parts — the narrative synthesis (three short
    paragraphs), the one-line recommended outcome, and the compressed
    Important/Minor summary — need roughly half of the step's own ~40-line
    budget, so a typical run's per-Critical table (here: 3 Criticals, one of
    each disposition shape) has to fit inside the other half. 20 lines is
    that ceiling, and this fixture is checked against it directly rather than
    asserted to satisfy it.
    """
    step = _resolution_step(GAUNTLET.read_text())
    example = _worked_example(step)
    lines = [line for line in example.splitlines() if line.strip()]

    critical_ids = set(re.findall(r"\bC\d+\b", example))
    assert len(critical_ids) >= 3, (
        f"the worked example must render at least 3 Criticals — found {critical_ids!r}"
    )

    reaches_lines = [line for line in lines if line.strip().startswith("Reaches:")]
    assert reaches_lines, (
        "the worked example must include a `reaches-downstream` row with a "
        "`Reaches:` line naming derived specs"
    )
    assert any(re.search(r"spec/[a-z0-9][a-z0-9-]*", line) for line in reaches_lines), (
        "the `Reaches:` line must name at least one derived spec by its record id"
    )

    assert len(lines) <= 20, (
        f"the worked example's per-Critical table rendered {len(lines)} non-blank "
        "lines, past the 20-line ceiling this fixture is checked against — see "
        "this test's docstring for how that ceiling was derived from the "
        "step's own ~40-line one-screen budget"
    )


def test_risk_and_dispute_are_operator_only_overrides():
    """Accepting a risk is a statement about what the project will live with.

    An agent that proposes `accepted-as-risk` — or drafts the reason text — signs
    the operator's name to a judgment only the operator can make, and the audit
    trail records it as theirs.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "are **operator-only overrides**" in step, (
        "`accepted-as-risk` and `disputed` must be marked operator-only overrides"
    )
    for name in ("accepted-as-risk", "disputed"):
        assert f"`{name}: <reason>`" in step, (
            f"the resolution step must carry `{name}: <reason>` with its reason slot"
        )
    assert "**never drafted for them**" in step, (
        "the reason text on an override must be the operator's own — a drafted "
        "reason turns the audit trail into the agent's opinion wearing the "
        "operator's signature"
    )


def test_answered_is_operator_only_with_a_quoted_never_drafted_reason():
    """`answered`'s safety property is the single most load-bearing one in the
    disposition vocabulary: the operator, not the agent, supplies the
    counterargument, and it is quoted verbatim rather than drafted.

    Without a pin naming `answered` specifically, `answered` could be dropped from
    the operator-only-overrides list, from the never-drafted rule, and from the
    reason-slot-never-empty rule, and this suite would stay green — the existing
    pins on those three rules were written before `answered` existed and check
    generic phrasing that holds regardless of which dispositions are named.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert (
        "`accepted-as-risk: <reason>`, `disputed: <reason>`, and "
        "`answered: <reason>` are **operator-only overrides**"
    ) in step, (
        "`answered` must be named alongside `accepted-as-risk` and `disputed` as "
        "an operator-only override — an agent may not propose it"
    )
    assert "`answered: <reason>`" in step, (
        "the resolution step must carry `answered: <reason>` with its reason slot"
    )
    assert (
        "**An override with no reason is incomplete.** `accepted-as-risk`, "
        "`disputed`, and `answered` carry the operator's reason text"
    ) in step, (
        "`answered` must be named among the dispositions whose reason text the "
        "agent may not supply on the operator's behalf"
    )
    assert "**Never record any of the three with a reason you drafted" in step, (
        "the never-drafted, never-empty prohibition must cover `answered` as one "
        "of 'the three' operator-only overrides, not just `accepted-as-risk` and "
        "`disputed`"
    )


def test_answered_is_not_terminal_and_folds_into_the_record_as_an_edit():
    """`answered`'s other load-bearing property: it is not a veto.

    The counterargument must land in the record as an edit, or the next gauntlet
    raises the identical finding against the next draft — the exact defect this
    disposition exists to fix.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "`answered` is **not terminal**" in step, (
        "the resolution step must state that `answered` is not terminal — a "
        "vetoing reading of `answered` reproduces the reframe-when-an-edit-would-do "
        "defect one disposition to the left"
    )
    assert "the counterargument is folded into the record as an edit" in step, (
        "an answered row's counterargument must be stated to land in the record as "
        "an edit — an answered row that resolves with no edit drafted leaves the "
        "counterargument out of the artifact, so the next gauntlet raises the same "
        "finding again"
    )


def test_re_adjudication_may_not_land_on_an_operator_only_disposition():
    """The invariant that stops the adjudicator self-authoring an operator veto.

    `answered` hands the adjudicator a row it must re-dispose. If the terms it may
    re-dispose to are not constrained to the two it may already propose, the
    adjudicator can write `accepted-as-risk` or `disputed` itself — an
    operator-only disposition, carrying an operator-only reason, authored by the
    agent under cover of "answering" the finding. That is the same signature
    forgery the empty-reason rule exists to prevent, reached by a different door.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "the re-adjudicated outcome is `resolved` or `revise`" in step, (
        "re-adjudication must be constrained to the two terms the adjudicator may "
        "already propose — an unconstrained re-adjudication vocabulary lets the "
        "agent author an operator-only disposition"
    )
    for term in ("accepted-as-risk", "disputed", "answered"):
        assert f"`{term}`" in step, f"the excluded term `{term}` must be named"
    assert (
        "never `accepted-as-risk`, `disputed`, or `answered` again" in step
    ), (
        "the three excluded terms must be named explicitly — a positive-only "
        "statement of the allowed set reads as a default, not a prohibition"
    )
    assert "self-author an operator-only disposition" in step, (
        "the prose must name WHY the constraint exists — an unexplained "
        "enumeration is the first thing a later edit relaxes"
    )


def test_no_craft_prose_names_the_discarded_route_vocabulary():
    """The two-route table, and its `advance route` / `reframe route` names, are
    gone from every craft prose file, not just the gauntlet skill itself.

    A record no longer routes to one of two destinations — it either advances or
    it does not, per the advance condition below — so the route vocabulary these
    two phrases named has nowhere left to live.
    """
    offenders = {
        route: [
            p for p in _craft_prose_files() if route in p.read_text()
        ]
        for route in ("advance route", "reframe route")
    }
    offenders = {route: paths for route, paths in offenders.items() if paths}
    assert not offenders, (
        "these craft files still name the discarded route vocabulary: "
        f"{ {route: [str(p.relative_to(CRAFT)) for p in paths] for route, paths in offenders.items()} } "
        "— a record now advances when no Critical carries a final `revise`, with "
        "no second destination to route to"
    )


def test_advance_condition_is_no_critical_carries_final_revise():
    """The route rule's replacement: one condition, not two destinations.

    Stated any other way — a threshold, a vote, a majority — the condition is
    reinvented per reader. Pinned as the literal test for it: does any Critical's
    FINAL disposition read `revise`.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert (
        "advances when no Critical carries a final disposition of `revise`"
    ) in step, (
        "the resolution step must state the advance condition in exactly these "
        "terms — a record advances when no Critical carries a final disposition "
        "of `revise`"
    )
    assert "There is no round cap" in step, (
        "the advance condition must state there is no round cap — operator "
        "overrides are what ends a revising record, not a limit on rounds"
    )
    assert "operator overrides are the termination guarantee" in step, (
        "the step must name operator overrides as the termination guarantee, "
        "given there is no round cap"
    )


def test_revise_round_and_run_are_both_defined_and_distinct():
    """`round` and `run` cannot be used interchangeably — a run is the whole
    invocation; a round is one adjudication cycle inside it. Conflating the two
    is exactly the internal contradiction a prior gauntlet review found here.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert (
        "A **revise round** is one adjudication cycle inside a single gauntlet "
        "invocation"
    ) in step, (
        "the resolution step must define a revise round as one adjudication "
        "cycle inside a single gauntlet invocation"
    )
    assert "A **run** is one invocation of" in step, (
        "the resolution step must define a run as one invocation of the skill, "
        "distinct from a round"
    )
    assert "re-runs only the passes that raised the surviving" in step, (
        "a revise round must be stated to re-run only the passes that raised "
        "the surviving `revise` Criticals — not the full roster"
    )


def test_accepted_tail_runs_per_round_withholding_only_the_advance():
    """A surviving `revise` must not withhold the writes this round already
    earned — only the flip. Otherwise a record mid-round loses `resolved` edits
    it already has, every time adjudication continues.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert (
        "Each revise round runs the full accepted tail" in step
        or "Every revise round runs the full accepted tail" in step
    ), (
        "the resolution step must state that each revise round runs the full "
        "accepted tail — that round's edits and provenance, not just the final "
        "one"
    )
    assert "land atomically before the round ends" in step, (
        "a round's `resolved` edits and provenance must be stated to land "
        "atomically before the round ends"
    )
    assert (
        "a surviving `revise` withholds only the advance, never the writes"
    ) in step, (
        "the step must state plainly that a surviving `revise` withholds only "
        "the advance — the writes already happened, round by round"
    )


def test_advancing_may_not_be_evaluated_while_a_critical_remains_answered():
    """`answered` is not a final disposition, so a advance check that reads it
    anyway is reading a disposition that has not happened yet.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "not yet a final disposition" in step, (
        "the step must state that an `answered` Critical is not yet a final "
        "disposition — it is a request for re-adjudication, not an outcome"
    )
    assert (
        "Advancing may not be evaluated while any Critical remains at `answered`"
        in step
    ), (
        "the step must forbid evaluating the advance condition while any "
        "Critical is still at `answered`"
    )


_GAUNTLET_STATUS_WRITE_RE = re.compile(r"--status\s+(\w+)")


def test_no_gauntlet_authored_write_sets_superseded_or_dropped():
    """No gauntlet outcome sets a record to `superseded` or `dropped`, in either
    mode — both statuses keep their existing (non-gauntlet) uses.

    A bare grep for the words `superseded` / `dropped` would also flag the
    predecessor-supersession write ("Supersession's predecessor write survives,
    decoupled from activation"), which is a legitimate, spec-preserved mechanism
    unrelated to this rule: it never sets a status on the record under review, and
    it does not come from a Critical's disposition. This pins the two specific
    command strings a disposition outcome would have written instead — the
    predecessor write's own placeholder (`<predecessor-adr-id>`) is untouched.
    """
    text = GAUNTLET.read_text()
    for offending_write in (
        "<spec-id> --status superseded",
        "<adr-id> --status dropped",
    ):
        assert offending_write not in text, (
            f"gauntlet/SKILL.md must not carry {offending_write!r} — no gauntlet "
            "outcome may set a record to `superseded` or `dropped`"
        )
    # The legitimate predecessor-supersession write must survive this sweep
    # unharmed — it is what proves the pin above is not a bare word ban.
    assert "<predecessor-adr-id> --status superseded --related adr=<adr-id>" in text, (
        "the predecessor-supersession write is a legitimate, non-gauntlet-outcome "
        "use of `superseded` and must still be present — this sweep bans two "
        "specific command strings, not the word"
    )


# --- the discard route stays deleted in the SIBLING skills too ---

# Files that describe the gauntlet from outside it. gauntlet/SKILL.md itself is
# deliberately excluded: it has legitimate, separately-pinned uses of the
# supersession vocabulary (the adr status vocab, the escalation table, and the
# predecessor-supersession write), and its own outcome vocabulary is pinned by
# `test_no_gauntlet_authored_write_sets_superseded_or_dropped` and
# `test_revise_disposition_withholds_advance_rather_than_superseding`.
_SIBLING_DESCRIBERS = (BRAINSTORM, DISTILL, PLAN, PLANNER, PREMISE_ATTACKER)

# A paragraph is talking about a gauntlet disposition if it names `revise` or the
# premise pass...
_DISPOSITION_TALK_RE = re.compile(r"revise|premise pass", re.IGNORECASE)
# ...and it is restating the deleted discard route if it also reaches for the
# supersession vocabulary in the same breath.
_DISCARD_TALK_RE = re.compile(r"supersed|successor", re.IGNORECASE)


def _paragraphs(path: Path) -> list[str]:
    return [" ".join(block.split()) for block in path.read_text().split("\n\n")]


def test_no_sibling_skill_restates_the_discard_route_in_revise_vocabulary():
    """The discard route is gone everywhere, not just in the gauntlet's own file.

    A `revise` disposition withholds the advance and starts another round; it never
    supersedes the record under review and never produces a successor record. The
    gauntlet's own absence sweep is scoped to gauntlet/SKILL.md, so every sibling
    that describes the gauntlet from outside — brainstorm's status lifecycle,
    distill's clustering rule, plan's plannability gate, the planner agent's
    provisional-plan caveat — was free to keep restating the deleted route in the
    new vocabulary. Three of them did.

    The rule this pins: no paragraph of a sibling may talk about a gauntlet
    disposition and about supersession/successors at the same time. They are
    unrelated mechanisms, and every observed drift came from a sentence that mixed
    them. A paragraph with a legitimate need for both (an ADR superseding an ADR,
    say) splits into two.
    """
    offenders = []
    for path in _SIBLING_DESCRIBERS:
        for para in _paragraphs(path):
            if _DISPOSITION_TALK_RE.search(para) and _DISCARD_TALK_RE.search(para):
                offenders.append(f"{path.relative_to(CRAFT)}: {para[:180]}")
    assert not offenders, (
        "a gauntlet `revise` disposition never supersedes the record under review "
        "and never yields a successor — these paragraphs restate the deleted "
        "discard route in the new vocabulary:\n  " + "\n  ".join(offenders)
    )


def test_the_planner_caveat_sends_the_operator_to_a_revise_round_not_a_successor():
    """The planner's returned-summary blockquote is the copy an operator reads.

    It told them a gauntlet finding voids the plan and restarts planning against a
    successor spec. No gauntlet outcome makes that write: the spec stays `draft`
    and is revised in place, so the plan is provisional against a spec that may
    CHANGE, not one that may be replaced.
    """
    caveat = _section(
        PLANNER.read_text(),
        "> Spec written at `draft`",
        "This is not a formality.",
        why="planner.md must carry the provisional-plan caveat blockquote",
    )
    # Strip the blockquote markers before flattening — a pinned sentence must
    # survive a line wrap inside the quote, not just outside it.
    flat = _flat(caveat.replace("\n>", "\n"))
    assert "the spec is revised in place and stays `draft`" in flat, (
        "the caveat must tell the operator what a surviving `revise` actually "
        "does — the spec is revised in place, never replaced"
    )
    assert "re-check this plan against the revised spec" in flat, (
        "the caveat must name the operator's actual next step: re-check the plan "
        "against the revised spec, not restart against a successor"
    )
    for banned in ("successor spec", "this plan is void"):
        assert banned not in flat, (
            f"the caveat must not claim {banned!r} — planning does not restart "
            "against a replacement spec, because no replacement is written"
        )


# --- the skill description is loaded at trigger time and must not lie ---


def _description_lead(text: str) -> str:
    """The `description:` block's leading paragraph — everything before TRIGGER.

    This is the text a harness loads into an agent's context when the skill
    triggers, so a false promise here is read before any of the body is.
    """
    return _section(
        text,
        "description: >",
        "TRIGGER when:",
        why="gauntlet/SKILL.md must carry a frontmatter `description:` block",
    )


def test_the_skill_description_does_not_promise_an_adr_activation_it_never_writes():
    lead = _flat(_description_lead(GAUNTLET.read_text()))
    assert "an adr to `active`" not in lead, (
        "the description must not promise it flips an adr to `active` — the body "
        "says 'Activation is not this skill's job', and the description is the "
        "half that gets loaded into an agent's context at trigger time"
    )
    assert "a draft adr stays `draft`" in lead, (
        "the description must state the adr outcome it actually writes: none"
    )
    assert "distill activates it" in lead, (
        "the description must name distill as the writer of the adr's activation, "
        "so an agent reading only the description knows where the flip lives"
    )


def test_security_criticals_are_exempt_from_compression():
    """Compression is a convenience; it must not eat the costliest finding class.

    A one-clause summary of a security finding reads as reasonable no matter what
    it elides, so the operator cannot tell what they accepted.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "**Security Criticals are never compressed.**" in step, (
        "the resolution step must exempt attacker-lens Criticals from compression"
    )
    assert "the actual proposed edit text" in step, (
        "a security Critical's row must carry the real edit text, not a clause "
        "standing in for it — the clause is exactly what hides the change being "
        "accepted"
    )


def test_important_and_minor_compress_to_count_and_theme():
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "a count plus a one-line theme" in step, (
        "Important and Minor findings must compress in the default output — they "
        "take no disposition, and enumerating them is what makes the deliverable "
        "too long to read"
    )


def test_resolved_edits_are_drafted_before_the_deliverable_is_presented():
    """Acceptance must be able to apply text that already exists.

    If the edit is composed after acceptance, the operator accepted a promise, and
    what lands in the record is unreviewed.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "**Draft every `resolved` edit in full before you present.**" in step, (
        "the resolution step must require every `resolved` edit be written before "
        "the deliverable is presented"
    )
    assert "acceptance applies that text verbatim" in step, (
        "acceptance must apply the drafted text verbatim — anything else means the "
        "operator approved a summary and the record received something else"
    )


def test_full_detail_is_on_request_and_binds_to_the_shared_finding_shape():
    """The compact default is only safe because the full detail is one ask away.

    And when it is printed it must read in the shape defined in one place. Without
    the pointer in that same paragraph, an editor tidying the sentence drops the
    binding and the detail view reverts to shorthand-first prose with no test to
    catch it.
    """
    paragraphs = [
        _flat(p)
        for p in GAUNTLET.read_text().split("\n\n")
        if "**Full finding detail is not printed by default.**" in _flat(p)
    ]
    assert paragraphs, (
        "gauntlet/SKILL.md must state that full finding detail is withheld from the "
        "default deliverable and available on request"
    )
    for paragraph in paragraphs:
        assert "show me the detail on C2" in paragraph, (
            "the detail-on-request instruction must show the request form, keyed to "
            f"a Critical id; got: {paragraph!r}"
        )
        assert "_shared/council.md" in paragraph, (
            "the detail view must point at _shared/council.md for the per-finding "
            "shape, the same way adjudication defers to it for the "
            f"speculative-Critical downgrade rule; got: {paragraph!r}"
        )
        assert "How a finding reads" in paragraph, (
            "the detail view must name the 'How a finding reads' section, not just "
            "the file — a bare file reference survives that section being renamed "
            f"away underneath it; got: {paragraph!r}"
        )

    shared = GAUNTLET.parent.parent / "_shared" / "council.md"
    assert "How a finding reads" in shared.read_text(), (
        "_shared/council.md must keep the 'How a finding reads' heading gauntlet "
        "points at, or the cross-file reference dangles"
    )


def test_zero_critical_runs_still_gate_on_acceptance():
    """A clean sweep is a result to accept, not a licence to advance unattended."""
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "#### Zero Criticals is still a decision" in step, (
        "the resolution step must cover the zero-Critical run explicitly"
    )
    assert "still gates on operator acceptance" in step, (
        "a run with no Criticals must still present the deliverable and wait — a "
        "gauntlet never advances a record on its own reading of a clean sweep"
    )
    assert "the compressed Important and Minor summary" in step, (
        "a clean-Critical run must still present the compressed Important and Minor "
        "summary — dropping it presents a run with real findings as one with none"
    )
    assert "no per-Critical table" in step, (
        "what a zero-Critical run omits is the per-Critical table specifically, "
        "since there are no rows for it to hold"
    )


def test_overrides_apply_in_one_round_trip():
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "**one round-trip**" in step, (
        "overrides must be collected from one reply and applied together — walking "
        "back through the table finding by finding is the interrogation this step "
        "replaces"
    )


def test_full_post_override_table_is_echoed_before_the_tail():
    """A one-line outcome cannot show a misapplied override.

    "dispute C3" recorded against C4 changes nothing that one line displays, and
    the audit trail it lands in is permanent.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "**Echo the full post-override table.**" in step, (
        "the resolution step must re-render the complete disposition table after "
        "any override"
    )
    assert "**not just the outcome line**" in step, (
        "the echo must be pinned as the full table rather than the outcome line "
        "alone"
    )
    assert "as the last thing before the accepted tail executes" in step, (
        "the echo must be positioned as the final output before the tail runs — an "
        "echo printed earlier can be followed by another change the operator never "
        "saw"
    )


def test_override_with_an_unknown_id_is_rejected_not_guessed():
    """An id outside the presented range means the operator and the agent disagree
    about what is on the table. Guessing settles that disagreement silently, in
    favor of the guess, straight into a permanent record.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "**An override naming an id outside the presented range is rejected.**" in step, (
        "the resolution step must reject an override naming an unpresented id"
    )
    assert "**Never map an unknown id onto the id you think was meant.**" in step, (
        "the rejection must forbid inferring the intended id — the re-ask is the "
        "only safe resolution"
    )


def test_override_without_a_reason_is_asked_back_never_drafted():
    """`accepted-as-risk` and `disputed` are the one path by which agent-authored
    text could enter the permanent audit trail as the operator's judgment.

    The vocabulary requires a reason and the agent may not write one, so an
    override naming either disposition without one has exactly one safe outcome:
    ask, and record nothing until it is answered.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "**An override with no reason is incomplete.**" in step, (
        "the resolution step must cover an override naming `accepted-as-risk` or "
        "`disputed` without supplying a reason"
    )
    assert "ask for the reason and record nothing until they give it" in step, (
        "the reasonless override must be asked back before anything is recorded — a "
        "disposition written now is a disposition written without its reason"
    )
    assert "never with the reason slot empty" in step, (
        "neither a drafted reason nor an empty one may be recorded; both put words "
        "in the operator's mouth in a permanent trail"
    )


def test_override_off_resolved_withdraws_that_rows_drafted_edit():
    """Every `resolved` row's edit is drafted before the deliverable is presented.

    So an override that moves a row OFF `resolved` is an override against text
    that already exists — and a diff assembled before the override carries the one
    change the operator explicitly declined into a record about to advance.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "**An override off `resolved` withdraws that row's drafted edit.**" in step, (
        "the override rules must state that overriding a Critical off `resolved` "
        "withdraws the edit drafted for it — nothing else in the step retracts an "
        "edit the operator declined"
    )
    assert "removes that row's edit from `$EDITS`" in step, (
        "the withdrawal must name the accepted set the tail actually writes, so "
        "the rule is applied to the diff rather than to the table's rendering"
    )


def test_override_into_resolved_re_presents_its_newly_drafted_edit():
    """The re-present fires on a revise-presence change — which an override into
    `resolved` need not cause, on a run another `revise` row still holds.

    That row's edit was never drafted (only proposals of `resolved` are), so
    accepting straight through would leave the edit composed after acceptance.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert (
        "**An override *into* `resolved` re-presents too, whatever the advance "
        "decision does.**" in step
    ), (
        "the override rules must cover an override INTO `resolved`, whose edit was "
        "never drafted — the revise-presence trigger alone misses it whenever "
        "another `revise` row holds the advance decision unchanged"
    )
    assert "a newly drafted edit is a change" in step, (
        "the re-present cap must be reconciled with this case explicitly, or the "
        "cap reads as forbidding the very re-present this rule requires"
    )


def test_revise_presence_changing_override_re_presents_before_any_write():
    """Whether any Critical still carries `revise` is the one thing an override
    can change that the operator did not directly name — so it goes back for
    acceptance before anything is written.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert (
        "**An override changing revise-presence re-presents once.**" in step
    ), (
        "an override that changes whether any Critical still carries `revise` "
        "must re-present the revised recommendation"
    )
    assert "**before anything is written**" in step, (
        "the re-present must precede every write — a revise-presence change "
        "discovered after the tail has started is a record already flipped the "
        "wrong way"
    )
    assert (
        "**The cap is one re-present per revise-presence change, not one per "
        "run.**" in step
    ), (
        "the step must say what happens when the reply to a re-present changes "
        "revise-presence again — read as a per-run cap, the second change would "
        "be written on a advance decision the operator never accepted"
    )
    assert "Each re-presented deliverable carries its round number" in step, (
        "a re-presented deliverable must carry its round number — with no round "
        "cap, the count is the only signal distinguishing convergence from a "
        "directionless loop"
    )


def test_resolution_names_its_escalation_points_in_the_execute_style():
    """Named escalation points are what make a future unattended mode a re-route
    table rather than a redesign. No unattended mode ships here — the naming is
    the whole of it.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "#### Escalation points" in step, (
        "the resolution step must collect its escalation points under a heading"
    )
    assert "_shared/execute.md" in step, (
        "the escalation-point naming must cite the shared execute contract it "
        "follows, rather than inventing a second convention for the same idea"
    )
    assert "Two modes, one procedure" in step, (
        "the citation must name the section, not just the file — a bare file "
        "reference survives that section being renamed away underneath it"
    )
    for point in _ESCALATION_POINTS:
        assert point in step, (
            f"escalation point {point!r} must be named in the resolution step"
        )
    assert "**No unattended mode ships here**" in step, (
        "the step must state that no unattended caller is wired — naming the "
        "escalation points is not the same as authorizing an auto-accept path"
    )


def test_failed_write_escalation_row_does_not_promise_an_advanced_record_under_review():
    """No write is ordered after a flip of the record under review, in either mode.

    The adr tail no longer flips the record it is reviewing — distill owns that
    now — and the spec tail's flip is its last write, so nothing can fail "after
    the flip" and leave the record under review advanced. The row previously
    qualified its `draft` claim for exactly that case and pointed at the adr
    tail's supersession write as the write that does it. That write flips the
    PREDECESSOR, a different record; reading it as the reviewed record is what
    the stale qualifier invited. An agent trusting it would report the reviewed
    record as possibly `active` when this skill can no longer make it so.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "unless the failure fell after the status flip" not in step, (
        "the `failed-write report` row still qualifies its `draft` claim for a "
        "post-flip failure of the record under review — no write is ordered after "
        "such a flip in either mode now that the adr tail does not flip at all"
    )
    row = next(
        line for line in _resolution_step(GAUNTLET.read_text()).splitlines()
        if "**failed-write report**" in line
    )
    assert "**predecessor**" in row and "stays `draft`" in row, (
        "the `failed-write report` row itself must name the one partial state a "
        "failed adr tail can genuinely leave behind — a predecessor already "
        "flipped `superseded` while the record under review stays `draft`. "
        "Asserting over the whole step matches the word elsewhere and pins nothing"
    )
    assert (
        "or whenever a prescription or edit not in the presented table was newly "
        "drafted" in step
    ), (
        "the `route-change re-present` row must cover the other re-present the "
        "override rules require — an override into `resolved` or `revise` on a "
        "run whose revise-presence never moved — or the row names a trigger "
        "narrower than the rule it restates"
    )


def test_shared_accepted_tail_does_not_claim_a_status_flip_in_every_mode():
    """The shared tail is shared, so it must not assert a step only one mode runs.

    Step 2 named the status flip as the universal second step, with the
    predecessor supersession write as the mode-specific extra. That inverted the
    truth after this change: the spec tail flips, the adr tail does not flip the
    record under review at all, and the write the adr tail does run targets a
    different record. A shared step asserting a flip both modes perform
    contradicts the per-mode adr section two hundred lines below it.
    """
    step = _flat(_resolution_step(GAUNTLET.read_text()))
    assert "2. **Then the status flip** — and, where the mode has one," not in step, (
        "the shared accepted tail still names the status flip as the universal "
        "second step; it must name the mode's remaining writes instead, since "
        "the adr tail flips nothing it is reviewing"
    )


def test_resolution_ends_at_the_accepted_tail_anchor():
    """The anchor is the seam between deciding and writing.

    Everything before it is presentation and acceptance; everything after it runs
    only once the operator has accepted. Naming it keeps the two halves editable
    independently of each other.
    """
    step = _resolution_step(GAUNTLET.read_text())
    assert ACCEPTED_TAIL_ANCHOR in step, (
        f"the resolution step must end with a {ACCEPTED_TAIL_ANCHOR!r} subsection — "
        "the named seam between the acceptance gate and the writes that follow it"
    )
    after_anchor = step[step.index(ACCEPTED_TAIL_ANCHOR) + len(ACCEPTED_TAIL_ANCHOR):]
    assert "\n#### " not in after_anchor, (
        f"{ACCEPTED_TAIL_ANCHOR!r} must be the LAST subsection of the resolution "
        "step — a subsection after it would run after acceptance while reading as "
        "part of the decision"
    )


# --- the accepted tail: one atomic write, then the flip, fail-closed ---

# The spec-mode tail. Scoped like the adr-mode section so a phrase that happens to
# appear in the shared step cannot satisfy a pin about what the spec tail states.
SPEC_TAIL_HEADER = "### 6. Stamp and advance"

# The adr-mode tail's ordered write sequence. Its own section because the order is
# the contract — a reader who finds only the individual commands, scattered across
# the sections that motivate them, has no way to know which runs first.
ADR_TAIL_HEADER = "### The accepted tail, in adr terms"

_ATOMIC_WRITE = "a single `lore record update --diff` write"
_ADR_LESSON_CREATE = "lore record create --kind lesson"
_ADR_LESSON_EDGE = "--related adr=<adr-id>"
_ADR_COUNTS_ANNOTATION = "--annotation gauntlet="

# The adr's half of the shared atomic write: the `--diff` body write carrying the
# accepted `resolved` edits. `--diff` and `--annotation` apply inside one
# read-modify-write, so both ride one invocation and a rejected hunk leaves body
# and annotation alike unwritten.
_ADR_EDITS_WRITE = "lore record update <adr-id> --diff"

# The spec's half of the same shared atomic write. The spec tail's `--diff` write
# carries all three of its payloads — the accepted edits, the provenance stamp, and
# the `## Gauntlet` detail section — so the spec mode needs its own command form and
# its own pin, symmetric with the adr one above.
_SPEC_EDITS_WRITE = "lore record update <spec-id> --diff"

# The two treatments the persisted-detail path runs before either payload is
# assembled. Both bind at the point the text enters `$EDITS` / `$DETAIL`: the
# retained finding detail is subagent-authored text the compact deliverable never
# printed, and this write is the only review it ever gets before the vault has it.
SHARED_EXECUTE = SKILLS_DIR / "_shared" / "execute.md"
RECEIVING_REVIEW = SKILLS_DIR / "receiving-code-review" / "SKILL.md"
_SCRUB_SOURCE_REF = "_shared/execute.md"
_SCRUB_NAME = "credential-pattern scrub"
_SCRUB_ANCHOR = "Credential-pattern scrub"
_RECEIVING_REVIEW_REF = "skills/receiving-code-review/SKILL.md"
_EVIDENCE_MARKER = "retained review evidence"

# The auditor-facing half of the scrub: evidence names a location, not a value.
# Pinned as the whole rule rather than as the bare token `file:line`, which prose
# saying the exact opposite would also contain.
_EVIDENCE_CITATION_RULE = "cut down to a `file:line` citation"

# What each per-mode tail must name to inherit the shared treatments. The tails
# restate their own deltas and defer everything else upward, so a tail that defers
# only sequence and failure behavior is a tail that writes unscrubbed text.
_PRE_WRITE_TREATMENTS = "pre-write scrub and marker"

# Fragments of the scrub's own regex list. They belong to exactly one file; any of
# them appearing in gauntlet/SKILL.md means the list was forked rather than reused,
# and the fork is what goes stale while the original gains patterns.
_SCRUB_PATTERN_FRAGMENTS = ["AKIA", "ghp_", "glpat-", "PRIVATE KEY"]


def _accepted_tail(text: str) -> str:
    """The shared accepted tail — everything from its anchor to the end of the step.

    The anchor is pinned as the step's last subsection, so "to the end of the step"
    is exactly the tail's body.
    """
    step = _resolution_step(text)
    assert ACCEPTED_TAIL_ANCHOR in step, (
        f"the resolution step must carry a {ACCEPTED_TAIL_ANCHOR!r} subsection"
    )
    return step[step.index(ACCEPTED_TAIL_ANCHOR):]


def _spec_tail(text: str) -> str:
    """The spec tail, bounded by the adr-mode heading that follows it.

    Bounded on that exact heading rather than on any `## ` line: the tail quotes a
    literal `## Gauntlet` body section inside a fenced block, and a generic bound
    would read that example as the end of the section.
    """
    return _section(text, SPEC_TAIL_HEADER, "\n" + ADR_SECTION_HEADER)


def _adr_tail(text: str) -> str:
    return _section(_adr_mode_section(text), ADR_TAIL_HEADER, "\n### ")


def test_accepted_tail_is_one_atomic_write_then_the_modes_remaining_writes():
    """Edits and stamp land together, or not at all — and the flip goes last.

    A record carrying half its accepted edits is a record nobody reviewed, and a
    flip that runs ahead of the edits advances a record whose accepted edits are
    still hypothetical.
    """
    tail = _flat(_accepted_tail(GAUNTLET.read_text()))
    assert "**One atomic write.**" in tail, (
        "the accepted tail must open with the single-write step — one write is what "
        "makes the accepted set all-or-nothing"
    )
    assert _ATOMIC_WRITE in tail, (
        "the tail must name the write form: every `resolved` edit plus the "
        f"provenance stamp apply as {_ATOMIC_WRITE} — not one write per Critical, "
        "and not edits now with the stamp to follow"
    )
    assert "**Then the mode's remaining writes**" in tail, (
        "the tail's second step must be the mode's remaining writes — the spec "
        "tail's status flip or the adr tail's predecessor supersession write. "
        "Naming a status flip both modes perform contradicts the adr tail, which "
        "flips nothing it is reviewing"
    )
    assert "only after that write has succeeded" in tail, (
        "the second step must be conditioned on the atomic write succeeding — an "
        "unconditional flip is the failure mode the ordering exists to prevent"
    )
    assert tail.index(_ATOMIC_WRITE) < tail.index(
        "**Then the mode's remaining writes**"
    ), (
        "the atomic write must be stated before the mode's remaining writes — the "
        "tail is an ordered sequence, and prose order is the only thing carrying "
        "that order"
    )


def test_accepted_tail_is_fail_closed():
    """A half-applied acceptance is not a state an agent may resolve on its own.

    Retrying, re-cutting the hunks, or flipping anyway all turn a visible failure
    into an invisible one — on a record that is about to advance.
    """
    tail = _flat(_accepted_tail(GAUNTLET.read_text()))
    assert "**On any rejected hunk or failed write, nothing further runs.**" in tail, (
        "the tail must stop dead on a failed write — no flip, no supersession "
        "write, no retry"
    )
    assert "The record stays `draft`" in tail, (
        "a failed tail must leave the record `draft` — the one status from which "
        "the run can simply be repeated"
    )
    assert "report the partial state explicitly" in tail, (
        "the agent must report which writes landed and which did not; a failure "
        "reported as 'something went wrong' leaves the operator to diff the record "
        "themselves"
    )
    assert "`failed-write report`" in tail, (
        "the tail must name the `failed-write report` escalation point it lands on, "
        "so the stop is the same named hand-back the escalation table lists"
    )


def test_accepted_tail_does_not_re_confirm_once_it_is_running():
    """Acceptance was the gate.

    A second confirmation inside the tail trains the operator to wave through the
    one prompt that would have mattered.
    """
    tail = _flat(_accepted_tail(GAUNTLET.read_text()))
    assert "**A successfully executing tail asks nothing.**" in tail, (
        "the tail must forbid mid-tail re-confirmation on the success path"
    )


def test_provenance_stamp_separates_proposals_accepted_from_overrides():
    """The audit trail's whole value is whose judgment each disposition was.

    A stamp that records only the disposition values loses the authorship the
    disposition rules exist to protect.
    """
    tail = _flat(_accepted_tail(GAUNTLET.read_text()))
    assert "distinguishes accepted-from-proposal dispositions from operator overrides" in tail, (
        "the provenance stamp must separate what the operator accepted as proposed "
        "from what they overrode"
    )
    assert "quotes the `C1`…`Cn` ids" in tail, (
        "the stamp must quote the Critical ids, so an auditor can line each "
        "disposition up against the table the operator actually saw"
    )


def test_provenance_split_is_derived_structurally_not_recalled():
    """`accepted-as-risk` and `disputed` cannot be agent-proposed.

    So their presence *is* the override, derivable from the record itself — which
    is what keeps the stamp from being the agent's recollection of the round-trip.
    """
    tail = _flat(_accepted_tail(GAUNTLET.read_text()))
    assert "**operator overrides by construction**" in tail, (
        "the tail must state the structural rule: a Critical carrying "
        "`accepted-as-risk` or `disputed` was overridden by definition, because the "
        "agent may not propose either"
    )
    assert "never from memory" in tail, (
        "the accepted/overridden split must be derived rather than recalled — a "
        "remembered split is an assertion, and the audit trail cannot tell the "
        "difference"
    )


def test_persisted_detail_runs_through_the_shared_credential_scrub():
    """The retained detail is the one payload nobody reads before it is permanent.

    Both tails write full, verbatim finding text into a git-backed vault that syncs
    to a whole team, and the compact deliverable is designed to keep most of that
    text off the operator's screen. A gauntlet run against a codebase holding a
    committed credential can quote that value as its evidence, so this scrub is the
    only thing between it and a second, durable home nobody was shown.
    """
    text = GAUNTLET.read_text()
    tail = _flat(_accepted_tail(text))

    assert _SCRUB_NAME in tail and _SCRUB_SOURCE_REF in tail, (
        "the shared accepted tail must send the persisted text through the "
        f"{_SCRUB_NAME!r} in {_SCRUB_SOURCE_REF} before the write"
    )
    assert "$EDITS" in tail and "$DETAIL" in tail, (
        "the scrub must bind where the text enters `$EDITS` / `$DETAIL` — a scrub "
        "named anywhere downstream of that is a scrub that runs after the write"
    )
    assert _EVIDENCE_CITATION_RULE in tail, (
        "a finding quoting a literal secret as evidence must have it cut down to a "
        f"`file:line` citation ({_EVIDENCE_CITATION_RULE!r}) — retaining the value "
        "is the whole exposure, and naming `file:line` without saying the value "
        "goes is a rule prose meaning the opposite would also satisfy"
    )
    assert _SCRUB_ANCHOR in SHARED_EXECUTE.read_text(), (
        f"{_SCRUB_SOURCE_REF} must still carry the scrub the gauntlet points at; a "
        "reference to a rule that moved is a reference to no rule at all"
    )
    for fragment in _SCRUB_PATTERN_FRAGMENTS:
        assert fragment not in text, (
            "the scrub's pattern list is reused by reference, never forked into "
            f"gauntlet/SKILL.md — found {fragment!r}, and two copies drift, with "
            "the stale one missing what the maintained one catches"
        )
    for mode, mode_tail in (("spec", _spec_tail(text)), ("adr", _adr_tail(text))):
        assert _PRE_WRITE_TREATMENTS in _flat(mode_tail), (
            f"the {mode} tail must inherit the shared pre-write treatments by name "
            f"({_PRE_WRITE_TREATMENTS!r}) — a per-mode tail that defers only "
            "sequence and failure behavior reads as a tail with no scrub"
        )


def test_persisted_detail_is_marked_data_not_instructions():
    """What this tail writes is what a later run reads back as prior art.

    The fact-verification pass checks claims against sibling records in the vault,
    and both persisted targets are exactly that sibling text. Unmarked, retained
    review evidence reads to the next agent as the record's own settled design, and
    a wrong or planted conclusion propagates into a future review as fact.
    """
    text = GAUNTLET.read_text()
    tail = _flat(_accepted_tail(text))

    assert _RECEIVING_REVIEW_REF in tail, (
        "the shared tail must cite the receiving-code-review pattern for the text "
        "it persists, rather than restating a trust scheme of its own"
    )
    assert RECEIVING_REVIEW.exists(), (
        f"{_RECEIVING_REVIEW_REF} must exist — the pointer is the whole mechanism"
    )

    spec_tail = _flat(_spec_tail(text)).lower()
    assert _EVIDENCE_MARKER in spec_tail, (
        "the spec tail's `## Gauntlet` section must carry the marker naming its "
        f"content {_EVIDENCE_MARKER!r}, evaluated as a claim about the spec"
    )
    assert spec_tail.index(_EVIDENCE_MARKER) < spec_tail.index(
        "adversarial spec review"
    ), (
        "the marker must open the retained section — a reader who meets the "
        "findings first has already read them as the spec's own content"
    )
    assert _EVIDENCE_MARKER in _flat(_adr_tail(text)).lower(), (
        "the adr tail's linked `lesson` record carries the same marker — it is the "
        "sibling record a later pass reads, exactly like the spec's section"
    )


def test_spec_tail_retains_full_detail_in_a_gauntlet_body_section():
    """The compact deliverable is only safe because nothing is thrown away.

    The detail the operator did not read still has to be reconstructable by whoever
    reads the advanced spec later.
    """
    tail = _flat(_spec_tail(GAUNTLET.read_text()))
    assert "full consolidated finding detail" in tail, (
        "the spec tail must say the withheld detail is retained, not discarded"
    )
    assert "`## Gauntlet` section appended to the spec body" in tail, (
        "the spec's detail target is a `## Gauntlet` body section — a spec body has "
        "no exhaustive-section contract, so the detail belongs in it"
    )
    assert "part of the same atomic write" in tail, (
        "the detail section must land in the one atomic write, not a second write "
        "after the flip — a `ready` spec missing its own review record is exactly "
        "the artifact the audit trail exists to prevent"
    )


def test_spec_tail_applies_the_accepted_edits_in_the_same_atomic_write():
    """The spec tail enumerates what its one write carries; it must also SHOW it.

    Symmetric with the adr tail's pin: a tail that names a `## Gauntlet` section
    and a `--status ready` flip, and gives a command form only for the flip, is a
    tail an agent can execute by flipping a spec whose accepted edits never landed.
    """
    tail = _spec_tail(GAUNTLET.read_text())
    edits_lines = [line for line in tail.splitlines() if _SPEC_EDITS_WRITE in line]
    assert edits_lines, (
        "the spec tail must carry the shared tail's atomic edits write "
        f"({_SPEC_EDITS_WRITE}) as a command — the only fenced command in the step "
        "being the flip is how a run advances a spec with its edits still unwritten"
    )
    flip_match = _SPEC_ADVANCE_RE.search(tail)
    assert flip_match, "the spec tail must carry the `ready` flip as a command"
    assert tail.index(_SPEC_EDITS_WRITE) < flip_match.start(), (
        "the edits write must precede the status flip — a flip ahead of the edits "
        "advances a spec whose accepted edits are still hypothetical"
    )


def test_spec_tail_flips_on_advance_and_starts_a_new_round_otherwise():
    tail = _spec_tail(GAUNTLET.read_text())
    assert "<spec-id> --status ready" in tail, (
        "the spec tail's advance path must carry the `ready` flip"
    )
    assert "<spec-id> --status superseded" not in tail, (
        "the spec tail must not carry a `superseded` flip — a surviving `revise` "
        "withholds only the advance, it does not route the spec anywhere"
    )
    flat = _flat(tail)
    assert "the spec does not flip" in flat, (
        "the spec tail must state that a surviving `revise` Critical leaves the "
        "spec unflipped"
    )
    assert "Begin the next revise round" in flat, (
        "the spec tail must state that adjudication continues into a new revise "
        "round rather than handing the record anywhere"
    )
    assert "fully formed" in flat, (
        "the advance-path handoff command must be emitted with the real record "
        "id, so the operator can paste it into a fresh session as-is"
    )


def test_adr_full_detail_goes_to_a_linked_lesson_record():
    """The adr body contract is exhaustive, so the detail cannot live in it.

    A linked `lesson` record is the target that keeps the detail without violating
    the four-section contract — and the `related adr=` edge is what makes it
    findable from the decision it reviewed.
    """
    adr_section = _adr_mode_section(GAUNTLET.read_text())
    lesson_lines = [
        line for line in adr_section.splitlines() if _ADR_LESSON_CREATE in line
    ]
    assert lesson_lines, (
        "the adr tail must create a linked `lesson` record for the full finding "
        f"detail ({_ADR_LESSON_CREATE}) — the four-section body cannot hold it"
    )
    for line in lesson_lines:
        assert _ADR_LESSON_EDGE in line, (
            "the lesson record must be created with its `related adr=` edge on the "
            "same command; an edge added by a later write is an edge that a failure "
            f"between the two never writes. Got: {line!r}"
        )
    assert _ANNOTATION_PROVENANCE_SENTENCE in adr_section, (
        "the lesson-record target must not displace the annotation-provenance rule "
        "— provenance still goes to annotations, never the body"
    )


def test_adr_tail_applies_the_accepted_edits_in_the_same_atomic_write():
    """The adr flip is into an immutable state, so the accepted edits precede it.

    What the exhaustive body contract keeps out of an adr is the *provenance and
    detail*, never the accepted content edits — those are edits to the record's own
    four sections, and a sequence that skips them flips a decision to `active` with
    every approved edit dropped.
    """
    tail = _adr_tail(GAUNTLET.read_text())
    edits_lines = [line for line in tail.splitlines() if _ADR_EDITS_WRITE in line]
    assert edits_lines, (
        "the adr tail must carry the shared tail's atomic edits write "
        f"({_ADR_EDITS_WRITE}) — without it the accepted `resolved` edits never "
        "reach the record the operator approved them for"
    )
    for line in edits_lines:
        assert _ADR_COUNTS_ANNOTATION in line, (
            "the edits and the counts annotation must ride ONE invocation — split "
            "across two writes, a rejected hunk leaves an annotation claiming a "
            f"review the body never received. Got: {line!r}"
        )


def test_adr_tail_writes_the_lesson_first_then_the_atomic_write_then_the_predecessor():
    """Order is the whole contract here, and it is chosen for its failure states.

    Lesson-first leaves, at worst, a `draft` adr with an extra record pointing at
    it — discoverable and harmless. The reverse order leaves a counts annotation
    claiming a review whose `lesson` record was never written. Neither write ever
    advances the record under review past `draft` — that edge belongs to distill
    now, not to this tail.
    """
    tail = _adr_tail(GAUNTLET.read_text())
    lesson_idx = tail.index(_ADR_LESSON_CREATE)
    edits_idx = tail.index(_ADR_EDITS_WRITE)
    annotation_idx = tail.index(_ADR_COUNTS_ANNOTATION)
    back_edge_idx = tail.index(_FORWARD_SUPERSESSION_BACK_EDGE)
    assert not _ADR_ADVANCE_RE.search(tail), (
        "the adr tail must not carry a `--status active` flip on the record under "
        "review — that edge belongs to distill now"
    )
    assert lesson_idx < edits_idx < back_edge_idx, (
        "the adr tail's sequence must be: create the `lesson` record, then the "
        "adr's one atomic write, then the predecessor's `superseded` back-edge"
    )
    assert edits_idx < annotation_idx < back_edge_idx, (
        "the counts annotation belongs to that same atomic write, ahead of the "
        "predecessor back-edge"
    )


def test_adr_counts_annotation_covers_the_whole_disposition_vocabulary():
    """A missing slot reads as a zero, and `revise` is the disposition that
    withholds the advance — so an adr whose annotation has no `revise` slot is
    annotated as a clean run even while it is still revising. `answered` is
    excluded from this loop on purpose:
    it is not a final disposition and contributes no term of its own to the
    annotation grammar (see `_FINAL_DISPOSITION_NAMES`).
    """
    flat = _flat(_adr_tail(GAUNTLET.read_text()))
    for name in _FINAL_DISPOSITION_NAMES:
        assert f"<n>-{name}" in flat, (
            f"the counts annotation must carry a `{name}` slot — the annotation is "
            "where an auditor reads the run's disposition counts, and an absent "
            "slot is indistinguishable from a count of zero"
        )


def test_adr_tail_is_fail_closed_and_reports_an_orphaned_lesson():
    """A lesson record left behind by a later failure is not garbage to ignore.

    It is the only trace that a review happened, and the operator cannot act on a
    record they were never told exists.
    """
    flat = _flat(_adr_tail(GAUNTLET.read_text()))
    assert "the adr under review stays `draft`" in flat, (
        "a failure anywhere in the adr tail must leave the record under review "
        "`draft` — nothing in this tail ever advances it, so this holds regardless "
        "of which write failed"
    )
    assert "**never silently abandoned**" in flat, (
        "an orphaned lesson record must be surfaced, not dropped"
    )
    assert "report the orphaned `lesson` record to the operator" in flat, (
        "the report must name the orphan explicitly — the operator decides whether "
        "to re-run or delete it"
    )


def test_adr_tail_states_the_predecessor_write_failure_branch():
    """A failure at the third write leaves the predecessor stranded `active`.

    Unlike the old flip-then-supersede order, the record under review is never
    made immutable by this failure — it is still `draft`, so the report must say
    what actually needs closing: the predecessor, not the successor.
    """
    flat = _flat(_adr_tail(GAUNTLET.read_text()))
    assert "the predecessor still `active`" in flat, (
        "the tail must state that a failure at the predecessor write leaves it "
        "still `active`, unlinked from an already-accepted successor"
    )
    assert _FORWARD_SUPERSESSION_BACK_EDGE in flat, (
        "the failure report must hand back the single write that closes the gap, "
        "not merely describe it"
    )


def test_neither_mode_routes_a_surviving_revise_to_a_terminal_status():
    """A surviving `revise` withholds the advance in both modes — it does not
    route a spec to `superseded` or an adr to `dropped`; both statuses keep
    only their pre-existing, non-gauntlet uses.
    """
    text = GAUNTLET.read_text()
    assert "<spec-id> --status superseded" not in _spec_tail(text), (
        "the spec tail must not take a spec to `superseded` on a surviving "
        "`revise` — it withholds the advance and starts another round instead"
    )
    adr_flat = _flat(_adr_mode_section(text))
    assert "the adr under review stays `draft`" in adr_flat, (
        "the adr-mode section must state that a surviving `revise` leaves the "
        "record under review `draft` — not `dropped`, which a draft adr never "
        "went `active` to supersede a predecessor from in the first place"
    )


def test_adr_tail_says_where_the_per_id_dispositions_land():
    """An annotation is a key/value; it cannot hold the shared stamp's id detail.

    The shared tail requires the stamp to quote `C1`…`Cn`. For an adr that has to
    land somewhere, or the mode silently drops half the stamp contract.
    """
    flat = _flat(_adr_tail(GAUNTLET.read_text()))
    assert "`C1`…`Cn` dispositions" in flat, (
        "the adr tail must say where the per-id dispositions land — an annotation "
        "carries counts, so the ids belong in the linked `lesson` record"
    )


def test_adr_lesson_record_holds_the_operator_reason_text_and_the_override_markers():
    """The shared stamp requires more per Critical than a bare disposition.

    It requires the `accepted-as-risk` / `disputed` reason in the operator's own
    words and the from-proposal-vs-override marker per id. An annotation is a
    key/value, so the adr mode has to give both a stated home or the mode drops
    the operator's own reasons from the trail of an immutable decision.
    """
    flat = _flat(_adr_tail(GAUNTLET.read_text()))
    assert "marked from-proposal or operator-override" in flat, (
        "the adr tail must say where the per-id from-proposal-vs-override marker "
        "lands — the counts annotation carries totals, not the per-id split"
    )
    assert "quoted in the operator's own words" in flat, (
        "the adr tail must give the `accepted-as-risk` / `disputed` reason text a "
        "home in the `lesson` record; written nowhere, the operator's stated reason "
        "for living with a risk is absent from a decision nothing can edit later"
    )


def test_adr_atomic_write_runs_even_with_no_resolved_edits():
    """A run with zero `resolved` Criticals still has provenance to write.

    The counts annotation rides the edits write, and an adr's exhaustive body will
    never hold provenance itself — so skipping the write on an empty diff flips the
    decision `active` with no record of the review at all.
    """
    flat = _flat(_adr_tail(GAUNTLET.read_text()))
    assert (
        "This write runs on every accepted run, including one with zero `resolved` "
        "Criticals" in flat
    ), (
        "the adr tail must state that the atomic write runs even when `$EDITS` is "
        "empty — described only for a non-empty diff, the counts annotation never "
        "lands on a clean run"
    )


def test_adr_tail_runs_identically_on_a_surviving_revise():
    """The first two writes land the same way on every accepted run — there is no
    flip left to withhold, so a surviving `revise` changes nothing about the tail
    itself, only whether another revise round follows it.
    """
    tail = _adr_tail(GAUNTLET.read_text())
    assert "<adr-id> --status dropped" not in tail, (
        "the adr tail must not carry a `dropped` flip on the record under review — "
        "a surviving `revise` only starts another round"
    )
    flat = _flat(tail)
    assert "land identically on either outcome" in flat, (
        "the adr tail must state that the lesson record and the atomic write land "
        "identically whether or not a Critical's final disposition is `revise`"
    )


def test_orphaned_lesson_report_names_the_recovery_query():
    """"Discoverable" is a claim the operator has to be able to act on.

    A reverse-edge lookup they have to invent is one they will not run.
    """
    flat = _flat(_adr_tail(GAUNTLET.read_text()))
    assert 'kind:lesson related-adr:"<adr-id>"' in flat, (
        "the orphan report must name a query that matches the edge the tail wrote. "
        "The edge is written `--related adr=<adr-id>` and stored verbatim, so the "
        "query has to name the same `<adr-id>` — quoted, because that id carries a "
        "`/` and an unquoted `/` is a KQL parse error"
    )
    assert _ADR_LESSON_EDGE in flat, (
        "the query and the edge must appear in the same paragraph spelled the same "
        "way — the failure this lookup exists to catch is the two drifting apart"
    )
    assert "projects as it writes" in flat, (
        "the lookup reads the `lesson` record's own forward edge, which the create "
        "projects on write — so the report must not attach the reverse-edge "
        "reindex caveat, which would tell the operator to discount a zero result "
        "that is in fact conclusive"
    )
    assert "report the record name alongside the query" in flat, (
        "the orphan report must name the record itself, so it survives a lookup "
        "that has not been reindexed yet"
    )


def test_calibration_names_only_adjudication_work_the_steps_still_define():
    """Calibration is read as a summary of the adjudicator's job.

    A bullet naming a step that no longer exists sends the adjudicator looking for
    work the process does not ask for — and quietly omits the work it does.
    """
    text = GAUNTLET.read_text()
    assert "section mapping" not in text, (
        "Calibration must not name 'section mapping' as the adjudicator's job — "
        "findings are consolidated into stable `C1`…`Cn` ids, not mapped back onto "
        "the record's sections"
    )
    assert "the single recommendation built out of them are the job" in _flat(text), (
        "Calibration must name the work the process actually defines: consolidate, "
        "spot-verify, and build the one recommendation the operator decides on"
    )


def test_no_craft_prose_carries_the_discarded_reframed_disposition():
    """`reframed` is gone from the disposition vocabulary in both gauntlet modes.

    Swept over every craft prose file, not just the four sibling files named in
    the task — a scoped grep would miss a sibling `_craft_prose_files()` was
    added specifically to catch.
    """
    offenders = [
        p for p in _craft_prose_files() if "reframed" in p.read_text()
    ]
    assert not offenders, (
        "these craft files still carry the discarded `reframed` disposition: "
        f"{[str(p.relative_to(CRAFT)) for p in offenders]} — it was replaced by "
        "`revise`, which carries a prescription and a declared scope instead of "
        "handing back a closed door"
    )


def test_premise_attacker_retains_all_three_verdicts_and_revise_not_discard():
    """The premise pass keeps its full mandate, altitude included.

    What changes under `revise` is only what a `framing-fails` verdict can
    PRODUCE — a prescription, never a discard. Losing any of the three verdicts,
    or losing the "produces a prescription, not a discard" restatement, would
    silently narrow the pass's mandate or reintroduce the discard route through
    the one pass most likely to reach for it.
    """
    text = PREMISE_ATTACKER.read_text()
    for verdict in ("framing-holds", "framing-wobbles", "framing-fails"):
        assert f"`{verdict}`" in text, (
            f"premise-attacker.md must retain the `{verdict}` verdict"
        )
    flat = _flat(text)
    assert (
        "A `framing-fails` verdict produces a `revise` prescription" in flat
    ), (
        "premise-attacker.md must state that a `framing-fails` verdict produces a "
        "`revise` prescription — its L74 reservation prose must say so explicitly, "
        "not leave it implied"
    )
    assert "never a discard" in flat, (
        "premise-attacker.md must state plainly that `framing-fails` never "
        "produces a discard — the gauntlet no longer has a discard route, and the "
        "premise pass is the one most likely to reach for it"
    )


def test_adr_tail_never_explains_provenance_by_reference_to_a_dropped_adr():
    """No gauntlet write sets an adr `dropped`, so provenance cannot be justified by one.

    The accepted tail explains why the `<n>-revise` count is worth stamping. That
    justification must name a state the gauntlet can actually leave an adr in. A
    surviving `revise` withholds the advance and leaves the adr `draft`; it never
    sends the adr to `dropped`, because no gauntlet-authored write sets that status
    at all — the same section pins exactly that, two paragraphs below. An audience
    named as "an auditor of a `dropped` adr" therefore describes a record this skill
    can no longer produce, which reads to a later editor as licence to restore the
    discard route the advance condition replaced.
    """
    tail = _flat(_adr_tail(GAUNTLET.read_text()))
    assert "auditor of a `dropped` adr" not in tail, (
        "the adr accepted tail justifies the `<n>-revise` count by appeal to an "
        "auditor of a `dropped` adr — a state no gauntlet write can produce now "
        "that the discard route is gone; name the `draft` adr the withheld advance "
        "actually leaves behind"
    )
    assert "`<n>-revise`" in tail, (
        "the adr accepted tail must still say what the `<n>-revise` count is for — "
        "deleting the stale audience must not delete the provenance rationale"
    )


def test_adr_mode_states_the_record_stays_draft_on_every_outcome():
    """Item 2 of the slice's test contract: the adr-mode prose must state this as
    a single, unconditional fact — not derivable only by combining the accept and
    revise branches separately.
    """
    adr_flat = _flat(_adr_mode_section(GAUNTLET.read_text()))
    assert "The gauntlet never advances the adr it is reviewing past `draft`, on either outcome." in adr_flat, (
        "gauntlet/SKILL.md's adr-mode section must state plainly, in one sentence, "
        "that the record under review never leaves `draft` regardless of outcome"
    )


def test_adr_mode_does_not_reopen_active_immutability():
    """`active` immutability moves WHEN it applies, not WHETHER it applies.

    A reader who sees activation move off the gauntlet could mistake it for a
    relaxation of immutability; this pins the explicit denial.
    """
    adr_flat = _flat(_adr_mode_section(GAUNTLET.read_text()))
    assert "`active` immutability is unchanged by this move" in adr_flat, (
        "gauntlet/SKILL.md must restate that `active` immutability is unchanged"
    )
    assert "this does not reopen that question for an already-`active` record" in adr_flat, (
        "gauntlet/SKILL.md must state explicitly that moving activation does not "
        "reopen editable-`active` ADRs"
    )
