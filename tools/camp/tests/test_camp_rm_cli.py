"""camp rm CLI integration: wiring to reconcile_break.

Test contract (TDD — all must RED before implementation, GREEN after):

1. camp rm <slug> invokes the real reconcile_break for the resolved group/slug
   (not the stub). Exit 0 on success; manifest removed.
2. dirty worktree is blocked without --force (non-zero, legible error).
3. dirty worktree succeeds with --force.
4. unknown slug → legible error, non-zero exit.
5. camp rm --name <slug> resolves via the --name flag.
6. camp rm with no slug and cwd outside a workspace → legible error.
7. The Slice-1 stub tests in test_slice1_cli_surface.py for camp rm have been
   replaced: camp rm via the group-aware path now exits 0 on a clean workspace,
   not non-zero with a stub message.

Pattern: fake-git + tmp_path + CAMP_STATE_DIR/CAMP_CONFIG_DIR (no real
~/.claude touched; no real claude exec needed for rm).
"""
from __future__ import annotations

import json
import os
import shutil
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


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
                   check=True, capture_output=True)
    # Self-origin so the configured base `origin/main` resolves locally (a real
    # member always has a fetchable/resolvable base).
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", str(path)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "fetch", "origin", "--quiet"],
                   check=True, capture_output=True)


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
def rm_env(tmp_path: Path):
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
        [sys.executable, str(_CLI_CAMP), "group", "rmgroup",
         "--member", f"repo_a={repo_a}", "--member", f"repo_b={repo_b}"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, f"group authoring failed: {r.stderr}"

    # Provision a workspace via camp ai (no harness exec needed)
    r2 = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "ai", "ws-slug", "--group", "rmgroup"],
        capture_output=True, text=True, env={**env, "CAMP_TEST_NO_EXEC": "1"},
    )
    assert r2.returncode == 0, f"camp ai failed: {r2.stderr}"

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


def _camp(rm_env, *args, extra_env=None):
    env = {**rm_env["env"]}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True, text=True, env=env,
    )


def _manifest_path(rm_env, slug="ws-slug"):
    return rm_env["state_dir"] / "rmgroup" / "worktrees" / slug / "manifest.json"


