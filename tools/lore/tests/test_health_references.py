"""Reference reading shared by the health checks: what each reference is and where it sits.

Fixtures are the real ``ScannedRecord`` / ``VaultScan`` types.
"""

import pytest

from conftest import load_script

references = load_script("lore.health.references")
model = load_script("lore.health.model")


def rec(kind, name, sidecar=None, body="", vault="a"):
    return model.ScannedRecord(vault, kind, name, "2026-03-01", sidecar or {}, body)


def refs(record):
    return [(r.field, r.target, r.default_kind, r.location) for r in references.iter_references(record)]


def wiki(body):
    return [(r.target, r.location) for r in references.iter_references(rec("lesson", "src", body=body))]


# --- body wikilink lines ----------------------------------------------------


def test_wikilinks_report_their_body_line():
    body = "one\ntwo\nsee [[alpha]]\nfour\nfive\nsix\nand [[beta]]\n"
    assert wiki(body) == [("alpha", 3), ("beta", 7)]


def test_moving_a_wikilink_changes_its_reported_line():
    assert wiki("[[alpha]]\nx\ny\n") == [("alpha", 1)]
    assert wiki("x\ny\n[[alpha]]\n") == [("alpha", 3)]


WIKILINK_LINE_CASES = {
    "after-fenced-block": ("a\n```\n[[hidden]]\nmore\n```\n[[real]]\n", 6),
    "after-indented-block": ("a\n\n    [[hidden]]\n    more\n\n[[real]]\n", 6),
    "after-multiline-inline-span": ("a `x\ny\nz` b\n[[real]]\n", 4),
    "crlf-body": ("a\r\nb\r\n```\r\n[[hidden]]\r\n```\r\nc\r\n[[real]]\r\n", 7),
}


@pytest.mark.parametrize(
    "body, line", WIKILINK_LINE_CASES.values(), ids=WIKILINK_LINE_CASES.keys()
)
def test_wikilink_line_is_true_after_code_and_line_endings(body, line):
    assert wiki(body) == [("real", line)]


def test_extract_wikilinks_returns_targets_only():
    assert references.extract_wikilinks("x\n[[a|alias]]\n`[[no]]`\n[[b#h]]") == ["a", "b"]


# --- list-field positions ---------------------------------------------------


def test_related_positions_count_within_each_kinds_list():
    record = rec("task", "src", {"related": {"spec": ["ok", "bad one", "bad two"], "lesson": ["x", "Bad"]}})
    assert refs(record) == [
        ("related spec", "ok", "spec", 1),
        ("related spec", "bad one", "spec", 2),
        ("related spec", "bad two", "spec", 3),
        ("related lesson", "x", "lesson", 1),
        ("related lesson", "Bad", "lesson", 2),
    ]


def test_an_unknown_related_kind_is_not_read():
    record = rec("task", "src", {"related": {"nonsense": ["x"], "spec": ["y"]}})
    assert [r[0] for r in refs(record)] == ["related spec"]


def test_non_string_entries_are_skipped_but_still_take_a_position():
    record = rec("decision", "src", {"supersedes": [7, "a", None, "b"]})
    assert refs(record) == [("supersedes", "a", None, 2), ("supersedes", "b", None, 4)]


def test_supersedes_and_task_depends_on_report_one_based_positions():
    record = rec("task", "src", {"supersedes": ["x", "y"], "depends-on": ["p", "q", "r"]})
    assert refs(record) == [
        ("supersedes", "x", None, 1),
        ("supersedes", "y", None, 2),
        ("depends-on", "p", "task", 1),
        ("depends-on", "q", "task", 2),
        ("depends-on", "r", "task", 3),
    ]


def test_spec_depends_on_drops_the_stage_and_keeps_the_position():
    record = rec("spec", "src", {"depends-on": ["task/a@draft", "b"]})
    assert refs(record) == [("depends-on", "task/a", None, 1), ("depends-on", "b", None, 2)]


def test_parent_reports_no_position():
    assert refs(rec("task", "src", {"parent": "plan"})) == [("parent", "plan", "task", None)]


# --- candidate lookup -------------------------------------------------------


def scan(vault, *records, unreadable=()):
    s = model.VaultScan(vault, f"/nowhere/{vault}")
    s.records.extend(records)
    s.unreadable.extend(model.UnreadableRecord(vault, k, n, "sidecar-malformed") for k, n in unreadable)
    return s


def names(records):
    return sorted((r.vault, r.kind, r.name) for r in records)


def test_a_kinded_reference_resolves_to_same_kind_records_in_every_vault():
    cands = references.build_candidates([
        scan("a", rec("lesson", "x", vault="a"), rec("task", "x", vault="a")),
        scan("b", rec("lesson", "x", vault="b")),
    ])
    found, unreadable = cands.lookup("lesson", "x")
    assert names(found) == [("a", "lesson", "x"), ("b", "lesson", "x")]
    assert unreadable is False


def test_a_bare_name_resolves_to_every_record_of_that_name():
    cands = references.build_candidates([
        scan("a", rec("lesson", "x", vault="a"), rec("task", "x", vault="a")),
        scan("b", rec("decision", "x", vault="b"), rec("task", "other", vault="b")),
    ])
    found, _ = cands.lookup(None, "x")
    assert names(found) == [("a", "lesson", "x"), ("a", "task", "x"), ("b", "decision", "x")]


def test_a_name_held_only_by_an_unreadable_record_reports_the_flag():
    cands = references.build_candidates([scan("a", unreadable=[("lesson", "hidden")])])
    assert cands.lookup("lesson", "hidden") == ((), True)
    assert cands.lookup(None, "hidden") == ((), True)
    assert cands.lookup("task", "hidden") == ((), False)
    assert cands.lookup(None, "absent") == ((), False)


def test_an_unreadable_record_is_flagged_alongside_a_readable_one():
    cands = references.build_candidates([
        scan("a", rec("lesson", "x"), unreadable=[("task", "x")]),
    ])
    found, unreadable = cands.lookup(None, "x")
    assert names(found) == [("a", "lesson", "x")] and unreadable is True
    assert cands.lookup("lesson", "x")[1] is False


def test_the_inverted_supersedes_map_returns_every_claimant_across_vaults():
    cands = references.build_candidates([
        scan("a", rec("decision", "new-a", {"supersedes": ["decision/old", "decision/other"]}, vault="a")),
        scan("b", rec("decision", "new-b", {"supersedes": ["decision/old"]}, vault="b"),
             rec("decision", "unrelated", vault="b")),
    ])
    assert names(cands.superseded_by("decision", "old")) == [
        ("a", "decision", "new-a"), ("b", "decision", "new-b"),
    ]
    assert names(cands.superseded_by("decision", "other")) == [("a", "decision", "new-a")]
    assert cands.superseded_by("decision", "nobody") == ()


def test_a_bare_supersedes_entry_claims_the_name_for_any_kind():
    cands = references.build_candidates([
        scan("a", rec("decision", "new", {"supersedes": ["old"]})),
    ])
    assert names(cands.superseded_by("lesson", "old")) == [("a", "decision", "new")]
    assert names(cands.superseded_by("decision", "old")) == [("a", "decision", "new")]


def test_a_malformed_supersedes_entry_claims_nothing_and_a_repeat_counts_once():
    cands = references.build_candidates([
        scan("a", rec("decision", "new", {"supersedes": ["Bad Entry!", "decision/old", "decision/old"]})),
    ])
    assert names(cands.superseded_by("decision", "old")) == [("a", "decision", "new")]
    assert cands.superseded_by("decision", "Bad Entry!") == ()
