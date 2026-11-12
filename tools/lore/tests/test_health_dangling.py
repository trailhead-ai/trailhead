"""Dangling-reference check behind the health report.

Fixtures are the real ``ScannedRecord`` / ``VaultScan`` types; one test runs the
real ``scan_vault`` over an on-disk vault and feeds its result to the check.
"""

import dataclasses
import json

import pytest

from conftest import load_script

dangling = load_script("lore.health.dangling")
model = load_script("lore.health.model")
scan_mod = load_script("lore.health.scan")


def rec(vault, kind, name, sidecar=None, body="", updated="2026-03-01"):
    return model.ScannedRecord(vault, kind, name, updated, sidecar or {}, body)


def vault(name, *records, **kw):
    s = model.VaultScan(name, f"/nowhere/{name}", **kw)
    s.records.extend(records)
    return s


def run(scans, selected=None):
    scans = list(scans)
    sel = {s.vault for s in scans} if selected is None else set(selected)
    return dangling.check_dangling(scans, sel)


def found(result):
    return sorted((f.name, f.field, f.target) for f in result.findings)


# (id, linking kind, sidecar, body, expected field, expected target)
SHAPES = [
    ("related", "task", {"related": {"lesson": ["gone"]}}, "", "related lesson", "lesson/gone"),
    ("related-kind-name", "task", {"related": {"lesson": ["decision/gone"]}}, "",
     "related lesson", "decision/gone"),
    ("supersedes", "decision", {"supersedes": ["decision/gone"]}, "", "supersedes",
     "decision/gone"),
    ("task-depends-on", "task", {"depends-on": ["gone"]}, "", "depends-on", "task/gone"),
    ("spec-depends-on-stage", "spec", {"depends-on": ["task/gone@draft"]}, "", "depends-on",
     "task/gone"),
    ("parent", "task", {"parent": "gone"}, "", "parent", "task/gone"),
    ("wikilink-kind", "lesson", {}, "see [[lesson/gone]] here", "wikilink", "lesson/gone"),
    ("wikilink-bare", "lesson", {}, "see [[gone]] here", "wikilink", "gone"),
    ("wikilink-alias", "lesson", {}, "see [[gone|the thing]]", "wikilink", "gone"),
    ("wikilink-heading", "lesson", {}, "see [[gone#intro]]", "wikilink", "gone"),
]
IDS = [s[0] for s in SHAPES]


def _targets(*, present):
    if not present:
        return []
    return [rec("a", k, "gone") for k in ("lesson", "decision", "task")]


@pytest.mark.parametrize("shape", SHAPES, ids=IDS)
def test_missing_target_is_reported_with_field_and_target(shape):
    _, kind, sidecar, body, field, target = shape
    res = run([vault("a", rec("a", kind, "src", sidecar, body))])
    assert found(res) == [("src", field, target)]


@pytest.mark.parametrize("shape", SHAPES, ids=IDS)
def test_existing_target_is_not_reported(shape):
    _, kind, sidecar, body, _, _ = shape
    res = run([vault("a", rec("a", kind, "src", sidecar, body), *_targets(present=True))])
    assert res.findings == []


def test_finding_carries_the_linking_record():
    res = run([vault("a", rec("a", "lesson", "src", {}, "[[gone]]", updated="2026-02-02"))])
    (f,) = res.findings
    assert (f.vault, f.kind, f.name, f.updated_at) == ("a", "lesson", "src", "2026-02-02")
    assert f.malformed is False


# --- which vaults count ----------------------------------------------------


def test_target_in_second_vault_is_not_reported_when_only_first_selected():
    a = vault("a", rec("a", "lesson", "src", {}, "[[lesson/gone]]"))
    b = vault("b", rec("b", "lesson", "gone"))
    res = run([a, b], selected={"a"})
    assert res.findings == []
    assert res.judged == {"a": 1}


def test_dangling_in_unselected_vault_is_not_reported():
    a = vault("a", rec("a", "lesson", "ok"))
    b = vault("b", rec("b", "lesson", "src", {}, "[[lesson/gone]]"))
    res = run([a, b], selected={"a"})
    assert res.findings == []
    res = run([a, b], selected={"b"})
    assert found(res) == [("src", "wikilink", "lesson/gone")]


