"""authored_content: a record's body minus its kind's pipeline-maintained sections."""

import importlib.util
import json
from pathlib import Path

import pytest

from lore.record.sections import authored_content, section_changed
from lore.record.store import neutralize_fences


def same(kind, before, after):
    return authored_content(kind, before) == authored_content(kind, after)


PLAN = "# Spec\n\nIntent paragraph.\n"
LEDGER = "## Slices\n\n- `task/one`, closed 2026-01-01, covers AC1\n"


def test_line_appended_inside_slices_is_pipeline():
    before = PLAN + "\n" + LEDGER
    after = before + "- `task/two`, closed 2026-01-02, covers AC2\n"
    assert same("spec", before, after)


@pytest.mark.parametrize("prior", [PLAN, PLAN.rstrip("\n")])
@pytest.mark.parametrize("sep", ["\n", "\n\n", ""])
def test_first_ever_slices_appended_is_pipeline(prior, sep):
    lead = "" if (sep == "" and prior.endswith("\n")) else sep or "\n"
    assert same("spec", prior, prior + lead + LEDGER)


def test_changed_line_inside_slices_entry_is_pipeline():
    before = PLAN + "\n" + LEDGER
    after = before.replace("covers AC1", "covers AC1, AC2")
    assert same("spec", before, after)


def test_change_one_line_above_slices_is_authored():
    before = "Intro line A\n## Slices\n- x\n"
    after = "Intro line B\n## Slices\n- x\n"
    assert not same("spec", before, after)


def test_change_after_following_h2_is_authored():
    before = LEDGER + "\n## Other\n\nProse A\n"
    after = LEDGER + "\n## Other\n\nProse B\n"
    assert not same("spec", before, after)


def test_h3_inside_slices_ends_the_section():
    before = LEDGER + "### Revised AC3\n\nText A\n"
    after = LEDGER + "### Revised AC3\n\nText B\n"
    assert not same("spec", before, after)


def test_unterminated_comment_hides_later_slices_heading():
    before = "<!-- never closed\n" + LEDGER
    after = before + "- `task/two`, closed 2026-01-02\n"
    assert not same("spec", before, after)


def test_terminated_comment_does_not_hide_later_slices_heading():
    before = "<!-- closed -->\n" + LEDGER
    after = before + "- `task/two`, closed 2026-01-02\n"
    assert same("spec", before, after)


def test_slices_heading_inside_fence_is_not_a_boundary():
    before = "```\n## Slices\n```\nauthored A\n"
    after = "```\n## Slices\n```\nauthored B\n"
    assert not same("spec", before, after)


def test_text_inside_fence_that_mentions_slices_is_authored():
    before = "~~~\n## Slices\nfenced A\n~~~\n"
    after = "~~~\n## Slices\nfenced B\n~~~\n"
    assert not same("spec", before, after)


def test_slices_heading_inside_comment_is_not_a_boundary():
    before = "<!--\n## Slices\n-->\nauthored A\n"
    after = "<!--\n## Slices\n-->\nauthored B\n"
    assert not same("spec", before, after)


def test_sections_are_found_on_the_text_the_guard_compares():
    tagged = "<external-memory\n>\nauthored\n" + LEDGER + "<external-memory>\n"
    grown = tagged + "- `task/two`, closed 2026-01-02\n"
    for body in (tagged, grown, PLAN + "\n" + LEDGER):
        assert authored_content("spec", neutralize_fences(body)) == neutralize_fences(
            authored_content("spec", body)
        )
    assert same("spec", neutralize_fences(tagged), neutralize_fences(grown))
    edited = tagged.replace("authored", "edited")
    assert not same("spec", neutralize_fences(tagged), neutralize_fences(edited))


@pytest.mark.parametrize("heading", ["## slices", "## SLICES", "## Slices"])
def test_heading_is_case_insensitive(heading):
    before = f"{PLAN}\n{heading}\n- a\n"
    after = f"{PLAN}\n{heading}\n- a\n- b\n"
    assert same("spec", before, after)


