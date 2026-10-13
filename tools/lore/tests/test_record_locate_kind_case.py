"""A located record carries the canonical lowercase kind, whatever casing the id used.

Kind-keyed guards read ``location.kind`` / ``location.record_id``; a case-variant
id must not reach them in its own spelling.
"""

from __future__ import annotations

import json

import pytest

from conftest import (  # noqa: F401
    load_script,
    make_vault as _make_vault,
    run_cli as _run,
)

BODY = "# Decision\n\nline one\n"
AMENDED = "# Decision\n\nline TWO\n"


@pytest.fixture()
def rs():
    return load_script("lore.record.store")


def _write(vault, kind, name):
    d = vault / kind
    d.mkdir(exist_ok=True)
    (d / f"{name}.md").write_text(BODY, encoding="utf-8")
    (d / f"{name}.json").write_text(
        json.dumps({"kind": kind, "title": name, "status": "active"}), encoding="utf-8"
    )


def _snapshot(vault):
    return {
        str(p.relative_to(vault)): p.read_bytes()
        for p in sorted(vault.rglob("*"))
        if p.is_file() and p.suffix in (".md", ".json")
    }


@pytest.mark.parametrize("spelling", ["spec", "Spec", "SPEC"])
def test_locate_resolves_every_casing_of_a_known_kind_to_the_canonical_one(rs, tmp_path, spelling):
    _write(tmp_path, "spec", "x")
    loc = rs.locate_record(f"{spelling}/x", vault_root=str(tmp_path))
    assert loc.kind == "spec"
    assert loc.record_id == "spec/x"
    assert loc.body_path == tmp_path / "spec" / "x.md"
    assert loc.sidecar_path == tmp_path / "spec" / "x.json"


def test_locate_canonicalizes_a_multi_letter_kind(rs, tmp_path):
    _write(tmp_path, "adr", "x")
    loc = rs.locate_record("ADR/x", vault_root=str(tmp_path))
    assert (loc.kind, loc.record_id) == ("adr", "adr/x")


def test_locate_leaves_an_unknown_kind_unfound(rs, tmp_path):
    _write(tmp_path, "spec", "x")
    _write(tmp_path, "widget", "x")
    with pytest.raises(rs.RecordNotFoundError):
        rs.locate_record("Widget/x", vault_root=str(tmp_path))


def test_locate_keeps_the_name_segment_case(rs, tmp_path):
    _write(tmp_path, "spec", "Mixed")
    loc = rs.locate_record("SPEC/Mixed", vault_root=str(tmp_path))
    assert loc.record_id == "spec/Mixed"


def _amend(vault, state, rid, body):
    return _run(
        ["record", "amend", rid, "--reason", "r", "--initiated-by", "operator"],
        vault=vault, state_dir=state, stdin_text=body,
    )


def test_amend_of_a_case_variant_session_id_is_refused_by_the_session_guard(tmp_path):
    vault, state = _make_vault(tmp_path)
    _write(vault, "session", "s1")
    before = _snapshot(vault)
    r = _amend(vault, state, "Session/s1", "log\n")
    refusal = load_script("lore.cli.record")._SESSION_AMEND_REFUSAL
    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert r.stderr.strip() == refusal
    assert _snapshot(vault) == before


def test_update_of_a_frozen_adr_by_case_variant_id_is_refused_and_writes_nothing(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "adr", "--title", "Frozen", "--keyword", "k",
         "--status", "active"],
        vault=vault, state_dir=state, stdin_text=BODY,
    )
    assert r.returncode == 0, r.stderr
    rid = r.stdout.strip()
    variant = "ADR/" + rid.split("/", 1)[1]
    before = _snapshot(vault)

    r = _run(["record", "update", variant], vault=vault, state_dir=state, stdin_text=AMENDED)

    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert "[frozen-body]" in r.stderr
    assert _snapshot(vault) == before
