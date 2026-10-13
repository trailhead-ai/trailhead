"""Crash-safe body + sidecar pair write and its recovery.

Every crash here is real: a subprocess whose test-side bootstrap patches
``os.replace`` to ``os._exit`` between the two renames, so the next lore
operation meets exactly the torn state a power cut would leave. The production
code carries no crash hook.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest

from conftest import (
    PLUGIN_ROOT,
    _cli_env,
    make_vault as _make_vault,
    run_cli as _run,
    run_cli_subprocess as _run_sub,
)

from lore import locking
from lore.record import journal

BODY = "# Decision\n\nline one\nline two\nline three\n"
AMENDED = "# Decision\n\nline one\nline TWO\nline three\n"

RECOVERY = "lore: recovered an interrupted write to {rid}; it did not land"


def _create(vault, state, title="Pair", body=BODY):
    r = _run(
        ["record", "create", "--kind", "decision", "--title", title, "--keyword", "k"],
        vault=vault, state_dir=state, stdin_text=body,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _paths(vault, rid):
    kind, name = rid.split("/", 1)
    return vault / kind / f"{name}.md", vault / kind / f"{name}.json"


def _snapshot(vault):
    return {
        str(p.relative_to(vault)): p.read_bytes()
        for p in sorted(vault.rglob("*"))
        if p.is_file() and p.suffix in (".md", ".json")
    }


def _history(vault, rid):
    return json.loads(_paths(vault, rid)[1].read_text()).get("change-history", [])


_CRASH_BOOTSTRAP = textwrap.dedent(
    """
    import os, sys
    sys.path.insert(0, {plugin_root!r})
    _real = os.replace
    _nth = {nth}
    _seen = [0]
    def _replace(src, dst, *a, **k):
        if ".pair." in os.fspath(src):
            _seen[0] += 1
            if _seen[0] == _nth:
                os._exit(9)
        return _real(src, dst, *a, **k)
    os.replace = _replace
    """
)


def _crash_cli(args, *, vault, state, stdin_text, nth=2):
    """Run a CLI command in a subprocess that dies before the ``nth`` pair rename."""
    boot = _CRASH_BOOTSTRAP.format(plugin_root=str(PLUGIN_ROOT), nth=nth) + textwrap.dedent(
        """
        from lore.cli import dispatch
        sys.exit(dispatch.main({args!r}) or 0)
        """
    ).format(args=list(args))
    env = _cli_env(vault, state, None)
    r = subprocess.run(
        [sys.executable, "-c", boot], input=stdin_text, capture_output=True, text=True, env=env
    )
    assert r.returncode == 9, (r.returncode, r.stderr)
    return r


def _crash_pair(root, state, rid, body, sidecar, *, nth=2):
    boot = _CRASH_BOOTSTRAP.format(plugin_root=str(PLUGIN_ROOT), nth=nth) + textwrap.dedent(
        """
        from lore.record import journal
        journal.write_pair({root!r}, {rid!r}, {body!r}, {sidecar!r})
        """
    ).format(root=str(root), rid=rid, body=body, sidecar=sidecar)
    env = dict(os.environ, XDG_STATE_HOME=str(state))
    r = subprocess.run([sys.executable, "-c", boot], capture_output=True, text=True, env=env)
    assert r.returncode == 9, (r.returncode, r.stderr)


def _journals(state, vault):
    d = journal.journal_dir(vault, env={"XDG_STATE_HOME": str(state)})
    return sorted(d.glob("*")) if d.exists() else []


def _amend_argv(vault, state, rid):
    """The full-body amend argv, naming the record's current revision as ``--base``."""
    shown = _run(["record", "show", rid, "--json"], vault=vault, state_dir=state)
    assert shown.returncode == 0, shown.stderr
    base = json.loads(shown.stdout)["revision"]
    return ["record", "amend", rid, "--reason", "r", "--initiated-by", "operator",
            "--base", base]