@pytest.mark.parametrize("heading", [" ## Slices", "  ## Slices", "text ## Slices", "See the ## Slices list"])
def test_indented_or_inline_mention_is_not_a_section(heading):
    before = f"{PLAN}\n{heading}\n- a\n"
    after = f"{PLAN}\n{heading}\n- a\n- b\n"
    assert not same("spec", before, after)


def test_two_driver_run_blocks_removed_and_plan_between_is_authored():
    run1 = "## Driver run\n\n- **Phase:** plan\n"
    run2 = "## Driver run\n\n- **Phase:** build\n"
    plan_a = "## Plan\n\nStep A\n\n"
    plan_b = "## Notes\n\nStep B\n\n"
    base = "# Task\n\nValue.\n\n"
    both = base + plan_a + run1 + plan_b + run2
    assert same("task", both, base + plan_a + run1 + plan_b)
    assert same("task", both, both.replace("build", "pr-tail"))
    assert not same("task", both, both.replace("Step B", "Step C"))
    assert not same("task", both, both.replace("Step A", "Step C"))


def test_sections_are_per_kind():
    slices_on_task = "# T\n\n" + LEDGER
    assert not same("task", slices_on_task, slices_on_task + "- more\n")
    driver_on_spec = "# S\n\n## Driver run\n\n- **Phase:** plan\n"
    assert not same("spec", driver_on_spec, driver_on_spec + "- **Phase:** build\n")
    assert same("spec", slices_on_task, slices_on_task + "- more\n")
    assert same("task", driver_on_spec, driver_on_spec + "- **Phase:** build\n")


@pytest.mark.parametrize("kind", ["lesson", "decision", "adr", "area", "unknown-kind"])
def test_other_kinds_are_returned_unchanged(kind):
    body = PLAN + "\n" + LEDGER + "## Driver run\n\n- x\n  \n\n"
    assert authored_content(kind, body) == body


@pytest.mark.parametrize("eol", ["\r\n", "\r", "\n"])
def test_commonmark_line_endings(eol):
    def b(*lines):
        return eol.join(lines) + eol

    before = b("# Spec", "", "Intro", "## Slices", "- one")
    after = b("# Spec", "", "Intro", "## Slices", "- one", "- two")
    assert same("spec", before, after)
    assert not same("spec", before, b("# Spec", "", "Intro B", "## Slices", "- one"))


def test_unicode_line_separators_do_not_anchor_a_heading():
    before = "Intro ## Slices\n- a\n"
    after = "Intro ## Slices\n- a\n- b\n"
    assert not same("spec", before, after)


def test_empty_body():
    assert authored_content("spec", "") == ""
    assert same("spec", "", "## Slices\n- a\n")


def test_body_that_is_only_the_heading():
    assert same("spec", "## Slices", "## Slices\n- a")
    assert same("spec", "## Slices", "")


def test_duplicate_slices_headings_fail_closed():
    before = PLAN + "\n" + LEDGER + "\nhidden A\n\n## Slices\nmore\n"
    after = PLAN + "\n" + LEDGER + "\nhidden B\n\n## Slices\nmore\n"
    assert not same("spec", before, after)
    single = PLAN + "\n" + LEDGER
    assert not same("spec", single + "\n## Slices\n- dup\n", single + "\n## Slices\n- other\n")


def test_unterminated_fence_hides_later_heading():
    before = "```\nopen\n" + LEDGER
    after = before + "- more\n"
    assert not same("spec", before, after)


def test_authored_text_survives_in_output():
    out = authored_content("spec", PLAN + "\n" + LEDGER + "\n## Other\n\nkeep me\n")
    assert "Intent paragraph." in out and "keep me" in out
    assert "task/one" not in out


FIXTURES = Path(__file__).parent / "fixtures" / "sections"
_REPLAY_PATH = Path(__file__).parent.parent / "scripts" / "replay_sections.py"


