"""camp remove (alias rm) CLI integration: wiring to reconcile_break.

`_cmd_break_group_cli` → `_cmd_remove_group_cli`, dispatched by the
canonical `remove` verb (alias `rm`). The handler makes NO `lore finish`/`lore
flush` call and has NO session-liveness precondition (the session lock is gone).
Confinement (remove only under state_dir("camp")/<group>/<slug>)
and the dirty-tree block (refuse unless --force) are retained. reconcile_break
acquires the slug-scoped reconcile lock so two concurrent removals of the same
slug serialize instead of racing the manifest write (TOCTOU close, Security
finding).

Test contract:
1. camp remove <slug> removes only paths under state_dir/<group>/<slug>; the
   manifest is gone on success and canonical repos outside the state dir are
   untouched.
2. No `lore` subprocess/call is made; no "refuse if session live" precondition
   exists in the remove handler or reconcile_break.
3. `rm` alias dispatches to the same handler as `remove`; a dirty worktree
   blocks removal without --force.
4. reconcile_break acquires the reconcile lock — the slug .reconcile.lock is
   HELD during break (a concurrent acquire is denied).

Pattern: fake-git + tmp_path + CAMP_STATE_DIR/CAMP_CONFIG_DIR (no real
~/.claude touched; no real claude exec needed for remove).
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"
_SCRIPTS_DIR = _PLUGIN_DIR / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _load_cli_module():
    """Import the CLI command-group module holding the remove handler.

    `_cmd_remove_group_cli` moved out of the monolithic `cli/camp` into the
    `camp.cli.lifecycle` command-group module (setup/sync/remove/rebase).
    """
    return importlib.import_module("camp.cli.lifecycle")


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
    # Self-origin so the configured base `origin/main` resolves locally (a real
    # member always has a fetchable/resolvable base).
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "fetch", "origin", "--quiet"], check=True, capture_output=True
    )


def _wait_provisioned(manifest_path: Path, members: list[str], timeout: float = 20.0) -> None:
    """Poll the manifest until all named members reach 'ready' state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = json.loads(manifest_path.read_text())
            states = {m["name"]: m["provision_state"] for m in data["members"]}
            if all(states.get(m) == "ready" for m in members):
                return
        except Exception:
            pass
        time.sleep(0.1)
    # Let the test surface the actual state
    data = json.loads(manifest_path.read_text())
    states = {m["name"]: m["provision_state"] for m in data["members"]}
    assert all(states.get(m) == "ready" for m in members), (
        f"Worktrees not ready after {timeout}s: {states}"
    )


@pytest.fixture()
def remove_env(tmp_path: Path):
    """CLI environment with two real git repos + a provisioned workspace.

    Waits for the background provisioner to complete so member worktrees
    exist on disk before returning — required for dirty-block tests.
    """
    config_dir = tmp_path / "camp-config"
    groups_dir = config_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "camp-state"
    state_dir.mkdir(parents=True, exist_ok=True)

    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)

    # Author group via real CLI
    r = subprocess.run(
        [
            sys.executable,
            str(_CLI_CAMP),
            "group",
            "rmgroup",
            "--member",
            f"repo_a={repo_a}",
            "--member",
            f"repo_b={repo_b}",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, f"group authoring failed: {r.stderr}"

    # Provision a workspace via camp new (no harness exec needed)
    r2 = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "new", "ws-slug", "--group", "rmgroup"],
        capture_output=True,
        text=True,
        env={**env, "CAMP_TEST_NO_EXEC": "1"},
    )
    assert r2.returncode == 0, f"camp new failed: {r2.stderr}"

    ws_dir = state_dir / "rmgroup" / "worktrees" / "ws-slug"
    manifest = ws_dir / "manifest.json"

    # Wait for the detached background provisioner to complete so member
    # worktrees exist on disk (needed for dirty-block tests).
    _wait_provisioned(manifest, ["repo_a", "repo_b"])

    return {
        "env": env,
        "config_dir": config_dir,
        "state_dir": state_dir,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "ws_dir": ws_dir,
        "tmp_path": tmp_path,
    }


