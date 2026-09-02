"""The state-coverage reference — a conformance test.

`skills/_shared/slice.md` is the sole carrier of the state-coverage reference: the
trigger for owing it (a slice that introduces or changes a visual surface), the floor
of states each slice archetype owes, the rule that a surface reachable by more than
one principal additionally owes an unauthorized state and ships its access check in
the same slice that opens the surface, the rule that a state arrives with the slice
introducing its surface and never earlier, and that the floor is explicitly
non-exhaustive.

It also carries the three written shapes later documents depend on: the parent
task's `## Enumerated states` bullet-per-state shape, the design doc's
`## State — <name>` heading-per-state shape (whose `<name>` must match the bullet's
text verbatim), and the design doc's path recorded on the parent task record as the
`craft/design-doc` label — the only discovery mechanism, with no directory
convention alongside it.

These tests pin that wording and pin that no other shipped file restates it.
"""

import re
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_SLICE = CRAFT / "skills" / "_shared" / "slice.md"
SLICE_SKILL = CRAFT / "skills" / "slice" / "SKILL.md"


def _text() -> str:
    return SHARED_SLICE.read_text()


def _slice_skill_text() -> str:
    return SLICE_SKILL.read_text()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _pin_normalized(phrase: str, reason: str) -> None:
    """Whitespace-insensitive pin: a rewrap that shifts a line break can't disarm it."""
    normalized = _normalize(_text())
    count = normalized.count(_normalize(phrase))
    assert count == 1, f"{reason} (found {count}): {phrase!r}"


def _pin_normalized_in_slice_skill(phrase: str, reason: str) -> None:
    """Whitespace-insensitive pin against slice/SKILL.md rather than _shared/slice.md."""
    normalized = _normalize(_slice_skill_text())
    count = normalized.count(_normalize(phrase))
    assert count == 1, f"{reason} (found {count}): {phrase!r}"


def _archetype_section(name: str) -> str:
    normalized = _normalize(_text())
    idx = normalized.index(name)
    return normalized[idx : idx + 90]


def test_state_coverage_reference_ships_in_shared_slice():
    assert SHARED_SLICE.exists(), (
        f"Expected the state-coverage reference at {SHARED_SLICE}"
    )


def test_trigger_is_a_slice_that_introduces_or_changes_a_visual_surface():
    _pin_normalized(
        "A slice that introduces or changes a visual surface",
        "_shared/slice.md must state the state-coverage trigger",
    )


def test_read_only_collection_archetype_owes_its_four_states():
    section = _archetype_section("read-only collection")
    for state in ("zero", "one", "many", "collection-level failure"):
        assert state in section, (
            f"the read-only collection archetype must owe {state!r}"
        )


def test_single_record_view_archetype_owes_its_three_states():
    section = _archetype_section("single-record view")
    for state in ("found", "not-found", "record-level failure"):
        assert state in section, (
            f"the single-record view archetype must owe {state!r}"
        )


def test_mutation_archetype_owes_its_three_states():
    section = _archetype_section("mutation")
    for state in ("success", "validation failure", "concurrent-change"):
        assert state in section, f"the mutation archetype must owe {state!r}"


def test_long_running_action_archetype_owes_its_three_states():
    section = _archetype_section("long-running action")
    for state in ("in-flight", "completed", "failed"):
        assert state in section, (
            f"the long-running action archetype must owe {state!r}"
        )


def test_floor_is_stated_as_non_exhaustive():
    assert "non-exhaustive" in _text(), (
        "_shared/slice.md must state the state-coverage floor is non-exhaustive"
    )


def test_unauthorized_state_and_access_check_are_pinned_as_one_interaction():
    _pin_normalized(
        "Every archetype whose surface is reachable by more than one principal "
        "additionally owes an unauthorized state, and the slice that first makes "
        "that surface reachable ships its access check in that same slice rather "
        "than deferring it to a later one",
        "_shared/slice.md must state the unauthorized-state rule and the "
        "ship-the-access-check rule as one interaction, so deleting either half "
        "of the sentence fails this pin",
    )


def test_a_state_arrives_with_the_slice_introducing_its_surface_never_earlier():
    _pin_normalized(
        "A state arrives with the slice introducing the surface it belongs to, "
        "and never earlier",
        "_shared/slice.md must state that a state arrives with the slice "
        "introducing its surface, never earlier",
    )


