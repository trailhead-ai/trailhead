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

from conftest import make_git_vault, make_vault, run_cli, write_vault_config
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
    assert r.returncode != 0
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
    assert r.returncode != 0
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