class TestCampRmSuccess:
    def test_rm_clean_workspace_exits_zero(self, rm_env):
        """camp rm <slug> on a clean workspace exits 0."""
        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, (
            f"camp rm should exit 0 on a clean workspace.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_rm_removes_manifest(self, rm_env):
        """camp rm <slug> removes the central manifest."""
        manifest = _manifest_path(rm_env)
        assert manifest.is_file(), "manifest should exist before rm"

        _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")

        assert not manifest.exists(), (
            f"manifest should be removed after camp rm.\n"
            f"manifest path: {manifest}"
        )

    def test_rm_name_flag_resolves_slug(self, rm_env):
        """camp rm --name <slug> uses the --name flag to resolve the slug."""
        r = _camp(rm_env, "rm", "--name", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, (
            f"camp rm --name <slug> should exit 0.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_rm_announces_removed_slug(self, rm_env):
        """camp rm output names the slug that was removed."""
        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert "ws-slug" in combined, (
            f"camp rm output should mention the slug.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


class TestCampRmDirtyBlock:
    def _make_dirty(self, rm_env):
        """Write an uncommitted file into the repo_a worktree."""
        ws = rm_env["ws_dir"]
        worktree_a = ws / "repo_a"
        if worktree_a.is_dir():
            (worktree_a / "dirty.txt").write_text("uncommitted change\n")

    def test_dirty_worktree_blocked_without_force(self, rm_env):
        """camp rm without --force blocks on a dirty worktree (non-zero exit)."""
        self._make_dirty(rm_env)
        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")
        assert r.returncode != 0, (
            f"camp rm should exit non-zero when worktree is dirty.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_dirty_worktree_blocked_message_mentions_dirty(self, rm_env):
        """Dirty-block error message mentions 'dirty' or 'uncommitted'."""
        self._make_dirty(rm_env)
        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert (
            "dirty" in combined.lower()
            or "uncommitted" in combined.lower()
            or "force" in combined.lower()
        ), (
            f"dirty-block error should mention 'dirty', 'uncommitted', or 'force'.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_dirty_worktree_succeeds_with_force(self, rm_env):
        """camp rm --force succeeds even when the worktree is dirty."""
        self._make_dirty(rm_env)
        r = _camp(rm_env, "rm", "--force", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, (
            f"camp rm --force should exit 0 on a dirty worktree.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_dirty_worktree_force_removes_manifest(self, rm_env):
        """camp rm --force removes the manifest even when the worktree was dirty."""
        self._make_dirty(rm_env)
        _camp(rm_env, "rm", "--force", "ws-slug", "--group", "rmgroup")
        assert not _manifest_path(rm_env).exists(), (
            "manifest should be removed after camp rm --force"
        )


class TestCampRmUnknownSlug:
    def test_unknown_slug_exits_nonzero(self, rm_env):
        """camp rm <unknown-slug> exits non-zero with a legible error."""
        r = _camp(rm_env, "rm", "no-such-slug", "--group", "rmgroup")
        assert r.returncode != 0, (
            f"camp rm on an unknown slug should exit non-zero.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_unknown_slug_error_is_legible(self, rm_env):
        """camp rm <unknown-slug> error message names the missing slug."""
        r = _camp(rm_env, "rm", "no-such-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert (
            "no-such-slug" in combined
            or "manifest" in combined.lower()
            or "not found" in combined.lower()
        ), (
            f"unknown-slug error should name the slug or explain what's missing.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


class TestCampRmInvokesReconcileBreak:
    def test_rm_reaches_reconcile_break_confinement(self, rm_env):
        """camp rm reachability: reconcile_break's confinement is exercised through
        the camp rm CLI path. A manifest with an old-layout path triggers a legible
        error, confirming the real reconcile_break is called (not the stub).
        """
        from manifest import write_central_manifest, manifest_path_for

        env = {"CAMP_STATE_DIR": str(rm_env["state_dir"])}
        mpath = manifest_path_for("rmgroup", "ws-slug", env=env)

        # Overwrite manifest with an old-layout worktree_path to trigger LegacyLayoutError
        # (which only reconcile_break checks — the stub never reads the manifest)
        old_layout_path = str(rm_env["repo_a"] / ".claude" / "worktrees" / "ws-slug" / "repo_a")
        data = json.loads(mpath.read_text())
        data["members"][0]["worktree_path"] = old_layout_path
        write_central_manifest(mpath, data)

        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert r.returncode != 0, "old-layout path should trigger a non-zero exit"
        assert (
            "legacy" in combined.lower()
            or "retired" in combined.lower()
            or "manually" in combined.lower()
        ), (
            f"camp rm should surface the LegacyLayoutError from reconcile_break.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


# ---------------------------------------------------------------------------
# Slice 1: best-effort `lore finish` in `camp rm` teardown.
#
# reconcile_break() runs `lore finish --worktree <slug>` (cwd=ws_dir) after the
# confinement pre-check and before the member-removal loop. Failures NEVER raise
# and NEVER gate path removal. Three outcome branches, each with distinct
# operator feedback (FileNotFoundError / non-zero exit / exit-0 finalize).
#
# Fixture discipline (per plan): the happy path needs a REAL lore + vault so the
# note actually transitions; the failure cases inject a fake `lore` shim on PATH.
# The two paths are kept separate so a stub never bleeds into the happy-path
# assertion.
# ---------------------------------------------------------------------------

_LORE_CLI = (
    _REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "cli" / "lore"
)


def _git_vault(tmp_path: Path) -> Path:
    """Create a minimal git-backed lore vault (commit.gpgsign=false for a
    deterministic test commit — mirrors tools/lore/tests/test_finalize.py)."""
    vault = tmp_path / "lore-vault"
    (vault / "sessions").mkdir(parents=True)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@e.st"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Tester"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
                   check=True, capture_output=True)
    return vault


def _seed_camp_session_note(vault: Path, slug: str, *, status: str = "active") -> Path:
    """Write a camp-shaped session note whose worktree-name == slug (KU-1)."""
    sessions_dir = vault / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    note = sessions_dir / f"2026-06-21-1200-{slug}.md"
    note.write_text(
        f"---\n"
        f"type: session\n"
        f"project: test-project\n"
        f"worktree: {slug}\n"
        f"branch: worktree-{slug}\n"
        f"started: 2026-06-21T12:00:00Z\n"
        f"ended:\n"
        f"areas: []\n"
        f"phase: Orient\n"
        f"session_id:\n"
        f"status: {status}\n"
        f"---\n\n"
        f"# Session: {slug}\n\n"
        f"## What we did\n\n"
        f"## Decided\n\n"
        f"## Learned\n\n"
        f"## Open questions\n"
    )
    return note


def _parse_frontmatter(note: Path) -> dict:
    text = note.read_text()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip()
    return fm


def _real_lore_env(tmp_path: Path, vault: Path) -> dict[str, str]:
    """Env additions so the `camp rm` subprocess resolves a REAL `lore` against
    `vault`. Prepends a PATH dir holding a `lore` shim that execs the lore CLI."""
    bindir = tmp_path / "lore-bin"
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "lore"
    shim.write_text(
        "#!/bin/sh\n"
        f'exec "{sys.executable}" "{_LORE_CLI}" "$@"\n'
    )
    shim.chmod(0o755)
    return {
        "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
        "LORE_VAULT": str(vault),
        "XDG_STATE_HOME": str(tmp_path / "lore-state"),
        "XDG_CONFIG_HOME": str(tmp_path / "lore-config"),
        "LORE_EMAIL": "tester@example.com",
    }


def _stub_lore_env(tmp_path: Path, *, exit_code: int, stderr: str) -> dict[str, str]:
    """Env additions so the `camp rm` subprocess resolves a STUB `lore` that
    exits with `exit_code` and writes `stderr` to its stderr."""
    bindir = tmp_path / "lore-stub-bin"
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "lore"
    shim.write_text(
        "#!/bin/sh\n"
        f'echo "{stderr}" 1>&2\n'
        f"exit {exit_code}\n"
    )
    shim.chmod(0o755)
    return {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}


def _absent_lore_env(tmp_path: Path) -> dict[str, str]:
    """Env additions so the `camp rm` subprocess finds NO `lore` on PATH (so the
    subprocess.run call raises FileNotFoundError) while `git` stays resolvable.

    A real `lore` may be installed on the developer's PATH, so we cannot just
    inherit it — that would run the real CLI against a real vault (Axiom 6).
    Instead PATH is a dedicated bindir holding only a `git` symlink: `lore` is
    genuinely absent, `git` (the one other binary reconcile shells out to) works.
    """
    bindir = tmp_path / "git-only-bin"
    bindir.mkdir(parents=True, exist_ok=True)
    git_real = shutil.which("git")
    assert git_real, "git must be available for the teardown to run"
    (bindir / "git").symlink_to(git_real)
    return {"PATH": str(bindir)}


class TestCampRmFinalizesLoreSession:
    """Slice 1 — camp rm finalizes the workspace's active lore session note."""

    def test_happy_path_finalizes_note_and_confirms(self, rm_env):
        """Real finalize: an active session note transitions to complete with an
        ended: timestamp, and camp rm emits the confirmation."""
        tmp_path = rm_env["tmp_path"]
        vault = _git_vault(tmp_path)
        note = _seed_camp_session_note(vault, "ws-slug")
        assert _parse_frontmatter(note)["status"] == "active"

        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup",
                  extra_env=_real_lore_env(tmp_path, vault))

        assert r.returncode == 0, (
            f"camp rm should exit 0.\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
        fm = _parse_frontmatter(note)
        assert fm.get("status") == "complete", (
            f"note should transition to complete.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}\n{note.read_text()}"
        )
        assert fm.get("ended", ""), "note should get a non-empty ended: timestamp"
        combined = r.stdout + r.stderr
        assert "finalized session note" in combined.lower(), (
            f"camp rm should confirm the finalize.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_no_active_note_is_silent(self, rm_env):
        """A workspace whose note is already finished: exit 0, worktree removed,
        no warning, and NO finalize confirmation (silent)."""
        tmp_path = rm_env["tmp_path"]
        vault = _git_vault(tmp_path)
        _seed_camp_session_note(vault, "ws-slug", status="complete")

        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup",
                  extra_env=_real_lore_env(tmp_path, vault))

        assert r.returncode == 0, (
            f"camp rm should exit 0.\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert not _manifest_path(rm_env).exists(), "manifest should be removed"
        combined = (r.stdout + r.stderr).lower()
        assert "finalized session note" not in combined, (
            f"no confirmation when nothing was finalized.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "warning" not in combined, (
            f"no warning when there is simply no active note.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_nonzero_exit_warns_and_teardown_completes(self, rm_env):
        """lore finish exiting non-zero: camp rm logs the failure warning and
        STILL removes the worktree + manifest (failure not appended to errors)."""
        tmp_path = rm_env["tmp_path"]
        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup",
                  extra_env=_stub_lore_env(tmp_path, exit_code=3,
                                           stderr="boom from lore"))

        assert r.returncode == 0, (
            f"teardown must complete despite lore failure.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert not _manifest_path(rm_env).exists(), "manifest should be removed"
        assert not rm_env["ws_dir"].exists(), "workspace dir should be removed"
        combined = r.stdout + r.stderr
        assert "lore finish failed (exit 3)" in combined, (
            f"camp rm should warn with the exit code.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_lore_absent_warns_and_teardown_completes(self, rm_env):
        """lore not on PATH (FileNotFoundError): camp rm logs the not-found
        warning and STILL removes the worktree + manifest."""
        tmp_path = rm_env["tmp_path"]
        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup",
                  extra_env=_absent_lore_env(tmp_path))

        assert r.returncode == 0, (
            f"teardown must complete when lore is absent.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert not _manifest_path(rm_env).exists(), "manifest should be removed"
        assert not rm_env["ws_dir"].exists(), "workspace dir should be removed"
        combined = r.stdout + r.stderr
        assert "lore not found on PATH" in combined, (
            f"camp rm should warn that lore is absent.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_finalize_runs_before_path_removal(self, rm_env):
        """Ordering: finalize runs while ws_dir still exists, so the note ends up
        complete even though the worktree is gone afterward (load-bearing
        end-state assertion). Confinement preserved: only the workspace dir is
        removed."""
        tmp_path = rm_env["tmp_path"]
        vault = _git_vault(tmp_path)
        note = _seed_camp_session_note(vault, "ws-slug")

        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup",
                  extra_env=_real_lore_env(tmp_path, vault))

        assert r.returncode == 0, (
            f"camp rm should exit 0.\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert not rm_env["ws_dir"].exists(), "workspace dir should be removed"
        assert _parse_frontmatter(note).get("status") == "complete", (
            "note must be finalized even though the worktree is now gone "
            "(proves finalize ran before path removal)"
        )
