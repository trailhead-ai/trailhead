"""Stale-tasks check behind the health report.

Fixtures are the real ``ScannedRecord`` / ``VaultScan`` types; ``now`` is injected.
"""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import load_script

stale = load_script("lore.health.stale")
model = load_script("lore.health.model")

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def ago(days=0, seconds=0):
    return (NOW - timedelta(days=days, seconds=seconds)).isoformat()


def rec(vault, kind, name, status="open", sidecar=None, body="", updated=ago(1)):
    sc = dict(sidecar or {})
    if status is not None:
        sc["status"] = status
    return model.ScannedRecord(vault, kind, name, updated, sc, body)


def task(name, days=1, status="open", vault="a", **kw):
    return rec(vault, "task", name, status, updated=ago(days), **kw)


def vault(name, *records, **kw):
    s = model.VaultScan(name, f"/nowhere/{name}", **kw)
    s.records.extend(records)
    return s


def run(scans, selected=None, window=30, now=NOW):
    scans = list(scans)
    sel = {s.vault for s in scans} if selected is None else set(selected)
    return stale.check_stale(scans, sel, now=now, window_days=window)


def names(res):
    assert isinstance(res, stale.StaleResult), res
    return sorted(f.name for f in res.findings)


def spec_link(name):
    return {"related": {"spec": [name]}}


def test_a_task_outside_the_window_is_reported_and_one_inside_is_not():
    res = run([vault("a", task("old", 31), task("new", 29))])
    assert names(res) == ["old"]
    f = res.findings[0]
    assert (f.vault, f.status, f.age_days, f.undated, f.retired_specs) == ("a", "open", 31, False, ())


def test_the_window_start_itself_is_inside_and_a_second_before_it_is_outside():
    res = run([vault("a", task("edge", 30),
                     rec("a", "task", "past", "open", updated=ago(30, 1)))])
    assert names(res) == ["past"]
    assert res.findings[0].age_days == 30


@pytest.mark.parametrize("window,days,reported", [(90, 91, True), (90, 31, False), (30, 31, True), (1, 2, True), (1, 0, False)])
def test_the_window_argument_moves_the_boundary(window, days, reported):
    res = run([vault("a", task("t", days))], window=window)
    assert (names(res) == ["t"]) is reported


def test_a_future_updated_at_is_not_stale():
    assert names(run([vault("a", rec("a", "task", "t", updated=ago(-400)))])) == []


@pytest.mark.parametrize("updated", [None, "yesterday", "2026-10-30", "2020-01-01T00:00:00", 5])
def test_a_task_with_no_readable_updated_at_is_reported_undated(updated):
    res = run([vault("a", rec("a", "task", "t", updated=updated))])
    (f,) = res.findings
    assert (f.undated, f.age_days, f.retired_specs) == (True, None, ())


@pytest.mark.parametrize("status", ["done", "dropped", "superseded", "paused", None, 7])
def test_only_the_four_unfinished_statuses_are_judged(status):
    s = vault("a", rec("a", "task", "t", status, updated=ago(500)))
    s.records[0].sidecar["status"] = status
    res = run([s])
    assert names(res) == []
    assert res.judged == {"a": 0}


@pytest.mark.parametrize("status", ["open", "ready", "in-progress", "blocked"])
def test_every_unfinished_status_is_judged_and_carried(status):
    res = run([vault("a", task("t", 99, status))])
    assert [f.status for f in res.findings] == [status]


@pytest.mark.parametrize("kind", ["decision", "session", "spec", "lesson", "adr"])
def test_no_other_kind_is_judged_however_old(kind):
    res = run([vault("a", rec("a", kind, "x", "open", updated=ago(900)))])
    assert names(res) == [] and res.judged == {"a": 0}


def test_judged_counts_unfinished_tasks_per_selected_vault():
    res = run([vault("a", task("t1"), task("t2", status="blocked"), task("d", status="done")),
               vault("b", task("t3", vault="b"), rec("b", "session", "s")),
               vault("c", task("t4", vault="c"))], selected=["a", "b"])
    assert res.judged == {"a": 2, "b": 1}


def test_an_unselected_vault_is_not_reported_but_resolves_specs():
    res = run([vault("a", task("t", 1, sidecar=spec_link("gone"), vault="a")),
               vault("b", rec("b", "spec", "gone", "dropped"))], selected=["a"])
    assert names(res) == ["t"]
    res = run([vault("a", task("old", 99)), vault("b", task("old2", 99, vault="b"))], selected=["b"])
    assert names(res) == ["old2"]


def test_related_spec_entry_to_a_dropped_spec_reports_a_fresh_task():
    res = run([vault("a", task("t", 1, sidecar=spec_link("s")), rec("a", "spec", "s", "dropped"))])
    (f,) = res.findings
    assert f.age_days is None and not f.undated
    assert [(r.vault, r.name, r.status) for r in f.retired_specs] == [("a", "s", "dropped")]


