"""Structural check behind ``lore validate``: one finding per defect of a malformed record.

Every test builds a vault under ``tmp_path`` that is valid except for one
defect, runs the real scan, and feeds its result to the check.
"""

import json
import os

import pytest

from conftest import load_script

scan_mod = load_script("lore.health.scan")
structure = load_script("lore.health.structure")


def _sidecar(**extra):
    return {"kind": "lesson", "title": "T", "status": "active",
            "created-at": "2026-01-01T00:00:00Z", "updated-at": "2026-03-01T00:00:00Z", **extra}


def _put(root, kind, name, sidecar="default", body="body\n"):
    d = root / kind
    d.mkdir(parents=True, exist_ok=True)
    if body is not None:
        (d / f"{name}.md").write_bytes(body if isinstance(body, bytes) else body.encode())
    if sidecar == "default":
        sidecar = _sidecar(kind=kind)
    if sidecar is not None:
        (d / f"{name}.json").write_text(
            sidecar if isinstance(sidecar, str) else json.dumps(sidecar)
        )


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "vault"
    r.mkdir()
    _put(r, "lesson", "good")
    return r


def check(root):
    scan = scan_mod.scan_vault("v", root)
    return structure.check_structure(scan, root)


def codes(findings):
    return sorted((f.record_id, f.code) for f in findings)


def test_valid_vault_has_no_findings_and_no_skips(root):
    _put(root, "decision", "other")
    assert check(root) == ([], 0)


DEFECTS = [
    ("invalid-json", "lesson", "{not json", "good", "sidecar-malformed"),
    ("json-array", "lesson", "[1, 2]", "good", "sidecar-malformed"),
    ("bad-status", "lesson", _sidecar(status="nonsense"), "good", "invalid-sidecar"),
    ("unknown-key", "lesson", _sidecar(bogus="x"), "good", "invalid-sidecar"),
    ("kind-mismatch", "lesson", _sidecar(kind="decision"), "good", "kind-mismatch"),
]


@pytest.mark.parametrize("case", DEFECTS, ids=[c[0] for c in DEFECTS])
def test_each_sidecar_defect_is_exactly_one_finding(root, case):
    _, kind, sidecar, name, code = case
    _put(root, kind, name, sidecar)
    findings, skips = check(root)
    assert codes(findings) == [(f"{kind}/{name}", code)]
    assert findings[0].vault == "v"
    assert skips == 0


def test_non_utf8_body_is_body_unreadable(root):
    _put(root, "lesson", "good", body=b"\xff\xfe\x00bad")
    assert codes(check(root)[0]) == [("lesson/good", "body-unreadable")]


def test_oversize_body_is_oversize(root):
    _put(root, "lesson", "good", body=b"x" * (scan_mod.MAX_RECORD_BYTES + 1))
    assert codes(check(root)[0]) == [("lesson/good", "oversize")]


def test_md_without_json_is_sidecar_missing(root):
    _put(root, "lesson", "lonely", sidecar=None)
    assert codes(check(root)[0]) == [("lesson/lonely", "sidecar-missing")]


def test_json_without_md_is_body_missing(root):
    _put(root, "lesson", "lonely", body=None)
    assert codes(check(root)[0]) == [("lesson/lonely", "body-missing")]


def test_orphan_with_invalid_record_name_is_ignored(root):
    _put(root, "lesson", "Not A Name", body=None)
    assert check(root) == ([], 0)


def test_unknown_kind_is_one_invalid_sidecar_naming_the_kind_and_no_mismatch(root):
    _put(root, "lesson", "good", _sidecar(kind="widget"))
    findings, _ = check(root)
    assert codes(findings) == [("lesson/good", "invalid-sidecar")]
    assert "widget" in findings[0].detail


def test_absent_kind_is_one_invalid_sidecar(root):
    side = _sidecar()
    del side["kind"]
    _put(root, "lesson", "good", side)
    assert codes(check(root)[0]) == [("lesson/good", "invalid-sidecar")]


def test_two_validate_errors_are_two_findings(root):
    _put(root, "lesson", "good", _sidecar(status="nonsense", bogus="x"))
    findings, _ = check(root)
    assert codes(findings) == [("lesson/good", "invalid-sidecar")] * 2
    assert len({f.detail for f in findings}) == 2


