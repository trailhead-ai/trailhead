"""Tests for the `## Follow-ups` section parser."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import followups  # noqa: E402


def entry(id_="F1", status="open", extra="", title="Short title", where="`a/b.py`"):
    return (
        f"- **{id_}.** {title}\n"
        f"  - status: {status}\n"
        "  - what: something is wrong\n"
        f"  - where: {where}\n"
        "  - found: during review\n"
        f"{extra}"
    )


def doc(section_body: str, before="# Spec\n\nprose\n\n", after="") -> str:
    return f"{before}## Follow-ups\n\n{section_body}{after}"


def test_no_section_yields_zero_entries():
    assert followups.parse_followups("# Spec\n\n## Slices\n\n- x\n") == []


def test_empty_section_yields_zero_entries():
    assert followups.parse_followups(doc("")) == []


def test_open_entry_parses_every_field():
    [e] = followups.parse_followups(doc(entry(extra="  - from: task/some-task_1.x\n")))
    assert (e.id, e.title, e.status_kind, e.status_detail) == ("F1", "Short title", "open", None)
    assert (e.what, e.where, e.found, e.from_) == (
        "something is wrong",
        ("a/b.py",),
        "during review",
        "task/some-task_1.x",
    )


def test_from_is_optional():
    [e] = followups.parse_followups(doc(entry()))
    assert e.from_ is None


@pytest.mark.parametrize(
    "status,kind,detail",
    [
        ("open", "open", None),
        ("folded into task/x", "folded", "task/x"),
        ("done by task/x", "done", "task/x"),
        ("dropped: not worth it", "dropped", "not worth it"),
    ],
)
def test_each_status_form_parses(status, kind, detail):
    [e] = followups.parse_followups(doc(entry(status=status)))
    assert (e.status_kind, e.status_detail) == (kind, detail)


def test_multiple_where_paths_parse_in_order():
    [e] = followups.parse_followups(doc(entry(where="`a/b.py`, `c.md`")))
    assert e.where == ("a/b.py", "c.md")


def test_entries_come_back_in_document_order():
    body = doc(entry("F2") + entry("F1") + entry("F10"))
    assert [e.id for e in followups.parse_followups(body)] == ["F2", "F1", "F10"]


def test_section_ends_at_next_heading():
    body = doc(entry(), after="\n## Slices\n\n- not a followup\n")
    assert [e.id for e in followups.parse_followups(body)] == ["F1"]


def test_fenced_code_inside_section_is_ignored():
    body = doc(entry() + "\n```\nstray prose\n- **F9.** fake\n```\n")
    assert [e.id for e in followups.parse_followups(body)] == ["F1"]


def test_html_comment_inside_section_is_ignored():
    body = doc(entry() + "\n<!--\nstray prose\n- **F9.** fake\n-->\n")
    assert [e.id for e in followups.parse_followups(body)] == ["F1"]


def test_heading_inside_fence_is_not_the_section():
    body = "```\n## Follow-ups\n" + entry() + "```\n"
    assert followups.parse_followups(body) == []


def test_unmarked_prose_in_section_is_an_error():
    with pytest.raises(followups.FollowupsError) as ei:
        followups.parse_followups(doc(entry() + "\nstray words\n"))
    assert ei.value.reason_code == "malformed-entry"
    assert "Follow-ups" in str(ei.value)


def test_prose_before_first_entry_is_an_error():
    with pytest.raises(followups.FollowupsError) as ei:
        followups.parse_followups(doc("intro words\n" + entry()))
    assert ei.value.reason_code == "malformed-entry"


def test_duplicate_unmasked_heading_is_an_error():
    body = doc(entry()) + "\n## Slices\n\n## Follow-ups\n\n" + entry("F2")
    with pytest.raises(followups.DuplicateHeadingError):
        followups.parse_followups(body)


def test_excise_section_removes_only_the_section():
    body = doc(entry(), before="# Spec\n\nprose\n\n", after="\n## Slices\n\n- x\n")
    assert followups.excise_section(body) == "# Spec\n\nprose\n\n## Slices\n\n- x"


def test_excise_section_without_section_only_trims_trailing_whitespace():
    assert followups.excise_section("# Spec\n\nprose\n\n\n") == "# Spec\n\nprose"
