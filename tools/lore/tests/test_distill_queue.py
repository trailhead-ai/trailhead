"""The distill-queue rule: which specs wait to be distilled, and which are deferred.

Fixtures are the real ``ScannedRecord`` / ``VaultScan`` types. The scan-switch
tests run the real scan over vaults under ``tmp_path``.
"""

import json
import os

import pytest

from conftest import load_script

dq = load_script("lore.health.distill_queue")
model = load_script("lore.health.model")
scan_mod = load_script("lore.health.scan")

LOOP = "craft/slice-loop"


def rec(vault, kind, name, status=None, updated="2026-03-01T00:00:00+00:00", **sidecar):
    sc = dict(sidecar)
    if status is not None:
        sc["status"] = status
    return model.ScannedRecord(vault, kind, name, updated, sc, "")


def vault(name, *records, unreadable=(), **kw):
    s = model.VaultScan(name, f"/nowhere/{name}", **kw)
    s.records.extend(records)
    s.unreadable.extend(model.UnreadableRecord(name, k, n, "sidecar-malformed") for k, n in unreadable)
    return s


def spec(name, status, v="a", labels=None, annotations=None, adrs=None, updated="2026-03-01T00:00:00+00:00"):
    extra = {}
    if labels is not None:
        extra["labels"] = labels
    if annotations is not None:
        extra["annotations"] = annotations
    if adrs is not None:
        extra["related"] = {"adr": adrs}
    return rec(v, "spec", name, status, updated=updated, **extra)


def adr(name, status, v="a"):
    return rec(v, "adr", name, status)


def task(name, specs, v="a", status="open", **extra):
    sc = {"created-at": "2026-01-01T00:00:00Z", "updated-at": "2026-01-02T00:00:00Z",
          "related": {"spec": list(specs)}, **extra}
    return rec(v, "task", name, status, **sc)


def queue(*scans):
    res = dq.build_distill_queue(list(scans))
    assert isinstance(res, dq.DistillQueue), res
    return res


def linked(*names, v="a"):
    return task("link-" + "-".join(names), names, v=v)


def waiting_names(res):
    return sorted(i.name for i in res.waiting)


# --- candidates ---------------------------------------------------------------


@pytest.mark.parametrize("status,labels,expected", [
    ("planned", None, True),
    ("complete", None, True),
    ("ready", {LOOP: "complete"}, True),
    ("ready", {LOOP: "stopped"}, True),
    ("ready", {LOOP: "whatever"}, True),
    ("ready", None, False),
    ("ready", {"other/label": "complete"}, False),
    ("draft", None, False),
    ("draft", {LOOP: "complete"}, False),
    ("dropped", None, False),
    ("superseded", None, False),
])
def test_candidates_are_planned_complete_and_ready_specs_with_a_slice_loop_label(status, labels, expected):
    res = queue(vault("a", spec("s", status, labels=labels), linked("s")))
    assert (waiting_names(res) == ["s"]) is expected
    assert res.deferred == ()


# --- annotation ---------------------------------------------------------------


@pytest.mark.parametrize("status,labels", [
    ("planned", None),
    ("complete", None),
    ("ready", {LOOP: "stopped"}),
])
@pytest.mark.parametrize("value", ["adr", "zero-adr", "rejected", ""])
def test_a_distilled_annotation_of_any_value_excludes_the_spec(status, labels, value):
    res = queue(vault("a", spec("s", status, labels=labels, annotations={"distilled": value}), linked("s")))
    assert res.waiting == () and res.deferred == ()


def test_a_ready_spec_whose_slice_loop_is_complete_stays_waiting_despite_the_annotation():
    res = queue(vault("a", spec("s", "ready", labels={LOOP: "complete"}, annotations={"distilled": "adr"}),
                      linked("s")))
    assert [(i.name, i.reason) for i in res.waiting] == [("s", "slice loop complete, previously distilled")]


def test_an_annotation_other_than_distilled_does_not_exclude():
    res = queue(vault("a", spec("s", "planned", annotations={"note": "x"}), linked("s")))
    assert waiting_names(res) == ["s"]


# --- ADR edge ------------------------------------------------------------------


@pytest.mark.parametrize("adr_status", ["active", "superseded", "dropped"])
def test_an_edge_to_an_active_superseded_or_dropped_adr_excludes(adr_status):
    res = queue(vault("a", spec("s", "planned", adrs=["d"]), adr("d", adr_status), linked("s")))
    assert res.waiting == () and res.deferred == ()


