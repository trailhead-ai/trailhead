"""The write journal lives in a directory other local users may try to plant.

It must be unreadable by parsing tricks (nesting, size), must refuse to live
behind a symlink or in a directory the user does not own exclusively, and its
record paths must stay inside the vault they were journalled for.
"""

from __future__ import annotations

import json
import shutil
import stat

import pytest

from conftest import make_vault as _make_vault, run_cli as _run
from test_record_pair_write import (
    AMENDED,
    BODY,
    _amend_argv,
    _crash_amend,
    _crash_pair,
    _create,
    _history,
    _journals,
    _paths,
    _snapshot,
)

from lore import locking
from lore.record import journal


def _jdir(vault, state):
    return journal.journal_dir(vault, env={"XDG_STATE_HOME": str(state)})


def _amend(vault, state, rid):
    return _run(_amend_argv(vault, state, rid), vault=vault, state_dir=state, stdin_text=AMENDED)


def _tree(path):
    return {
        str(p.relative_to(path)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(path.rglob("*"))
    }


# --- L1: a journal that cannot be parsed never raises out of the hook -------


def test_a_deeply_nested_journal_is_discarded_with_a_warning_and_the_next_write_proceeds(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    argv = _amend_argv(vault, state, rid)
    jdir = _jdir(vault, state)
    jdir.mkdir(parents=True, mode=0o700, exist_ok=True)
    (jdir / "deadbeef.json").write_text("[" * 100_000)

    r = _run(argv, vault=vault, state_dir=state, stdin_text=AMENDED)

    assert r.returncode == 0, r.stderr
    assert "unreadable write journal" in r.stderr
    assert _paths(vault, rid)[0].read_text() == AMENDED
    assert _journals(state, vault) == []


def test_a_journal_larger_than_the_cap_is_discarded_unread(tmp_path, monkeypatch):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    jdir = _jdir(vault, state)
    _crash_amend(vault, state, rid)
    (jfile,) = _journals(state, vault)
    size = jfile.stat().st_size
    monkeypatch.setattr(journal, "_MAX_JOURNAL_BYTES", size - 1)

    r = _run(["record", "show", rid], vault=vault, state_dir=state)

    assert r.returncode == 0, r.stderr
    assert "unreadable write journal" in r.stderr
    assert not jdir.exists() or list(jdir.iterdir()) == []
    assert _paths(vault, rid)[0].read_text() == AMENDED


def test_a_journal_within_the_cap_is_still_applied(tmp_path, monkeypatch):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    _crash_amend(vault, state, rid)
    (jfile,) = _journals(state, vault)
    monkeypatch.setattr(journal, "_MAX_JOURNAL_BYTES", jfile.stat().st_size)

    r = _run(["record", "show", rid], vault=vault, state_dir=state)

    assert r.returncode == 0, r.stderr
    assert _paths(vault, rid)[0].read_text() == BODY


# --- L3: the journal directory ----------------------------------------------


def test_a_journal_directory_that_is_a_symlink_fails_the_write_and_is_left_alone(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    target = tmp_path / "elsewhere"
    target.mkdir()
    target.chmod(0o755)
    (target / "keep.txt").write_text("keep")
    jroot = _jdir(vault, state).parent
    shutil.rmtree(jroot)
    jroot.symlink_to(target)
    before_tree, before_mode = _tree(target), stat.S_IMODE(target.stat().st_mode)
    before_vault = _snapshot(vault)

    r = _amend(vault, state, rid)

    assert r.returncode != 0
    assert r.stderr.startswith("lore:") and "symlink" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr
    assert _tree(target) == before_tree
    assert stat.S_IMODE(target.stat().st_mode) == before_mode
    assert _snapshot(vault) == before_vault


def test_a_world_writable_journal_directory_is_refused(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    jdir = _jdir(vault, state)
    jdir.mkdir(parents=True, mode=0o700, exist_ok=True)
    jdir.chmod(0o777)
    before_vault = _snapshot(vault)

    r = _amend(vault, state, rid)

    assert r.returncode != 0
    assert r.stderr.startswith("lore:") and "mode" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr
    assert _snapshot(vault) == before_vault
    assert stat.S_IMODE(jdir.stat().st_mode) == 0o777


def test_a_group_readable_journal_directory_is_refused_for_recovery_too(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    _crash_amend(vault, state, rid)
    jdir = _jdir(vault, state)
    jdir.chmod(0o750)

    r = _run(["record", "show", rid], vault=vault, state_dir=state)

    assert r.returncode != 0
    assert r.stderr.startswith("lore:") and "mode" in r.stderr, r.stderr
    assert _paths(vault, rid)[0].read_text() == AMENDED
    assert len(list(jdir.iterdir())) == 1


def test_a_journal_directory_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    monkeypatch.setattr(
        journal, "_is_mine", lambda st: not stat.S_ISDIR(st.st_mode)
    )
    before_vault = _snapshot(vault)

    r = _amend(vault, state, rid)

    assert r.returncode != 0
    assert r.stderr.startswith("lore:") and "owned" in r.stderr, r.stderr
    assert _snapshot(vault) == before_vault


def test_a_journal_file_owned_by_someone_else_is_not_applied(tmp_path, monkeypatch):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    _crash_amend(vault, state, rid)
    monkeypatch.setattr(
        journal, "_is_mine", lambda st: not stat.S_ISREG(st.st_mode)
    )

    r = _run(["record", "show", rid], vault=vault, state_dir=state)

    assert r.returncode == 0, r.stderr
    assert "unreadable write journal" in r.stderr
    assert _paths(vault, rid)[0].read_text() == AMENDED


def test_a_journal_file_that_is_a_symlink_is_not_followed(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    _crash_amend(vault, state, rid)
    (jfile,) = _journals(state, vault)
    real = tmp_path / "real-journal.json"
    shutil.move(jfile, real)
    jfile.symlink_to(real)

    r = _run(["record", "show", rid], vault=vault, state_dir=state)

    assert r.returncode == 0, r.stderr
    assert _paths(vault, rid)[0].read_text() == AMENDED
    assert json.loads(real.read_text())["id"] == rid
    assert not jfile.is_symlink()


def test_an_existing_loose_journal_parent_is_tightened_by_a_write(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    parent = _jdir(vault, state).parent
    parent.chmod(0o755)

    assert _amend(vault, state, rid).returncode == 0

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


def test_a_fresh_journal_directory_is_created_owner_only(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)

    assert _amend(vault, state, rid).returncode == 0

    jdir = _jdir(vault, state)
    assert stat.S_IMODE(jdir.stat().st_mode) == 0o700
    assert stat.S_IMODE(jdir.parent.stat().st_mode) == 0o700


# --- L4: record paths stay inside the vault ----------------------------------


def _escape_kind_dir(vault, tmp_path, rid):
    kind = rid.split("/", 1)[0]
    outside = tmp_path / "outside"
    shutil.move(str(vault / kind), str(outside))
    (vault / kind).symlink_to(outside)
    return outside


def test_write_pair_refuses_a_kind_directory_that_resolves_outside_the_vault(tmp_path, monkeypatch):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    outside = _escape_kind_dir(vault, tmp_path, rid)
    before = _tree(outside)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    with pytest.raises(locking.VaultRecoveryError, match="outside the vault"):
        journal.write_pair(vault, rid, "body\n", "{}")
    assert _tree(outside) == before


def test_amend_through_the_cli_with_an_escaping_kind_directory_writes_nothing_outside(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    argv = _amend_argv(vault, state, rid)
    outside = _escape_kind_dir(vault, tmp_path, rid)
    before = _tree(outside)

    r = _run(argv, vault=vault, state_dir=state, stdin_text=AMENDED)

    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert _tree(outside) == before


def test_create_through_the_cli_with_an_escaping_kind_directory_writes_nothing_outside(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    outside = _escape_kind_dir(vault, tmp_path, rid)
    before = _tree(outside)

    r = _run(
        ["record", "create", "--kind", "decision", "--title", "Another", "--keyword", "k"],
        vault=vault, state_dir=state, stdin_text=BODY,
    )

    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert _tree(outside) == before


def test_recovery_does_not_write_through_a_kind_directory_that_resolves_outside(
    tmp_path, monkeypatch
):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    _crash_amend(vault, state, rid)
    outside = _escape_kind_dir(vault, tmp_path, rid)
    before = _tree(outside)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    with pytest.raises(locking.VaultRecoveryError, match="outside the vault"):
        journal.recover_vault(vault)

    assert _tree(outside) == before
    assert len(_journals(state, vault)) == 1


def test_a_record_inside_the_vault_is_still_written_and_recovered(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    _crash_pair(
        vault, state, rid, AMENDED, _paths(vault, rid)[1].read_text(), nth=2
    )
    assert _paths(vault, rid)[0].read_text() == AMENDED

    assert _run(["record", "show", rid], vault=vault, state_dir=state).returncode == 0

    assert _paths(vault, rid)[0].read_text() == BODY
    assert _history(vault, rid) == []
