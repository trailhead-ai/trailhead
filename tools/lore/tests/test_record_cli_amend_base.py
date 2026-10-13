"""The revision handshake: ``record show`` hands out a body revision and
``record amend --base`` refuses to write over a body that changed after it was read."""

from __future__ import annotations

import hashlib
import json

from conftest import load_script, make_vault as _make_vault, run_cli as _run
from test_record_cli_amend import (
    AMENDED, BODY, _amend, _body, _create, _revision, _sidecar, _snapshot,
)

LONG = "".join(f"line {i:02d}\n" for i in range(1, 31))


def _adr(tmp_path, body=BODY):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "adr", "A Decision", status="draft", body=body)
    return vault, state, rid


def _update_body(vault, state, rid, body):
    r = _run(["record", "update", rid], vault=vault, state_dir=state, stdin_text=body)
    assert r.returncode == 0, r.stderr


def _update(vault, state, rid, *flags):
    r = _run(["record", "update", rid, *flags], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr


def _show(vault, state, rid, *flags):
    return _run(["record", "show", rid, *flags], vault=vault, state_dir=state)


# Contract 1
def test_amend_with_the_revision_just_read_succeeds_and_history_grows(tmp_path):
    vault, state, rid = _adr(tmp_path)
    rev = _revision(vault, state, rid)
    r = _amend(vault, state, rid, AMENDED, base=rev)
    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == AMENDED
    assert len(_sidecar(vault, rid)["change-history"]) == 1
    r = _amend(vault, state, rid, BODY, base=_revision(vault, state, rid))
    assert r.returncode == 0, r.stderr
    assert len(_sidecar(vault, rid)["change-history"]) == 2


# Contract 2
def test_amend_against_a_body_changed_by_update_is_refused_and_writes_nothing(tmp_path):
    vault, state, rid = _adr(tmp_path)
    stale = _revision(vault, state, rid)
    _update_body(vault, state, rid, "# Decision\n\nsomeone else's edit\n")
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED, base=stale)
    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert stale in r.stderr
    assert _revision(vault, state, rid) in r.stderr
    assert _snapshot(vault) == before


def test_amend_against_a_body_changed_by_another_amend_is_refused_and_writes_nothing(tmp_path):
    vault, state, rid = _adr(tmp_path)
    stale = _revision(vault, state, rid)
    assert _amend(vault, state, rid, AMENDED).returncode == 0
    before = _snapshot(vault)
    r = _amend(vault, state, rid, "# Decision\n\nmine\n", base=stale)
    assert r.returncode != 0
    assert "lore record history" in r.stderr
    assert _snapshot(vault) == before


# Contract 3
def test_full_body_amend_without_base_is_refused_pointing_at_record_show(tmp_path):
    vault, state, rid = _adr(tmp_path)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, AMENDED, base=None)
    assert r.returncode != 0
    assert "--base" in r.stderr
    assert "lore record show" in r.stderr
    assert _snapshot(vault) == before


# Contract 3a
def test_stale_refusal_names_who_changed_the_record_and_when(tmp_path):
    vault, state, rid = _adr(tmp_path)
    stale = _revision(vault, state, rid)
    _update_body(vault, state, rid, "# Decision\n\nsomeone else's edit\n")
    side = _sidecar(vault, rid)
    r = _amend(vault, state, rid, AMENDED, base=stale)
    assert r.returncode != 0
    assert side["updated-by"] in r.stderr
    assert side["updated-at"] in r.stderr
    assert "lore record show" in r.stderr


# Contract 4
def test_diff_without_base_applies_and_keeps_a_concurrent_non_overlapping_edit(tmp_path):
    vault, state, rid = _adr(tmp_path, LONG)
    store = load_script("lore.record.store")
    diff = store.make_unified_diff(LONG, LONG.replace("line 28\n", "line 28 mine\n"))
    theirs = LONG.replace("line 02\n", "line 02 theirs\n")
    _update_body(vault, state, rid, theirs)
    r = _amend(vault, state, rid, diff, diff=True)
    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == theirs.replace("line 28\n", "line 28 mine\n")


def test_diff_with_a_stale_base_is_refused_even_though_it_would_apply(tmp_path):
    vault, state, rid = _adr(tmp_path, LONG)
    store = load_script("lore.record.store")
    diff = store.make_unified_diff(LONG, LONG.replace("line 28\n", "line 28 mine\n"))
    stale = _revision(vault, state, rid)
    _update_body(vault, state, rid, LONG.replace("line 02\n", "line 02 theirs\n"))
    before = _snapshot(vault)
    r = _amend(vault, state, rid, diff, diff=True, base=stale)
    assert r.returncode != 0
    assert stale in r.stderr
    assert _snapshot(vault) == before


def test_diff_with_the_current_base_applies(tmp_path):
    vault, state, rid = _adr(tmp_path, LONG)
    store = load_script("lore.record.store")
    diff = store.make_unified_diff(LONG, LONG.replace("line 28\n", "line 28 mine\n"))
    r = _amend(vault, state, rid, diff, diff=True, base=_revision(vault, state, rid))
    assert r.returncode == 0, r.stderr


# Contract 5
def test_label_and_status_updates_do_not_change_the_revision_and_are_kept(tmp_path):
    vault, state, rid = _adr(tmp_path)
    rev = _revision(vault, state, rid)
    _update(vault, state, rid, "--label", "craft/team=core", "--status", "active")
    assert _revision(vault, state, rid) == rev
    r = _amend(vault, state, rid, AMENDED, base=rev)
    assert r.returncode == 0, r.stderr
    side = _sidecar(vault, rid)
    assert side["status"] == "active"
    assert side["labels"] == {"craft/team": "core"}
    assert _body(vault, rid) == AMENDED


# Contract 6
def test_plain_show_stdout_is_exactly_the_body_with_and_without_history(tmp_path):
    vault, state, rid = _adr(tmp_path)
    plain = _show(vault, state, rid)
    assert plain.stdout == BODY
    assert _amend(vault, state, rid, AMENDED).returncode == 0
    amended = _show(vault, state, rid)
    assert amended.stdout == AMENDED
    assert "lore record history" in amended.stderr


def test_stderr_revision_equals_json_revision_and_the_body_digest(tmp_path):
    vault, state, rid = _adr(tmp_path)
    for body in (BODY, AMENDED):
        _update_body(vault, state, rid, body)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        plain = _show(vault, state, rid)
        assert f"revision: {digest}" in plain.stderr.splitlines()
        assert json.loads(_show(vault, state, rid, "--json").stdout)["revision"] == digest


def test_revision_changes_when_and_only_when_the_body_changes(tmp_path):
    vault, state, rid = _adr(tmp_path)
    r0 = _revision(vault, state, rid)
    _update(vault, state, rid, "--label", "craft/team=core")
    assert _revision(vault, state, rid) == r0
    _update_body(vault, state, rid, AMENDED)
    assert _revision(vault, state, rid) != r0
    _update_body(vault, state, rid, BODY)
    assert _revision(vault, state, rid) == r0