def _crash_amend(vault, state, rid):
    return _crash_cli(
        _amend_argv(vault, state, rid),
        vault=vault, state=state, stdin_text=AMENDED,
    )


# ---------------------------------------------------------------------------
# 1 — an injected failure at each step leaves pre-write or post-write, never mixed
# ---------------------------------------------------------------------------


def _fail_nth(real, n=1):
    calls = {"n": 0}

    def wrapper(*a, **k):
        calls["n"] += 1
        if calls["n"] == n:
            raise OSError(5, "injected failure")
        return real(*a, **k)

    return wrapper


@pytest.mark.parametrize(
    "step", ["stage_body", "stage_sidecar", "journal_write", "second_rename", "journal_remove"]
)
def test_failure_at_each_step_never_leaves_a_mixed_record(tmp_path, monkeypatch, step):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    before = _snapshot(vault)

    if step == "stage_body":
        monkeypatch.setattr(journal, "_stage", _fail_nth(journal._stage, 1))
    elif step == "stage_sidecar":
        monkeypatch.setattr(journal, "_stage", _fail_nth(journal._stage, 2))
    elif step == "journal_write":

        def torn_journal(jfile, payload):
            jfile.parent.mkdir(parents=True, exist_ok=True)
            jfile.write_text('{"root": "')
            raise OSError(28, "no space left on device")

        monkeypatch.setattr(journal, "_write_journal", torn_journal)
    elif step == "journal_remove":
        monkeypatch.setattr(journal, "_remove_journal", _fail_nth(journal._remove_journal))
    else:
        real = os.replace
        seen = {"n": 0}

        def replace(src, dst, *a, **k):
            if ".pair." in os.fspath(src):
                seen["n"] += 1
                if seen["n"] == 2:
                    raise OSError(5, "injected failure")
            return real(src, dst, *a, **k)

        monkeypatch.setattr(os, "replace", replace)

    r = _run(
        _amend_argv(vault, state, rid),
        vault=vault, state_dir=state, stdin_text=AMENDED,
    )
    monkeypatch.undo()

    after = _snapshot(vault)
    body_p, side_p = _paths(vault, rid)
    rel = {p: str(p.relative_to(vault)) for p in (body_p, side_p)}
    body_changed = after[rel[body_p]] != before[rel[body_p]]
    side_changed = after[rel[side_p]] != before[rel[side_p]]
    assert body_changed == side_changed, "body and sidecar must move together"
    assert r.returncode != 0
    assert after == before, "a failed write must roll back to the pre-write record"
    assert len(_history(vault, rid)) == (1 if body_changed else 0)
    assert not list(vault.rglob("*.pair.*.tmp")), "no staged temp file may be left in the vault"
    assert _journals(state, vault) == []


# ---------------------------------------------------------------------------
# 2 / 3 — hard crash between the two renames
# ---------------------------------------------------------------------------


