"""Brainstorm's single-spec exit.

A brainstorm produces exactly one `spec` record. When discovery converges on
"one design change, more than one spec of work", that observation is *scope advice*,
not a second exit shape: the session picks the one piece to spec now and captures the
rest through the same Defer mechanism step 3 already uses — a `task` record at `open`
with a revisit condition, noted in the written spec. Fanning a decision out into
deliverable work is the slice loop's job, one increment at a time.

Brainstorm authors no ADR. Distill does, backward from finished work
(`test_only_distill_flips_an_adr_active` in `test_gauntlet_contract.py` pins the
activation half). Brainstorm still never flips a status itself — that discipline is
pinned for the spec branch by `test_gauntlet_contract.py::
test_brainstorm_hands_off_to_gauntlet_and_does_not_advance`.

These are content anchors on the prose, not a runtime harness — same contract-pin
style as `test_gauntlet_contract.py` and `test_adr_area_template_contract.py`.
"""

from __future__ import annotations

from pathlib import Path

BRAINSTORM = (
    Path(__file__).parent.parent / "plugins" / "craft" / "skills" / "brainstorm" / "SKILL.md"
)

# The scope-advice trigger, pinned verbatim so the same wording fires session to
# session rather than drifting into synonyms an agent might not recognize.
_ALTITUDE_TRIGGER_PHRASE = "one design change, more than one spec of work"

# The single-spec exit rule, pinned verbatim.
_ONE_SPEC_RULE = "A brainstorm exits with exactly one spec"

# What fans a spec out into deliverable work, now that brainstorm does not.
_FAN_OUT_OWNER = "The slice loop is what fans a spec out into deliverable work"

# The Defer capture for scope discovered but not specced now. Pinned to the altitude
# section rather than file-wide: step 3 states the same Defer pattern in near-identical
# words, so a whole-file match is satisfied by step 3 alone and pins nothing here.
_DEFER_CAPTURE = "capture each piece you are not speccing now as a deferred `task`"


# The exit-gate handoff quote, keyed by the heading that introduces it and the
# trailing instruction that ends it. There is one quote now: the spec exit is the
# only exit.
_SPEC_HANDOFF_HEADER = "**The handoff:**"
_HANDOFF_STOP = "**Print the handoff command fully formed**"


def _text() -> str:
    return BRAINSTORM.read_text(encoding="utf-8")


def _handoff_quote(header: str, stop_marker: str) -> str:
    """One handoff quote, scoped to its own branch.

    Scoped rather than matched file-wide: both branches say similar things, so a
    file-wide count of a shared phrase is satisfied by either branch saying it
    twice and pins neither handoff it names.
    """
    text = _text()
    assert header in text, f"brainstorm/SKILL.md must carry a {header!r} handoff quote"
    start = text.index(header)
    stop = text.find(stop_marker, start + len(header))
    assert stop != -1, (
        f"the {header!r} handoff quote must be followed by {stop_marker!r}, "
        "the marker that bounds it"
    )
    return text[start:stop]


def _flat(text: str) -> str:
    """Whitespace-collapsed prose, so a pinned phrase survives a line wrap."""
    return " ".join(text.split())


def _altitude_section() -> str:
    """Section 6, up to 6a's heading, whitespace-collapsed — scoped so a pin here
    cannot be satisfied by step 3's own statement of the Defer pattern elsewhere in
    the file, and flattened so a rewrap cannot disarm it."""
    text = _text()
    header = "### 6. Altitude Check"
    assert header in text, "brainstorm/SKILL.md must carry a '### 6. Altitude Check' section"
    start = text.index(header)
    stop = text.index("### 6a.", start)
    return _flat(text[start:stop])


def test_brainstorm_skill_ships():
    assert BRAINSTORM.exists(), f"Expected brainstorm/SKILL.md at {BRAINSTORM}"


def test_brainstorm_never_flips_a_spec_to_ready():
    """Regression guard for the spec-branch disclaimer this slice must not disturb."""
    assert "Do not flip the spec to `ready` yourself" in _text()


def test_brainstorm_still_hands_off_to_gauntlet_for_the_spec_branch():
    """Regression guard: the pre-existing spec-branch handoff must survive untouched."""
    assert "/craft:gauntlet" in _text()


def test_handoff_does_not_cite_the_gauntlet_internal_step_numbering():
    """The gauntlet's resolution flow forks per mode after its shared steps.

    A spec advances in the numbered spec tail; an adr advances in the adr tail, which
    carries no step number at all. Citing one number for both is wrong for the adr
    branch, and it re-breaks every time the gauntlet renumbers.
    """
    text = " ".join(_text().split())
    claim = "the `gauntlet` skill owns the flip"
    assert claim in text, (
        "brainstorm/SKILL.md must state that the gauntlet owns the spec flip "
        "record kinds — that ownership is what makes the review unskippable"
    )
    sentence = text[text.index(claim):].split(".")[0]
    assert "step" not in sentence, (
        "the flip-ownership claim must not name a step of the gauntlet — the adr "
        "flip does not live in a numbered step, and a number here pins brainstorm "
        f"to the gauntlet's headings. Got: {sentence!r}"
    )


