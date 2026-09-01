"""`/craft:plan`'s dual entry points — a slice parent, or a topic.

`/craft:plan` runs the same downstream design work either way, but the two entry
points write differently:

  - **Slice-rooted** (argument resolves to an existing `task` record): plan fills
    that parent's body with the plan sections via an update and writes the
    component-shaped child tasks beneath it. It does NOT create a second parent.
    It writes no spec status — the `ready -> planned` advance does not fire.
  - **Topic-rooted** (argument is anything else): plan creates its own parent task
    and, unchanged, still advances a `ready` spec to `planned`. It also gains one
    cross-check: if the resolved spec already has an open slice parent, plan says
    so rather than silently creating a duplicate parent beside it.

Both halves are pinned here so neither path can be silently deleted.
"""

from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
PLAN_SKILL = CRAFT / "skills" / "plan" / "SKILL.md"


def _text() -> str:
    return PLAN_SKILL.read_text()


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


# --- topic-rooted path: unchanged, still advances ready -> planned ---


def test_topic_rooted_path_still_advances_ready_spec_to_planned():
    assert (
        "advance the spec's status `ready → planned` "
        "(`lore record update <spec-id> --status planned`)" in _text()
    ), (
        "plan/SKILL.md must still document the topic-rooted path's write that "
        "advances a ready spec to planned — the old behavior must not be "
        "silently deleted"
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