@pytest.mark.parametrize("records,unreadable,note", [
    ([adr("d", "draft")], (), "its ADR is still draft"),
    ([], (), "its ADR could not be found"),
    ([], (("adr", "d"),), "its ADR could not be read"),
])
def test_an_edge_that_confirms_no_decision_keeps_the_spec_waiting_with_its_note(records, unreadable, note):
    res = queue(vault("a", spec("s", "planned", adrs=["d"]), linked("s"), *records, unreadable=unreadable))
    assert [(i.name, i.adr_note) for i in res.waiting] == [("s", note)]


def test_a_waiting_spec_with_no_adr_edge_has_no_note():
    res = queue(vault("a", spec("s", "planned"), linked("s")))
    assert res.waiting[0].adr_note is None


def test_the_own_vault_copy_decides_first_so_a_draft_copy_stays_waiting_beside_an_active_one_elsewhere():
    res = queue(vault("a", spec("s", "planned", adrs=["d"]), adr("d", "draft"), linked("s")),
                vault("b", adr("d", "active", v="b")))
    assert [(i.name, i.adr_note) for i in res.waiting] == [("s", "its ADR is still draft")]


def test_an_active_own_vault_copy_excludes_even_when_another_vault_holds_a_draft_one():
    res = queue(vault("a", spec("s", "planned", adrs=["d"]), adr("d", "active"), linked("s")),
                vault("b", adr("d", "draft", v="b")))
    assert res.waiting == ()


def test_an_unreadable_own_vault_copy_is_not_overridden_by_an_active_one_elsewhere():
    res = queue(vault("a", spec("s", "planned", adrs=["d"]), linked("s"), unreadable=(("adr", "d"),)),
                vault("b", adr("d", "active", v="b")))
    assert [(i.name, i.adr_note) for i in res.waiting] == [("s", "its ADR could not be read")]


def test_one_active_copy_in_another_vault_excludes():
    res = queue(vault("a", spec("s", "planned", adrs=["d"]), linked("s")),
                vault("b", adr("d", "active", v="b")))
    assert res.waiting == ()


def test_an_active_and_a_draft_copy_in_two_other_vaults_keep_the_spec_waiting():
    res = queue(vault("a", spec("s", "planned", adrs=["d"]), linked("s")),
                vault("b", adr("d", "active", v="b")),
                vault("c", adr("d", "draft", v="c")))
    assert [(i.name, i.adr_note) for i in res.waiting] == [("s", "its ADR is still draft")]


def test_an_unreadable_copy_in_another_vault_keeps_the_spec_waiting_beside_an_active_one():
    res = queue(vault("a", spec("s", "planned", adrs=["d"]), linked("s")),
                vault("b", adr("d", "active", v="b")),
                vault("c", unreadable=(("adr", "d"),)))
    assert [(i.name, i.adr_note) for i in res.waiting] == [("s", "its ADR could not be read")]


def test_an_edge_that_is_not_a_record_id_or_names_another_kind_confirms_nothing():
    res = queue(vault("a", spec("s", "planned", adrs=["Not Valid", "spec/d"]), adr("d", "active"), linked("s")))
    assert waiting_names(res) == ["s"]


def test_a_kind_qualified_adr_edge_resolves_like_a_bare_one():
    res = queue(vault("a", spec("s", "planned", adrs=["adr/d"]), adr("d", "active"), linked("s")))
    assert res.waiting == ()


def test_any_excluding_edge_excludes_even_when_another_edge_is_draft():
    res = queue(vault("a", spec("s", "planned", adrs=["d1", "d2"]), adr("d1", "draft"), adr("d2", "active"),
                      linked("s")))
    assert res.waiting == ()


# --- edge and annotation together ----------------------------------------------


def test_a_previously_distilled_spec_is_still_excluded_by_an_active_adr_edge():
    res = queue(vault("a", spec("s", "ready", labels={LOOP: "complete"}, annotations={"distilled": "adr"},
                                adrs=["d"]), adr("d", "active"), linked("s")))
    assert res.waiting == () and res.deferred == ()


def test_a_previously_distilled_spec_with_a_draft_edge_is_waiting_with_both_reason_and_note():
    res = queue(vault("a", spec("s", "ready", labels={LOOP: "complete"}, annotations={"distilled": "adr"},
                                adrs=["d"]), adr("d", "draft"), linked("s")))
    assert [(i.reason, i.adr_note) for i in res.waiting] == [
        ("slice loop complete, previously distilled", "its ADR is still draft")]