def _replay_module():
    spec = importlib.util.spec_from_file_location("replay_sections", _REPLAY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CASES = json.loads((FIXTURES / "cases.json").read_text())


@pytest.mark.parametrize("name", sorted(_CASES))
def test_real_shape_fixture_classifies_as_the_replay_did(name):
    case = _CASES[name]
    before = (FIXTURES / name / "before.md").read_bytes().decode()
    after = (FIXTURES / name / "after.md").read_bytes().decode()
    replay_class = _replay_module().expected_class(case["kind"], before, after)
    lore_class = "pipeline" if same(case["kind"], before, after) else "authored"
    assert replay_class == case["expected"]
    assert lore_class == case["expected"]


def test_fixture_corpus_produces_both_classes():
    seen = set()
    for name, case in _CASES.items():
        before = (FIXTURES / name / "before.md").read_bytes().decode()
        after = (FIXTURES / name / "after.md").read_bytes().decode()
        seen.add("pipeline" if same(case["kind"], before, after) else "authored")
    assert seen == {"pipeline", "authored"}


def test_four_space_indented_hash_line_does_not_end_the_section():
    before = LEDGER + "    # wrapped entry text\n- b\n"
    assert same("spec", before, before + "- c\n")


def test_three_space_indented_heading_ends_the_section():
    before = LEDGER + "   ## Other\n\nProse A\n"
    assert not same("spec", before, before.replace("Prose A", "Prose B"))


FOLLOWUPS = "## Follow-ups\n\n- `lesson/one`, raised 2026-01-01\n"
FOLLOWUPS_MORE = FOLLOWUPS + "- `lesson/two`, raised 2026-01-02\n"


def test_followups_change_without_existing_section_is_pipeline():
    assert same("spec", PLAN, PLAN + "\n" + FOLLOWUPS)


def test_followups_change_with_existing_section_is_pipeline():
    assert same("spec", PLAN + "\n" + FOLLOWUPS, PLAN + "\n" + FOLLOWUPS_MORE)


def test_followups_change_before_slices_is_pipeline():
    before = PLAN + "\n" + FOLLOWUPS + "\n" + LEDGER
    after = PLAN + "\n" + FOLLOWUPS_MORE + "\n" + LEDGER
    assert same("spec", before, after)


def test_followups_change_after_slices_is_pipeline():
    before = PLAN + "\n" + LEDGER + "\n" + FOLLOWUPS
    after = PLAN + "\n" + LEDGER + "\n" + FOLLOWUPS_MORE
    assert same("spec", before, after)


def test_followups_change_alongside_authored_change_is_authored():
    before = PLAN + "\n" + FOLLOWUPS
    after = PLAN.replace("Intent", "Changed intent") + "\n" + FOLLOWUPS_MORE
    assert not same("spec", before, after)


def test_duplicate_followups_headings_exempt_nothing():
    base = PLAN + "\n" + FOLLOWUPS
    assert not same("spec", base + "\n## Follow-ups\nA\n", base + "\n## Follow-ups\nB\n")


@pytest.mark.parametrize("wrapper", ["```\n{h}\n```\n", "<!--\n{h}\n-->\n"])
def test_followups_heading_inside_fence_or_comment_is_not_a_section(wrapper):
    hidden = wrapper.format(h="## Follow-ups\nnote one")
    before = PLAN + "\n" + hidden
    after = PLAN + "\n" + hidden.replace("note one", "note two")
    assert not same("spec", before, after)


@pytest.mark.parametrize("heading", ["## Follow-ups (out of scope)", "### Follow-ups"])
def test_near_miss_followups_heading_is_authored(heading):
    before = PLAN + "\n" + heading + "\n\n- a\n"
    after = PLAN + "\n" + heading + "\n\n- b\n"
    assert not same("spec", before, after)


def test_followups_section_in_a_task_is_authored():
    before = "# Task\n\nintro\n\n## Follow-ups\n\n- a\n"
    after = before.replace("- a", "- b")
    assert not same("task", before, after)


FU = "## Follow-ups"
FU_BODY = PLAN + "\n## Follow-ups\n\n- F1 one\n"


def test_section_changed_is_false_for_identical_bodies_and_when_absent_in_both():
    assert not section_changed(FU_BODY, FU_BODY, FU)
    assert not section_changed(PLAN, PLAN + "\nmore intro\n", FU)


def test_section_changed_sees_an_existing_section_edited_extended_or_removed():
    assert section_changed(FU_BODY, FU_BODY + "- F2 two\n", FU)
    assert section_changed(FU_BODY, FU_BODY.replace("F1 one", "F1 uno"), FU)
    assert section_changed(FU_BODY, PLAN, FU)


def test_section_changed_sees_the_section_created_from_nothing():
    assert section_changed(PLAN, FU_BODY, FU)


def test_section_changed_ignores_blank_lines_before_the_next_section():
    grown = FU_BODY + "\n## Slices\n\n- a\n"
    assert not section_changed(FU_BODY, grown, FU)
    assert not section_changed(FU_BODY.rstrip("\n"), FU_BODY, FU)


def test_section_changed_ignores_edits_outside_the_section_and_other_sections():
    other = FU_BODY + "\n## Slices\n\n- a\n"
    assert not section_changed(other, other.replace("- a", "- b"), FU)
    assert not section_changed(FU_BODY, FU_BODY.replace("Intent", "Changed intent"), FU)


def test_section_changed_ends_the_section_at_the_next_heading():
    before = FU_BODY + "\n## Notes\n\nx\n"
    assert not section_changed(before, before.replace("x", "y"), FU)


def test_section_changed_is_ambiguity_closed_when_a_heading_repeats_and_bodies_differ():
    twice = FU_BODY + "\n## Follow-ups\n\n- F9 stray\n"
    assert section_changed(twice, twice.replace("F9 stray", "F9 other"), FU)
    assert section_changed(twice, twice.replace("Intent", "Changed"), FU)
    assert not section_changed(twice, twice, FU)


def test_section_changed_ignores_a_heading_hidden_in_a_fence():
    hidden = PLAN + "\n```\n## Follow-ups\n- F1\n```\n"
    assert not section_changed(hidden, hidden.replace("- F1", "- F2"), FU)


WAITING = "## Waiting\n\n- **AC1.** set aside\n  - waits on: x\n  - 2026-11-03 waiting: y\n"


def test_waiting_section_is_pipeline_for_a_spec_and_authored_for_other_kinds():
    before = PLAN
    after = PLAN + "\n" + WAITING
    assert same("spec", before, after)
    assert same("spec", after, after.replace("set aside", "parked"))
    assert not same("spec", after, after.replace("Intent", "Changed intent"))
    assert not same("task", before, after)


def test_waiting_ends_at_the_next_heading_so_text_after_it_stays_authored():
    body = PLAN + "\n" + WAITING + "\n## Other\n\nkeep\n"
    assert not same("spec", body, body.replace("keep", "changed"))
    assert same("spec", body, body.replace("set aside", "parked"))


def test_duplicate_waiting_headings_are_not_exempted():
    twice = PLAN + "\n" + WAITING + "\n" + WAITING
    assert not same("spec", twice, twice.replace("parked", "x").replace("set aside", "other"))
    assert "set aside" in authored_content("spec", twice)


def test_waiting_heading_inside_a_fence_is_not_the_section():
    hidden = PLAN + "\n```\n" + WAITING + "```\n"
    assert not same("spec", hidden, hidden.replace("set aside", "parked"))


def test_section_changed_sees_a_waiting_entry_added_edited_and_removed():
    base = PLAN + "\n" + WAITING
    assert section_changed(PLAN, base, "## Waiting")
    assert section_changed(base, base + "- **AC2.** x\n", "## Waiting")
    assert section_changed(base, PLAN, "## Waiting")
    assert not section_changed(base, base.replace("Intent", "Other"), "## Waiting")
    assert not section_changed(PLAN, PLAN + "\n## Slices\n- a\n", "## Waiting")


def test_waiting_unregistered_makes_a_waiting_change_authored(monkeypatch):
    from lore.record import sections

    base = PLAN + "\n" + WAITING
    monkeypatch.setitem(
        sections.PIPELINE_SECTIONS,
        "spec",
        tuple(s for s in sections.PIPELINE_SECTIONS["spec"] if s.heading != "## Waiting"),
    )
    assert not same("spec", PLAN, base)