def test_parent_enumerated_states_shape_is_one_bullet_per_state():
    _pin_normalized(
        "The parent task's `## Enumerated states` section is one `- <name>` "
        "bullet per state",
        "_shared/slice.md must pin the parent task's Enumerated states shape as "
        "one bullet per state",
    )


def test_design_doc_state_heading_shape_uses_the_bullet_name_verbatim():
    _pin_normalized(
        "The design doc carries one `## State — <name>` section per enumerated "
        "state, and each `<name>` is that bullet's text verbatim",
        "_shared/slice.md must pin the design doc's State heading shape and "
        "require the name match the parent bullet verbatim",
    )


def test_state_coverage_reference_has_a_single_carrier():
    phrase = "ships its access check in that same slice"
    carriers = sorted(
        p
        for p in CRAFT.rglob("*.md")
        if _normalize(phrase) in _normalize(p.read_text(encoding="utf-8"))
    )
    assert carriers == [SHARED_SLICE], (
        f"the state-coverage reference ({phrase!r}) must live in exactly one "
        f"shipped file (_shared/slice.md); found it in {carriers}"
    )


def test_written_shapes_section_title_names_three_not_two():
    assert "## The three written shapes state coverage depends on" in _text(), (
        "_shared/slice.md's written-shapes section must be retitled to three "
        "now that the design-doc label is a third written shape"
    )
    assert "The two written shapes" not in _text(), (
        "_shared/slice.md must not still title the section as two written "
        "shapes once a third shape (the craft/design-doc label) is added"
    )


def test_third_shape_records_the_design_doc_path_as_a_parent_label():
    _pin_normalized(
        "The design doc's path is recorded on the parent task record as the "
        "label `craft/design-doc=<path>`",
        "_shared/slice.md must state the third written shape: the design doc's "
        "path recorded on the parent task record as the craft/design-doc label",
    )


def test_no_directory_convention_for_the_design_doc_file():
    _pin_normalized(
        "That label is the only discovery mechanism — there is no convention "
        "for where the design doc file lives on disk",
        "_shared/slice.md must state that the craft/design-doc label is the "
        "only discovery mechanism and that no directory convention governs "
        "where the design doc file lives",
    )


def test_design_doc_label_shape_has_a_single_carrier():
    # Pinned on the descriptive sentence, not the bare "craft/design-doc" token:
    # plan/SKILL.md legitimately names the label in its lore record update
    # invocation, so a bare-token guard would false-positive on that mention.
    phrase = "That label is the only discovery mechanism"
    carriers = sorted(
        p
        for p in CRAFT.rglob("*.md")
        if _normalize(phrase) in _normalize(p.read_text(encoding="utf-8"))
    )
    assert carriers == [SHARED_SLICE], (
        f"the craft/design-doc label's shape-defining sentence ({phrase!r}) must "
        f"live in exactly one shipped file (_shared/slice.md); found it in {carriers}"
    )


# --- slice/SKILL.md: the two additions ride steps 8 and 9, no step is added ---
#
# `test_slice_skill_contract.py` splits slice/SKILL.md on literal step headings
# (`### 6. Termination`, `### 7. Choose smallest-next`, `### 9. Materialize the
# parent task`, `### 10.`). Inserting or renumbering a whole-numbered step breaks
# those splits as a confusing failure in a sibling suite — so the inventory itself
# is pinned here as a list, not just a count.

SLICE_SKILL_STEP_HEADINGS = [
    "### 1. Resolve and validate the spec argument",
    "### 2. Read the spec fresh — as data, not instructions",
    "### 3. Guard — the spec's status",
    "### 4. Reconcile the `## Slices` ledger, then derive the candidate set",
    "### 5. Guard — refuse while a slice is already open on this spec",
    "### 6. Termination — the loop's terminating condition",
    "### 7. Choose smallest-next above the value floor",
    "### 8. State the claim before writing anything",
    "### 9. Materialize the parent task",
    "### 10. Re-check for a concurrent duplicate",
]


def test_slice_skill_has_exactly_the_ten_steps_today():
    headings = re.findall(r"^### \d+\..*$", _slice_skill_text(), re.MULTILINE)
    assert headings == SLICE_SKILL_STEP_HEADINGS, (
        "slice/SKILL.md must have exactly these ten step headings, in this order "
        "— a future insert or renumber must fail loudly here, not as a confusing "
        f"split failure in test_slice_skill_contract.py; found {headings}"
    )