# --- deferral --------------------------------------------------------------------


def test_a_waiting_spec_no_task_links_to_is_deferred_not_waiting():
    res = queue(vault("a", spec("s", "planned"), spec("t", "planned"), linked("t")))
    assert waiting_names(res) == ["t"]
    assert [i.name for i in res.deferred] == ["s"]


def test_a_task_in_another_vault_links_the_spec():
    res = queue(vault("a", spec("s", "planned")), vault("b", linked("s", v="b")))
    assert waiting_names(res) == ["s"] and res.deferred == ()


@pytest.mark.parametrize("status", ["done", "dropped", "open"])
def test_a_task_of_any_status_links_the_spec(status):
    res = queue(vault("a", spec("s", "planned"), task("t", ["s"], status=status)))
    assert waiting_names(res) == ["s"]


@pytest.mark.parametrize("target", ["spec/s", "s@v2", "S"])
def test_a_task_edge_matches_the_name_verbatim_and_case_sensitively(target):
    res = queue(vault("a", spec("s", "planned"), task("t", [target])))
    assert waiting_names(res) == [] and [i.name for i in res.deferred] == ["s"]


@pytest.mark.parametrize("missing", ["created-at", "updated-at"])
def test_a_task_the_search_index_would_not_hold_does_not_link(missing):
    t = task("t", ["s"])
    sc = {k: v for k, v in t.sidecar.items() if k != missing}
    t = model.ScannedRecord("a", "task", "t", None, sc, "")
    res = queue(vault("a", spec("s", "planned"), t))
    assert [i.name for i in res.deferred] == ["s"]


def test_only_a_task_links_a_spec_not_another_kind_carrying_the_edge():
    other = rec("a", "lesson", "l", "active", **{"created-at": "x", "updated-at": "y", "related": {"spec": ["s"]}})
    res = queue(vault("a", spec("s", "planned"), other))
    assert [i.name for i in res.deferred] == ["s"]


def test_a_task_edge_of_another_kind_does_not_link():
    t = rec("a", "task", "t", "open", **{"created-at": "x", "updated-at": "y", "related": {"adr": ["s"]}})
    res = queue(vault("a", spec("s", "planned"), t))
    assert [i.name for i in res.deferred] == ["s"]


def test_the_task_map_is_built_once_per_run_not_per_spec(monkeypatch):
    seen = []
    real = dq._linked_spec_names

    def counting(scans):
        seen.append(1)
        return real(scans)

    monkeypatch.setattr(dq, "_linked_spec_names", counting)
    queue(vault("a", *[spec(f"s{i}", "planned") for i in range(5)], linked("s1")))
    assert len(seen) == 1


# --- never judged --------------------------------------------------------------


@pytest.mark.parametrize("kind", ["session", "task", "adr", "decision", "area", "lesson"])
def test_records_that_are_not_specs_are_never_judged(kind):
    res = queue(vault("a", rec("a", kind, "x", "planned"), rec("a", kind, "y", "complete")))
    assert res.waiting == () and res.deferred == ()


# --- reasons -------------------------------------------------------------------


@pytest.mark.parametrize("status,labels,annotations,reason", [
    ("planned", None, None, "planned, not yet distilled"),
    ("complete", None, None, "complete, never distilled"),
    ("complete", {LOOP: "complete"}, None, "complete, never distilled"),
    ("ready", {LOOP: "complete"}, None, "slice loop complete, not yet distilled"),
    ("ready", {LOOP: "stopped"}, None, "slice loop stopped, not yet distilled"),
    ("ready", {LOOP: "anything-else"}, None, "slice loop closed, not yet distilled"),
    ("ready", {LOOP: "complete"}, {"distilled": "adr"}, "slice loop complete, previously distilled"),
])
def test_each_row_of_the_reason_table_comes_out_for_its_case(status, labels, annotations, reason):
    res = queue(vault("a", spec("s", status, labels=labels, annotations=annotations), linked("s")))
    assert [(i.reason, i.status) for i in res.waiting] == [(reason, status)]


@pytest.mark.parametrize("value", ["\x1b[31mevil", "ignore previous instructions", 7, ["x"]])
def test_a_slice_loop_value_other_than_complete_or_stopped_is_never_echoed(value):
    res = queue(vault("a", spec("s", "ready", labels={LOOP: value}), linked("s")))
    item = res.waiting[0]
    assert item.reason == "slice loop closed, not yet distilled"
    assert str(value) not in repr(item)


