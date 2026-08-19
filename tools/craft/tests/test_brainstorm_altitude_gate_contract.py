"""Brainstorm's altitude gate — the ADR-vs-spec exit fork.

Brainstorm normally exits into a single `spec` record. When discovery converges on
"one design change, more than one spec of work" — one decision that fans out into
several independently-shippable pieces — the exit artifact changes shape: a draft
`adr` record (the decision) plus named `spec` seeds (the pieces), each seed wired to
the ADR with a `related: adr=` edge from the moment it's created, then brainstormed
and gauntleted separately at its own altitude.

Brainstorm still never flips a status itself in either branch — that discipline
(pinned for the spec branch by `test_gauntlet_contract.py::
test_brainstorm_hands_off_to_gauntlet_and_does_not_freeze`) must hold for the new ADR
branch too: no craft file may flip an adr `active` except the gauntlet
(`test_only_gauntlet_flips_an_adr_active` in `test_gauntlet_contract.py`), so brainstorm
must never carry the literal `--status active` write.

These are content anchors on the prose, not a runtime harness — same contract-pin
style as `test_gauntlet_contract.py` and `test_adr_area_template_contract.py`.
"""

from __future__ import annotations

from pathlib import Path

BRAINSTORM = (
    Path(__file__).parent.parent / "plugins" / "craft" / "skills" / "brainstorm" / "SKILL.md"
)

# The trigger phrase for the altitude gate — pinned verbatim so the same wording
# fires session to session rather than drifting into synonyms an agent might not
# recognize as the gate.
_ALTITUDE_TRIGGER_PHRASE = "one design change, more than one spec of work"

# The seed's related-edge-from-birth rule, pinned verbatim, so the skill prose and
# this pin share exactly one spelling of the phrase.
_SEED_EDGE_PHRASE = "related: adr=<the-adr>"

# The rejected-ADR orphan rule, pinned verbatim.
_ORPHAN_RULE = (
    "its seeded derived specs are orphaned back to brainstorm — their "
    "`related: adr=` edge points at the `dropped` draft as provenance"
)


def _text() -> str:
    return BRAINSTORM.read_text(encoding="utf-8")


def test_brainstorm_skill_ships():
    assert BRAINSTORM.exists(), f"Expected brainstorm/SKILL.md at {BRAINSTORM}"


def test_altitude_gate_trigger_phrase_pinned():
    assert _ALTITUDE_TRIGGER_PHRASE in _text(), (
        "brainstorm/SKILL.md must pin the altitude-gate trigger phrase verbatim — "
        f"{_ALTITUDE_TRIGGER_PHRASE!r} — so the escalation to an ADR fires "
        "consistently session to session rather than on a synonym an agent invents."
    )


def test_altitude_gate_creates_a_draft_adr_via_lore_record_create():
    text = _text()
    assert "--kind adr" in text, (
        "brainstorm/SKILL.md must create the altitude-gate's ADR via "
        "`lore record create --kind adr`"
    )
    assert "templates/adr.md" in text, (
        "brainstorm/SKILL.md must render the ADR body from templates/adr.md, the "
        "same four-section template the gauntlet and distill rituals use"
    )


def test_altitude_gate_leaves_the_adr_at_its_default_draft_status():
    """No explicit --status flag on the ADR create — `draft` is the kind's default."""
    text = _text()
    assert "--kind adr" in text
    assert "--status draft" not in text, (
        "the ADR create should rely on the kind's default status (`draft`) rather "
        "than stamping it explicitly — matching the slice's 'created at default "
        "draft status' framing"
    )


def test_seeds_carry_related_adr_edge_from_birth():
    text = _text()
    assert _SEED_EDGE_PHRASE in text, (
        f"brainstorm/SKILL.md must pin the seed edge phrase verbatim — {_SEED_EDGE_PHRASE!r}"
    )
    assert "from birth" in text, (
        "brainstorm/SKILL.md must state the edge is written at seed creation, not "
        "added later — 'from birth' is the phrase that rules out a two-step write"
    )
    assert "--related adr=" in text, (
        "brainstorm/SKILL.md must show the actual CLI flag (--related adr=<adr-id>) "
        "for wiring a seed to its ADR, not just describe the edge in prose"
    )


def test_seeds_are_each_brainstormed_and_gauntleted_separately():
    text = _text()
    assert "separately" in text and "own altitude" in text, (
        "brainstorm/SKILL.md must state each seed is brainstormed and gauntleted "
        "separately at its own altitude — the ADR session's job stops at planting "
        "the seeds, not fleshing every one of them out"
    )