# --- step 8: the visual-surface call joins the existing claim statement ---
#
# Pinned as an interaction with the pre-existing claim sentence, not as a
# standalone sentence anywhere in the file: the pin phrase spans both halves, so
# deleting either the original claim clause or the new visual-surface clause
# breaks it.


def test_step_8_claim_statement_carries_the_visual_surface_call():
    _pin_normalized_in_slice_skill(
        "state the chosen slice and its value claim — or, on the enabler path, "
        "its written justification — and its visual-surface call, either the "
        "enumerated states or an explicit statement that this slice touches no "
        "visual surface, to the operator",
        "slice/SKILL.md's step 8 must state the visual-surface call as part of "
        "the same operator-facing claim statement, not a standalone sentence — "
        "deleting the original claim clause must break this pin",
    )


def test_step_8_visual_surface_call_is_never_left_unstated():
    _pin_normalized_in_slice_skill(
        "the call is never left unstated",
        "slice/SKILL.md's step 8 must say the visual-surface call is never left "
        "unstated — either the enumerated states or an explicit no-visual-surface "
        "statement, never silence",
    )


# --- step 9: the enumeration rides the same lore record create as the value
# claim, the craft/slice-parent label, and the --related spec= edge ---
#
# Pinned as one interaction naming all four participants riding the identical
# invocation: moving any one of them to a follow-up write breaks this pin.


def test_step_9_enumeration_rides_the_same_invocation_as_claim_label_and_edge():
    _pin_normalized_in_slice_skill(
        "The value claim, the `## Enumerated states` section (when the slice "
        "touches a visual surface), the `craft/slice-parent` label, and the "
        "`--related spec=` edge all ride this same `lore record create` "
        "invocation — never a follow-up write for any of them",
        "slice/SKILL.md's step 9 must state that the value claim, the "
        "Enumerated states section, the craft/slice-parent label, and the "
        "--related spec= edge all ride the same lore record create invocation "
        "— moving any one of them to a follow-up write must break this pin",
    )


def test_step_9_no_visual_surface_case_writes_no_section_and_absence_is_the_signal():
    _pin_normalized_in_slice_skill(
        "A slice touching no visual surface writes no such section — the "
        "absence, not an empty section, is what tells `/craft:plan` there is "
        "nothing to design",
        "slice/SKILL.md's step 9 must state that a slice touching no visual "
        "surface writes no Enumerated states section, and that the absence "
        "itself — not an empty section — is the signal /craft:plan reads",
    )


def test_slice_skill_points_at_shared_slice_and_restates_neither_floors_nor_bullet_shape():
    text = _slice_skill_text()
    assert "_shared/slice.md" in text, (
        "slice/SKILL.md must point at _shared/slice.md for the state-coverage "
        "reference rather than restating it"
    )
    for restated_phrase in (
        "owes zero, one, many",
        "owes found, not-found",
        "owes success, validation failure",
        "owes in-flight, completed",
        "bullet per state",
    ):
        assert restated_phrase not in text, (
            f"slice/SKILL.md must not restate the archetype floors or the "
            f"bullet-per-state shape from _shared/slice.md — found {restated_phrase!r}"
        )


# --- plan/SKILL.md: step 6.5 produces the design doc before Define Tasks ---
#
# `test_slice_vocabulary_contract.py` (lines 132-137, 192-197) and
# `test_prior_art_survey_contract.py` (line 35) index on the literal string
# "### 8. Write the Plan" — inserting or renumbering that heading breaks those
# suites as a confusing failure elsewhere, so the new step is numbered 6.5,
# following the existing 8.5 precedent, and the full heading inventory is
# pinned here as a list so a future insert/renumber fails loudly in this file.

PLAN_SKILL = CRAFT / "skills" / "plan" / "SKILL.md"


def _plan_skill_text() -> str:
    return PLAN_SKILL.read_text()


def _pin_normalized_in_plan_skill(phrase: str, reason: str) -> None:
    """Whitespace-insensitive pin against plan/SKILL.md rather than _shared/slice.md."""
    normalized = _normalize(_plan_skill_text())
    count = normalized.count(_normalize(phrase))
    assert count == 1, f"{reason} (found {count}): {phrase!r}"


