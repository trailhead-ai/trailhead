"""`/craft:plan`'s dual entry points — a slice parent, or a topic.

`/craft:plan` runs the same downstream design work either way, but the two entry
points write differently:

  - **Slice-rooted** (argument resolves to an existing `task` record): plan fills
    that parent's body with the plan sections via an update and writes the
    component-shaped child tasks beneath it. It does NOT create a second parent.
    It writes no spec status; neither does the topic-rooted path.
  - **Topic-rooted** (argument is anything else): plan creates its own parent task
    and refuses a `ready` spec, routing it to `/craft:slice`. It also gains one
    cross-check: if the resolved spec already has an open slice parent, plan says
    so rather than silently creating a duplicate parent beside it.

Both halves are pinned here so neither path can be silently deleted.
"""

import re
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
PLAN_SKILL = CRAFT / "skills" / "plan" / "SKILL.md"


def _text() -> str:
    return PLAN_SKILL.read_text()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _pin_normalized(phrase: str, reason: str) -> None:
    """Whitespace-insensitive pin: a rewrap that shifts a line break can't disarm it."""
    normalized = _normalize(_text())
    assert normalized.count(_normalize(phrase)) == 1, (
        f"pinned phrase must be whitespace-normalized-unique in the file "
        f"(found {normalized.count(_normalize(phrase))}): {phrase!r}"
    )


# --- the discrimination rule between the two entry points ---


def test_states_the_rule_distinguishing_a_slice_parent_argument_from_a_topic():
    assert (
        "an argument resolving to an existing `task` record takes the "
        "slice-rooted path; anything else is a topic and takes the topic-rooted "
        "path" in _text()
    ), (
        "plan/SKILL.md must state explicitly how the two entry points are "
        "distinguished — an argument resolving to an existing task record is "
        "slice-rooted, anything else is topic-rooted"
    )


# --- slice-rooted path: updates the existing parent, creates no second one ---


def test_slice_rooted_path_updates_the_existing_parent_body():
    assert (
        "Rooted at a slice parent, fill that existing parent task's body with "
        "the plan sections via an update" in _text()
    ), (
        "plan/SKILL.md must state that the slice-rooted path fills the existing "
        "parent task's body via an update"
    )


def test_slice_rooted_path_creates_no_second_parent():
    assert (
        "Do not create a second parent task" in _text()
    ), (
        "plan/SKILL.md must state explicitly that the slice-rooted path creates "
        "no second parent task — the defect this task exists to prevent"
    )


def test_slice_rooted_path_writes_children_beneath_the_existing_parent():
    assert (
        "write the component-shaped child tasks beneath that existing parent" in _text()
    ), (
        "plan/SKILL.md must state that the slice-rooted path writes its child "
        "tasks beneath the existing (slice) parent, not a newly created one"
    )


# --- slice-rooted path: writes no spec status ---


def test_slice_rooted_path_writes_no_spec_status():
    assert (
        "Rooted at a slice parent, write no spec status" in _text()
    ), (
        "plan/SKILL.md must state that the slice-rooted path writes no spec "
        "status — the ready -> planned advance does not fire on this path"
    )


# --- topic-rooted path: refuses a ready spec, writes no spec status ---


def test_topic_rooted_path_refuses_a_ready_spec_and_routes_to_the_slice_loop():
    """A `ready` spec belongs to the slice loop, not to whole-feature planning.

    Topic-rooted planning gated only `draft` before. A `ready` spec fell through,
    was planned whole, and was advanced to `planned` — the status `/craft:slice`
    then refuses. With the advance gone, an ungated `ready` spec would be worse
    still: it would carry neither `planned` nor the slice-loop marker, so neither
    of distill's candidate queries could ever reach it.
    """
    text = _text()
    assert "A `ready` spec is not planned whole" in text, (
        "plan/SKILL.md's topic-rooted path must refuse a `ready` spec, mirroring "
        "the `draft` -> /craft:gauntlet gate beside it"
    )
    assert "/craft:slice spec/<spec-id>" in text, (
        "the refusal must name the remedy as a fully formed command — the "
        "operator must always know what to run next"
    )


