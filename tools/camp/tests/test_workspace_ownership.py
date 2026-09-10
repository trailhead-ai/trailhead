"""Tests for the owning-host field on the workspace manifest.

Test contract (all must RED before implementation, GREEN after):

1. `camp new` on a host with a declared self-name writes a manifest whose
   `owner` is that name.
2. `camp new` on a host with no declared self-name writes a manifest with no
   `owner` key at all — read back through the real reader.
3. `owner_of` on a manifest with no `owner` key returns None.
4. `owner_of` on a manifest carrying an owner returns it.
5. Compatibility: create/provision/list/activate/remove a keyless workspace
   exactly as before; re-reading the manifest after each step shows no
   `owner` key was added. This is the no-backfill claim, executed.
6. Re-running the seed on an existing workspace that already carries an
   owner leaves that owner unchanged, including when the running host's
   declared name differs — the seed never re-stamps.
7. Exactly one owning host: `owner_of` refuses a non-string owner with the
   named manifest error rather than silently coercing it.

Fixtures use real synthetic git repos in tmp_path + CAMP_STATE_DIR/CAMP_CONFIG_DIR
env injection; no real claude exec, no ~/.config or ~/.local/state touched.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"], check=True, capture_output=True
    )
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "fetch", "origin", "--quiet"], check=True, capture_output=True
    )


def _make_group(name, members, *, branch_pattern="worktree-{slug}"):
    return {"group": {"name": name}, "members": members, "branch_pattern": branch_pattern}


def _env(tmp_path: Path) -> dict[str, str]:
    """CAMP_STATE_DIR + CAMP_CONFIG_DIR + HOME, all hermetic. No hosts.toml
    written by default — a host that never declares a name."""
    config_root = tmp_path / "config"
    config_root.mkdir(parents=True, exist_ok=True)
    return {
        "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
        "CAMP_CONFIG_DIR": str(config_root),
        "HOME": str(tmp_path / "home"),
    }


def _declare_self_name(env: dict[str, str], self_name: str) -> None:
    config_root = Path(env["CAMP_CONFIG_DIR"])
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "hosts.toml").write_text(f'self_name = "{self_name}"\n', encoding="utf-8")


def _workspace_dir(group_name: str, slug: str, env):
    from camp.group.manifest import workspace_dir

    return workspace_dir(group_name, slug, env=env)


def _manifest_path(group_name: str, slug: str, env):
    from camp.group.manifest import manifest_path_for

    return manifest_path_for(group_name, slug, env=env)


@pytest.fixture()
def one_member_group(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    _init_git_repo(repo_a)
    group = _make_group(
        "owng",
        [{"name": "repo_a", "repo_root": str(repo_a), "tasks": [], "base": "origin/main"}],
    )
    return {"group": group, "repo_a": repo_a, "tmp_path": tmp_path}


@pytest.fixture(autouse=True)
def _stub_spawn(monkeypatch):
    """Never spawn a real detached provisioner in these tests."""
    import camp.provision.provision as provision

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)


# ---------------------------------------------------------------------------
# 1. Declared self-name is stamped as owner
# ---------------------------------------------------------------------------


class TestOwnerStampedFromDeclaredName:
    def test_camp_new_stamps_declared_self_name_as_owner(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.group.manifest import read_central_manifest, owner_of

        g = one_member_group
        env = _env(g["tmp_path"])
        _declare_self_name(env, "andromeda")

        bring_up_workspace(g["group"], "feat-o", env=env)

        data = read_central_manifest(_manifest_path("owng", "feat-o", env))
        assert owner_of(data) == "andromeda"


# ---------------------------------------------------------------------------
# 2. No declared self-name -> no owner key at all
# ---------------------------------------------------------------------------


class TestOwnerAbsentWithNoDeclaredName:
    def test_camp_new_with_no_declared_name_writes_no_owner_key(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.group.manifest import read_central_manifest

        g = one_member_group
        env = _env(g["tmp_path"])  # no hosts.toml written

        bring_up_workspace(g["group"], "feat-o", env=env)

        data = read_central_manifest(_manifest_path("owng", "feat-o", env))
        assert "owner" not in data


# ---------------------------------------------------------------------------
# 3/4/7. owner_of accessor
# ---------------------------------------------------------------------------


class TestOwnerOfAccessor:
    def test_owner_of_missing_key_returns_none(self):
        from camp.group.manifest import owner_of

        assert owner_of({"schema_version": 1, "members": []}) is None

    def test_owner_of_present_returns_it(self):
        from camp.group.manifest import owner_of

        assert owner_of({"owner": "andromeda"}) == "andromeda"

    def test_owner_of_non_string_raises_manifest_error(self):
        from camp.group.manifest import owner_of, ManifestError

        with pytest.raises(ManifestError):
            owner_of({"owner": 42})


# ---------------------------------------------------------------------------
# 5. Compatibility / no-backfill across the full lifecycle
# ---------------------------------------------------------------------------


class TestNoBackfillAcrossFullLifecycle:
    def test_lifecycle_verbs_never_add_owner_to_a_keyless_manifest(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group, cmd_ls_group
        from camp.provision.activation import activate_member
        from camp.cli.lifecycle import _cmd_remove_group_cli
        from camp.group.manifest import read_central_manifest

        g = one_member_group
        env = _env(g["tmp_path"])  # no self-name declared
        slug = "feat-o"
        mpath = _manifest_path("owng", slug, env)

        # created
        bring_up_workspace(g["group"], slug, env=env)
        assert "owner" not in read_central_manifest(mpath)

        # provisioned
        cmd_setup_group(g["group"], slug, env=env)
        assert "owner" not in read_central_manifest(mpath)

        # listed
        entries = cmd_ls_group(g["group"], env=env)
        assert any(e["slug"] == slug for e in entries)
        assert "owner" not in read_central_manifest(mpath)

        # activated
        activate_member(g["group"], slug, "repo_a", env=env)
        assert "owner" not in read_central_manifest(mpath)

        # removed — the owner-absence handling introduces no new failure on
        # the most destructive lifecycle verb.
        _cmd_remove_group_cli(["--name", slug, "--force"], g["group"], env, dry_run=False)
        assert not mpath.exists()


# ---------------------------------------------------------------------------
# 6. Never re-stamps an existing owner
# ---------------------------------------------------------------------------


class TestSeedNeverReStamps:
    def test_rerunning_seed_keeps_original_owner_even_if_host_name_differs(
        self, one_member_group
    ):
        from camp.provision.provision import seed_pending_workspace
        from camp.group.manifest import read_central_manifest, owner_of

        g = one_member_group
        env = _env(g["tmp_path"])
        _declare_self_name(env, "andromeda")
        slug = "feat-o"

        seed_pending_workspace(g["group"], slug, env=env)
        mpath = _manifest_path("owng", slug, env)
        assert owner_of(read_central_manifest(mpath)) == "andromeda"

        # The workspace changes hands: this host now declares a different name.
        _declare_self_name(env, "orion")

        seed_pending_workspace(g["group"], slug, env=env)
        data = read_central_manifest(mpath)
        assert owner_of(data) == "andromeda", "seed must never re-stamp an existing owner"


# ---------------------------------------------------------------------------
# 8. The writer-level guard: write_central_manifest refuses to drop or change
#    an on-disk owner unless the caller opts in.
# ---------------------------------------------------------------------------


class TestWriterGuardRefusesOwnerDrop:
    def test_write_refuses_write_that_would_drop_existing_owner(self, tmp_path):
        from camp.group.manifest import ManifestError, read_central_manifest, write_central_manifest

        mpath = tmp_path / "manifest.json"
        write_central_manifest(mpath, {"schema_version": 1, "owner": "andromeda", "members": []})

        with pytest.raises(ManifestError) as exc_info:
            write_central_manifest(mpath, {"schema_version": 1, "members": []})

        assert str(mpath) in str(exc_info.value)
        assert read_central_manifest(mpath)["owner"] == "andromeda", (
            "a refused write must leave the on-disk owner untouched"
        )

    def test_write_refuses_write_that_would_change_existing_owner_without_opt_in(self, tmp_path):
        from camp.group.manifest import ManifestError, read_central_manifest, write_central_manifest

        mpath = tmp_path / "manifest.json"
        write_central_manifest(mpath, {"schema_version": 1, "owner": "andromeda", "members": []})

        with pytest.raises(ManifestError):
            write_central_manifest(mpath, {"schema_version": 1, "owner": "orion", "members": []})

        assert read_central_manifest(mpath)["owner"] == "andromeda"


class TestWriterGuardOptIn:
    def test_write_accepts_owner_change_with_explicit_opt_in(self, tmp_path):
        from camp.group.manifest import owner_of, read_central_manifest, write_central_manifest

        mpath = tmp_path / "manifest.json"
        write_central_manifest(mpath, {"schema_version": 1, "owner": "andromeda", "members": []})

        write_central_manifest(
            mpath,
            {"schema_version": 1, "owner": "orion", "members": []},
            allow_owner_change=True,
        )

        assert owner_of(read_central_manifest(mpath)) == "orion"


class TestWriterGuardPassthroughWhenDiskHasNoOwner:
    def test_write_accepts_any_write_when_disk_carries_no_owner(self, tmp_path):
        from camp.group.manifest import owner_of, read_central_manifest, write_central_manifest

        mpath = tmp_path / "manifest.json"
        write_central_manifest(mpath, {"schema_version": 1, "members": []})

        write_central_manifest(mpath, {"schema_version": 1, "owner": "andromeda", "members": []})

        assert owner_of(read_central_manifest(mpath)) == "andromeda"

    def test_write_with_opt_in_also_accepted_when_disk_carries_no_owner(self, tmp_path):
        from camp.group.manifest import owner_of, read_central_manifest, write_central_manifest

        mpath = tmp_path / "manifest.json"
        write_central_manifest(mpath, {"schema_version": 1, "members": []})

        write_central_manifest(
            mpath,
            {"schema_version": 1, "owner": "andromeda", "members": []},
            allow_owner_change=True,
        )

        assert owner_of(read_central_manifest(mpath)) == "andromeda"


# ---------------------------------------------------------------------------
# 9. Real lifecycle verbs against a record-carrying workspace.
# ---------------------------------------------------------------------------


class TestFullReconcileSurvivesOwner:
    def test_reconcile_worktree_carries_forward_top_level_owner(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.provision.reconcile import reconcile_worktree
        from camp.group.manifest import read_central_manifest, owner_of

        g = one_member_group
        env = _env(g["tmp_path"])
        _declare_self_name(env, "andromeda")
        slug = "feat-o"

        bring_up_workspace(g["group"], slug, env=env)
        mpath = _manifest_path("owng", slug, env)
        assert owner_of(read_central_manifest(mpath)) == "andromeda"

        reconcile_worktree(g["group"], slug, env=env)

        assert owner_of(read_central_manifest(mpath)) == "andromeda"


class TestCampSetupSurvivesOwner:
    def test_setup_group_re_run_preserves_owner(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group
        from camp.group.manifest import read_central_manifest, owner_of

        g = one_member_group
        env = _env(g["tmp_path"])
        _declare_self_name(env, "andromeda")
        slug = "feat-o"
        bring_up_workspace(g["group"], slug, env=env)
        mpath = _manifest_path("owng", slug, env)

        cmd_setup_group(g["group"], slug, env=env)
        assert owner_of(read_central_manifest(mpath)) == "andromeda"

        # Re-run on an already-provisioned workspace.
        cmd_setup_group(g["group"], slug, env=env)
        assert owner_of(read_central_manifest(mpath)) == "andromeda"


class TestCampSyncSurvivesOwner:
    def test_sync_group_preserves_owner(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group, cmd_sync_group
        from camp.group.manifest import read_central_manifest, owner_of

        g = one_member_group
        env = _env(g["tmp_path"])
        _declare_self_name(env, "andromeda")
        slug = "feat-o"
        bring_up_workspace(g["group"], slug, env=env)
        cmd_setup_group(g["group"], slug, env=env)
        mpath = _manifest_path("owng", slug, env)

        cmd_sync_group(g["group"], env=env)

        assert owner_of(read_central_manifest(mpath)) == "andromeda"


class TestCampRebaseSurvivesOwner:
    def test_rebase_cli_preserves_owner(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group
        from camp.cli.lifecycle import _cmd_rebase_group_cli
        from camp.group.manifest import read_central_manifest, owner_of

        g = one_member_group
        env = _env(g["tmp_path"])
        _declare_self_name(env, "andromeda")
        slug = "feat-o"
        bring_up_workspace(g["group"], slug, env=env)
        cmd_setup_group(g["group"], slug, env=env)
        mpath = _manifest_path("owng", slug, env)

        _cmd_rebase_group_cli(["--name", slug], g["group"], env, dry_run=False)

        assert owner_of(read_central_manifest(mpath)) == "andromeda"


class TestActivateMemberSurvivesOwner:
    def test_activate_member_mark_activated_write_preserves_owner(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group
        from camp.provision.activation import activate_member
        from camp.group.manifest import read_central_manifest, owner_of

        g = one_member_group
        env = _env(g["tmp_path"])
        _declare_self_name(env, "andromeda")
        slug = "feat-o"
        bring_up_workspace(g["group"], slug, env=env)
        cmd_setup_group(g["group"], slug, env=env)  # brings repo_a to "ready"
        mpath = _manifest_path("owng", slug, env)

        activate_member(g["group"], slug, "repo_a", env=env)

        assert owner_of(read_central_manifest(mpath)) == "andromeda"


class TestActivateTaskPersistSurvivesOwner:
    def test_run_activate_tasks_in_background_write_preserves_owner(self, tmp_path):
        from camp.group.manifest import (
            manifest_path_for,
            owner_of,
            read_central_manifest,
            workspace_dir,
            write_central_manifest,
        )
        from camp.provision.activation import run_activate_tasks_in_background

        repo_a = tmp_path / "repo_a"
        _init_git_repo(repo_a)
        member_config = {
            "name": "repo_a",
            "repo_root": str(repo_a),
            "tasks": [
                {
                    "name": "dep-install",
                    "phase": "activate",
                    "required": False,
                    "timeout_seconds": None,
                    "steps": [{"name": "dep-install", "cmd": ["true"]}],
                }
            ],
            "base": "origin/main",
        }
        group = _make_group("owng2", [member_config])
        env = _env(tmp_path)
        slug = "feat-o"

        wt_path = workspace_dir("owng2", slug, env=env) / "repo_a"
        wt_path.mkdir(parents=True, exist_ok=True)

        mpath = manifest_path_for("owng2", slug, env=env)
        write_central_manifest(
            mpath,
            {
                "schema_version": 1,
                "group": "owng2",
                "slug": slug,
                "branch": f"worktree-{slug}",
                "members": [
                    {
                        "name": "repo_a",
                        "repo_root": str(repo_a),
                        "worktree_path": str(wt_path),
                        "provision_state": "ready",
                        "tasks": {},
                    }
                ],
                "owner": "andromeda",
            },
        )

        run_activate_tasks_in_background(group, slug, "repo_a", env=env)

        data = read_central_manifest(mpath)
        assert owner_of(data) == "andromeda"
        # confirm the write site actually ran, not merely returned early
        assert data["members"][0]["work_state"] == "ready"


class TestMemberStateFlipSurvivesOwner:
    def test_flip_member_state_unlocked_preserves_owner(self, tmp_path):
        from camp.group.manifest import (
            flip_member_state_unlocked,
            owner_of,
            read_central_manifest,
            reconcile_lock,
            write_central_manifest,
        )

        mpath = tmp_path / "manifest.json"
        write_central_manifest(
            mpath,
            {
                "schema_version": 1,
                "group": "g",
                "slug": "s",
                "branch": "worktree-s",
                "members": [
                    {
                        "name": "m",
                        "repo_root": "/x",
                        "worktree_path": "/y",
                        "provision_state": "pending",
                    }
                ],
                "owner": "andromeda",
            },
        )

        with reconcile_lock(mpath.parent):
            flip_member_state_unlocked(mpath, "m", "ready")

        data = read_central_manifest(mpath)
        assert owner_of(data) == "andromeda"
        assert data["members"][0]["provision_state"] == "ready"


class TestSequentialWritesSurviveOwner:
    def test_reconcile_then_setup_in_sequence_preserves_owner(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.provision.reconcile import reconcile_worktree
        from camp.provision.lifecycle import cmd_setup_group
        from camp.group.manifest import read_central_manifest, owner_of

        g = one_member_group
        env = _env(g["tmp_path"])
        _declare_self_name(env, "andromeda")
        slug = "feat-o"
        bring_up_workspace(g["group"], slug, env=env)
        mpath = _manifest_path("owng", slug, env)

        reconcile_worktree(g["group"], slug, env=env)
        assert owner_of(read_central_manifest(mpath)) == "andromeda"

        cmd_setup_group(g["group"], slug, env=env)
        assert owner_of(read_central_manifest(mpath)) == "andromeda"


class TestReconcileNeverInventsOwnership:
    def test_reconcile_and_setup_add_no_owner_to_keyless_manifest(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.provision.reconcile import reconcile_worktree
        from camp.provision.lifecycle import cmd_setup_group
        from camp.group.manifest import read_central_manifest

        g = one_member_group
        env = _env(g["tmp_path"])  # no self-name declared
        slug = "feat-o"
        bring_up_workspace(g["group"], slug, env=env)
        mpath = _manifest_path("owng", slug, env)
        assert "owner" not in read_central_manifest(mpath)

        reconcile_worktree(g["group"], slug, env=env)
        assert "owner" not in read_central_manifest(mpath)

        cmd_setup_group(g["group"], slug, env=env)
        assert "owner" not in read_central_manifest(mpath)


# ---------------------------------------------------------------------------
# 10. Hot-path safety: the guard changes nothing about an ownerless manifest.
# ---------------------------------------------------------------------------


class TestHotPathSafetyForOwnerlessWorkspace:
    def test_seeded_manifest_matches_expected_full_shape_with_no_owner(self, one_member_group):
        from camp.provision.provision import seed_pending_workspace
        from camp.group.manifest import read_central_manifest

        g = one_member_group
        env = _env(g["tmp_path"])  # no self-name declared
        slug = "feat-o"

        mpath = seed_pending_workspace(g["group"], slug, env=env)
        data = read_central_manifest(mpath)

        wt_path = _workspace_dir("owng", slug, env) / "repo_a"
        assert data == {
            "schema_version": 1,
            "group": "owng",
            "slug": slug,
            "branch": f"worktree-{slug}",
            "members": [
                {
                    "name": "repo_a",
                    "repo_root": str(g["repo_a"]),
                    "worktree_path": str(wt_path),
                    "provision_state": "pending",
                }
            ],
        }


# ---------------------------------------------------------------------------
# 11. The per-member carry-forward set is unaffected by the top-level fix.
# ---------------------------------------------------------------------------


class TestPerMemberCarryForwardUnaffectedByOwnerFix:
    def test_reconcile_preserves_member_level_keys_with_owner_present(self, one_member_group):
        from camp.provision.provision import bring_up_workspace
        from camp.provision.reconcile import reconcile_worktree
        from camp.group.manifest import read_central_manifest, write_central_manifest

        g = one_member_group
        env = _env(g["tmp_path"])
        _declare_self_name(env, "andromeda")
        slug = "feat-o"
        bring_up_workspace(g["group"], slug, env=env)
        mpath = _manifest_path("owng", slug, env)

        # Simulate cmd_setup_group having already flipped this member and
        # recorded per-task state, which reconcile_worktree's per-member
        # carry-forward reads on its next run.
        data = read_central_manifest(mpath)
        data["members"][0].update(
            {
                "provision_state": "ready",
                "activated": True,
                "work_state": "ready",
                "tasks": {"dep-install": {"state": "ok"}},
            }
        )
        write_central_manifest(mpath, data)

        reconcile_worktree(g["group"], slug, env=env)

        rebuilt = read_central_manifest(mpath)["members"][0]
        assert rebuilt["provision_state"] == "ready"
        assert rebuilt["activated"] is True
        assert rebuilt["work_state"] == "ready"
        assert rebuilt["tasks"] == {"dep-install": {"state": "ok"}}