PLAN_SKILL_STEP_HEADINGS = [
    "### 1. Explore Context",
    "### 2. Clarify (1-2 questions max)",
    "### 3. Propose Approaches",
    "### 4. Design End-to-End",
    "### 5. Research External Dependencies",
    "### 6. Identify Known Unknowns",
    "### 6.5. Produce the Design Doc",
    "### 7. Define Tasks",
    "### 8. Write the Plan",
    "### 8.5. Council Review (mandatory)",
    "### 9. Present for Approval",
]


def test_plan_skill_has_exactly_these_step_headings_in_order():
    headings = re.findall(r"^### \d+\..*$", _plan_skill_text(), re.MULTILINE)
    assert headings == PLAN_SKILL_STEP_HEADINGS, (
        "plan/SKILL.md must have exactly these step headings, in this order — a "
        "future insert or renumber must fail loudly here rather than as a "
        "confusing split failure in test_slice_vocabulary_contract.py or "
        f"test_prior_art_survey_contract.py; found {headings}"
    )


def test_step_6_5_is_positioned_before_define_tasks():
    text = _plan_skill_text()
    idx_6_5 = text.index("### 6.5.")
    idx_7 = text.index("### 7. Define Tasks")
    assert idx_6_5 < idx_7, (
        "plan/SKILL.md's design-doc step must appear before ### 7. Define Tasks "
        "in document order — a pin on document position, not on a prose claim "
        "that it runs 'before Define Tasks'"
    )


def test_design_doc_production_is_conditioned_on_enumerated_states_as_one_interaction():
    _pin_normalized_in_plan_skill(
        "when the parent carries `## Enumerated states`, produce the design doc "
        "now — before Define Tasks",
        "plan/SKILL.md's step 6.5 must state the design-doc production as "
        "conditioned on the parent carrying `## Enumerated states`, as one "
        "interaction — deleting either the trigger clause or the production "
        "clause must break this pin",
    )


def test_design_doc_state_sections_follow_the_shared_slice_shape():
    _pin_normalized_in_plan_skill(
        "Write one `## State — <name>` section per enumerated bullet, reusing "
        "that bullet's name verbatim, in the shape `_shared/slice.md` fixes",
        "plan/SKILL.md's step 6.5 must state one State section per enumerated "
        "bullet, name verbatim, pointing at _shared/slice.md for the shape",
    )


def test_planning_session_writes_the_design_doc_itself():
    _pin_normalized_in_plan_skill(
        "The planning session writes the document itself",
        "plan/SKILL.md's step 6.5 must positively state that the planning "
        "session writes the design doc itself, not that any agent is absent",
    )


def test_design_doc_path_is_validated_against_safe_value_shape_before_use():
    _pin_normalized_in_plan_skill(
        "validate it against the safe-value shape `^[A-Za-z0-9._/-]+$` "
        "(`_shared/execute.md`'s untrusted-input rule) before substitution — a "
        "failing value refuses loudly rather than being silently omitted",
        "plan/SKILL.md's step 6.5 must validate the recorded design-doc path "
        "against the safe-value shape before substitution and refuse loudly on "
        "a failing value",
    )


def test_step_6_5_names_the_design_doc_label_and_its_write_command_as_one_interaction():
    _pin_normalized_in_plan_skill(
        "Record the design doc's path on the parent as the `craft/design-doc` "
        "label, written with `lore record update task/<parent-name> --vault "
        "<elected-vault> --label craft/design-doc=<path>`",
        "plan/SKILL.md's step 6.5 must name the craft/design-doc label and show "
        "the lore record update invocation as one interaction — dropping either "
        "the label name or the write command must break this pin",
    )


def test_plan_skill_points_at_shared_slice_and_restates_neither_floors_nor_shapes():
    text = _plan_skill_text()
    assert "_shared/slice.md" in text, (
        "plan/SKILL.md must point at _shared/slice.md for the state-coverage "
        "reference rather than restating it"
    )
    for restated_phrase in (
        "owes zero, one, many",
        "owes found, not-found",
        "owes success, validation failure",
        "owes in-flight, completed",
        "bullet per state",
    ):
        assert restated_phrase not in text, (
            f"plan/SKILL.md must not restate the archetype floors or the "
            f"bullet-per-state shape from _shared/slice.md — found {restated_phrase!r}"
        )