def test_spec_handoff_reflects_recommend_then_accept():
    """The gauntlet gates on one recommendation the user accepts or overrides.

    The spec-branch (6a) handoff quote is what tells the user what to expect next,
    so it must describe that gate rather than a walk through each finding.
    """
    quote = _handoff_quote(_SPEC_HANDOFF_HEADER, _HANDOFF_STOP)
    assert "dispositioned what it finds" not in quote, (
        "brainstorm/SKILL.md's spec handoff must describe the gauntlet's gate as "
        "one recommendation the user accepts or overrides, not a finding-by-finding "
        "disposition walk-through"
    )
    assert "accepted its recommendation" in quote, (
        "brainstorm/SKILL.md's spec handoff must describe flipping to `ready` "
        "once the user has accepted the gauntlet's recommendation, matching "
        "gauntlet's recommend-then-accept resolution flow"
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


def test_brainstorm_exits_with_exactly_one_spec():
    assert _ONE_SPEC_RULE in _flat(_text()), (
        "brainstorm/SKILL.md must state that a brainstorm exits with exactly one "
        f"spec — {_ONE_SPEC_RULE!r} — so an agent reading it cannot infer a second "
        "exit shape that writes several spec records at once."
    )


def test_the_slice_loop_is_named_as_what_fans_work_out():
    assert _FAN_OUT_OWNER in _flat(_text()), (
        "brainstorm/SKILL.md must name the slice loop as what fans a spec out into "
        "deliverable work — an operator whose idea spans several specs needs "
        "somewhere to be routed, not just a refusal."
    )


def test_the_scope_advice_trigger_phrase_is_pinned():
    """The observation survives the fork's removal — it is still the signal an
    operator needs — but as advice about what to spec now, not as a branch."""
    assert _ALTITUDE_TRIGGER_PHRASE in _altitude_section(), (
        "brainstorm/SKILL.md's altitude section must keep the trigger phrase "
        f"verbatim — {_ALTITUDE_TRIGGER_PHRASE!r} — so the same wording fires "
        "session to session rather than a synonym an agent invents."
    )


def test_discovered_scope_is_captured_as_a_deferred_task():
    """Without a durable capture the discovery is lost: an operator brainstorms a
    three-spec idea, ships one, and nothing in the vault records that the other two
    were ever identified. Scoped to the altitude section — step 3 states the same
    Defer pattern, so a file-wide match would pass on step 3's wording alone."""
    section = _altitude_section()
    assert _DEFER_CAPTURE in section, (
        "brainstorm/SKILL.md's altitude section must capture scope it is not "
        "speccing now as a deferred `task` record, reusing step 3's Defer pattern "
        "rather than leaving the discovery as spoken advice."
    )
    assert "revisit condition" in section, (
        "the deferred capture must carry a revisit condition, as step 3's Defer "
        "mechanism does — a task with no revisit condition is a note nobody returns to"
    )


def test_the_picker_points_at_the_value_floor_heuristic():
    assert "_shared/slice.md" in _altitude_section(), (
        "brainstorm/SKILL.md must point the choice of which piece to spec now at "
        "the value-floor heuristic `/craft:slice` uses, rather than leaving an agent "
        "to default to the largest or most obvious piece."
    )


def test_brainstorm_makes_no_spec_or_adr_write_beyond_the_single_spec():
    """Scoped to `spec` and `adr` deliberately: brainstorm legitimately creates
    `decision` records (prior-art survey, step 3) and `task` records (step 3's Defer,
    and the altitude capture above), so a blanket 'only record-creating write' claim
    would be false and could never go green."""
    text = _text()
    assert text.count("--kind spec") == 1, (
        "brainstorm/SKILL.md must carry exactly one `lore record create --kind spec` "
        f"write (found {text.count('--kind spec')}) — a second one is the fan-out "
        "this change removes"
    )
    assert "--kind adr" not in text, (
        "brainstorm/SKILL.md must author no ADR — distill is craft's ADR author, "
        "and it writes them backward from finished work"
    )


def test_distill_is_the_only_craft_path_that_authors_an_adr():
    """Pinned against distill itself, so brainstorm cannot quietly regain the
    capability without this going red on the other side of the claim."""
    distill = BRAINSTORM.parent.parent / "distill" / "SKILL.md"
    assert distill.exists(), f"expected distill/SKILL.md at {distill}"
    assert "--kind adr" in distill.read_text(encoding="utf-8"), (
        "distill/SKILL.md must carry the ADR-authoring write — it is the only craft "
        "path that authors an ADR, and this pin is what makes that claim checkable"
    )
