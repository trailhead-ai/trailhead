"""Lesson-growth check behind the health report.

Fixtures are the real ``ScannedRecord`` / ``VaultScan`` types; ``now`` is injected.
"""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import load_script

lessons = load_script("lore.health.lessons")
model = load_script("lore.health.model")

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
LABEL = {"craft/dispatch-lesson": "executor"}


def ago(days=0, seconds=0):
    return (NOW - timedelta(days=days, seconds=seconds)).isoformat()


def lesson(name, created=None, *, vault="a", kind="lesson", status="active", labels=LABEL, updated=None):
    sc = {"status": status, "created-at": created if created is not None else ago(100)}
    if labels is not None:
        sc["labels"] = dict(labels)
    return model.ScannedRecord(vault, kind, name, updated or sc["created-at"], sc, "")


def vault(name, *records, **kw):
    s = model.VaultScan(name, f"/nowhere/{name}", **kw)
    s.records.extend(records)
    return s


def run(scans, selected=None, window=30):
    scans = list(scans)
    sel = {s.vault for s in scans} if selected is None else set(selected)
    return lessons.check_lessons(scans, sel, now=NOW, window_days=window)


def counts(res, v="a"):
    return res.judged[v], sum(1 for f in res.findings if f.vault == v)


def test_a_vault_with_no_dispatch_lessons_is_zero_of_zero():
    res = run([vault("a")])
    assert counts(res) == (0, 0)
    assert res.findings == [] and res.skipped == {}


def test_lessons_all_older_than_the_window_count_but_are_not_added():
    res = run([vault("a", *(lesson(f"l{i}", ago(40 + i)) for i in range(3)))])
    assert counts(res) == (3, 0)


def test_a_lesson_inside_the_window_is_listed_and_the_others_are_only_counted():
    res = run([vault("a", lesson("new", ago(5)), lesson("old1", ago(50)), lesson("old2", ago(90)))])
    assert counts(res) == (3, 1)
    assert [f.name for f in res.findings] == ["new"]
    f = res.findings[0]
    assert (f.vault, f.kind, f.created_at) == ("a", "lesson", ago(5))


@pytest.mark.parametrize("window,days,added", [(7, 10, False), (14, 10, True), (30, 31, False),
                                               (30, 30, True), (1, 2, False)])
def test_the_window_moves_a_lesson_across_the_boundary_on_either_side(window, days, added):
    res = run([vault("a", lesson("l", ago(days)))], window=window)
    assert counts(res) == (1, int(added))


@pytest.mark.parametrize("created,added", [(ago(0, 1), True), (ago(0), True), (ago(0, -1), False), (ago(-3), False)])
def test_the_window_ends_at_now_so_a_lesson_dated_after_it_is_counted_but_not_added(created, added):
    assert counts(run([vault("a", lesson("l", created))])) == (1, int(added))


def test_a_second_past_the_window_start_is_outside():
    res = run([vault("a", lesson("past", ago(30, 1)))])
    assert counts(res) == (1, 0)


def test_records_that_are_not_executor_dispatch_lessons_are_not_counted():
    res = run([vault(
        "a",
        lesson("unlabelled", ago(1), labels=None),
        lesson("other-value", ago(1), labels={"craft/dispatch-lesson": "reviewer"}),
        lesson("other-key", ago(1), labels={"craft/other": "executor"}),
        lesson("dropped", ago(1), status="dropped"),
        lesson("superseded", ago(1), status="superseded"),
        lesson("a-task", ago(1), kind="task"),
        lesson("counted", ago(1)),
    )])
    assert counts(res) == (1, 1)
    assert [f.name for f in res.findings] == ["counted"]


def test_a_lesson_with_a_non_mapping_labels_field_is_not_counted():
    rec = lesson("odd", ago(1))
    rec.sidecar["labels"] = ["craft/dispatch-lesson=executor"]
    assert counts(run([vault("a", rec)])) == (0, 0)


@pytest.mark.parametrize("created", ["yesterday", "2026-10-30", "2026-10-30T10:00:00", 7])
def test_an_unparseable_created_at_counts_toward_the_total_not_as_added(created):
    rec = lesson("l", ago(1))
    rec.sidecar["created-at"] = created
    assert counts(run([vault("a", rec, lesson("ok", ago(1)))])) == (2, 1)


def test_added_lessons_sort_by_created_at_whatever_their_updated_at_or_name_order():
    older = lesson("zzz", ago(10), updated=ago(0))
    newer = lesson("aaa", ago(5), updated=ago(9))
    res = run([vault("a", newer, older)])
    assert [f.name for f in res.findings] == ["zzz", "aaa"]