def test_session_record_is_never_reported_but_is_a_target():
    sess = rec("a", "session", "s1", {"related": {"lesson": ["gone"]}}, "[[gone]]")
    linker = rec("a", "lesson", "src", {}, "[[session/s1]] and [[s1]]")
    res = run([vault("a", sess, linker)])
    assert res.findings == []


# --- wikilink parsing -------------------------------------------------------

CODE_FORMS = {
    "backtick-fence": "```\n[[missing]]\n```\n",
    "long-backtick-fence": "`````\n[[missing]]\n```\n[[missing]]\n`````\n",
    "longer-closing-fence": "```\n[[missing]]\n````\n",
    "tilde-fence": "~~~\n[[missing]]\n~~~\n",
    "unclosed-fence": "intro\n```\n[[missing]]\nmore\n",
    "double-backtick-span": "see ``[[missing]]`` ok",
    "single-backtick-span": "see `[[missing]]` ok",
    "indented-block": "intro\n\n    [[missing]]\n\nafter\n",
}


@pytest.mark.parametrize("text", CODE_FORMS.values(), ids=CODE_FORMS.keys())
def test_wikilink_in_code_is_not_reported(text):
    res = run([vault("a", rec("a", "lesson", "src", {}, text))])
    assert res.findings == []


def test_same_wikilink_in_prose_is_reported():
    res = run([vault("a", rec("a", "lesson", "src", {}, "plain [[missing]] prose"))])
    assert found(res) == [("src", "wikilink", "missing")]


def test_link_after_a_closed_fence_and_span_is_still_reported():
    body = "```\n[[a]]\n```\n`[[b]]` then [[missing]]\n"
    res = run([vault("a", rec("a", "lesson", "src", {}, body))])
    assert found(res) == [("src", "wikilink", "missing")]


def test_bare_wikilink_matching_any_kind_is_not_reported():
    v = vault("a", rec("a", "lesson", "src", {}, "[[shared]]"), rec("a", "decision", "shared"))
    assert run([v]).findings == []


def test_typed_wikilink_needs_that_kind():
    v = vault("a", rec("a", "lesson", "src", {}, "[[lesson/shared]]"),
              rec("a", "decision", "shared"))
    assert found(run([v])) == [("src", "wikilink", "lesson/shared")]


MALFORMED = {
    "prose": "[[some prose here]]",
    "newline": "[[line\nbreak]]",
    "overlong": "[[" + "a" * 400 + "]]",
    "unknown-kind": "[[nokind/name]]",
    "uppercase": "[[Secret-Token]]",
    "empty": "[[|alias]]",
}


@pytest.mark.parametrize("text", MALFORMED.values(), ids=MALFORMED.keys())
def test_malformed_wikilink_is_flagged_without_echoing_the_target(text):
    res = run([vault("a", rec("a", "lesson", "src", {}, f"x {text} y"))])
    (f,) = res.findings
    assert (f.malformed, f.field, f.name) == (True, "wikilink", "src")
    rendered = json.dumps(dataclasses.asdict(f))
    inner = text[2:-2].split("|")[0]
    for part in filter(None, (inner, *inner.split())):
        assert part not in rendered.replace("src", "").replace("wikilink", "")


def test_malformed_sidecar_reference_is_flagged_without_echoing():
    res = run([vault("a", rec("a", "task", "src", {"depends-on": ["Bad Target!"]}))])
    (f,) = res.findings
    assert (f.malformed, f.field) == (True, "depends-on")
    assert "Bad" not in json.dumps(dataclasses.asdict(f))


# --- skip -------------------------------------------------------------------


def test_unreadable_vault_skips_with_no_findings():
    a = vault("a", rec("a", "lesson", "src", {}, "[[gone]]"))
    b = vault("b", rec("b", "lesson", "ok"))
    bad = vault("bad", unreadable_reason="vault-unreadable")
    res = run([a, b, bad])
    assert isinstance(res, dangling.DanglingSkipped)
    assert res.vaults == ("bad",)
    assert not hasattr(res, "findings")