def test_sidecar_symlinked_outside_names_no_outside_path(root, tmp_path):
    outside = tmp_path / "outside-secret.json"
    outside.write_text(json.dumps(_sidecar()))
    (root / "lesson" / "good.json").unlink()
    (root / "lesson" / "good.json").symlink_to(outside)
    scan = scan_mod.scan_vault("v", root)
    findings, _ = structure.check_structure(scan, root)
    assert scan.skipped_escaping == 1
    assert all("outside-secret" not in f.detail and "outside-secret" not in f.record_id
               for f in findings)


def test_symlinked_orphan_is_skip_not_body_missing_and_never_opened(root, tmp_path, monkeypatch):
    outside = tmp_path / "outside-secret.json"
    outside.write_text(json.dumps(_sidecar()))
    (root / "lesson" / "ghost.json").symlink_to(outside)
    scan = scan_mod.scan_vault("v", root)
    opened = []
    real_open = os.open

    def spy_os_open(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    import builtins
    real_builtin = builtins.open

    def spy_open(path, *a, **k):
        opened.append(str(path))
        return real_builtin(path, *a, **k)

    monkeypatch.setattr(os, "open", spy_os_open)
    monkeypatch.setattr(builtins, "open", spy_open)
    findings, skips = structure.check_structure(scan, root)
    assert findings == []
    assert skips == 1
    assert not [p for p in opened if "ghost" in p or "outside-secret" in p]


def _total_escaping(root):
    scan = scan_mod.scan_vault("v", root)
    findings, skips = structure.check_structure(scan, root)
    return findings, scan.skipped_escaping + skips


def test_symlinked_md_orphan_outside_is_counted_exactly_once(root, tmp_path):
    outside = tmp_path / "outside-secret.md"
    outside.write_text("secret\n")
    (root / "lesson" / "ghost.md").symlink_to(outside)
    findings, total = _total_escaping(root)
    assert findings == []
    assert total == 1


def test_symlinked_json_orphan_outside_is_counted_exactly_once(root, tmp_path):
    outside = tmp_path / "outside-secret.json"
    outside.write_text(json.dumps(_sidecar()))
    (root / "lesson" / "ghost.json").symlink_to(outside)
    findings, total = _total_escaping(root)
    assert findings == []
    assert total == 1


def test_md_orphan_symlinked_inside_the_vault_is_sidecar_missing(root):
    (root / "lesson" / "alias.md").symlink_to(root / "lesson" / "good.md")
    findings, total = _total_escaping(root)
    assert codes(findings) == [("lesson/alias", "sidecar-missing")]
    assert total == 0


def test_json_orphan_symlinked_inside_the_vault_is_body_missing_and_never_opened(root, monkeypatch):
    (root / "lesson" / "alias.json").symlink_to(root / "lesson" / "good.json")
    scan = scan_mod.scan_vault("v", root)
    opened = []
    real_os_open = os.open
    import builtins
    real_builtin = builtins.open

    def spy_os_open(path, *a, **k):
        opened.append(str(path))
        return real_os_open(path, *a, **k)

    def spy_open(path, *a, **k):
        opened.append(str(path))
        return real_builtin(path, *a, **k)

    monkeypatch.setattr(os, "open", spy_os_open)
    monkeypatch.setattr(builtins, "open", spy_open)
    findings, skips = structure.check_structure(scan, root)
    assert codes(findings) == [("lesson/alias", "body-missing")]
    assert skips == 0
    assert not [p for p in opened if "alias" in p]


def test_a_directory_named_as_the_sidecar_is_one_finding(root):
    (root / "lesson" / "good.json").unlink()
    (root / "lesson" / "good.json").mkdir()
    findings, _ = check(root)
    assert [f.record_id for f in findings] == ["lesson/good"]


def test_non_kind_top_level_dirs_are_ignored(root):
    for d in ("sites", ".git"):
        (root / d).mkdir()
        (root / d / "stray.md").write_text("x")
    assert check(root) == ([], 0)


def test_findings_are_sorted_and_deterministic(root):
    _put(root, "lesson", "zed", _sidecar(status="nonsense"))
    _put(root, "lesson", "alpha", sidecar=None)
    findings, _ = check(root)
    assert [f.record_id for f in findings] == ["lesson/alpha", "lesson/zed"]