def _camp(remove_env, *args, extra_env=None):
    env = {**remove_env["env"]}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _manifest_path(remove_env, slug="ws-slug"):
    return remove_env["state_dir"] / "rmgroup" / "worktrees" / slug / "manifest.json"


class TestCampRemoveSuccess:
    def test_remove_clean_workspace_exits_zero(self, remove_env):
        """camp remove <slug> on a clean workspace exits 0."""
        r = _camp(remove_env, "remove", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, (
            f"camp remove should exit 0 on a clean workspace.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_remove_removes_manifest(self, remove_env):
        """camp remove <slug> removes the central manifest."""
        manifest = _manifest_path(remove_env)
        assert manifest.is_file(), "manifest should exist before remove"

        _camp(remove_env, "remove", "ws-slug", "--group", "rmgroup")

        assert not manifest.exists(), (
            f"manifest should be removed after camp remove.\nmanifest path: {manifest}"
        )

    def test_remove_only_touches_paths_under_state_dir(self, remove_env):
        """camp remove removes only paths under state_dir/<group>/<slug>; the
        canonical member repos (outside the state dir) are untouched."""
        ws_dir = remove_env["ws_dir"]
        repo_a = remove_env["repo_a"]
        repo_b = remove_env["repo_b"]
        assert ws_dir.is_dir(), "workspace dir should exist before remove"

        r = _camp(remove_env, "remove", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, f"camp remove failed: {r.stderr}"

        assert not ws_dir.exists(), "the workspace dir under state_dir must be removed"
        # Canonical repos (outside state_dir) must be untouched.
        assert (repo_a / "README.md").is_file(), "canonical repo_a must be untouched"
        assert (repo_b / "README.md").is_file(), "canonical repo_b must be untouched"

    def test_remove_name_flag_resolves_slug(self, remove_env):
        """camp remove --name <slug> uses the --name flag to resolve the slug."""
        r = _camp(remove_env, "remove", "--name", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, (
            f"camp remove --name <slug> should exit 0.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_remove_announces_removed_slug(self, remove_env):
        """camp remove output names the slug that was removed."""
        r = _camp(remove_env, "remove", "ws-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert "ws-slug" in combined, (
            f"camp remove output should mention the slug.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


class TestCampRemoveAliasParity:
    def test_rm_alias_exits_zero(self, remove_env):
        """`camp rm <slug>` (alias) dispatches to the same handler and exits 0."""
        r = _camp(remove_env, "rm", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, (
            f"camp rm (alias) should exit 0 on a clean workspace.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_rm_alias_removes_manifest(self, remove_env):
        """`camp rm` (alias) removes the manifest, same as `camp remove`."""
        manifest = _manifest_path(remove_env)
        assert manifest.is_file()
        _camp(remove_env, "rm", "ws-slug", "--group", "rmgroup")
        assert not manifest.exists(), "camp rm (alias) should remove the manifest"


class TestCampRemoveDirtyBlock:
    def _make_dirty(self, remove_env):
        """Write an uncommitted file into the repo_a worktree."""
        ws = remove_env["ws_dir"]
        worktree_a = ws / "repo_a"
        if worktree_a.is_dir():
            (worktree_a / "dirty.txt").write_text("uncommitted change\n")

    def test_dirty_worktree_blocked_without_force(self, remove_env):
        """camp remove without --force blocks on a dirty worktree (non-zero exit)."""
        self._make_dirty(remove_env)
        r = _camp(remove_env, "remove", "ws-slug", "--group", "rmgroup")
        assert r.returncode != 0, (
            f"camp remove should exit non-zero when worktree is dirty.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_dirty_worktree_blocked_via_rm_alias(self, remove_env):
        """The dirty-tree block holds through the `rm` alias too."""
        self._make_dirty(remove_env)
        r = _camp(remove_env, "rm", "ws-slug", "--group", "rmgroup")
        assert r.returncode != 0, (
            f"camp rm (alias) should also block on a dirty worktree.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_dirty_worktree_blocked_message_mentions_dirty(self, remove_env):
        """Dirty-block error message mentions 'dirty' or 'uncommitted'."""
        self._make_dirty(remove_env)
        r = _camp(remove_env, "remove", "ws-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert (
            "dirty" in combined.lower()
            or "uncommitted" in combined.lower()
            or "force" in combined.lower()
        ), (
            f"dirty-block error should mention 'dirty', 'uncommitted', or 'force'.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_dirty_worktree_succeeds_with_force(self, remove_env):
        """camp remove --force succeeds even when the worktree is dirty."""
        self._make_dirty(remove_env)
        r = _camp(remove_env, "remove", "--force", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, (
            f"camp remove --force should exit 0 on a dirty worktree.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_dirty_worktree_force_removes_manifest(self, remove_env):
        """camp remove --force removes the manifest even when the worktree was dirty."""
        self._make_dirty(remove_env)
        _camp(remove_env, "remove", "--force", "ws-slug", "--group", "rmgroup")
        assert not _manifest_path(remove_env).exists(), (
            "manifest should be removed after camp remove --force"
        )


class TestCampRemoveUnknownSlug:
    def test_unknown_slug_exits_nonzero(self, remove_env):
        """camp remove <unknown-slug> exits non-zero with a legible error."""
        r = _camp(remove_env, "remove", "no-such-slug", "--group", "rmgroup")
        assert r.returncode != 0, (
            f"camp remove on an unknown slug should exit non-zero.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_unknown_slug_error_is_legible(self, remove_env):
        """camp remove <unknown-slug> error message names the missing slug."""
        r = _camp(remove_env, "remove", "no-such-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert (
            "no-such-slug" in combined
            or "manifest" in combined.lower()
            or "not found" in combined.lower()
        ), (
            f"unknown-slug error should name the slug or explain what's missing.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


class TestCampRemoveInvokesReconcileBreak:
    def test_remove_reaches_reconcile_break_confinement(self, remove_env):
        """camp remove reachability: reconcile_break's confinement is exercised
        through the camp remove CLI path. A manifest with an old-layout path
        triggers a legible error, confirming the real reconcile_break is called
        (not the stub).
        """
        from camp.group.manifest import write_central_manifest, manifest_path_for

        env = {"CAMP_STATE_DIR": str(remove_env["state_dir"])}
        mpath = manifest_path_for("rmgroup", "ws-slug", env=env)

        # Overwrite manifest with an old-layout worktree_path to trigger LegacyLayoutError
        # (which only reconcile_break checks — the stub never reads the manifest)
        old_layout_path = str(remove_env["repo_a"] / ".claude" / "worktrees" / "ws-slug" / "repo_a")
        data = json.loads(mpath.read_text())
        data["members"][0]["worktree_path"] = old_layout_path
        write_central_manifest(mpath, data)

        r = _camp(remove_env, "remove", "ws-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert r.returncode != 0, "old-layout path should trigger a non-zero exit"
        assert (
            "legacy" in combined.lower()
            or "retired" in combined.lower()
            or "manually" in combined.lower()
        ), (
            f"camp remove should surface the LegacyLayoutError from reconcile_break.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


# ===========================================================================
# Structural: no lore / no session-liveness precondition in the remove path
# ===========================================================================


class TestRemovePathHasNoLoreOrSessionPrecondition:
    """The remove handler + reconcile_break make no `lore` call and carry no
    session-liveness precondition (there is no session lock)."""

    def test_remove_handler_is_named_remove(self):
        """The remove handler is named `_cmd_remove_group_cli`."""
        mod = _load_cli_module()
        assert hasattr(mod, "_cmd_remove_group_cli"), (
            "the remove handler must be named _cmd_remove_group_cli"
        )

    def test_remove_handler_makes_no_lore_or_session_call(self):
        mod = _load_cli_module()
        src = inspect.getsource(mod._cmd_remove_group_cli)
        # Narrow to actual lore invocations: module import or subprocess argv.
        # Raw substring "lore" would trip on innocent words like "explore";
        # we check specifically for `import lore`, `from lore`, or `"lore"`/`'lore'`
        # as a string literal (the lore CLI binary name in a subprocess call).
        assert not re.search(r'\bimport lore\b', src), (
            "remove handler must not import the lore module"
        )
        assert not re.search(r'\bfrom lore\b', src), (
            "remove handler must not import from the lore module"
        )
        assert '"lore"' not in src and "'lore'" not in src, (
            "remove handler must not invoke the lore CLI (no lore string literal)"
        )
        assert "session_lock" not in src, "remove handler must have no session-lock precondition"
        assert "acquire_session" not in src, (
            "remove handler must have no session-liveness precondition"
        )

    def test_reconcile_break_makes_no_lore_or_session_call(self):
        import camp.provision.reconcile as reconcile

        src = inspect.getsource(reconcile.reconcile_break)
        # Same narrowed assertion: check for import or subprocess invocation,
        # not raw substring (avoids false hits on "explore", "folklore", etc.).
        assert not re.search(r'\bimport lore\b', src), (
            "reconcile_break must not import the lore module"
        )
        assert not re.search(r'\bfrom lore\b', src), (
            "reconcile_break must not import from the lore module"
        )
        assert '"lore"' not in src and "'lore'" not in src, (
            "reconcile_break must not invoke the lore CLI (no lore string literal)"
        )
        assert "session_lock" not in src, "reconcile_break must hold no session-lock precondition"
        assert "acquire_session" not in src, (
            "reconcile_break must have no session-liveness precondition"
        )


# ===========================================================================
# In-process: reconcile_break acquires the slug reconcile lock (TOCTOU close)
# ===========================================================================


def _make_group_config(name, members, *, branch_pattern="worktree-{slug}"):
    return {"group": {"name": name}, "members": members, "branch_pattern": branch_pattern}


def _camp_state_env(tmp_path: Path) -> dict[str, str]:
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root)}


@pytest.fixture()
def inproc_group(tmp_path: Path, monkeypatch):
    """In-process group + env with the detached provisioner stubbed.

    Used for direct reconcile_break calls (lock-held + no-spurious-dir tests)."""
    import camp.provision.provision as provision

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)

    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)
    group = _make_group_config(
        "removegroup",
        [
            {"name": "repo_a", "repo_root": str(repo_a), "bootstrap": [], "base": "origin/main"},
            {"name": "repo_b", "repo_root": str(repo_b), "bootstrap": [], "base": "origin/main"},
        ],
    )
    env = _camp_state_env(tmp_path)
    return {"group": group, "repo_a": repo_a, "repo_b": repo_b, "env": env, "tmp_path": tmp_path}


def _provision_inproc(g, slug):
    from camp.provision.provision import bring_up_workspace
    from camp.provision.lifecycle import cmd_setup_group

    bring_up_workspace(g["group"], slug, env=g["env"])
    cmd_setup_group(g["group"], slug, env=g["env"])


class TestReconcileBreakHoldsLock:
    def test_reconcile_break_holds_reconcile_lock(self, inproc_group, monkeypatch):
        """The slug-scoped .reconcile.lock is HELD across the removal: a
        concurrent (separate-fd) non-blocking acquire is denied while
        reconcile_break runs (serializes the TOCTOU race)."""
        import fcntl

        import camp.provision.reconcile as reconcile
        from camp.group.manifest import manifest_path_for, lock_path_for
        from camp.provision.reconcile import reconcile_break

        g = inproc_group
        _provision_inproc(g, "feat-lock")
        mpath = manifest_path_for("removegroup", "feat-lock", env=g["env"])
        lock_path = lock_path_for(mpath.parent)

        observed: dict[str, bool] = {}
        orig = reconcile._remove_worktree_for_member

        def probe(*args, **kwargs):
            fd = open(str(lock_path), "w")
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Acquired → reconcile_break is NOT holding the lock.
                observed["held"] = False
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                # Denied → reconcile_break holds the exclusive lock.
                observed["held"] = True
            finally:
                fd.close()
            return orig(*args, **kwargs)

        monkeypatch.setattr(reconcile, "_remove_worktree_for_member", probe)

        reconcile_break(g["group"], "feat-lock", env=g["env"])

        assert observed.get("held") is True, (
            "reconcile_break must hold the slug reconcile lock during removal"
        )

    def test_lock_survives_workspace_rmtree(self, inproc_group, monkeypatch):
        """The held lock must outlive the teardown rmtree of ws_dir.

        reconcile_break flocks the slug lock then rmtree's the workspace dir at
        the END of its critical section. If the lockfile lived INSIDE ws_dir, the
        rmtree would delete its inode, and a concurrent acquirer arriving in the
        rmtree→release window would mkdir + flock a brand-new inode and win the
        lock — zero mutual exclusion. We simulate that acquirer by probing
        EXACTLY as reconcile_lock does (mkdir the lock's parent, open, flock NB)
        immediately AFTER the real rmtree, while reconcile_break still holds the
        lock. With the lockfile outside ws_dir the probe must be DENIED."""
        import fcntl
        import shutil

        import camp.provision.reconcile as reconcile
        from camp.group.manifest import manifest_path_for, lock_path_for
        from camp.provision.reconcile import reconcile_break

        g = inproc_group
        _provision_inproc(g, "feat-rmtree")
        mpath = manifest_path_for("removegroup", "feat-rmtree", env=g["env"])
        lock_path = lock_path_for(mpath.parent)

        observed: dict[str, bool] = {}
        real_rmtree = shutil.rmtree

        def rmtree_then_probe(path, *args, **kwargs):
            real_rmtree(path, *args, **kwargs)  # delete ws_dir for real
            # Mimic a concurrent reconcile_lock acquirer to the letter.
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = open(str(lock_path), "w")
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                observed["denied"] = False  # got it → mutual exclusion broken
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                observed["denied"] = True  # blocked → lock survived rmtree
            finally:
                fd.close()

        monkeypatch.setattr(reconcile.shutil, "rmtree", rmtree_then_probe)

        reconcile_break(g["group"], "feat-rmtree", env=g["env"])

        assert observed.get("denied") is True, (
            "the slug lock must survive the workspace-dir rmtree — a "
            "concurrent acquirer must still be blocked after teardown"
        )

    def test_remove_unknown_slug_creates_no_workspace_dir(self, inproc_group):
        """Removing a nonexistent slug raises ManifestError WITHOUT pre-creating
        its workspace dir — the reconcile lock's mkdir must be guarded by a
        fail-fast manifest read (else a later `camp new <slug>` wrongly resumes a
        ghost workspace)."""
        from camp.group.manifest import ManifestError, workspace_dir
        from camp.provision.reconcile import reconcile_break

        g = inproc_group
        ws = workspace_dir("removegroup", "ghost-slug", env=g["env"])
        assert not ws.exists()
        with pytest.raises(ManifestError):
            reconcile_break(g["group"], "ghost-slug", env=g["env"])
        assert not ws.exists(), (
            "removing a nonexistent slug must not pre-create its workspace dir"
        )


class TestRemoveExitCode:
    """The remove handler must wire reconcile_break's status. Per-member
    removal failures return status='ok_with_errors'; reporting success + exit 0
    would tell a scripted caller teardown succeeded when it did not."""

    def test_total_removal_failure_exits_nonzero_empty_stdout(
        self, inproc_group, monkeypatch, capsys
    ):
        import camp.provision.reconcile as reconcile

        cli = _load_cli_module()
        g = inproc_group

        def fake_break(group, slug, *, env=None, force=False):
            return {
                "status": "ok_with_errors",
                "slug": slug,
                "removed": [],
                "errors": [
                    "repo_a: git worktree remove failed",
                    "repo_b: git worktree remove failed",
                ],
            }

        monkeypatch.setattr(reconcile, "reconcile_break", fake_break)

        with pytest.raises(SystemExit) as exc:
            cli._cmd_remove_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        assert exc.value.code != 0, "total removal failure must exit nonzero"
        captured = capsys.readouterr()
        assert captured.out == "", "no success line on stdout when removal failed"
        assert "failed to remove" in captured.err
        assert "repo_a" in captured.err and "repo_b" in captured.err

    def test_partial_removal_failure_exits_nonzero(self, inproc_group, monkeypatch, capsys):
        import camp.provision.reconcile as reconcile

        cli = _load_cli_module()
        g = inproc_group

        def fake_break(group, slug, *, env=None, force=False):
            return {
                "status": "ok_with_errors",
                "slug": slug,
                "removed": ["repo_a"],
                "errors": ["repo_b: git worktree remove failed"],
            }

        monkeypatch.setattr(reconcile, "reconcile_break", fake_break)

        with pytest.raises(SystemExit) as exc:
            cli._cmd_remove_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        assert exc.value.code != 0
        captured = capsys.readouterr()
        assert captured.out == "", "partial failure must not print a success line"
        assert "partially removed" in captured.err and "repo_a" in captured.err

    def test_clean_removal_still_exits_zero(self, inproc_group, monkeypatch, capsys):
        import camp.provision.reconcile as reconcile

        cli = _load_cli_module()
        g = inproc_group

        def fake_break(group, slug, *, env=None, force=False):
            return {"status": "ok", "slug": slug, "removed": ["repo_a", "repo_b"], "errors": []}

        monkeypatch.setattr(reconcile, "reconcile_break", fake_break)

        # No SystemExit on the happy path.
        cli._cmd_remove_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        captured = capsys.readouterr()
        assert "removed worktree 'feat-x'" in captured.out


# ===========================================================================
# Adversarial confinement: symlink-escape paths raise ConfinementError
# ===========================================================================


class TestConfinementAdversarial:
    """The two ConfinementError paths in reconcile_break are each hit by a
    genuinely hostile symlink that makes a path resolve outside the confinement
    root.  These are the paths the LegacyLayoutError test cannot exercise:
    - Path A: a member worktree_path that is a symlink escaping the workspace dir
      (ConfinementError in the member-loop pre-check, before any removal).
    - Path B: the workspace dir itself resolves outside the worktrees root
      (ConfinementError in the pre-rmtree re-check, after all member removals).
    In both cases the target outside the confinement root must survive undeleted.
    """

    def test_member_symlink_escape_raises_confinement_error(self, inproc_group):
        """A manifest whose member worktree_path is a symlink that resolves
        outside the workspace dir triggers ConfinementError BEFORE any removal
        — the symlink target outside the state dir is not touched."""
        from camp.group.manifest import manifest_path_for, write_central_manifest, workspace_dir
        from camp.provision.reconcile import ConfinementError, reconcile_break

        g = inproc_group
        _provision_inproc(g, "feat-symlink")

        ws_dir = workspace_dir("removegroup", "feat-symlink", env=g["env"])
        mpath = manifest_path_for("removegroup", "feat-symlink", env=g["env"])

        # Adversarial target lives outside the state dir — must NOT be deleted.
        outside_target = g["tmp_path"] / "adversarial-target"
        outside_target.mkdir()
        sentinel = outside_target / "keep.txt"
        sentinel.write_text("must survive\n")

        # Plant a symlink INSIDE the workspace dir that points to the outside target.
        evil_link = ws_dir / "evil-worktree-link"
        evil_link.symlink_to(outside_target)
        assert evil_link.resolve() == outside_target.resolve(), "symlink setup sanity"
        assert evil_link.resolve() != ws_dir.resolve(), "symlink escapes ws_dir"

        # Overwrite one member entry to use the symlink path as worktree_path.
        data = json.loads(mpath.read_text())
        data["members"][0]["worktree_path"] = str(evil_link)
        write_central_manifest(mpath, data)

        # reconcile_break must detect the escape and raise ConfinementError.
        # It must NOT proceed to remove anything outside the state dir.
        with pytest.raises(ConfinementError):
            reconcile_break(g["group"], "feat-symlink", env=g["env"])

        # The adversarial target must be completely untouched.
        assert sentinel.exists(), (
            "confinement guard must not touch the symlink target outside the state dir"
        )
        assert outside_target.is_dir(), (
            "the outside-target directory must survive after ConfinementError"
        )

    def test_confinement_precedes_dirty_check_git_exec(self, inproc_group, monkeypatch):
        """The confinement pre-check must run BEFORE the dirty-check, so a
        worktree_path that escapes the workspace never gets a `git -C <path>`
        executed in it. Plant an escaping symlink as a member worktree_path, spy
        on _git_is_dirty, and assert ConfinementError is raised WITHOUT it firing
        (force defaults False, so the dirty-check would otherwise run)."""
        import camp.provision.reconcile as reconcile
        from camp.group.manifest import manifest_path_for, write_central_manifest, workspace_dir
        from camp.provision.reconcile import ConfinementError, reconcile_break

        g = inproc_group
        _provision_inproc(g, "feat-order")

        ws_dir = workspace_dir("removegroup", "feat-order", env=g["env"])
        mpath = manifest_path_for("removegroup", "feat-order", env=g["env"])

        outside_target = g["tmp_path"] / "order-target"
        outside_target.mkdir()
        evil_link = ws_dir / "evil-order-link"
        evil_link.symlink_to(outside_target)

        data = json.loads(mpath.read_text())
        data["members"][0]["worktree_path"] = str(evil_link)
        write_central_manifest(mpath, data)

        dirty_calls: list = []
        orig = reconcile._git_is_dirty
        monkeypatch.setattr(
            reconcile, "_git_is_dirty", lambda p: dirty_calls.append(p) or orig(p)
        )

        with pytest.raises(ConfinementError):
            reconcile_break(g["group"], "feat-order", env=g["env"])  # force=False

        assert dirty_calls == [], (
            "confinement must gate the dirty-check — no `git -C` may run in an "
            "unconfined worktree_path"
        )

    def test_workspace_dir_resolved_outside_root_refused_before_rmtree(
        self, inproc_group, monkeypatch
    ):
        """When the workspace dir itself resolves outside the worktrees root
        (e.g. via a symlink), reconcile_break raises ConfinementError at the
        pre-rmtree re-check — after member removals succeed but before shutil.rmtree
        — and the outside dir is NOT deleted (ConfinementError is the last guard).

        Setup: monkeypatch reconcile.workspace_dir to return an outside path;
        write member worktree_paths as nonexistent dirs under that outside path
        (wt_path.is_dir() == False → idempotent no-op, no errors, removals 'succeed');
        the pre-rmtree guard then sees the outside dir is not under worktrees_root.
        """
        import camp.provision.reconcile as reconcile
        from camp.group.manifest import manifest_path_for, write_central_manifest
        from camp.provision.reconcile import ConfinementError, reconcile_break

        g = inproc_group
        _provision_inproc(g, "feat-ws-escape")

        mpath = manifest_path_for("removegroup", "feat-ws-escape", env=g["env"])

        # Outside dir: not under the camp state worktrees root.
        outside = g["tmp_path"] / "outside-ws"
        outside.mkdir()
        sentinel = outside / "keep.txt"
        sentinel.write_text("must survive\n")

        # Rewrite manifest members to point to nonexistent paths under `outside`
        # (wt_path.is_dir() == False in _remove_worktree_for_member → no-op,
        # member is counted as 'removed' with no error).
        data = json.loads(mpath.read_text())
        for entry in data["members"]:
            entry["worktree_path"] = str(outside / entry["name"])
        write_central_manifest(mpath, data)

        # Patch reconcile.workspace_dir so ws_dir returns the outside path.
        # This makes ws_resolved resolve outside the real worktrees_root, which
        # triggers ConfinementError at the pre-rmtree re-check.
        real_workspace_dir = reconcile.workspace_dir

        def fake_workspace_dir(group_name, slug, *, env=None):
            if slug == "feat-ws-escape":
                return outside
            return real_workspace_dir(group_name, slug, env=env)

        monkeypatch.setattr(reconcile, "workspace_dir", fake_workspace_dir)

        # reconcile_break reaches the pre-rmtree re-check and raises.
        with pytest.raises(ConfinementError):
            reconcile_break(g["group"], "feat-ws-escape", env=g["env"])

        # The outside dir must not be rmtree'd — the guard fired before shutil.rmtree.
        assert sentinel.exists(), (
            "pre-rmtree confinement guard must not delete the outside dir"
        )
        assert outside.is_dir(), (
            "the outside workspace dir must survive after ConfinementError"
        )
