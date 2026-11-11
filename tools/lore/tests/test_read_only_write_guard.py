"""Every lore write into a ``read-only`` vault is refused at the write lock.

A read-only vault's folder is changed by hand outside lore, so lore must never
write into it — not a record, not a lock file, not a session sidecar. The lock is
the chokepoint (``lore.locking``); the writers that bypass it (session store,
``vault delete --remove-from-disk``) are guarded at their own site with the same
error. Every CLI test snapshots the read-only folder (relative paths + bytes,
directories included) before the command and asserts it identical afterwards.
Config is real JSON under ``tmp_path`` with injected XDG dirs — never the live
install.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import (
    load_script, make_bare_remote, make_git_vault, make_vault, run_cli, write_vault_config,
)
from lore import locking

SID = "aaaaaaaa-1111-4111-8111-111111111111"
MESSAGE = "lore: vault ro is read-only — its content is changed by hand outside lore"


def _snapshot(folder: Path) -> dict[str, bytes | None]:
    """Every path under *folder* (files by bytes, directories as ``None``)."""
    out: dict[str, bytes | None] = {}
    for p in sorted(folder.rglob("*")):
        out[str(p.relative_to(folder))] = p.read_bytes() if p.is_file() else None
    return out


class _Install:
    """A default vault plus one vault ``ro`` that starts writable and is then
    declared read-only, so its records are real ones placed through the CLI."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.default, self.state = make_vault(tmp_path)
        self.ro = tmp_path / "ro"
        self.ro.mkdir()
        self.writable = tmp_path / "writable"
        self.writable.mkdir()
        self.config_home = tmp_path / "config"
        self.env = {"XDG_CONFIG_HOME": str(self.config_home)}
        self.config([("default", "default", self.default), ("ro", "repo", self.ro)])
        self.a = self._create("task", "Alpha")
        self.b = self._create("task", "Beta")
        self.config(
            [
                ("default", "default", self.default),
                ("ro", "read-only", self.ro),
                ("writable", "repo", self.writable),
            ]
        )

    def config(self, vaults):
        write_vault_config(self.config_home, vaults)

    def run(self, args, stdin_text=None, env_extra=None, cwd=None):
        env = dict(self.env)
        env.update(env_extra or {})
        return run_cli(
            args, vault=self.default, state_dir=self.state, stdin_text=stdin_text,
            env_extra=env, cwd=cwd,
        )

    def _create(self, kind, title):
        r = self.run(
            ["record", "create", "--kind", kind, "--title", title, "--keyword", "k",
             "--vault", "ro"],
            stdin_text="body\n",
        )
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()


@pytest.fixture
def install(tmp_path):
    return _Install(tmp_path)


def _refused(r):
    assert r.returncode != 0, r.stdout
    assert "Traceback" not in r.stderr, r.stderr
    assert r.stderr.strip().splitlines().count(MESSAGE) == 1, r.stderr
    assert len(r.stderr.strip().splitlines()) == 1, r.stderr