def test_rejected_adr_orphans_its_seeded_specs():
    assert _ORPHAN_RULE in _text(), (
        f"brainstorm/SKILL.md must pin the rejected-ADR orphan rule verbatim — {_ORPHAN_RULE!r}"
    )


def test_altitude_gate_hands_off_to_gauntlet_with_the_adr_id():
    text = _text()
    assert "/craft:gauntlet <adr-id>" in text, (
        "brainstorm/SKILL.md must hand the altitude-gate branch off to "
        "`/craft:gauntlet <adr-id>` — the adr-target mode of the same gauntlet skill "
        "the spec branch hands off to"
    )


def test_brainstorm_never_flips_a_spec_to_ready():
    """Regression guard for the spec-branch disclaimer this slice must not disturb."""
    assert "Do not flip the spec to `ready` yourself" in _text()


def test_brainstorm_still_hands_off_to_gauntlet_for_the_spec_branch():
    """Regression guard: the pre-existing spec-branch handoff must survive untouched."""
    assert "/craft:gauntlet" in _text()


def test_handoff_does_not_cite_the_gauntlet_internal_step_numbering():
    """The gauntlet's resolution flow forks per mode after its shared steps.

    A spec freezes in the numbered spec tail; an adr freezes in the adr tail, which
    carries no step number at all. Citing one number for both is wrong for the adr
    branch, and it re-breaks every time the gauntlet renumbers.
    """
    text = " ".join(_text().split())
    claim = "owns the flip, spec or adr alike"
    assert claim in text, (
        "brainstorm/SKILL.md must state that the gauntlet owns the flip for both "
        "record kinds — that ownership is what makes the review unskippable"
    )
    sentence = text[text.index(claim):].split(".")[0]
    assert "step" not in sentence, (
        "the flip-ownership claim must not name a step of the gauntlet — the adr "
        "flip does not live in a numbered step, and a number here pins brainstorm "
        f"to the gauntlet's headings. Got: {sentence!r}"
    )


def test_brainstorm_never_writes_the_adr_active_flip():
    """Brainstorm creates the draft ADR and hands off — it must never flip statuses
    itself, in either branch. The gauntlet alone owns `draft -> active`
    (`test_only_gauntlet_flips_an_adr_active` in test_gauntlet_contract.py); if this
    literal string ever appears here, brainstorm has grown a bypass around it."""
    assert "--status active" not in _text(), (
        "brainstorm/SKILL.md must never carry the literal `--status active` write — "
        "the gauntlet alone flips an adr to active, never brainstorm"
    )


def test_spec_handoff_reflects_recommend_then_accept():
    """The gauntlet gates on one recommendation the user accepts or overrides.

    The spec-branch (6a) handoff quote is what tells the user what to expect next,
    so it must describe that gate rather than a walk through each finding.
    """
    text = _text()
    assert "dispositioned what it finds" not in text, (
        "brainstorm/SKILL.md's spec handoff must describe the gauntlet's gate as "
        "one recommendation the user accepts or overrides, not a finding-by-finding "
        "disposition walk-through"
    )
    assert "accepted its recommendation" in text, (
        "brainstorm/SKILL.md's spec handoff must describe flipping to `ready` "
        "once the user has accepted the gauntlet's recommendation, matching "
        "gauntlet's recommend-then-accept resolution flow"
    )


def test_adr_handoff_reflects_recommend_then_accept():
    """Same recommend-then-accept alignment for the altitude-gate (6b) handoff quote."""
    text = _text()
    assert text.count("accepted its recommendation") >= 2, (
        "brainstorm/SKILL.md must describe both the spec-branch and adr-branch "
        "handoffs in recommend-then-accept terms — the gauntlet flips a record "
        "once its recommendation is accepted, not once every finding is "
        "individually dispositioned"
    )


def test_ready_edge_description_reflects_recommend_then_accept():
    """The Status Lifecycle section's prose description of the draft -> ready edge
    must also describe recommend-then-accept, not per-finding disposition."""
    text = _text()
    assert "Criticals are dispositioned" not in text, (
        "brainstorm/SKILL.md must not describe the gauntlet's `draft` -> `ready` "
        "flip as happening once Criticals 'are dispositioned' — the flip follows "
        "the operator accepting the gauntlet's recommendation"
    )
    assert "operator has accepted" in text or "operator accepts" in text, (
        "brainstorm/SKILL.md's Status Lifecycle section must describe the "
        "gauntlet's flip in operator-accepts-the-recommendation terms"
    )