def test_escaping_records_skip_naming_that_vault():
    a = vault("a", rec("a", "lesson", "src", {}, "[[gone]]"))
    b = vault("b", skipped_escaping=2)
    res = run([a, b])
    assert isinstance(res, dangling.DanglingSkipped)
    assert res.vaults == ("b",)


def test_every_unreadable_vault_is_named():
    res = run([vault("x", unreadable_reason="r"), vault("y", skipped_escaping=1), vault("z")])
    assert res.vaults == ("x", "y")


def test_unreadable_record_is_still_a_target():
    a = vault("a", rec("a", "lesson", "src", {}, "[[lesson/hidden]]"))
    a.unreadable.append(model.UnreadableRecord("a", "lesson", "hidden", "sidecar-malformed"))
    assert run([a]).findings == []


# --- malformed values and counts -------------------------------------------


def test_related_string_does_not_raise_and_other_references_still_checked():
    r = rec("a", "task", "src", {"related": "lesson/x", "parent": "gone"}, "[[gone2]]")
    res = run([vault("a", r)])
    assert found(res) == [("src", "parent", "task/gone"), ("src", "wikilink", "gone2")]


@pytest.mark.parametrize("sidecar", [
    {"related": {"lesson": "gone"}},
    {"related": {"lesson": [1, None, {"a": 1}]}},
    {"related": {7: ["x"]}},
    {"related": {"nokind": ["x"]}},
    {"supersedes": "decision/gone"},
    {"supersedes": [3, ["a"]]},
    {"depends-on": "gone"},
    {"depends-on": [None, 4]},
    {"parent": ["gone"]},
    {"parent": 5},
], ids=lambda s: json.dumps(s)[:40])
def test_malformed_field_values_are_skipped_without_raising(sidecar):
    res = run([vault("a", rec("a", "task", "src", sidecar, ""))])
    assert res.findings == []


def test_non_string_body_and_non_dict_sidecar_do_not_raise():
    r1 = model.ScannedRecord("a", "task", "x", None, {}, None)
    r2 = model.ScannedRecord("a", "task", "y", None, ["not", "a", "dict"], "ok")
    assert run([vault("a", r1, r2)]).findings == []


def test_judged_count_excludes_sessions_and_unreadable_records():
    a = vault("a", rec("a", "lesson", "l1"), rec("a", "task", "t1"), rec("a", "session", "s1"))
    a.unreadable.append(model.UnreadableRecord("a", "lesson", "bad", "oversize"))
    b = vault("b", rec("b", "lesson", "l2"))
    res = run([a, b], selected={"a", "b"})
    assert res.judged == {"a": 2, "b": 1}


def test_judged_count_is_per_selected_vault_only():
    a = vault("a", rec("a", "lesson", "l1"))
    b = vault("b", rec("b", "lesson", "l2"))
    assert run([a, b], selected={"b"}).judged == {"b": 1}


# --- seam: real scan --------------------------------------------------------


def test_real_scan_output_feeds_the_check(tmp_path):
    side = {"title": "T", "status": "active", "created-at": "2026-01-01",
            "updated-at": "2026-03-05"}
    for root in ("one", "two"):
        (tmp_path / root / "lesson").mkdir(parents=True)
    (tmp_path / "one/lesson/src.md").write_text("see [[lesson/there]] and [[lesson/gone]]")
    (tmp_path / "one/lesson/src.json").write_text(json.dumps(side))
    (tmp_path / "two/lesson/there.md").write_text("x")
    (tmp_path / "two/lesson/there.json").write_text(json.dumps(side))
    scans = [scan_mod.scan_vault(n, tmp_path / n) for n in ("one", "two")]
    res = dangling.check_dangling(scans, {"one"})
    assert [(f.vault, f.name, f.updated_at, f.target) for f in res.findings] == [
        ("one", "src", "2026-03-05", "lesson/gone")
    ]
    assert res.judged == {"one": 1}