def test_hard_crash_amend_is_rolled_back_by_the_next_lore_operation(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    before = _snapshot(vault)
    _crash_amend(vault, state, rid)
    torn = _snapshot(vault)
    assert torn != before and _history(vault, rid) == [], "the crash must tear the pair"

    shown = _run_sub(["record", "show", rid], vault=vault, state_dir=state)

    assert shown.returncode == 0, shown.stderr
    assert shown.stdout == BODY
    assert RECOVERY.format(rid=rid) in shown.stderr
    hist = _run_sub(["record", "history", rid], vault=vault, state_dir=state)
    assert "at:" not in hist.stdout
    assert _snapshot(vault) == before
    assert _journals(state, vault) == []


def test_hard_crash_create_leaves_no_record_after_recovery(tmp_path):
    vault, state = _make_vault(tmp_path)
    _crash_cli(
        ["record", "create", "--kind", "decision", "--title", "Orphan", "--keyword", "k"],
        vault=vault, state=state, stdin_text=BODY,
    )
    rid = "decision/orphan"
    assert _paths(vault, rid)[0].exists(), "precondition: the crash left an orphan body"

    r = _run_sub(["record", "history", rid], vault=vault, state_dir=state)

    assert RECOVERY.format(rid=rid) in r.stderr
    assert not _paths(vault, rid)[0].exists()
    assert not _paths(vault, rid)[1].exists()
    assert _journals(state, vault) == []


# ---------------------------------------------------------------------------
# 4 — truncated journal
# ---------------------------------------------------------------------------


def test_truncated_journal_is_removed_with_a_warning_and_the_next_write_proceeds(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    argv = _amend_argv(vault, state, rid)
    jdir = journal.journal_dir(vault, env={"XDG_STATE_HOME": str(state)})
    jdir.mkdir(parents=True, mode=0o700, exist_ok=True)
    (jdir / "deadbeef.json").write_text('{"root": "' + str(vault.resolve()) + '", "id": "decis')

    r = _run(
        argv,
        vault=vault, state_dir=state, stdin_text=AMENDED,
    )

    assert r.returncode == 0, r.stderr
    assert "unreadable write journal" in r.stderr
    assert _paths(vault, rid)[0].read_text() == AMENDED
    assert len(_history(vault, rid)) == 1
    assert _journals(state, vault) == []


# ---------------------------------------------------------------------------
# 5 — changed since the crash
# ---------------------------------------------------------------------------


def test_a_file_changed_after_the_crash_is_left_alone_with_a_warning(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    _crash_amend(vault, state, rid)
    body_p, side_p = _paths(vault, rid)
    body_p.write_text("# someone else's edit\n")
    side_before = side_p.read_bytes()

    r = _run_sub(["record", "show", rid], vault=vault, state_dir=state)

    assert body_p.read_text() == "# someone else's edit\n"
    assert side_p.read_bytes() == side_before
    assert rid in r.stderr and "changed after an interrupted write" in r.stderr
    assert RECOVERY.format(rid=rid) not in r.stderr
    assert _journals(state, vault) == []


# ---------------------------------------------------------------------------
# 6 — foreign root / symlinked record file
# ---------------------------------------------------------------------------


def test_a_journal_recorded_for_another_vault_is_not_applied(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    _crash_amend(vault, state, rid)
    (jfile,) = _journals(state, vault)
    data = json.loads(jfile.read_text())
    data["root"] = str(tmp_path / "some-other-vault")
    jfile.write_text(json.dumps(data))
    torn = _snapshot(vault)

    r = _run_sub(["record", "show", rid], vault=vault, state_dir=state)

    assert _snapshot(vault) == torn, "a foreign-root journal must not touch this vault"
    assert RECOVERY.format(rid=rid) not in r.stderr
    assert jfile.exists()


def test_a_symlinked_record_file_is_neither_followed_nor_restored(tmp_path, capsys):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    _crash_amend(vault, state, rid)
    body_p, _ = _paths(vault, rid)
    # The symlink target holds exactly the journal's AFTER image, so only a
    # refusal to follow can keep recovery from "restoring" through it.
    outside = tmp_path / "outside.md"
    outside.write_bytes(body_p.read_bytes())
    body_p.unlink()
    body_p.symlink_to(outside)

    with locking.vault_write_lock(vault):
        pass

    err = capsys.readouterr().err
    assert body_p.is_symlink()
    assert outside.read_text() == AMENDED
    assert rid in err and "changed after an interrupted write" in err
    assert _journals(state, vault) == []


# ---------------------------------------------------------------------------
# 7 — restore I/O failure
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_restore_that_cannot_write_fails_the_operation_and_keeps_the_journal(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    _crash_amend(vault, state, rid)
    kind_dir = _paths(vault, rid)[0].parent
    kind_dir.chmod(0o555)
    try:
        r = _run_sub(["record", "show", rid], vault=vault, state_dir=state)
        assert r.returncode != 0
        assert "lore:" in r.stderr and rid in r.stderr
        assert "Traceback" not in r.stderr
        assert len(_journals(state, vault)) == 1
    finally:
        kind_dir.chmod(0o755)

    again = _run_sub(["record", "show", rid], vault=vault, state_dir=state)
    assert again.returncode == 0 and again.stdout == BODY
    assert _journals(state, vault) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_writer_whose_recovery_fails_exits_with_a_named_lore_message(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    argv = _amend_argv(vault, state, rid)
    _crash_amend(vault, state, rid)
    kind_dir = _paths(vault, rid)[0].parent
    kind_dir.chmod(0o555)
    try:
        r = _run_sub(
            argv,
            vault=vault, state_dir=state, stdin_text=AMENDED,
        )
    finally:
        kind_dir.chmod(0o755)
    assert r.returncode != 0
    assert r.stderr.startswith("lore:") and rid in r.stderr
    assert "record amend failed" not in r.stderr and "Traceback" not in r.stderr
    assert len(_journals(state, vault)) == 1


# ---------------------------------------------------------------------------
# 8 — readers do not block when nothing is pending
# ---------------------------------------------------------------------------


def test_record_show_does_not_wait_for_the_vault_lock_when_no_journal_exists(tmp_path):
    from test_vault_write_lock import _spawn_holder

    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    holder = _spawn_holder(vault, 30)
    try:
        r = subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "cli" / "lore"), "record", "show", rid],
            capture_output=True, text=True, env=_cli_env(vault, state, None), timeout=20,
        )
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 0, r.stderr
    assert r.stdout == BODY
    assert "waiting for the vault write lock" not in r.stderr


# ---------------------------------------------------------------------------
# 10 — owner-only, and nothing the vault's git would see
# ---------------------------------------------------------------------------


def test_journal_is_owner_only_and_leaves_nothing_in_the_vault_after_recovery(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    subprocess.run(["git", "-C", str(vault), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "commit.gpgsign=false", "commit", "-qm", "base"],
        check=True,
    )
    _crash_amend(vault, state, rid)

    (jfile,) = _journals(state, vault)
    assert stat.S_IMODE(jfile.stat().st_mode) == 0o600
    assert stat.S_IMODE(jfile.parent.stat().st_mode) == 0o700
    pending = subprocess.run(
        ["git", "-C", str(vault), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True,
    ).stdout
    assert "journal" not in pending and "state" not in pending, pending

    _run_sub(["record", "show", rid], vault=vault, state_dir=state)

    status = subprocess.run(
        ["git", "-C", str(vault), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert status == "", status


# ---------------------------------------------------------------------------
# sync reports a failed recovery as such, not as a lock failure
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_sync_reports_a_failed_recovery_by_record_not_as_a_lock_failure(tmp_path):
    from conftest import write_vault_config
    from test_sync_multi_vault import _make_vault as make_git_vault
    from test_sync_multi_vault import run_cli as sync_cli

    state = tmp_path / "state"
    state.mkdir()
    vault = make_git_vault(tmp_path / "v", dirty=False)
    (vault / "task" / "x.md").write_text(BODY)
    (vault / "task" / "x.json").write_text("{}\n")
    _crash_pair(vault, state, "task/x", AMENDED, '{"changed": true}\n')
    config_home = tmp_path / "config"
    write_vault_config(config_home, [("default", "default", vault)])
    (vault / "task").chmod(0o555)
    try:
        r = sync_cli(["sync"], config_home=config_home, state_dir=state)
    finally:
        (vault / "task").chmod(0o755)

    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "task/x" in r.stderr and "roll back" in r.stderr
    assert "failed to acquire vault lock" not in r.stderr
    assert "already in progress" not in r.stderr
    assert len(_journals(state, vault)) == 1
