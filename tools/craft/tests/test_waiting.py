"""Tests for the `## Waiting` section parser."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import waiting  # noqa: E402
from waiting import REASON_CODES, WaitingParseError, parse_waiting  # noqa: E402

PENDING = """\
- **AC32.** pending check — task/build-the-thing
  - check: the banner shows
  - how: open the app and look
  - changed behaviour: yes
  - 2026-11-02 waiting: set by slice
"""

SET_ASIDE = """\
- **AC17.** set aside
  - waits on: legal sign-off
  - 2026-11-03 waiting: parked
"""


def body(section: str, before: str = "# Spec\n\nIntro.\n\n", after: str = "") -> str:
    return f"{before}## Waiting\n\n{section}{after}"


def code_of(text: str) -> str:
    with pytest.raises(WaitingParseError) as exc:
        parse_waiting(text)
    assert exc.value.code in REASON_CODES
    return exc.value.code


def test_no_heading_is_zero_entries_and_a_section_with_one_check_is_one_entry():
    assert parse_waiting("# Spec\n\nNothing here.\n") == []
    entries = parse_waiting(body(PENDING))
    assert len(entries) == 1
    e = entries[0]
    assert e.criterion_id == "AC32"
    assert e.building_slice == "build-the-thing"
    assert e.kind == "pending check"
    assert e.check == "the banner shows"
    assert e.how == "open the app and look"
    assert e.changed_behaviour is True
    assert e.waits_on is None
    assert e.history == (("2026-11-02", "waiting", "set by slice"),)
    assert e.status == "waiting"
    assert e.set_date == "2026-11-02"


def test_pending_check_and_set_aside_parse_to_two_kinds():
    pending, aside = parse_waiting(body(PENDING + "\n" + SET_ASIDE))
    assert (pending.kind, aside.kind) == ("pending check", "set aside")
    assert aside.criterion_id == "AC17"
    assert aside.building_slice is None
    assert aside.check is None and aside.how is None
    assert aside.waits_on == "legal sign-off"
    assert aside.changed_behaviour is False


def test_status_follows_latest_history_line_and_history_keeps_order():
    waiting_only = parse_waiting(body(PENDING))[0]
    failed = parse_waiting(body(PENDING + "  - 2026-11-04 failed: nope\n"))[0]
    assert waiting_only.status == "waiting"
    assert failed.status == "failed"
    assert failed.history == (
        ("2026-11-02", "waiting", "set by slice"),
        ("2026-11-04", "failed", "nope"),
    )
    assert failed.set_date == "2026-11-02"


@pytest.mark.parametrize("value,expected", [("yes", True), ("no", False)])
def test_changed_behaviour_yes_and_no(value, expected):
    text = PENDING.replace("changed behaviour: yes", f"changed behaviour: {value}")
    assert parse_waiting(body(text))[0].changed_behaviour is expected


@pytest.mark.parametrize("value", ["maybe", "Yes", "true"])
def test_changed_behaviour_other_values_refuse(value):
    text = PENDING.replace("changed behaviour: yes", f"changed behaviour: {value}")
    assert code_of(body(text)) == "bad-changed-behaviour"


def test_changed_behaviour_absent_or_empty_refuses_as_missing():
    absent = "".join(ln for ln in PENDING.splitlines(keepends=True) if "changed" not in ln)
    assert code_of(body(absent)) == "missing-changed-behaviour"
    empty = PENDING.replace("changed behaviour: yes", "changed behaviour:")
    assert code_of(body(empty)) == "missing-changed-behaviour"


def _without(text: str, line_prefix: str) -> str:
    return "".join(
        ln for ln in text.splitlines(keepends=True) if not ln.lstrip(" -").startswith(line_prefix)
    )


FAIL_CLOSED = {
    "duplicate-heading": lambda: body(PENDING, after="\n## Other\n\nx\n\n## Waiting\n\n" + SET_ASIDE),
    "unterminated-fence": lambda: body(PENDING + "\n```\nstray\n"),
    "unmarked-prose": lambda: body(PENDING + "\nSome stray prose.\n"),
    "missing-history": lambda: body(_without(PENDING, "2026-11-02")),
    "unknown-kind": lambda: body(PENDING.replace("pending check", "maybe check")),
    "unknown-status": lambda: body(PENDING.replace("waiting:", "pending:")),
    "malformed-date": lambda: body(PENDING.replace("2026-11-02", "2026-13-45")),
    "missing-check": lambda: body(_without(PENDING, "check:")),
    "missing-how": lambda: body(_without(PENDING, "how:")),
    "missing-waits-on": lambda: body(_without(SET_ASIDE, "waits on:")),
    "bad-building-slice": lambda: body(PENDING.replace("task/build-the-thing", "task/Bad Slice")),
    "duplicate-entry": lambda: body(PENDING + "\n" + PENDING),
    "duplicate-field": lambda: body(PENDING.replace("  - how:", "  - check: again\n  - how:")),
    "unexpected-field": lambda: body(SET_ASIDE.replace("  - 2026", "  - check: x\n  - 2026")),
    "bad-entry-line (field after history)": lambda: body(PENDING + "  - how: again\n"),
    "bad-entry-line (unreadable indented line)": lambda: body(PENDING + "    stray indented\n"),
}


@pytest.mark.parametrize("case", sorted(FAIL_CLOSED))
def test_each_fail_closed_case_refuses_with_its_own_code(case):
    assert code_of(FAIL_CLOSED[case]()) == case.split(" ")[0]


def test_prose_and_orphan_field_before_any_entry_refuse_as_unmarked_prose():
    assert code_of(body("Some stray prose.\n\n" + PENDING)) == "unmarked-prose"
    assert code_of(body("  - check: orphan\n\n" + PENDING)) == "unmarked-prose"


def test_reason_codes_are_distinct_per_case():
    codes = {code_of(make()) for make in FAIL_CLOSED.values()}
    assert codes == {case.split(" ")[0] for case in FAIL_CLOSED}


def test_malformed_date_shapes_all_refuse():
    for bad in ("2026-1-4", "26-11-02", "2026-02-30", "2026/11/02"):
        assert code_of(body(PENDING.replace("2026-11-02", bad))) == "malformed-date"


def test_building_slice_shape_variants_refuse():
    for bad in ("task/", "build-the-thing", "task/-x", "task/a/b", "task/A"):
        text = PENDING.replace("task/build-the-thing", bad)
        assert code_of(body(text)) == "bad-building-slice", bad


def test_comment_and_fence_inside_section_contribute_no_entry():
    section = (
        "<!-- - **AC99.** set aside -->\n"
        "<!--\n- **AC98.** set aside\n-->\n"
        "```\n- **AC97.** set aside\n```\n" + PENDING
    )
    entries = parse_waiting(body(section))
    assert [e.criterion_id for e in entries] == ["AC32"]


def test_unterminated_comment_refuses_rather_than_dropping_entries():
    assert code_of(body(PENDING + "\n<!-- never closed\n" + SET_ASIDE)) == "unterminated-fence"


def test_waiting_heading_inside_a_fence_is_not_the_section():
    text = "# Spec\n\n```\n## Waiting\n\n" + PENDING + "```\n"
    assert parse_waiting(text) == []
    real = text + "\n## Waiting\n\n" + SET_ASIDE
    assert [e.criterion_id for e in parse_waiting(real)] == ["AC17"]


def test_section_ends_at_next_heading():
    entries = parse_waiting(body(PENDING, after="\n## Slices\n\n" + SET_ASIDE))
    assert [e.criterion_id for e in entries] == ["AC32"]


def test_entry_shaped_text_before_the_section_is_ignored():
    text = SET_ASIDE + "\n" + body(PENDING, before="")
    assert [e.criterion_id for e in parse_waiting(text)] == ["AC32"]


def test_error_carries_code_and_message():
    with pytest.raises(WaitingParseError) as exc:
        parse_waiting(body("prose\n"))
    assert exc.value.code == "unmarked-prose"
    assert "prose" in str(exc.value)
    assert waiting.WaitingParseError is WaitingParseError


def _pending(cid, slice_):
    return PENDING.replace("AC32", cid).replace("task/build-the-thing", f"task/{slice_}")


def test_one_criterion_under_two_building_slices_is_two_entries_each_with_its_own_slice():
    entries = parse_waiting(body(_pending("AC7", "a") + "\n" + _pending("AC7", "b")))
    assert [(e.criterion_id, e.building_slice) for e in entries] == [("AC7", "a"), ("AC7", "b")]


def test_the_key_is_criterion_and_building_slice_or_none_for_a_set_aside_criterion():
    entries = parse_waiting(body(_pending("AC7", "a") + "\n" + SET_ASIDE))
    assert [e.key for e in entries] == [("AC7", "a"), ("AC17", None)]


def test_a_set_aside_criterion_and_a_pending_check_for_it_are_two_entries():
    text = body(SET_ASIDE.replace("AC17", "AC7") + "\n" + _pending("AC7", "a"))
    assert [e.key for e in parse_waiting(text)] == [("AC7", None), ("AC7", "a")]


def test_a_repeated_key_refuses_and_names_the_criterion_and_building_slice():
    with pytest.raises(WaitingParseError) as exc:
        parse_waiting(body(_pending("AC7", "a") + "\n" + _pending("AC7", "b") + "\n" + _pending("AC7", "a")))
    assert exc.value.code == "duplicate-entry"
    assert "AC7" in str(exc.value) and "task/a" in str(exc.value) and "task/b" not in str(exc.value)


def test_a_set_aside_criterion_given_twice_refuses_as_a_repeated_key():
    two = SET_ASIDE + "\n" + SET_ASIDE
    assert code_of(body(two)) == "duplicate-entry"
    with pytest.raises(WaitingParseError) as exc:
        parse_waiting(body(two))
    assert "AC17" in str(exc.value) and "set aside" in str(exc.value)


@pytest.mark.parametrize(
    "heading", ["### Note", "# Top", "#### Deep", "###### Six", "   ### Indented", "###", "## Other"]
)
def test_any_unmasked_atx_heading_ends_the_section(heading):
    entries = parse_waiting(body(PENDING, after=f"\n{heading}\n\nprose after it\n\n" + SET_ASIDE))
    assert [e.criterion_id for e in entries] == ["AC32"]


@pytest.mark.parametrize(
    "line", ["    ### four spaces", "####### seven", "#nospace", "### \u200b".replace("### ", "###\u00a0")]
)
def test_a_line_that_is_not_an_atx_heading_does_not_end_the_section(line):
    with pytest.raises(WaitingParseError) as exc:
        parse_waiting(body(PENDING, after=f"\n{line}\n"))
    assert exc.value.code in {"unmarked-prose", "bad-entry-line"}


def test_a_heading_inside_a_fence_in_the_section_does_not_end_it():
    entries = parse_waiting(body(PENDING + "\n```\n### Note\n```\n\n" + SET_ASIDE.replace("AC17", "AC18")))
    assert [e.criterion_id for e in entries] == ["AC32", "AC18"]


def test_the_shared_helpers_for_waiting_with_the_section_absent():
    text = "# Spec\n\nprose\n\n"
    assert not waiting.has_section(text)
    assert waiting.section_is_last(text)
    assert waiting.excise_section(text) == "# Spec\n\nprose"


def test_the_shared_helpers_for_waiting_with_the_section_last():
    text = body(PENDING, before="# Spec\n\nprose\n\n") + "\n\n"
    assert waiting.has_section(text)
    assert waiting.section_is_last(text)
    assert waiting.excise_section(text) == "# Spec\n\nprose"


def test_the_shared_helpers_for_waiting_with_the_section_mid_body():
    text = body(PENDING, before="# Spec\n\n", after="\n## Slices\n\n- x\n")
    assert waiting.has_section(text)
    assert not waiting.section_is_last(text)
    assert waiting.excise_section(text) == "# Spec\n\n## Slices\n\n- x"


def test_the_shared_helpers_for_waiting_ignore_a_fenced_heading_and_refuse_a_duplicate():
    fenced = "# Spec\n\n```\n## Waiting\n```\n"
    assert not waiting.has_section(fenced)
    with pytest.raises(waiting.DuplicateHeadingError):
        waiting.has_section(body(PENDING, after="\n## Waiting\n"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t, v: t.replace("pending check", v),
        lambda t, v: t.replace("waiting:", f"{v}:"),
        lambda t, v: t.replace("2026-11-02", v),
    ],
    ids=["kind", "status", "date"],
)
def test_vault_text_echoed_in_a_parse_refusal_is_bounded(mutate):
    with pytest.raises(WaitingParseError) as e:
        parse_waiting(body(mutate(PENDING, "9" + "z" * 5000)))
    assert len(str(e.value)) < 200