def test_a_body_wikilink_to_a_superseded_spec_reports_the_task():
    res = run([vault("a", task("t", 1, body="see [[spec/s]] and\n[[s]]\n"),
                     rec("a", "spec", "s", "superseded"))])
    (f,) = res.findings
    assert [(r.name, r.status) for r in f.retired_specs] == [("s", "superseded")]


def test_a_spec_in_another_vault_is_resolved():
    res = run([vault("a", task("t", 1, sidecar=spec_link("s"))), vault("b", rec("b", "spec", "s", "dropped"))])
    assert [(r.vault, r.name) for r in res.findings[0].retired_specs] == [("b", "s")]


@pytest.mark.parametrize("status", ["ready", "active", "complete", "draft"])
def test_a_link_to_a_spec_that_is_not_retired_is_not_reported(status):
    res = run([vault("a", task("t", 1, sidecar=spec_link("s")), rec("a", "spec", "s", status))])
    assert names(res) == []


def test_a_reference_that_resolves_to_a_live_and_a_retired_spec_is_not_reported():
    res = run([vault("a", task("t", 1, sidecar=spec_link("s")), rec("a", "spec", "s", "dropped")),
               vault("b", rec("b", "spec", "s", "ready"))])
    assert names(res) == []


def test_a_spec_with_an_unrecognised_status_is_not_retired():
    res = run([vault("a", task("t", 1, sidecar=spec_link("s")), rec("a", "spec", "s", "archived"))])
    assert names(res) == []


def test_a_bare_name_matching_a_dropped_spec_and_an_active_decision_is_not_reported():
    res = run([vault("a", task("t", 1, body="[[x]]"), rec("a", "spec", "x", "dropped"),
                     rec("a", "decision", "x", "active"))])
    assert names(res) == []


def test_a_bare_name_matching_only_a_dropped_decision_is_left_to_retired_links():
    res = run([vault("a", task("t", 1, body="[[x]]"), rec("a", "decision", "x", "dropped"))])
    assert names(res) == []


def test_a_bare_name_matching_a_dropped_spec_and_a_dropped_decision_is_reported():
    res = run([vault("a", task("t", 1, body="[[x]]"), rec("a", "spec", "x", "dropped"),
                     rec("a", "decision", "x", "dropped"))])
    assert [(r.name, r.status) for r in res.findings[0].retired_specs] == [("x", "dropped")]


def test_a_spec_that_is_unreadable_is_not_reported():
    s = vault("a", task("t", 1, sidecar=spec_link("s")), rec("a", "spec", "s", "dropped"))
    s.unreadable.append(model.UnreadableRecord("a", "spec", "s", "malformed"))
    assert names(run([s])) == []


def test_a_dangling_spec_reference_is_not_reported():
    assert names(run([vault("a", task("t", 1, sidecar=spec_link("nope")))])) == []


def test_a_task_that_is_old_and_tied_to_a_dropped_spec_is_one_finding_with_both_reasons():
    res = run([vault("a", task("t", 200, sidecar=spec_link("s"), body="[[s]] [[spec/s2]]"),
                     rec("a", "spec", "s", "dropped"), rec("a", "spec", "s2", "superseded"))])
    (f,) = res.findings
    assert f.age_days == 200
    assert [(r.name, r.status) for r in f.retired_specs] == [("s", "dropped"), ("s2", "superseded")]


def test_the_supersedes_field_is_not_a_tie():
    res = run([vault("a", task("t", 1, sidecar={"supersedes": ["spec/s"]}), rec("a", "spec", "s", "dropped"))])
    assert names(res) == []


def test_a_sessions_would_be_findings_are_never_reported():
    res = run([vault("a", rec("a", "session", "s", "open", {"related": {"spec": ["s"]}}, updated=ago(900)),
                     rec("a", "spec", "s", "dropped"))])
    assert res.findings == []


def test_not_fully_read_vault_skips_the_check_naming_it():
    res = run([vault("a", task("t", 99)), vault("b", unreadable_reason="denied"), vault("c", skipped_escaping=1)])
    assert res == stale.StaleSkipped(("b", "c"))


def test_the_check_survives_an_in_place_reload():
    scans = [vault("a", task("t", 99, sidecar=spec_link("s")), rec("a", "spec", "s", "dropped"))]
    first = run(scans)
    load_script("lore.health.stale")
    again = load_script("lore.health.stale").check_stale(scans, {"a"}, now=NOW, window_days=30)
    assert len(again.findings) == 1 and again.judged == first.judged
    assert again.findings[0].age_days == first.findings[0].age_days == 99