def test_no_path_advances_a_spec_to_planned():
    """`planned` stays in the spec status vocabulary but stops being written.

    This pins the behaviour the removal is for: a spec's status is craft's record
    of where it sits in the loop, and planning is not a transition in that loop.
    """
    _pin_normalized(
        "Planning writes no spec status on either path",
        "plan/SKILL.md must state that neither the slice-rooted nor the "
        "topic-rooted path writes a spec status — pinned unique, so deleting the "
        "statement at its owning site cannot be masked by a restatement elsewhere.",
    )
# --- topic-rooted path: cross-check for an existing open slice parent ---


def test_topic_rooted_path_warns_on_an_existing_open_slice_parent():
    assert (
        "if the resolved spec already has an open slice parent, say so rather "
        "than silently creating a duplicate parent beside it" in _text()
    ), (
        "plan/SKILL.md must state that the topic-rooted path cross-checks for "
        "an existing open slice parent on the spec and warns rather than "
        "silently duplicating it"
    )


# --- the cross-check query's <spec-name> interpolation names its shape check ---


def test_cross_check_query_names_the_safe_value_shape_check():
    assert (
        "validate `<spec-name>` against the safe-value shape `_shared/execute.md` "
        "codifies" in _text()
    ), (
        "plan/SKILL.md must name the shape check on <spec-name> at the "
        "cross-check query's interpolation site — a pointer to the shared rule "
        "in _shared/execute.md, not a restatement of it"
    )


# --- the topic-rooted path's spec-link write shape-checks <spec-name> too ---
#
# The cross-check query above names its shape check explicitly. The sibling
# `--related spec=<spec-name>` write a few lines down substitutes the same
# vault-sourced <spec-name> with no shape check named at its own site — an
# unguarded interpolation that reads, to anyone scanning the file, as an
# intentional exception rather than an oversight.


def test_spec_link_write_names_the_safe_value_shape_check():
    _pin_normalized(
        "validate `<spec-name>` against the safe-value shape "
        "`_shared/execute.md` codifies for any vault-sourced value entering a "
        "command before it is substituted into the spec-link write",
        "plan/SKILL.md's topic-rooted spec-link write (`--related "
        "spec=<spec-name>`) must name the same safe-value shape check the "
        "nearby cross-check query names, so the file does not read as though "
        "this site were an intentional unguarded exception",
    )


def test_spec_link_write_shape_check_is_co_located_with_the_write():
    link_write_site = _text().split(
        "link the parent task to it with `lore record update <parent-id> "
        "--related spec=<spec-name>`"
    )[0][-400:]
    assert "safe-value shape `_shared/execute.md` codifies" in link_write_site, (
        "plan/SKILL.md's shape check on <spec-name> must sit immediately "
        "before the `--related spec=<spec-name>` write it governs, not only "
        "at the earlier cross-check query"
    )


# --- CRITICAL: the slice-rooted write preserves the existing value claim ---


def test_slice_rooted_write_reads_the_existing_body_first():
    assert (
        "*Slice-rooted:* read the existing parent task body first, and preserve "
        "both its\n     `**Value claim:**` section" in _text()
    ), (
        "plan/SKILL.md's slice-rooted write must read the existing parent body "
        "first, before rendering plan sections into it"
    )


def test_slice_rooted_write_preserves_the_value_claim_section_unchanged():
    assert (
        "`**Value claim:**` section (or enabler justification) and its "
        "`## Enumerated states`\n     section, when present, unchanged"
        in _text()
    ), (
        "plan/SKILL.md's slice-rooted write must preserve the parent's "
        "`**Value claim:**` section (or enabler justification) unchanged — "
        "destroying it would erase /craft:slice's value claim, the artifact "
        "this whole spec exists to produce"
    )