# ---------------------------------------------------------------------------
# AC4 / AC5 — creation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", ["repo", "product", "suite", "team"])
def test_create_with_a_scope_flag_naming_the_read_only_vault_lands_elsewhere(install, scope):
    before = _snapshot(install.ro)
    r = install.run(
        ["record", "create", "--kind", "blob", "--title", f"via {scope}", f"--{scope}", "ro"],
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    kind, name = r.stdout.strip().split("/", 1)
    assert (install.default / kind / f"{name}.md").exists()
    assert _snapshot(install.ro) == before


def test_create_routed_by_a_camp_group_binding_never_lands_in_the_read_only_vault(install):
    groups = install.tmp / "groups"
    groups.mkdir()
    member = install.tmp / "member"
    member.mkdir()
    (groups / "g.toml").write_text(
        '[group]\nname = "g"\n\n[[members]]\nname = "repo"\n'
        f'repo_root = "{member}"\n\n[[lore_scopes]]\nscope = "product"\nname = "ro"\n',
        encoding="utf-8",
    )
    before = _snapshot(install.ro)
    r = install.run(
        ["record", "create", "--kind", "blob", "--title", "grouped"],
        stdin_text="x\n",
        env_extra={"LORE_GROUPS_DIR": str(groups)},
        cwd=member,
    )
    assert r.returncode == 0, r.stderr
    kind, name = r.stdout.strip().split("/", 1)
    assert (install.default / kind / f"{name}.md").exists()
    assert _snapshot(install.ro) == before


def test_create_naming_the_read_only_vault_is_refused_and_a_writable_one_succeeds(install):
    before_ro = _snapshot(install.ro)
    before_default = _snapshot(install.default)
    r = install.run(
        ["record", "create", "--kind", "blob", "--title", "Nope", "--vault", "ro"],
        stdin_text="x\n",
    )
    _refused(r)
    assert _snapshot(install.ro) == before_ro
    assert _snapshot(install.default) == before_default

    ok = install.run(
        ["record", "create", "--kind", "blob", "--title", "Yes", "--vault", "writable"],
        stdin_text="x\n",
    )
    assert ok.returncode == 0, ok.stderr
    assert (install.writable / "blob" / "yes.md").exists()


def test_create_naming_a_read_only_vault_whose_folder_is_absent_is_refused_and_creates_nothing(
    install,
):
    import shutil

    shutil.rmtree(install.ro)
    r = install.run(
        ["record", "create", "--kind", "blob", "--title", "Nope", "--vault", "ro"],
        stdin_text="x\n",
    )
    _refused(r)
    assert not install.ro.exists()

    ok = install.run(
        ["record", "create", "--kind", "blob", "--title", "Yes", "--vault", "writable"],
        stdin_text="x\n",
    )
    assert ok.returncode == 0, ok.stderr
    assert (install.writable / "blob" / "yes.md").exists()


# ---------------------------------------------------------------------------
# AC6 — every change to an existing record
# ---------------------------------------------------------------------------


def _commands(i):
    return {
        "update": (["record", "update", i.a, "--vault", "ro"], "new body\n"),
        "amend": (
            ["record", "amend", i.a, "--vault", "ro", "--reason", "r",
             "--initiated-by", "operator"],
            "amended body\n",
        ),
        "supersede": (["record", "supersede", i.a, "--by", i.b, "--vault", "ro"], None),
        "rename": (["record", "rename", i.a, "--title", "Gamma", "--vault", "ro"], None),
        "delete": (["record", "delete", i.a, "--vault", "ro"], None),
    }


@pytest.mark.parametrize("command", ["update", "amend", "supersede", "rename", "delete"])
def test_changing_a_record_in_a_read_only_vault_is_refused_and_the_folder_is_unchanged(
    install, command
):
    args, stdin = _commands(install)[command]
    if command == "amend":
        shown = install.run(["record", "show", install.a, "--json", "--vault", "ro"])
        assert shown.returncode == 0, shown.stderr
        args = args + ["--base", json.loads(shown.stdout)["revision"]]
    before = _snapshot(install.ro)
    r = install.run(args, stdin_text=stdin)
    _refused(r)
    assert _snapshot(install.ro) == before


# ---------------------------------------------------------------------------
# Session writers
# ---------------------------------------------------------------------------


def _alias_default_as_read_only(install):
    """A second config entry resolving to the default vault's own folder."""
    install.config(
        [("default", "default", install.default), ("ro", "read-only", install.default)]
    )


def test_session_candidate_refuses_a_read_only_session_vault_and_lands_when_writable(install):
    args = ["session", "candidate", "--session-id", SID, "--kind", "spec", "--phase", "Plan"]
    ok = install.run(args, stdin_text="first\n")
    assert ok.returncode == 0, ok.stderr
    # Fresh folder for the refused case: nothing session-shaped may appear.
    _alias_default_as_read_only(install)
    install.run(["session", "candidate", "--session-id", SID, "--kind", "spec", "--phase", "Plan"],
                stdin_text="second\n")
    before = _snapshot(install.default)
    r = install.run(args, stdin_text="third\n")
    _refused(r)
    assert _snapshot(install.default) == before


def test_session_candidate_into_a_read_only_default_creates_no_session_dir(tmp_path):
    inst = _Install(tmp_path)
    _alias_default_as_read_only(inst)
    before = _snapshot(inst.default)
    r = inst.run(
        ["session", "candidate", "--session-id", SID, "--kind", "spec", "--phase", "Plan"],
        stdin_text="x\n",
    )
    _refused(r)
    assert _snapshot(inst.default) == before
    assert not (inst.default / "session").exists()


def test_flush_of_a_read_only_session_vault_is_one_line_and_leaves_the_folder_alone(install):
    cand = ["session", "candidate", "--session-id", SID, "--kind", "spec", "--phase", "Plan"]
    assert install.run(cand, stdin_text="x\n").returncode == 0
    _alias_default_as_read_only(install)
    before = _snapshot(install.default)
    r = install.run(["flush", "--session-id", SID])
    _refused(r)
    assert _snapshot(install.default) == before


SID2 = "bbbbbbbb-2222-4222-8222-222222222222"
_CANDIDATE = ["session", "candidate", "--kind", "spec", "--phase", "Plan"]


def test_session_referenced_into_a_read_only_session_vault_is_refused_and_lands_when_writable(
    install,
):
    assert install.run(_CANDIDATE + ["--session-id", SID], stdin_text="x\n").returncode == 0
    args = ["session", "referenced", "task/alpha", "--session-id", SID]

    before_writable = _snapshot(install.default)
    ok = install.run(args)
    assert ok.returncode == 0, ok.stderr
    assert _snapshot(install.default) != before_writable, "the control reference must land"

    _alias_default_as_read_only(install)
    before = _snapshot(install.default)
    r = install.run(args)
    _refused(r)
    assert _snapshot(install.default) == before


def _flush_and_revert_env(install, monkeypatch):
    from lore.search import index as index_store

    monkeypatch.setenv("XDG_CONFIG_HOME", str(install.config_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(install.state))
    return lambda: index_store.open_index(env={"XDG_STATE_HOME": str(install.state)})


def _sidecar_status(install):
    return json.loads((install.default / "session" / f"{SID}.json").read_text())["status"]


def test_revert_flush_of_a_read_only_session_vault_raises_and_a_writable_one_reverts(
    install, monkeypatch
):
    from lore.session import store

    open_index = _flush_and_revert_env(install, monkeypatch)
    assert install.run(_CANDIDATE + ["--session-id", SID], stdin_text="x\n").returncode == 0
    flushed = store.flush_session(
        SID, vault_root=str(install.default), committer="t@example.com", open_index=open_index
    )
    assert flushed == store.FLUSH_FLUSHED
    assert _sidecar_status(install) == "clean"

    _alias_default_as_read_only(install)
    before = _snapshot(install.default)
    with pytest.raises(locking.ReadOnlyVaultError):
        store.revert_flush(
            SID, vault_root=str(install.default), committer="t@example.com",
            open_index=open_index,
        )
    assert _snapshot(install.default) == before
    assert _sidecar_status(install) == "clean"

    install.config([("default", "default", install.default)])
    store.revert_flush(
        SID, vault_root=str(install.default), committer="t@example.com", open_index=open_index
    )
    assert _sidecar_status(install) == "dirty"


def test_batch_flush_of_a_read_only_session_vault_is_one_line_and_a_writable_one_flushes(
    tmp_path,
):
    inst = _Install(tmp_path)
    make_git_vault(inst.default)
    for sid in (SID, SID2):
        assert inst.run(_CANDIDATE + ["--session-id", sid], stdin_text="x\n").returncode == 0
    _alias_default_as_read_only(inst)
    before = _snapshot(inst.default)
    r = inst.run(["flush", "all", "--no-sync"])
    _refused(r)
    assert _snapshot(inst.default) == before
    for sid in (SID, SID2):
        assert json.loads((inst.default / "session" / f"{sid}.json").read_text())["status"] == "dirty"

    inst.config([("default", "default", inst.default)])
    ok = inst.run(["flush", "all", "--no-sync"])
    assert ok.returncode == 0, ok.stderr
    for sid in (SID, SID2):
        assert json.loads((inst.default / "session" / f"{sid}.json").read_text())["status"] == "clean"


def _git(path, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def _behind_origin(tmp_path):
    """A git vault one commit behind its origin, fetched."""
    vault = make_git_vault(tmp_path / "pulled")
    remote = make_bare_remote(tmp_path / "remote.git")
    _git(vault, "remote", "add", "origin", str(remote))
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert _git(vault, "push", "-u", "origin", branch).returncode == 0
    other = make_git_vault(tmp_path / "other")
    _git(other, "remote", "add", "origin", str(remote))
    assert _git(other, "fetch", "origin").returncode == 0
    assert _git(other, "reset", "--hard", f"origin/{branch}").returncode == 0
    (other / "from-elsewhere.md").write_text("new\n")
    _git(other, "add", "-A")
    assert _git(other, "commit", "-m", "elsewhere").returncode == 0
    assert _git(other, "push", "origin", branch).returncode == 0
    assert _git(vault, "fetch", "origin").returncode == 0
    return vault


def _pull(sync, vault):
    lines: list[str] = []
    out = sync._pull_one(
        vault, lines.append, lines.append, already_fetched=True,
        resolve_conflicts=True, name="pulled", shared=False,
    )
    return out, lines


def test_a_pull_into_a_read_only_vault_raises_rather_than_holding_it(tmp_path, monkeypatch):
    vault = _behind_origin(tmp_path)
    cfg = tmp_path / "cfg"
    write_vault_config(cfg, [("default", "default", make_vault(tmp_path / "d")[0]),
                             ("pulled", "read-only", vault)])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    sync = load_script("lore.cli.sync")
    state = load_script("lore.cli.resolve_state")
    before = _snapshot(vault)
    with pytest.raises(locking.ReadOnlyVaultError):
        _pull(sync, vault)
    assert _snapshot(vault) == before
    assert not state.vault_is_held(vault)


def test_a_pull_into_a_writable_vault_lands_the_commit(tmp_path, monkeypatch):
    vault = _behind_origin(tmp_path)
    cfg = tmp_path / "cfg"
    write_vault_config(cfg, [("default", "default", make_vault(tmp_path / "d")[0]),
                             ("pulled", "repo", vault)])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    sync = load_script("lore.cli.sync")
    (state_after, pulled), lines = _pull(sync, vault)
    assert (state_after, pulled) == (sync.PULL_OK, 1), lines
    assert (vault / "from-elsewhere.md").exists()


# ---------------------------------------------------------------------------
# vault delete
# ---------------------------------------------------------------------------


def test_vault_delete_remove_from_disk_refuses_a_read_only_folder(install):
    before = _snapshot(install.ro)
    r = install.run(["vault", "delete", "ro", "--remove-from-disk", "--yes"])
    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert MESSAGE in r.stderr
    assert _snapshot(install.ro) == before
    assert any(v["name"] == "ro" for v in _config_vaults(install))


def _nested_install(install):
    """A writable vault ``team`` under the vaults root holding a read-only ``ncore``."""
    vaults = install.state / "lore" / "vaults"
    team = vaults / "team"
    nested = team / "nested-core"
    nested.mkdir(parents=True)
    (nested / "kept.md").write_text("by hand\n", encoding="utf-8")
    (team / "own.md").write_text("team record\n", encoding="utf-8")
    install.config(
        [
            ("default", "default", install.default),
            ("team", "repo", team),
            ("ncore", "read-only", nested),
        ]
    )
    return team, nested


def test_vault_delete_remove_from_disk_refuses_a_folder_holding_a_read_only_vault(install):
    team, nested = _nested_install(install)
    before = _snapshot(team)
    r = install.run(["vault", "delete", "team", "--remove-from-disk", "--yes"])
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr
    assert r.stderr.strip().splitlines() == [
        "lore: vault ncore is read-only — its content is changed by hand outside lore"
    ], r.stderr
    assert _snapshot(team) == before
    assert any(v["name"] == "team" for v in _config_vaults(install))


def test_vault_delete_remove_from_disk_of_a_folder_beside_a_read_only_one_is_removed(install):
    vaults = install.state / "lore" / "vaults"
    team = vaults / "team"
    team.mkdir(parents=True)
    (team / "own.md").write_text("team record\n", encoding="utf-8")
    sib = vaults / "team-x"
    sib.mkdir()
    (sib / "own.md").write_text("sibling\n", encoding="utf-8")
    install.config(
        [
            ("default", "default", install.default),
            ("team", "repo", team),
            ("sibx", "read-only", sib),
        ]
    )
    before = _snapshot(sib)
    r = install.run(["vault", "delete", "team", "--remove-from-disk", "--yes"])
    assert r.returncode == 0, r.stderr
    assert not team.exists()
    assert _snapshot(sib) == before


def test_plain_vault_delete_of_a_read_only_entry_removes_only_the_config_entry(install):
    before = _snapshot(install.ro)
    r = install.run(["vault", "delete", "ro"])
    assert r.returncode == 0, r.stderr
    assert not any(v["name"] == "ro" for v in _config_vaults(install))
    assert _snapshot(install.ro) == before


def _config_vaults(install):
    cfg = install.config_home / "lore" / "config.json"
    return json.loads(cfg.read_text(encoding="utf-8"))["vaults"]


# ---------------------------------------------------------------------------
# vault reformat / sync boundaries
# ---------------------------------------------------------------------------


def test_vault_reformat_reaching_a_read_only_vault_is_one_line_not_a_traceback(install):
    before = _snapshot(install.ro)
    r = install.run(["vault", "reformat"])
    # A sweep skips a read-only vault with one line and otherwise succeeds.
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert MESSAGE in r.stderr
    assert _snapshot(install.ro) == before


def test_sync_reports_a_read_only_vault_by_message_not_as_a_silent_lock_skip(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    default = make_git_vault(tmp_path / "default")
    ro = make_git_vault(tmp_path / "ro")
    config_home = tmp_path / "config"
    write_vault_config(config_home, [("default", "default", default), ("ro", "read-only", ro)])
    (ro / "by-hand.md").write_text("edited outside lore\n")
    before = _snapshot(ro)
    r = run_cli(["sync"], vault=default, state_dir=state,
                env_extra={"XDG_CONFIG_HOME": str(config_home)})
    assert "Traceback" not in r.stderr, r.stderr
    assert MESSAGE in r.stderr
    assert "failed to acquire vault lock" not in r.stderr
    # A sweep skips a read-only vault with one line and otherwise succeeds.
    assert r.returncode == 0, r.stderr
    assert _snapshot(ro) == before
    # The read-only vault does not strand the writable one: it is still synced.
    assert "default:" in r.stdout, r.stdout


# ---------------------------------------------------------------------------
# The lock itself
# ---------------------------------------------------------------------------


@pytest.fixture
def lockenv(tmp_path):
    cfg = tmp_path / "config"
    env = {
        "XDG_CONFIG_HOME": str(cfg),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
    }
    roots = {}
    for name in ("default", "ro", "writable"):
        roots[name] = tmp_path / name
        roots[name].mkdir()

    def configure(entries):
        write_vault_config(cfg, entries)

    configure(
        [
            ("default", "default", roots["default"]),
            ("ro", "read-only", roots["ro"]),
            ("writable", "repo", roots["writable"]),
        ]
    )
    return tmp_path, env, roots, configure


def test_lock_on_a_read_only_root_raises_and_changes_nothing(lockenv):
    tmp, env, roots, _ = lockenv
    before = _snapshot(roots["ro"])
    with pytest.raises(locking.ReadOnlyVaultError) as exc:
        with locking.vault_write_lock(roots["ro"], env=env):
            pytest.fail("the lock must not be acquired")
    assert str(exc.value) == MESSAGE
    assert _snapshot(roots["ro"]) == before
    assert not (roots["ro"] / ".lore.lock").exists()


def test_lock_on_a_writable_root_acquires_and_creates_its_lock_file(lockenv):
    _, env, roots, _ = lockenv
    with locking.vault_write_lock(roots["writable"], env=env):
        assert (roots["writable"] / ".lore.lock").exists()


def test_lock_through_a_symlink_to_a_read_only_root_raises(lockenv):
    tmp, env, roots, _ = lockenv
    link = tmp / "link"
    link.symlink_to(roots["ro"])
    before = _snapshot(roots["ro"])
    with pytest.raises(locking.ReadOnlyVaultError):
        with locking.vault_write_lock(link, env=env):
            pass
    assert _snapshot(roots["ro"]) == before


def test_a_folder_two_entries_resolve_to_is_read_only_if_either_is(lockenv):
    tmp, env, roots, configure = lockenv
    configure(
        [
            ("default", "default", roots["default"]),
            ("writable", "repo", roots["writable"]),
            ("alias", "read-only", roots["writable"]),
        ]
    )
    with pytest.raises(locking.ReadOnlyVaultError) as exc:
        with locking.vault_write_lock(roots["writable"], env=env):
            pass
    assert "vault alias is read-only" in str(exc.value)
    assert not (roots["writable"] / ".lore.lock").exists()


def test_a_reentrant_acquisition_is_guarded_and_creates_nothing(lockenv):
    tmp, env, roots, configure = lockenv
    with locking.vault_write_lock(roots["writable"], env=env):
        # Declared read-only while this thread already holds the lock.
        configure(
            [
                ("default", "default", roots["default"]),
                ("writable", "read-only", roots["writable"]),
            ]
        )
        before = _snapshot(roots["writable"])
        with pytest.raises(locking.ReadOnlyVaultError):
            with locking.vault_write_lock(roots["writable"], env=env):
                pass
        assert _snapshot(roots["writable"]) == before
    assert locking._depths().get(str((roots["writable"] / ".lore.lock").resolve())) in (None, 0)


def test_locks_over_several_roots_refuse_before_taking_any(lockenv):
    _, env, roots, _ = lockenv
    with pytest.raises(locking.ReadOnlyVaultError):
        with locking.vault_write_locks(roots["default"], roots["ro"], env=env):
            pass
    assert not (roots["default"] / ".lore.lock").exists()
    assert not (roots["ro"] / ".lore.lock").exists()


def _absent_read_only(lockenv):
    tmp, env, roots, configure = lockenv
    missing = tmp / "ro-missing"
    configure(
        [
            ("default", "default", roots["default"]),
            ("ro", "read-only", missing),
            ("writable", "repo", roots["writable"]),
        ]
    )
    return env, roots, missing


def _spellings(missing):
    return [missing, missing.parent / "." / "ro-missing", missing.parent / "x" / ".." / "ro-missing"]


def test_lock_on_a_read_only_root_whose_folder_is_absent_raises_and_creates_nothing(lockenv):
    env, roots, missing = _absent_read_only(lockenv)
    for spelling in _spellings(missing):
        with pytest.raises(locking.ReadOnlyVaultError) as exc:
            with locking.vault_write_lock(spelling, env=env):
                pytest.fail("the lock must not be acquired")
        assert str(exc.value) == MESSAGE
        assert not missing.exists()
    with locking.vault_write_lock(roots["writable"], env=env):
        assert (roots["writable"] / ".lore.lock").exists()


def test_locks_over_several_roots_refuse_an_absent_read_only_root_before_taking_any(lockenv):
    env, roots, missing = _absent_read_only(lockenv)
    with pytest.raises(locking.ReadOnlyVaultError) as exc:
        with locking.vault_write_locks(roots["default"], missing, env=env):
            pytest.fail("the locks must not be acquired")
    assert str(exc.value) == MESSAGE
    assert not missing.exists()
    assert not (roots["default"] / ".lore.lock").exists()
    with locking.vault_write_locks(roots["default"], roots["writable"], env=env):
        assert (roots["writable"] / ".lore.lock").exists()


def test_assert_writable_refuses_an_absent_read_only_root_and_passes_an_absent_writable_one(lockenv):
    env, roots, missing = _absent_read_only(lockenv)
    with pytest.raises(locking.ReadOnlyVaultError):
        locking.assert_writable(missing, env=env)
    locking.assert_writable(missing.parent / "not-declared", env=env)
    assert locking.read_only_vault_name(missing, env=env) == "ro"
    assert not missing.exists()


def test_the_refusal_is_not_an_oserror_so_sync_cannot_read_it_as_a_lock_failure(lockenv):
    _, env, roots, _ = lockenv
    with pytest.raises(Exception) as exc:
        with locking.vault_write_lock(roots["ro"], env=env):
            pass
    assert not isinstance(exc.value, OSError)


def test_with_no_config_every_root_is_writable(tmp_path):
    root = tmp_path / "v"
    root.mkdir()
    env = {"XDG_CONFIG_HOME": str(tmp_path / "nocfg"), "XDG_STATE_HOME": str(tmp_path / "s"),
           "HOME": str(tmp_path / "h")}
    with locking.vault_write_lock(root, env=env):
        assert (root / ".lore.lock").exists()


@pytest.mark.parametrize("content", ["{not json", '{"vaults": "nope"}'])
def test_a_config_that_cannot_be_read_fails_closed(lockenv, content):
    tmp, env, roots, _ = lockenv
    (tmp / "config" / "lore" / "config.json").write_text(content, encoding="utf-8")
    with pytest.raises(locking.VaultWriteRefusedError) as exc:
        with locking.vault_write_lock(roots["writable"], env=env):
            pass
    assert not isinstance(exc.value, locking.ReadOnlyVaultError)
    assert not isinstance(exc.value, OSError)
    assert not (roots["writable"] / ".lore.lock").exists()


def test_is_read_only_root_reports_the_declaring_vault_name(lockenv):
    _, env, roots, _ = lockenv
    assert locking.read_only_vault_name(roots["ro"], env=env) == "ro"
    assert locking.read_only_vault_name(roots["writable"], env=env) is None


def _unborn_behind_origin(tmp_path):
    """A git vault with no commits whose origin has a branch to adopt, fetched."""
    remote = make_bare_remote(tmp_path / "remote.git")
    seed = make_git_vault(tmp_path / "seed")
    _git(seed, "remote", "add", "origin", str(remote))
    branch = _git(seed, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert _git(seed, "push", "origin", branch).returncode == 0
    vault = make_git_vault(tmp_path / "unborn", commit=False)
    _git(vault, "remote", "add", "origin", str(remote))
    assert _git(vault, "fetch", "origin").returncode == 0
    assert _git(vault, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0
    return vault


def _configure_pull(tmp_path, monkeypatch, vault, scope):
    cfg = tmp_path / "cfg"
    write_vault_config(cfg, [("default", "default", make_vault(tmp_path / "d")[0]),
                             ("pulled", scope, vault)])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    return cfg


def test_an_unborn_branch_pull_with_an_unreadable_config_holds_the_vault_for_a_person(
    tmp_path, monkeypatch
):
    vault = _unborn_behind_origin(tmp_path)
    cfg = _configure_pull(tmp_path, monkeypatch, vault, "repo")
    (cfg / "lore" / "config.json").write_text("{not json", encoding="utf-8")
    sync = load_script("lore.cli.sync")
    state = load_script("lore.cli.resolve_state")
    out, lines = _pull(sync, vault)
    assert out == (sync.PULL_AWAITING_PERSON, 0), lines
    assert any("cannot be read" in ln for ln in lines), lines
    assert state.vault_is_held(vault)
    assert _git(vault, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0


def test_an_unborn_branch_pull_into_a_read_only_vault_raises_and_a_writable_one_adopts_origin(
    tmp_path, monkeypatch
):
    vault = _unborn_behind_origin(tmp_path)
    _configure_pull(tmp_path, monkeypatch, vault, "read-only")
    sync = load_script("lore.cli.sync")
    state = load_script("lore.cli.resolve_state")
    with pytest.raises(locking.ReadOnlyVaultError):
        _pull(sync, vault)
    assert not state.vault_is_held(vault)
    assert _git(vault, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0

    _configure_pull(tmp_path, monkeypatch, vault, "repo")
    out, lines = _pull(sync, vault)
    assert out[0] == sync.PULL_OK, lines
    assert _git(vault, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0