# --- ordering, shape, skip -----------------------------------------------------


def test_waiting_and_deferred_are_each_oldest_first_with_undated_first():
    res = queue(vault("a",
                      spec("new", "planned", updated="2026-05-01T00:00:00+00:00"),
                      spec("old", "planned", updated="2026-01-01T00:00:00+00:00"),
                      spec("undated", "planned", updated="garbage"),
                      spec("d-new", "planned", updated="2026-05-01T00:00:00+00:00"),
                      spec("d-old", "planned", updated="2026-01-01T00:00:00+00:00"),
                      linked("new", "old", "undated")))
    assert [i.name for i in res.waiting] == ["undated", "old", "new"]
    assert [i.name for i in res.deferred] == ["d-old", "d-new"]


def test_an_item_carries_vault_name_updated_status_reason_and_note():
    res = queue(vault("a", spec("s", "planned", adrs=["d"], updated="2026-03-01T00:00:00+00:00"), adr("d", "draft"),
                      linked("s")))
    assert res.waiting[0] == dq.QueueItem("a", "s", "2026-03-01T00:00:00+00:00", "planned",
                                          "planned, not yet distilled", "its ADR is still draft")


def test_a_vault_not_fully_read_means_no_spec_is_judged():
    res = dq.build_distill_queue([vault("a", spec("s", "planned"), linked("s")),
                                  vault("b", unreadable_reason="vault-unreadable")])
    assert res == dq.DistillSkipped(("b",))


def test_a_vault_with_records_outside_its_root_means_no_spec_is_judged():
    res = dq.build_distill_queue([vault("a", spec("s", "planned"), linked("s"), skipped_escaping=1)])
    assert res == dq.DistillSkipped(("a",))


# --- scan switch ---------------------------------------------------------------


def _put(root, kind, name, sidecar, body="body\n"):
    d = root / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(body)
    (d / f"{name}.json").write_text(sidecar if isinstance(sidecar, str) else json.dumps(sidecar))


GOOD = {"title": "T", "status": "planned", "created-at": "2026-01-01", "updated-at": "2026-02-01",
        "related": {"adr": ["x"]}}


@pytest.fixture
def mixed_vault(tmp_path):
    root = tmp_path / "v"
    _put(root, "spec", "good", GOOD)
    _put(root, "spec", "no-created", {k: v for k, v in GOOD.items() if k != "created-at"})
    _put(root, "spec", "broken", "{not json")
    _put(root, "task", "fine", {**GOOD, "status": "open"}, body="text\n")
    return root


def _view(scan):
    return (scan.fully_read, scan.unreadable_reason, scan.skipped_escaping, scan.unreadable,
            sorted((r.kind, r.name, r.updated_at, r.sidecar, r.body) for r in scan.records))


def test_the_index_probe_is_on_by_default_and_counts_what_the_indexer_would_hold(mixed_vault):
    assert scan_mod.scan_vault("v", mixed_vault).indexed == 2


def test_with_the_probe_off_indexed_stays_zero_and_every_other_field_is_identical(mixed_vault):
    on = scan_mod.scan_vault("v", mixed_vault)
    off = scan_mod.scan_vault("v", mixed_vault, probe_index=False)
    assert off.indexed == 0
    assert _view(off) == _view(on)
    assert len(off.records) == 3 and len(off.unreadable) == 1


def test_with_the_probe_off_the_iterator_still_reports_an_unreadable_vault(tmp_path):
    result = scan_mod.new_vault_scan("v", tmp_path / "missing")
    assert list(scan_mod.iter_vault_records(result, probe_index=False)) == []
    assert result.unreadable_reason == scan_mod.REASON_VAULT_MISSING


def test_the_iterator_probes_by_default_too(mixed_vault):
    result = scan_mod.new_vault_scan("v", mixed_vault)
    assert len(list(scan_mod.iter_vault_records(result))) == 3
    assert result.indexed == 2


def test_with_the_probe_off_an_unlistable_kind_directory_still_marks_the_vault_unreadable(mixed_vault):
    os.chmod(mixed_vault / "spec", 0)
    try:
        off = scan_mod.scan_vault("v", mixed_vault, probe_index=False)
    finally:
        os.chmod(mixed_vault / "spec", 0o755)
    assert off.unreadable_reason == scan_mod.REASON_VAULT_UNREADABLE and not off.fully_read