def test_slice_rooted_write_names_why_the_value_claim_matters():
    assert (
        "the artifact this whole spec exists to produce and the\n     "
        "field the spec's `## Slices` ledger reads on a later pass" in _text()
    ), (
        "plan/SKILL.md must name why the value claim must survive — it's the "
        "artifact this whole spec exists to produce and the field the ledger "
        "reads on a later pass"
    )


def test_slice_rooted_write_appends_plan_sections_after_the_preserved_claim():
    assert (
        "Render the same template sections,\n     append them after the "
        "preserved sections" in _text()
    ), (
        "plan/SKILL.md must append the rendered plan sections after the "
        "preserved value claim, not overwrite it"
    )


# --- the slice-rooted path's framing narrows to the chosen slice ---


def test_top_level_framing_narrows_on_the_slice_rooted_path():
    assert (
        "On the slice-rooted path, \"whole\" narrows: `/craft:slice` has "
        "already chosen the increment, so this skill designs the whole of "
        "that one slice, not the whole feature." in _text()
    ), (
        "plan/SKILL.md's opening framing must narrow to the chosen slice on "
        "the slice-rooted path, not claim to design the whole feature"
    )


def test_step_1_skips_the_topic_search_on_the_slice_rooted_path():
    assert (
        "**On the slice-rooted path, skip this topic search.** The spec is "
        "already linked to the slice parent via `--related spec=`" in _text()
    ), (
        "plan/SKILL.md's step 1 must skip the topic-search spec lookup on the "
        "slice-rooted path and resolve the spec via the parent's related-spec "
        "edge instead"
    )


def test_step_1_scopes_design_to_the_chosen_slice():
    assert (
        "scope the design below to the chosen slice, not the whole feature"
        in _text()
    ), (
        "plan/SKILL.md's step 1 must scope the design to the chosen slice on "
        "the slice-rooted path"
    )


# --- the divergence sentence covers Steps 8.5 and 9, not only Step 8 ---


def test_divergence_sentence_names_steps_8_5_and_9():
    assert (
        "Steps 8.5 and 9 then run against whichever parent Step 8 produced."
        in _text()
    ), (
        "plan/SKILL.md's entry-point section must name Steps 8.5 and 9, not "
        "claim the two paths diverge only in Step 8"
    )


# --- the opening line no longer claims plan builds slices ---
#
# /craft:plan always produces tasks — the component-shaped unit beneath a slice.
# It never builds "in slices": /craft:slice is what chooses a slice, and plan
# decomposes the one it's rooted at (or, on the topic-rooted path, the whole
# feature) into tasks. "then build it in slices" was accurate before the slice
# loop existed; it now sits beside the slice-rooted clause it contradicts.


def test_opening_line_no_longer_claims_plan_builds_in_slices():
    assert "then build it in slices" not in _text(), (
        "plan/SKILL.md's opening line must not claim plan 'builds in slices' "
        "— plan always produces tasks; /craft:slice is what chooses a slice, "
        "and the slice-rooted clause right beside this sentence says plan "
        "designs one slice's tasks, not that plan itself builds slices"
    )


def test_opening_line_says_plan_builds_in_tasks():
    assert "Design the whole feature end-to-end, then build it in tasks" in _text(), (
        "plan/SKILL.md's opening line must say plan builds the feature 'in "
        "tasks' — the component-shaped unit it actually writes, whether "
        "rooted at a slice or a topic"
    )


def test_cross_check_query_is_scoped_to_labelled_slice_parents():
    """plan's cross-check asks the same question /craft:slice's guard asks — "is a
    slice already open on this spec" — so it carries the same over-match. An
    unrelated linked task made plan warn about a duplicate slice parent that does
    not exist."""
    assert (
        'lore search "kind:task related-spec:<spec-name> has:label.craft.slice-parent '
        '-status:done -status:dropped -status:superseded"' in _text()
    ), (
        "plan/SKILL.md's open-slice-parent cross-check must filter to "
        "`has:label.craft.slice-parent` — the marker /craft:slice writes at "
        "materialization — so a follow-up or coordination task linked to the same "
        "spec is not reported as an open slice parent"
    )