def test_added_lessons_sort_by_created_at_across_vaults():
    res = run([vault("a", lesson("x", ago(5))), vault("b", lesson("y", ago(10), vault="b"))])
    assert [(f.vault, f.name) for f in res.findings] == [("b", "y"), ("a", "x")]


def test_an_unreadable_lesson_skips_that_vault_only():
    bad = vault("a", lesson("x", ago(1)))
    bad.unreadable.append(model.UnreadableRecord("a", "lesson", "broken", "sidecar-malformed"))
    res = run([bad, vault("b", lesson("y", ago(1), vault="b"))])
    assert res.skipped == {"a": lessons.UNREADABLE_LESSON}
    assert "a" not in res.judged
    assert counts(res, "b") == (1, 1)
    assert [f.vault for f in res.findings] == ["b"]


def test_an_unreadable_record_of_another_kind_does_not_skip():
    s = vault("a", lesson("x", ago(1)))
    s.unreadable.append(model.UnreadableRecord("a", "task", "broken", "sidecar-malformed"))
    assert counts(run([s])) == (1, 1)


def test_an_escaped_lesson_skips_that_vault_only():
    s = vault("a", lesson("x", ago(1)))
    s.skipped_escaping = 1
    s.escaped.append(model.EscapedRecord("a", "lesson", "esc"))
    res = run([s, vault("b", lesson("y", ago(1), vault="b"))])
    assert res.skipped == {"a": lessons.ESCAPED_LESSON}
    assert counts(res, "b") == (1, 1)


def test_an_escape_of_another_kind_does_not_skip():
    s = vault("a", lesson("x", ago(1)))
    s.skipped_escaping = 1
    s.escaped.append(model.EscapedRecord("a", "task", "esc"))
    assert counts(run([s])) == (1, 1)


def test_a_vault_not_fully_read_skips_that_vault_only():
    s = vault("a", unreadable_reason="vault-missing")
    res = run([s, vault("b", lesson("y", ago(1), vault="b"))])
    assert res.skipped == {"a": lessons.NOT_FULLY_READ}
    assert counts(res, "b") == (1, 1)


def test_an_escape_no_kind_can_be_attributed_to_skips_as_not_fully_read():
    s = vault("a", lesson("x", ago(1)))
    s.skipped_escaping = 1
    assert run([s]).skipped == {"a": lessons.NOT_FULLY_READ}


def test_only_selected_vaults_are_judged_and_each_vault_counts_its_own():
    res = run([vault("a", lesson("x", ago(1))), vault("b", lesson("y", ago(1), vault="b"),
                                                         lesson("z", ago(90), vault="b"))],
              selected=["b"])
    assert res.judged == {"b": 2}
    assert [f.vault for f in res.findings] == ["b"]


def _sidecar_only(name, created=None, *, vault="a", kind="lesson", status="active", labels=LABEL):
    sc = {"status": status, "created-at": created if created is not None else ago(100)}
    if labels is not None:
        sc["labels"] = dict(labels)
    return model.SidecarOnlyRecord(vault, kind, name, sc)


def _with_oversize_body(scan, *only):
    for o in only:
        scan.sidecar_only.append(o)
        scan.unreadable.append(model.UnreadableRecord(o.vault, o.kind, o.name, "oversize"))
    return scan


def test_an_oversize_body_lesson_is_counted_and_listed_from_its_sidecar():
    s = _with_oversize_body(vault("a", lesson("x", ago(5))), _sidecar_only("big", ago(2)))
    res = run([s])
    assert res.skipped == {}
    assert counts(res) == (2, 2)
    assert [f.name for f in res.findings] == ["x", "big"]


def test_an_oversize_body_lesson_outside_the_window_or_retired_or_unlabelled_is_not_listed_or_counted():
    s = _with_oversize_body(vault("a"), _sidecar_only("old", ago(90)),
                            _sidecar_only("gone", ago(2), status="dropped"),
                            _sidecar_only("plain", ago(2), labels=None))
    res = run([s])
    assert counts(res) == (1, 0)


def test_an_unreadable_lesson_with_no_sidecar_still_skips_the_vault():
    s = _with_oversize_body(vault("a", lesson("x", ago(5))), _sidecar_only("big", ago(2)))
    s.unreadable.append(model.UnreadableRecord("a", "lesson", "bigsc", "oversize"))
    res = run([s])
    assert res.skipped == {"a": lessons.UNREADABLE_LESSON}
    assert "a" not in res.judged
