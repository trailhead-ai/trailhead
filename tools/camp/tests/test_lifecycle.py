"""Tests for unified-layout worktree lifecycle + central manifest.

This relocates member worktrees from the OLD per-repo layout
    <repo_root>/.claude/worktrees/<slug>/
to the unified workspace layout
    central_state_dir(group)/worktrees/<slug>/<member>/

Test contract (all must RED before implementation, GREEN after):

1. reconcile creates …/worktrees/<slug>/<member> for each member on
   worktree-<slug>; the central manifest lists both with worktree_path under the
   workspace dir; the configured bootstrap command runs per member.

2. status/break/ls operate across config members; fleet view (slug=None) works.

3. Partial-creation atomicity: re-run completes the set; manifest never lists a
   partial set.

4. Real bootstrap FAILURE on member-2 → atomicity holds, legible error names the
   member, no manifest written.

5. break removal confinement: resolves BOTH the manifest worktree_path AND the
   workspace dir before the is_relative_to check. A symlink-escaping path is
   REJECTED; an OLD-layout path (outside the workspace dir) → legible
   schema_version/legacy-layout error, not a half-applied break.

6. Break atomicity symmetry: a mid-break failure → manifest not left listing a
   removed member.

7. Concurrent-run guard: two reconciles racing the same slug don't both add.

8. Malformed/truncated central manifest → legible error, not a traceback.

9. Branch-base policy: default base origin/main; per-member `base` override
   honored. Asserts the `git worktree add -b <branch> <wt> <base>` invocation on
   a fake (the fetch is deferred to the async provisioner).

10. Success summary: a one-line summary on success.

Fixtures use synthetic git repos in tmp_path (real git init + commit so
git worktree add actually works) plus fake-git assertions for the branch-base
invocation shape. The resolver's env= injection is used for all state paths.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# Helpers — synthetic git repos
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    """Initialize a real git repo at path with an initial commit."""
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
    readme = path / "README.md"
    readme.write_text("# test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        capture_output=True,
    )


def _make_group_config(
    name: str,
    members: list[dict[str, Any]],
    *,
    branch_pattern: str = "worktree-{slug}",
) -> dict[str, Any]:
    """Build a parsed group config dict matching group_config.load_group output."""
    return {
        "group": {"name": name},
        "members": members,
        "branch_pattern": branch_pattern,
    }


def _provision_task(name: str, cmd: list[str], *, required: bool = True) -> dict[str, Any]:
    """Build a member provision-phase task in the config-resolved shape (steps
    carry {name, cmd}), matching what load_group emits for a member's tasks."""
    return {
        "name": name,
        "phase": "provision",
        "required": required,
        "timeout_seconds": None,
        "steps": [{"name": name, "cmd": cmd}],
    }


def _camp_state_env(tmp_path: Path) -> dict[str, str]:
    """Return env override dict pointing CAMP_STATE_DIR at tmp_path."""
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root)}


def _member_wt(group_name: str, slug: str, member: str, env: dict[str, str]) -> Path:
    """Return the unified-layout worktree path for a member:
    central_state_dir(group)/worktrees/<slug>/<member>."""
    from camp.group.resolve import central_state_dir

    return central_state_dir(group_name, env=env) / "worktrees" / slug / member


# ---------------------------------------------------------------------------
# Fixture: 2-member synthetic group
# ---------------------------------------------------------------------------


@pytest.fixture()
def two_member_group(tmp_path: Path):
    """A 2-member group with real git repos, bootstrap sentinel files, and env."""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    # Bootstrap for each: touch a sentinel file to prove it ran
    sentinel_a = tmp_path / "bootstrap_a_ran"
    sentinel_b = tmp_path / "bootstrap_b_ran"

    group = _make_group_config(
        "testgroup",
        [
            {
                "name": "repo_a",
                "repo_root": str(repo_a),
                "tasks": [_provision_task("bootstrap", ["touch", str(sentinel_a)])],
            },
            {
                "name": "repo_b",
                "repo_root": str(repo_b),
                "tasks": [_provision_task("bootstrap", ["touch", str(sentinel_b)])],
            },
        ],
    )
    env = _camp_state_env(tmp_path)
    return {
        "group": group,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "sentinel_a": sentinel_a,
        "sentinel_b": sentinel_b,
        "env": env,
        "tmp_path": tmp_path,
    }


# ---------------------------------------------------------------------------
# Test 1: reconcile_worktree creates both member worktrees + manifest
# ---------------------------------------------------------------------------


class TestReconcileWorktreeCreates:
    def test_creates_both_worktrees_on_correct_branch(self, two_member_group):
        """Both member worktrees land under …/worktrees/<slug>/<member>."""
        from camp.provision.reconcile import reconcile_worktree

        g = two_member_group
        slug = "feat-x"

        reconcile_worktree(g["group"], slug, env=g["env"])

        wt_a = _member_wt("testgroup", slug, "repo_a", g["env"])
        wt_b = _member_wt("testgroup", slug, "repo_b", g["env"])
        assert wt_a.is_dir(), f"worktree for repo_a not found at {wt_a}"
        assert wt_b.is_dir(), f"worktree for repo_b not found at {wt_b}"

        # Verify branch name
        branch_a = subprocess.run(
            ["git", "-C", str(wt_a), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert branch_a == f"worktree-{slug}"

    def test_central_manifest_written_after_success(self, two_member_group):
        """Central manifest is written at central_state_dir/worktrees/<slug>/manifest.json."""
        from camp.group.resolve import central_state_dir
        from camp.group.manifest import read_central_manifest
        from camp.provision.reconcile import reconcile_worktree

        g = two_member_group
        slug = "feat-x"

        reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        assert manifest_path.is_file(), f"manifest not found at {manifest_path}"

        data = read_central_manifest(manifest_path)
        assert data is not None
        member_names = [m["name"] for m in data["members"]]
        assert "repo_a" in member_names
        assert "repo_b" in member_names

    def test_manifest_lists_correct_worktree_paths(self, two_member_group):
        """Manifest records the actual worktree paths for each member."""
        from camp.group.manifest import read_central_manifest
        from camp.provision.reconcile import reconcile_worktree
        from camp.group.resolve import central_state_dir

        g = two_member_group
        slug = "feat-x"
        reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        data = read_central_manifest(manifest_path)

        wt_a = _member_wt("testgroup", slug, "repo_a", g["env"])
        wt_b = _member_wt("testgroup", slug, "repo_b", g["env"])

        by_name = {m["name"]: m for m in data["members"]}
        assert Path(by_name["repo_a"]["worktree_path"]) == wt_a
        assert Path(by_name["repo_b"]["worktree_path"]) == wt_b

    def test_configured_bootstrap_runs_per_member(self, two_member_group):
        """The bootstrap command from the group config runs for each member."""
        from camp.provision.reconcile import reconcile_worktree

        g = two_member_group
        reconcile_worktree(g["group"], "feat-x", env=g["env"])

        assert g["sentinel_a"].exists(), "bootstrap_a sentinel not created"
        assert g["sentinel_b"].exists(), "bootstrap_b sentinel not created"

    def test_manifest_mode_is_0600(self, two_member_group):
        """Central manifest is written with mode 0o600 (umask-proof)."""
        from camp.provision.reconcile import reconcile_worktree
        from camp.group.resolve import central_state_dir

        g = two_member_group
        slug = "feat-x"
        reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        mode = manifest_path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"


# ---------------------------------------------------------------------------
# Test 2: idempotency — re-run reconcile is a no-op
# ---------------------------------------------------------------------------


class TestReconcileIdempotency:
    def test_rerun_is_noop_no_worktree_already_exists_error(self, two_member_group):
        """Re-running reconcile on an already-created worktree is a no-op (no crash)."""
        from camp.provision.reconcile import reconcile_worktree

        g = two_member_group
        slug = "feat-x"
        reconcile_worktree(g["group"], slug, env=g["env"])
        # Second run must not raise or produce 'already exists' errors
        reconcile_worktree(g["group"], slug, env=g["env"])

    def test_rerun_does_not_create_duplicate_manifest_entries(self, two_member_group):
        """Re-running does not duplicate entries in the manifest."""
        from camp.provision.reconcile import reconcile_worktree
        from camp.group.manifest import read_central_manifest
        from camp.group.resolve import central_state_dir

        g = two_member_group
        slug = "feat-x"
        reconcile_worktree(g["group"], slug, env=g["env"])
        reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        data = read_central_manifest(manifest_path)
        assert len(data["members"]) == 2


# ---------------------------------------------------------------------------
# Test 3: partial-creation atomicity (crash before member-2)
# ---------------------------------------------------------------------------


class TestPartialCreationAtomicity:
    def test_partial_create_then_rerun_completes_set(self, two_member_group):
        """After partial creation (member-1 only), re-run completes without crash."""
        from camp.provision.reconcile import reconcile_worktree
        from camp.group.resolve import central_state_dir

        g = two_member_group
        slug = "feat-partial"

        # Manually create only the first member's worktree (simulate crash after member-1)
        wt_a = _member_wt("testgroup", slug, "repo_a", g["env"])
        wt_a.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "-C", str(g["repo_a"]), "worktree", "add", str(wt_a), "-b", f"worktree-{slug}"],
            check=True,
            capture_output=True,
        )

        # Verify no manifest yet
        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        assert not manifest_path.exists()

        # Re-run reconcile → must complete the set
        reconcile_worktree(g["group"], slug, env=g["env"])

        wt_b = _member_wt("testgroup", slug, "repo_b", g["env"])
        assert wt_b.is_dir(), "member-2 worktree not created on re-run"
        assert manifest_path.is_file(), "manifest not written after completing partial creation"

    def test_manifest_never_lists_partial_set(self, two_member_group):
        """The manifest is never written until ALL members have their worktree."""
        from camp.group.resolve import central_state_dir
        from camp.provision.reconcile import reconcile_worktree

        g = two_member_group
        slug = "feat-partial2"

        original_add = None

        def failing_add_for_member_b(member, wt_path, branch, repo_root):
            """Fail when processing repo_b."""
            if member["name"] == "repo_b":
                raise RuntimeError("simulated crash mid-creation")
            return original_add(member, wt_path, branch, repo_root)

        from camp.provision.reconcile import _add_worktree_for_member as _orig

        original_add = _orig

        with patch("camp.provision.reconcile._add_worktree_for_member", side_effect=failing_add_for_member_b):
            with pytest.raises(Exception):
                reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        # Manifest must NOT have been written with partial data
        assert not manifest_path.exists(), (
            "manifest was written despite partial member creation failure"
        )


# ---------------------------------------------------------------------------
# Test 4: bootstrap FAILURE on member-2 → atomicity holds
# ---------------------------------------------------------------------------


class TestBootstrapFailureAtomicity:
    def test_bootstrap_failure_on_member2_no_manifest(self, tmp_path):
        """A real bootstrap failure on member-2 → no manifest written."""
        from camp.provision.reconcile import reconcile_worktree
        from camp.group.resolve import central_state_dir

        repo_a = tmp_path / "repo_a"
        repo_b = tmp_path / "repo_b"
        _init_git_repo(repo_a)
        _init_git_repo(repo_b)

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "failgroup",
            [
                {
                    "name": "repo_a",
                    "repo_root": str(repo_a),
                    "tasks": [_provision_task("bootstrap", ["true"])],  # always succeeds
                },
                {
                    "name": "repo_b",
                    "repo_root": str(repo_b),
                    "tasks": [_provision_task("bootstrap", ["false"])],  # always fails (exit 1)
                },
            ],
        )

        slug = "test-fail"
        with pytest.raises(Exception) as exc_info:
            reconcile_worktree(group, slug, env=env)

        # Error message must name the failing member
        assert "repo_b" in str(exc_info.value), (
            f"error should name failing member 'repo_b', got: {exc_info.value}"
        )

        # No manifest must have been written
        state_dir = central_state_dir("failgroup", env=env)
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        assert not manifest_path.exists(), "manifest written despite bootstrap failure"

    def test_bootstrap_failure_error_names_member(self, tmp_path):
        """Bootstrap failure error message clearly names the failing member."""
        from camp.provision.reconcile import reconcile_worktree

        repo_a = tmp_path / "repo_a"
        repo_b = tmp_path / "repo_b"
        _init_git_repo(repo_a)
        _init_git_repo(repo_b)

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "failgroup",
            [
                {"name": "repo_a", "repo_root": str(repo_a),
                 "tasks": [_provision_task("bootstrap", ["true"])]},
                {"name": "repo_b", "repo_root": str(repo_b),
                 "tasks": [_provision_task("bootstrap", ["false"])]},
            ],
        )

        with pytest.raises(Exception) as exc_info:
            reconcile_worktree(group, "fail-slug", env=env)

        assert "repo_b" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test: branch-base policy (fake git — fetch deferred to the async provisioner)
# ---------------------------------------------------------------------------


class _FakeGit:
    """Records git invocations and returns canned results.

    By default reports the worktree branch as absent (so the create path
    `worktree add -b <branch> <wt> <base>` runs) and the base ref as resolvable.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, repo_root, *args):
        self.calls.append(list(args))

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        res = _Result()
        # `branch --list <branch>` → empty (branch does not exist locally).
        if args and args[0] == "branch":
            res.stdout = ""
        # `rev-parse --verify <base>` → success (base resolves).
        if args and args[0] == "rev-parse":
            res.stdout = "deadbeef"
        # `worktree list --porcelain` → empty (not registered).
        return res

    def worktree_add_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["worktree", "add"]]


class TestBranchBasePolicy:
    def test_default_base_origin_main_in_worktree_add(self, monkeypatch, tmp_path):
        """Default base origin/main is the start-point of `worktree add -b`."""
        import camp.provision.reconcile as reconcile
        from camp.provision.reconcile import _add_worktree_for_member

        fake = _FakeGit()
        monkeypatch.setattr(reconcile, "_git", fake)

        member = {"name": "repo_a", "repo_root": str(tmp_path / "repo_a")}
        wt_path = tmp_path / "ws" / "feat" / "repo_a"
        _add_worktree_for_member(
            member,
            wt_path,
            "worktree-feat",
            Path(member["repo_root"]),
            base="origin/main",
            slug="feat",
        )

        adds = fake.worktree_add_calls()
        assert len(adds) == 1, f"expected one worktree add, got: {fake.calls}"
        argv = adds[0]
        assert "-b" in argv
        assert "worktree-feat" in argv
        # The start-point (last positional) is the configured base.
        assert argv[-1] == "origin/main", f"base not honored: {argv}"

    def test_per_member_base_override_honored(self, monkeypatch, tmp_path):
        """A per-member `base` overrides the default in `worktree add -b`."""
        import camp.provision.reconcile as reconcile
        from camp.provision.reconcile import _add_worktree_for_member

        fake = _FakeGit()
        monkeypatch.setattr(reconcile, "_git", fake)

        member = {"name": "repo_a", "repo_root": str(tmp_path / "repo_a")}
        wt_path = tmp_path / "ws" / "feat" / "repo_a"
        _add_worktree_for_member(
            member,
            wt_path,
            "worktree-feat",
            Path(member["repo_root"]),
            base="origin/trunk",
            slug="feat",
        )

        argv = fake.worktree_add_calls()[0]
        assert argv[-1] == "origin/trunk", f"per-member base not honored: {argv}"

    def test_reconcile_reads_member_base_from_config(self, monkeypatch, tmp_path):
        """reconcile_worktree threads each member's `base` into the add invocation."""
        import camp.provision.reconcile as reconcile

        captured: dict[str, str] = {}

        def fake_add(member, wt_path, branch, repo_root, *, base, slug):
            captured[member["name"]] = base

        monkeypatch.setattr(reconcile, "_add_worktree_for_member", fake_add)

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "basegroup",
            [
                {"name": "repo_a", "repo_root": str(tmp_path / "a"), "tasks": []},
                {
                    "name": "repo_b",
                    "repo_root": str(tmp_path / "b"),
                    "tasks": [],
                    "base": "origin/trunk",
                },
            ],
        )

        reconcile.reconcile_worktree(group, "feat-base", env=env)

        assert captured["repo_a"] == "origin/main"
        assert captured["repo_b"] == "origin/trunk"


# ---------------------------------------------------------------------------
# worktree admin name == slug (stage + git worktree move)
# ---------------------------------------------------------------------------


def _admin_name(wt: Path) -> str:
    """The git-internal worktree admin name = basename of `git rev-parse --git-dir`.

    This is exactly what Claude Code surfaces as `workspace.git_worktree`.
    """
    git_dir = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(git_dir).name


class TestWorktreeAdminName:
    def test_admin_name_is_slug_not_member(self, two_member_group):
        """Each member worktree's git admin name is the SLUG; folder is the member."""
        from camp.provision.reconcile import reconcile_worktree

        g = two_member_group
        slug = "lore-refactor-s3"

        reconcile_worktree(g["group"], slug, env=g["env"])

        for member in ("repo_a", "repo_b"):
            wt = _member_wt("testgroup", slug, member, g["env"])
            assert wt.is_dir(), f"worktree missing for {member} at {wt}"
            # Folder keeps the member name…
            assert wt.name == member
            # …but git's internal admin name is the slug (what git_worktree reports).
            assert _admin_name(wt) == slug, (
                f"admin name for {member} should be {slug!r}, got {_admin_name(wt)!r}"
            )

    def test_idempotent_rerun_is_noop(self, two_member_group):
        """A second reconcile is a clean no-op — worktrees + admin name unchanged."""
        from camp.provision.reconcile import reconcile_worktree

        g = two_member_group
        slug = "feat-idem"

        reconcile_worktree(g["group"], slug, env=g["env"])
        reconcile_worktree(g["group"], slug, env=g["env"])  # must not raise

        wt = _member_wt("testgroup", slug, "repo_a", g["env"])
        assert wt.is_dir()
        assert _admin_name(wt) == slug

    def test_member_equal_slug_short_circuit_still_adds(self, tmp_path):
        """When slug == member name (stage == wt_path) the add happens directly,
        with no move, and still produces a registered worktree named the slug."""
        from camp.provision.reconcile import _add_worktree_for_member, _worktree_registered

        repo = tmp_path / "trailhead"
        _init_git_repo(repo)
        slug = "trailhead"  # equals the member name
        member = {"name": "trailhead", "repo_root": str(repo)}
        wt_path = tmp_path / "ws" / slug / "trailhead"

        _add_worktree_for_member(
            member,
            wt_path,
            f"worktree-{slug}",
            repo,
            base="origin/main",
            slug=slug,
        )

        assert wt_path.is_dir()
        assert _worktree_registered(repo, wt_path)
        assert _admin_name(wt_path) == slug

    def test_partial_stage_recovery_resumes_at_move(self, tmp_path):
        """A prior run that added <stage> but never moved → recover via move, not a
        failing re-add."""
        from camp.provision.reconcile import _add_worktree_for_member

        repo = tmp_path / "trailhead"
        _init_git_repo(repo)
        slug = "feat-recov"
        member = {"name": "trailhead", "repo_root": str(repo)}
        wt_path = tmp_path / "ws" / slug / "trailhead"
        stage = wt_path.parent / slug
        wt_path.parent.mkdir(parents=True, exist_ok=True)

        # Simulate the partial state: stage added (registered) but not moved.
        branch = f"worktree-{slug}"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(stage), "HEAD"],
            check=True,
            capture_output=True,
        )
        assert stage.is_dir()

        # Recovery must move (not re-add, which would fail "already registered").
        _add_worktree_for_member(
            member,
            wt_path,
            branch,
            repo,
            base="origin/main",
            slug=slug,
        )

        assert wt_path.is_dir(), "recovery did not produce the final worktree"
        assert not stage.exists(), "stage should have been moved away"
        assert _admin_name(wt_path) == slug

    def test_orphaned_stage_dir_is_cleaned_and_add_succeeds(self, tmp_path):
        """A stage dir left on disk but NOT git-registered (failed prior add /
        pruned registry) must not brick the fresh add — it is cleared first."""
        from camp.provision.reconcile import _add_worktree_for_member

        repo = tmp_path / "trailhead"
        _init_git_repo(repo)
        slug = "feat-orphan"
        member = {"name": "trailhead", "repo_root": str(repo)}
        wt_path = tmp_path / "ws" / slug / "trailhead"
        stage = wt_path.parent / slug

        # Orphaned stage: a real directory with content, not a registered worktree.
        stage.mkdir(parents=True)
        (stage / "leftover.txt").write_text("stale\n")

        _add_worktree_for_member(
            member,
            wt_path,
            f"worktree-{slug}",
            repo,
            base="origin/main",
            slug=slug,
        )

        assert wt_path.is_dir(), "add should succeed despite the orphaned stage dir"
        assert not stage.exists(), "orphaned stage dir should have been cleared/moved"
        assert _admin_name(wt_path) == slug

    def test_confinement_rejects_escaping_slug(self, tmp_path):
        """A '../'-laden slug that would push <stage> outside the workspace is
        rejected with ReconcileError before any git call."""
        from camp.provision.reconcile import _add_worktree_for_member, ReconcileError

        repo = tmp_path / "trailhead"
        _init_git_repo(repo)
        member = {"name": "trailhead", "repo_root": str(repo)}
        wt_path = tmp_path / "ws" / "feat" / "trailhead"

        with pytest.raises(ReconcileError):
            _add_worktree_for_member(
                member,
                wt_path,
                "worktree-feat",
                repo,
                base="origin/main",
                slug="../escape",
            )


# ---------------------------------------------------------------------------
# Test 5: break removal confinement
# ---------------------------------------------------------------------------


class TestBreakRemovalConfinement:
    """Confinement anchors on the resolved workspace dir
    (central_state_dir(group)/worktrees/<slug>), NOT the repo_root.

    Both the manifest-supplied target and the workspace dir are .resolve()'d
    BEFORE the is_relative_to check, so a symlink-escaping path is rejected.
    """

    def test_break_rejects_path_outside_workspace_dir(self, two_member_group, tmp_path):
        """A manifest worktree_path outside the workspace dir → named error, no deletion."""
        from camp.group.resolve import central_state_dir
        from camp.provision.reconcile import reconcile_break, reconcile_worktree

        g = two_member_group
        slug = "confinement-test"
        reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        data = json.loads(manifest_path.read_text())

        # Point repo_b's worktree_path at an unrelated dir outside the workspace.
        evil_path = str(tmp_path / "outside" / "evil_dir")
        for m in data["members"]:
            if m["name"] == "repo_b":
                m["worktree_path"] = evil_path
        manifest_path.write_text(json.dumps(data))

        with pytest.raises(Exception) as exc_info:
            reconcile_break(g["group"], slug, env=g["env"])

        err_msg = str(exc_info.value).lower()
        assert any(
            tok in err_msg for tok in ("confinement", "outside", "not relative", "workspace")
        ), f"confinement error message unclear: {exc_info.value}"

    def test_break_rejects_symlink_escaping_workspace_dir(self, two_member_group, tmp_path):
        """A worktree_path that is a symlink escaping the workspace dir → rejected.

        The target lexically sits inside the workspace dir but resolves outside
        it; resolve-before-check must catch this.
        """
        from camp.group.resolve import central_state_dir
        from camp.provision.reconcile import reconcile_break, reconcile_worktree

        g = two_member_group
        slug = "confinement-symlink"
        reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        workspace_dir = state_dir / "worktrees" / slug

        # An escape target outside the workspace dir.
        escape_target = tmp_path / "escape_target"
        escape_target.mkdir(parents=True, exist_ok=True)

        # A symlink that lives inside the workspace dir but points outside it.
        sneaky_link = workspace_dir / "sneaky"
        sneaky_link.symlink_to(escape_target, target_is_directory=True)

        manifest_path = workspace_dir / "manifest.json"
        data = json.loads(manifest_path.read_text())
        for m in data["members"]:
            if m["name"] == "repo_b":
                m["worktree_path"] = str(sneaky_link)
        manifest_path.write_text(json.dumps(data))

        with pytest.raises(Exception) as exc_info:
            reconcile_break(g["group"], slug, env=g["env"])

        err_msg = str(exc_info.value).lower()
        assert any(
            tok in err_msg for tok in ("confinement", "outside", "not relative", "workspace")
        ), f"symlink-escape error message unclear: {exc_info.value}"

        # The escape target must NOT have been removed.
        assert escape_target.is_dir(), "symlink-escaping break removed the escape target"

    def test_break_legacy_layout_path_legible_error(self, two_member_group, tmp_path):
        """An OLD-layout manifest path (under repo_root, outside the workspace dir)
        → legible legacy-layout error, NOT a half-applied break."""
        from camp.group.resolve import central_state_dir
        from camp.provision.reconcile import reconcile_break, reconcile_worktree

        g = two_member_group
        slug = "legacy-layout"
        reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        workspace_dir = state_dir / "worktrees" / slug
        manifest_path = workspace_dir / "manifest.json"
        data = json.loads(manifest_path.read_text())

        # Rewrite repo_b to the OLD per-repo layout path (outside the workspace dir).
        old_layout = g["repo_b"] / ".claude" / "worktrees" / slug
        old_layout.mkdir(parents=True, exist_ok=True)
        for m in data["members"]:
            if m["name"] == "repo_b":
                m["worktree_path"] = str(old_layout)
        manifest_path.write_text(json.dumps(data))

        with pytest.raises(Exception) as exc_info:
            reconcile_break(g["group"], slug, env=g["env"])

        err_msg = str(exc_info.value).lower()
        assert "legacy" in err_msg or "git worktree remove" in err_msg, (
            f"legacy-layout error must be legible, got: {exc_info.value}"
        )

        # No half-applied break: repo_a's (valid) worktree must still be present
        # because the pre-check aborts before any removal.
        wt_a = _member_wt("testgroup", slug, "repo_a", g["env"])
        assert wt_a.is_dir(), "legacy-layout abort must not remove the valid member worktree"


# ---------------------------------------------------------------------------
# Test 6: break atomicity symmetry
# ---------------------------------------------------------------------------


class TestBreakAtomicitySymmetry:
    def test_mid_break_failure_manifest_not_left_listing_removed_member(self, two_member_group):
        """A mid-break failure must not strand a manifest listing an already-removed member.

        reconcile_break returns ok_with_errors (no raise) on partial failure,
        but must update the manifest to remove entries for successfully-removed
        members so the manifest never lists a member whose worktree is gone.
        """
        from camp.provision.reconcile import reconcile_worktree, reconcile_break
        from camp.group.manifest import read_central_manifest
        from camp.group.resolve import central_state_dir

        g = two_member_group
        slug = "break-atomic"
        reconcile_worktree(g["group"], slug, env=g["env"])

        # Simulate mid-break failure: remove repo_a's worktree but then fail on repo_b
        call_count = [0]

        def patched_remove(member, wt_path, repo_root, workspace_dir, *, force):
            call_count[0] += 1
            if call_count[0] == 1:
                # First member (repo_a): remove for real
                subprocess.run(
                    ["git", "-C", str(repo_root), "worktree", "remove", str(wt_path)],
                    check=True,
                    capture_output=True,
                )
                return
            # Second member (repo_b): simulate failure
            raise RuntimeError("simulated removal failure on member 2")

        with patch("camp.provision.reconcile._remove_worktree_for_member", side_effect=patched_remove):
            result = reconcile_break(g["group"], slug, env=g["env"])

        # ok_with_errors: repo_a removed, repo_b failed
        assert result["status"] == "ok_with_errors"
        assert "repo_a" in result["removed"]
        assert result["errors"]  # repo_b error recorded

        # The manifest must NOT list repo_a (whose worktree is already removed).
        # It should either be gone or updated to contain only repo_b.
        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"

        if manifest_path.exists():
            data = read_central_manifest(manifest_path)
            # repo_a's worktree is gone — manifest must not list it
            removed_but_listed = [
                m["name"]
                for m in data["members"]
                if m["name"] == "repo_a" and not Path(m["worktree_path"]).exists()
            ]
            assert not removed_but_listed, (
                "manifest lists repo_a as a member but its worktree is already removed — "
                "break atomicity symmetry violated"
            )


# ---------------------------------------------------------------------------
# Test 7: concurrent-run guard
# ---------------------------------------------------------------------------


class TestConcurrentRunGuard:
    def test_concurrent_reconciles_do_not_double_add_worktree(self, two_member_group):
        """Two concurrent reconcile_worktree calls must not both git-worktree-add."""
        from camp.provision.reconcile import reconcile_worktree

        g = two_member_group
        slug = "concurrent-slug"

        errors = []

        def run_reconcile():
            try:
                reconcile_worktree(g["group"], slug, env=g["env"])
            except Exception as e:
                errors.append(e)

        # Race two threads
        t1 = threading.Thread(target=run_reconcile)
        t2 = threading.Thread(target=run_reconcile)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        # Neither thread should have a fatal git error about 'already exists'
        for e in errors:
            assert "already exists" not in str(e).lower(), (
                f"concurrent add produced 'already exists' error: {e}"
            )

        # The worktrees should exist and be valid
        wt_a = _member_wt("testgroup", slug, "repo_a", g["env"])
        wt_b = _member_wt("testgroup", slug, "repo_b", g["env"])
        assert wt_a.is_dir()
        assert wt_b.is_dir()


# ---------------------------------------------------------------------------
# Test 8: malformed/truncated manifest → legible error
# ---------------------------------------------------------------------------


class TestMalformedManifest:
    def test_truncated_manifest_gives_legible_error_from_read(self, two_member_group):
        """A truncated manifest file raises ManifestError, not a raw exception."""
        from camp.group.manifest import read_central_manifest, ManifestError
        from camp.group.resolve import central_state_dir
        from camp.provision.reconcile import reconcile_worktree

        g = two_member_group
        slug = "bad-manifest"
        reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        manifest_path.write_text("{truncated json")

        with pytest.raises(ManifestError) as exc_info:
            read_central_manifest(manifest_path)

        # Must be a named error, not a raw json decode traceback
        assert "manifest" in str(exc_info.value).lower() or str(manifest_path) in str(
            exc_info.value
        )

    def test_malformed_manifest_status_gives_legible_error(self, two_member_group):
        """cmd_status with a malformed manifest must not traceback."""
        from camp.provision.reconcile import reconcile_worktree
        from camp.group.resolve import central_state_dir
        from camp.group.manifest import ManifestError

        g = two_member_group
        slug = "bad-status"
        reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        manifest_path.write_text("{bad json!!!")

        # Status via the group-aware path must give ManifestError, not raw JSONDecodeError
        from camp.group.manifest import read_central_manifest

        with pytest.raises(ManifestError):
            read_central_manifest(manifest_path)


# ---------------------------------------------------------------------------
# Test 9: central manifest path via resolver env= injection
# ---------------------------------------------------------------------------


class TestCentralManifestPath:
    def test_manifest_path_uses_camp_state_dir_injection(self, tmp_path):
        """Central manifest path is central_state_dir(group)/worktrees/<slug>/manifest.json."""
        from camp.group.resolve import central_state_dir

        custom_state = tmp_path / "custom-state"
        custom_state.mkdir()
        env = {"CAMP_STATE_DIR": str(custom_state), "HOME": str(tmp_path)}

        state_dir = central_state_dir("mygroup", env=env)
        assert state_dir == custom_state / "mygroup"

    def test_manifest_written_to_injected_path(self, tmp_path):
        """reconcile_worktree writes manifest under the CAMP_STATE_DIR-injected path."""
        from camp.provision.reconcile import reconcile_worktree
        from camp.group.resolve import central_state_dir

        repo_a = tmp_path / "repo_a"
        _init_git_repo(repo_a)

        custom_state = tmp_path / "custom-state"
        custom_state.mkdir()
        env = {"CAMP_STATE_DIR": str(custom_state), "HOME": str(tmp_path)}

        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(repo_a), "tasks": []}],
        )

        slug = "path-test"
        reconcile_worktree(group, slug, env=env)

        state_dir = central_state_dir("mygroup", env=env)
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        assert manifest_path.is_file()


# ---------------------------------------------------------------------------
# Test 10: success summary
# ---------------------------------------------------------------------------


class TestSuccessSummary:
    def test_reconcile_worktree_returns_summary(self, two_member_group):
        """reconcile_worktree returns a result dict with summary info."""
        from camp.provision.reconcile import reconcile_worktree

        g = two_member_group
        result = reconcile_worktree(g["group"], "feat-summary", env=g["env"])

        # Result must include member count and manifest path
        assert result is not None
        assert result.get("member_count") == 2
        assert "manifest_path" in result
        assert "members" in result


# ---------------------------------------------------------------------------
# Test: cmd_status / cmd_ls — fleet view + scoped
# ---------------------------------------------------------------------------


class TestCmdStatusAndLs:
    def test_cmd_status_fleet_view_lists_all_worktrees(self, two_member_group):
        """cmd_status with slug=None from repo root → fleet view (all group worktrees)."""
        from camp.provision.reconcile import reconcile_worktree
        from camp.provision.lifecycle import cmd_status_group

        g = two_member_group
        reconcile_worktree(g["group"], "alpha", env=g["env"])
        reconcile_worktree(g["group"], "beta", env=g["env"])

        result = cmd_status_group(g["group"], slug=None, env=g["env"])
        slugs = [w["slug"] for w in result["worktrees"]]
        assert "alpha" in slugs
        assert "beta" in slugs

    def test_cmd_status_scoped_returns_single_worktree(self, two_member_group):
        """cmd_status with a specific slug returns only that worktree."""
        from camp.provision.reconcile import reconcile_worktree
        from camp.provision.lifecycle import cmd_status_group

        g = two_member_group
        reconcile_worktree(g["group"], "alpha", env=g["env"])
        reconcile_worktree(g["group"], "beta", env=g["env"])

        result = cmd_status_group(g["group"], slug="alpha", env=g["env"])
        assert len(result["worktrees"]) == 1
        assert result["worktrees"][0]["slug"] == "alpha"

    def test_cmd_ls_returns_all_worktrees_for_group(self, two_member_group):
        """cmd_ls returns all worktrees for the group."""
        from camp.provision.reconcile import reconcile_worktree
        from camp.provision.lifecycle import cmd_ls_group

        g = two_member_group
        reconcile_worktree(g["group"], "alpha", env=g["env"])
        reconcile_worktree(g["group"], "beta", env=g["env"])

        entries = cmd_ls_group(g["group"], env=g["env"])
        slugs = [e["slug"] for e in entries]
        assert "alpha" in slugs
        assert "beta" in slugs

    def test_cmd_ls_empty_when_no_worktrees(self, two_member_group):
        """cmd_ls returns empty list when no worktrees created yet."""
        from camp.provision.lifecycle import cmd_ls_group

        g = two_member_group
        entries = cmd_ls_group(g["group"], env=g["env"])
        assert entries == []


# ---------------------------------------------------------------------------
# Test: cmd_break — removes worktrees + manifest
# ---------------------------------------------------------------------------


class TestCmdBreak:
    def test_break_removes_both_member_worktrees(self, two_member_group):
        """break removes both member worktrees."""
        from camp.provision.reconcile import reconcile_worktree, reconcile_break

        g = two_member_group
        slug = "break-me"
        reconcile_worktree(g["group"], slug, env=g["env"])

        wt_a = _member_wt("testgroup", slug, "repo_a", g["env"])
        wt_b = _member_wt("testgroup", slug, "repo_b", g["env"])
        assert wt_a.is_dir()
        assert wt_b.is_dir()

        reconcile_break(g["group"], slug, env=g["env"])

        assert not wt_a.is_dir(), "repo_a worktree should be removed"
        assert not wt_b.is_dir(), "repo_b worktree should be removed"

    def test_break_removes_central_manifest(self, two_member_group):
        """break removes the central manifest."""
        from camp.provision.reconcile import reconcile_worktree, reconcile_break
        from camp.group.resolve import central_state_dir

        g = two_member_group
        slug = "break-manifest"
        reconcile_worktree(g["group"], slug, env=g["env"])

        state_dir = central_state_dir("testgroup", env=g["env"])
        manifest_path = state_dir / "worktrees" / slug / "manifest.json"
        assert manifest_path.is_file()

        reconcile_break(g["group"], slug, env=g["env"])

        assert not manifest_path.exists(), "manifest should be removed after break"

    def test_break_dirty_worktree_blocked_without_force(self, two_member_group):
        """break on a dirty worktree fails unless --force."""
        from camp.provision.reconcile import reconcile_worktree, reconcile_break

        g = two_member_group
        slug = "break-dirty"
        reconcile_worktree(g["group"], slug, env=g["env"])

        # Make repo_a worktree dirty
        wt_a = _member_wt("testgroup", slug, "repo_a", g["env"])
        (wt_a / "dirty_file.txt").write_text("uncommitted change")

        with pytest.raises(Exception) as exc_info:
            reconcile_break(g["group"], slug, env=g["env"], force=False)

        assert (
            "dirty" in str(exc_info.value).lower() or "uncommitted" in str(exc_info.value).lower()
        )

    def test_break_dirty_worktree_succeeds_with_force(self, two_member_group):
        """break --force succeeds even with dirty worktrees."""
        from camp.provision.reconcile import reconcile_worktree, reconcile_break

        g = two_member_group
        slug = "break-force"
        reconcile_worktree(g["group"], slug, env=g["env"])

        wt_a = _member_wt("testgroup", slug, "repo_a", g["env"])
        (wt_a / "dirty_file.txt").write_text("uncommitted change")

        # Should not raise
        reconcile_break(g["group"], slug, env=g["env"], force=True)

        assert not wt_a.is_dir()


# ---------------------------------------------------------------------------
# Test: cmd_sync across group members
# ---------------------------------------------------------------------------


class TestCmdSync:
    def test_cmd_sync_operates_on_all_group_members(self, two_member_group):
        """cmd_sync_group reports all group members (canonical repos)."""
        from camp.provision.lifecycle import cmd_sync_group

        g = two_member_group
        result = cmd_sync_group(g["group"], env=g["env"])

        # Both members reported
        assert "repo_a" in result["members"]
        assert "repo_b" in result["members"]


# ---------------------------------------------------------------------------
# Test: manifest.py read/write API
# ---------------------------------------------------------------------------


class TestManifestAPI:
    def test_write_and_read_roundtrip(self, tmp_path):
        """write_central_manifest + read_central_manifest round-trips correctly."""
        from camp.group.manifest import write_central_manifest, read_central_manifest

        manifest_path = tmp_path / "manifest.json"
        data = {
            "schema_version": 1,
            "group": "testgroup",
            "slug": "feat-x",
            "branch": "worktree-feat-x",
            "members": [
                {
                    "name": "repo_a",
                    "repo_root": "/tmp/a",
                    "worktree_path": "/tmp/a/.claude/worktrees/feat-x",
                },
                {
                    "name": "repo_b",
                    "repo_root": "/tmp/b",
                    "worktree_path": "/tmp/b/.claude/worktrees/feat-x",
                },
            ],
        }

        write_central_manifest(manifest_path, data)
        result = read_central_manifest(manifest_path)

        assert result["group"] == "testgroup"
        assert result["slug"] == "feat-x"
        assert len(result["members"]) == 2

    def test_write_is_atomic(self, tmp_path):
        """write_central_manifest uses atomic write (temp + os.replace)."""
        from camp.group.manifest import write_central_manifest, read_central_manifest

        manifest_path = tmp_path / "manifest.json"
        data = {"schema_version": 1, "group": "g", "slug": "s", "branch": "b", "members": []}

        # Write once
        write_central_manifest(manifest_path, data)

        # Verify it was actually written
        assert manifest_path.is_file()
        result = read_central_manifest(manifest_path)
        assert result is not None

    def test_remove_central_manifest(self, tmp_path):
        """remove_central_manifest removes the file."""
        from camp.group.manifest import write_central_manifest, remove_central_manifest

        manifest_path = tmp_path / "manifest.json"
        write_central_manifest(
            manifest_path,
            {"schema_version": 1, "group": "g", "slug": "s", "branch": "b", "members": []},
        )
        assert manifest_path.exists()

        remove_central_manifest(manifest_path)
        assert not manifest_path.exists()

    def test_remove_central_manifest_noop_if_absent(self, tmp_path):
        """remove_central_manifest is a no-op if the file doesn't exist."""
        from camp.group.manifest import remove_central_manifest

        manifest_path = tmp_path / "nonexistent.json"
        remove_central_manifest(manifest_path)  # should not raise

    def test_work_state_for_member_defaults_to_pending_when_key_absent(self):
        """A member entry lacking "work_state" (every manifest written before
        this key existed) reads as not-yet-work-ready rather than raising."""
        from camp.group.manifest import work_state_for_member

        assert work_state_for_member({"name": "repo"}) == "pending"

    def test_work_state_for_member_returns_stored_value(self):
        """A member entry carrying "work_state" reports it verbatim."""
        from camp.group.manifest import work_state_for_member

        assert work_state_for_member({"name": "repo", "work_state": "ready"}) == "ready"


# ---------------------------------------------------------------------------
# Test: provision_status_code reports two independent facts; status_header
# derives the workspace header from both without the exit code ever
# depending on work-readiness.
# ---------------------------------------------------------------------------


class TestStatusTwoFacts:
    def _seed(self, group, slug, env, members):
        """Seed a workspace then overwrite each named member's entry with the
        given fields (e.g. {"provision_state": "ready", "work_state": "pending"}).
        """
        from camp.provision.provision import seed_pending_workspace
        from camp.group.manifest import (
            manifest_path_for,
            read_central_manifest,
            write_central_manifest,
            reconcile_lock,
        )

        seed_pending_workspace(group, slug, env=env)
        mpath = manifest_path_for(group["group"]["name"], slug, env=env)
        with reconcile_lock(mpath.parent):
            data = read_central_manifest(mpath)
            for m in data["members"]:
                if m["name"] in members:
                    m.update(members[m["name"]])
            write_central_manifest(mpath, data)
        return mpath

    def test_report_carries_work_state_per_member(self, two_member_group):
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed(
            g["group"],
            "wf1",
            g["env"],
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "pending"},
            },
        )
        _code, report = provision_status_code(g["group"], "wf1", env=g["env"])
        by_name = {m["name"]: m["work_state"] for m in report["members"]}
        assert by_name == {"repo_a": "ready", "repo_b": "pending"}

    def test_work_code_rollup_ready_when_all_ready_or_not_applicable(self, two_member_group):
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed(
            g["group"],
            "wf2",
            g["env"],
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "not-applicable"},
            },
        )
        _code, report = provision_status_code(g["group"], "wf2", env=g["env"])
        assert report["work_code"] == 0

    def test_work_code_rollup_pending(self, two_member_group):
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed(
            g["group"],
            "wf3",
            g["env"],
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "pending"},
            },
        )
        _code, report = provision_status_code(g["group"], "wf3", env=g["env"])
        assert report["work_code"] == 2

    def test_work_code_rollup_failed(self, two_member_group):
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed(
            g["group"],
            "wf4",
            g["env"],
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "failed"},
            },
        )
        _code, report = provision_status_code(g["group"], "wf4", env=g["env"])
        assert report["work_code"] == 3

    def test_exit_code_derives_from_boot_readiness_alone(self, two_member_group):
        """A member whose work_state is failed must not push the process exit
        code to 3 while every member is boot-ready — the exit code carries
        boot-readiness only, never work-readiness."""
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed(
            g["group"],
            "wf5",
            g["env"],
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "failed"},
            },
        )
        code, report = provision_status_code(g["group"], "wf5", env=g["env"])
        assert code == 0
        assert report["code"] == 0
        assert report["work_code"] == 3

    def test_json_key_set_conformance(self, two_member_group):
        """The keys read by the concierge skill and five sibling specs — slug,
        code, members[].provision_state, members[].tasks, members[].reason —
        are still present with unchanged meaning. A future rename of any of
        these must fail HERE, not silently in a downstream consumer."""
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed(
            g["group"],
            "wf6",
            g["env"],
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {
                    "provision_state": "failed",
                    "work_state": "pending",
                    "reason": "boom",
                },
            },
        )
        _code, report = provision_status_code(g["group"], "wf6", env=g["env"])

        assert {"slug", "code", "members"} <= set(report.keys())
        for m in report["members"]:
            assert {"name", "provision_state", "tasks"} <= set(m.keys())
        by_name = {m["name"]: m for m in report["members"]}
        assert by_name["repo_b"]["reason"] == "boom"

    def test_status_header_all_ready(self, two_member_group):
        from camp.provision.lifecycle import provision_status_code, status_header

        g = two_member_group
        self._seed(
            g["group"],
            "wf7",
            g["env"],
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "not-applicable"},
            },
        )
        _code, report = provision_status_code(g["group"], "wf7", env=g["env"])
        assert status_header(report) == "ready"

    def test_status_header_mixed_boot_ready_work_pending(self, two_member_group):
        """The headline behavior change: a workspace whose members are all
        boot-ready but still installing dependencies gets a header that says
        so, distinct from both "ready" and the old hardcoded "provisioning"."""
        from camp.provision.lifecycle import provision_status_code, status_header

        g = two_member_group
        self._seed(
            g["group"],
            "wf8",
            g["env"],
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "pending"},
            },
        )
        _code, report = provision_status_code(g["group"], "wf8", env=g["env"])
        assert status_header(report) == "ready, work pending"

    def test_status_header_failed_takes_precedence(self, two_member_group):
        from camp.provision.lifecycle import provision_status_code, status_header

        g = two_member_group
        self._seed(
            g["group"],
            "wf9",
            g["env"],
            {
                "repo_a": {"provision_state": "failed", "work_state": "pending"},
                "repo_b": {"provision_state": "ready", "work_state": "ready"},
            },
        )
        _code, report = provision_status_code(g["group"], "wf9", env=g["env"])
        assert status_header(report) == "failed"



# ---------------------------------------------------------------------------
# camp setup: activate-phase retry that does not hold the reconcile lock.
#
# Test contract:
# - camp setup retries a member's FAILED activate-phase task, running its
#   cleanup first.
# - An activate-phase task recorded "ok" is not re-run.
# - Lock-scope: while camp setup runs a long activate-phase task, a concurrent
#   reconcile on the same slug is not blocked for the task's duration.
# - The manifest mutation itself still happens under .reconcile.lock.
# - A crash mid-task leaves no lock held and the member retryable.
# - camp setup --status stays strictly read-only.
# ---------------------------------------------------------------------------


def _activate_task(
    name: str, cmds: list[list[str]], *, required: bool = True, cleanup: list[str] | None = None
) -> dict[str, Any]:
    """Build a member activate-phase task in the config-resolved shape."""
    task: dict[str, Any] = {
        "name": name,
        "phase": "activate",
        "required": required,
        "timeout_seconds": None,
        "steps": [{"name": name, "cmd": cmd} for cmd in cmds],
    }
    if cleanup is not None:
        task["cleanup"] = cleanup
    return task


def _seed_ready_member_with_activate_task(
    tmp_path: Path,
    group_name: str,
    slug: str,
    member_name: str,
    *,
    task_state: str,
    env: dict[str, str],
) -> Path:
    """Seed a manifest with one member: provision_state ready, activated,
    and a single activate-phase task recorded at `task_state` (or absent
    entirely when task_state is None). Returns the member's worktree dir."""
    from camp.group.manifest import manifest_path_for, write_central_manifest

    mpath = manifest_path_for(group_name, slug, env=env)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    wt_path = mpath.parent / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    member_entry: dict[str, Any] = {
        "name": member_name,
        "repo_root": "/tmp/fake-repo",
        "worktree_path": str(wt_path),
        "provision_state": "ready",
        "activated": True,
    }
    if task_state is not None:
        member_entry["tasks"] = {"npm-ci": {"state": task_state}}

    write_central_manifest(
        mpath,
        {
            "schema_version": 1,
            "group": group_name,
            "slug": slug,
            "branch": f"worktree-{slug}",
            "members": [member_entry],
        },
    )
    return wt_path


class TestSetupActivatePhaseRetry:
    def test_retries_failed_activate_task_with_cleanup_first(self, tmp_path):
        """camp setup retries a FAILED activate-phase task, running cleanup
        first — reusing the same body camp activate's detached run executes."""
        from camp.provision.lifecycle import cmd_setup_group

        group_name = "actgroup"
        member_name = "repo_a"
        slug = "act-retry"
        env = _camp_state_env(tmp_path)

        _seed_ready_member_with_activate_task(
            tmp_path, group_name, slug, member_name, task_state="failed", env=env
        )

        task = _activate_task(
            "npm-ci", [["npm", "ci"]], cleanup=["rm", "-rf", "node_modules"]
        )
        group = _make_group_config(group_name, [{"name": member_name, "repo_root": "/tmp/fake-repo", "tasks": [task]}])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            cmd_setup_group(group, slug, env=env)

        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0][0][0] == ["rm", "-rf", "node_modules"]
        assert mock_run.call_args_list[1][0][0] == ["npm", "ci"]

        from camp.group.manifest import manifest_path_for, read_central_manifest

        mpath = manifest_path_for(group_name, slug, env=env)
        data = read_central_manifest(mpath)
        assert data["members"][0]["tasks"]["npm-ci"]["state"] == "ok"

    def test_does_not_rerun_activate_task_recorded_ok(self, tmp_path):
        """A task recorded 'ok' is not re-run by camp setup."""
        from camp.provision.lifecycle import cmd_setup_group

        group_name = "actgroup"
        member_name = "repo_a"
        slug = "act-noop"
        env = _camp_state_env(tmp_path)

        _seed_ready_member_with_activate_task(
            tmp_path, group_name, slug, member_name, task_state="ok", env=env
        )

        task = _activate_task("npm-ci", [["npm", "ci"]])
        group = _make_group_config(group_name, [{"name": member_name, "repo_root": "/tmp/fake-repo", "tasks": [task]}])

        with patch("subprocess.run") as mock_run:
            cmd_setup_group(group, slug, env=env)

        mock_run.assert_not_called()

    def test_activate_retry_does_not_hold_reconcile_lock_across_task_subprocess(self, tmp_path):
        """The point of the slice: while camp setup runs a long activate-phase
        task, a concurrent reconcile on the same slug must not stall for the
        task's duration."""
        import fcntl

        from camp.group.manifest import lock_path_for
        from camp.provision.lifecycle import cmd_setup_group

        group_name = "actgroup"
        member_name = "repo_a"
        slug = "act-lock-scope"
        env = _camp_state_env(tmp_path)

        _seed_ready_member_with_activate_task(
            tmp_path, group_name, slug, member_name, task_state="failed", env=env
        )

        task = _activate_task("npm-ci", [["npm", "ci"]])
        group = _make_group_config(group_name, [{"name": member_name, "repo_root": "/tmp/fake-repo", "tasks": [task]}])

        task_started = threading.Event()
        release_task = threading.Event()

        def slow_run(*args, **kwargs):
            task_started.set()
            # Deliberately far longer than any realistic lock-acquire delay:
            # proves the probe acquires without waiting this task out, rather
            # than merely acquiring faster than a tight number. `release_task`
            # lets the proof end this wait as soon as it's done, rather than
            # paying for the full margin in teardown every run.
            release_task.wait(timeout=10.0)
            return MagicMock(returncode=0, stdout="", stderr="")

        errors: list[Exception] = []

        def run_setup():
            try:
                with patch("subprocess.run", side_effect=slow_run):
                    cmd_setup_group(group, slug, env=env)
            except Exception as e:  # pragma: no cover - surfaced via assert below
                errors.append(e)

        t = threading.Thread(target=run_setup)
        t.start()
        try:
            assert task_started.wait(timeout=5.0), "activate task never started"

            from camp.group.resolve import central_state_dir

            ws_dir = central_state_dir(group_name, env=env) / "worktrees" / slug
            lock_path = lock_path_for(ws_dir)

            start = time.monotonic()
            probe_fd = open(str(lock_path), "w")
            fcntl.flock(probe_fd.fileno(), fcntl.LOCK_EX)
            elapsed = time.monotonic() - start
            fcntl.flock(probe_fd.fileno(), fcntl.LOCK_UN)
            probe_fd.close()

            assert elapsed < 3.0, (
                f"acquiring .reconcile.lock took {elapsed:.2f}s while the activate "
                "task was running — the lock must not be held across the task subprocess"
            )
        finally:
            # The proof is already complete by this point; don't pay for the
            # remainder of the wait margin in teardown.
            release_task.set()
            t.join(timeout=15.0)
        assert not errors, errors

    def test_activate_retry_manifest_write_happens_under_reconcile_lock(self, tmp_path):
        """Narrowing lock scope must not drop the lock from the write it
        protects: the manifest persist still happens while .reconcile.lock is
        held."""
        import fcntl

        import camp.group.manifest as manifest_mod
        from camp.provision.lifecycle import cmd_setup_group

        group_name = "actgroup"
        member_name = "repo_a"
        slug = "act-write-locked"
        env = _camp_state_env(tmp_path)

        _seed_ready_member_with_activate_task(
            tmp_path, group_name, slug, member_name, task_state="failed", env=env
        )

        task = _activate_task("npm-ci", [["npm", "ci"]])
        group = _make_group_config(group_name, [{"name": member_name, "repo_root": "/tmp/fake-repo", "tasks": [task]}])

        from camp.group.resolve import central_state_dir

        ws_dir = central_state_dir(group_name, env=env) / "worktrees" / slug
        lock_path = manifest_mod.lock_path_for(ws_dir)

        observed = {"locked_during_write": None}
        original_write = manifest_mod.write_central_manifest

        def spy_write(path, data):
            probe_fd = open(str(lock_path), "w")
            try:
                fcntl.flock(probe_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                observed["locked_during_write"] = False
                fcntl.flock(probe_fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                observed["locked_during_write"] = True
            finally:
                probe_fd.close()
            return original_write(path, data)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            with patch.object(manifest_mod, "write_central_manifest", side_effect=spy_write):
                cmd_setup_group(group, slug, env=env)

        assert observed["locked_during_write"] is True, (
            "the manifest write must happen while .reconcile.lock is held"
        )

    def test_activate_retry_crash_mid_task_leaves_no_lock_held_and_member_retryable(
        self, tmp_path
    ):
        """Killing camp setup while it's mid-way through an activate-phase
        task's subprocess must leave BOTH .reconcile.lock and the member's
        activate guard free, and the member's failed state intact for a
        later retry."""
        import fcntl
        import json as _json

        from camp.group.manifest import lock_path_for, manifest_path_for, read_central_manifest
        from camp.provision.activation import member_guard_lock_path
        from camp.provision.lifecycle import cmd_setup_group

        group_name = "actgroup"
        member_name = "repo_a"
        slug = "act-crash"
        env = _camp_state_env(tmp_path)

        _seed_ready_member_with_activate_task(
            tmp_path, group_name, slug, member_name, task_state="failed", env=env
        )

        task = {
            "name": "npm-ci",
            "phase": "activate",
            "required": True,
            "timeout_seconds": None,
            "steps": [{"cmd": [sys.executable, "-c", "import time; time.sleep(5)"]}],
        }
        group = {
            "group": {"name": group_name},
            "members": [{"name": member_name, "repo_root": "/tmp/fake-repo", "tasks": [task]}],
            "branch_pattern": "worktree-{slug}",
        }

        script = (
            "import sys, json\n"
            f"sys.path.insert(0, {str(_PLUGIN_DIR)!r})\n"
            "from camp.provision.lifecycle import cmd_setup_group\n"
            f"group = json.loads({_json.dumps(group)!r})\n"
            f"env = json.loads({_json.dumps(env)!r})\n"
            f"cmd_setup_group(group, {slug!r}, env=env)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", script])
        try:
            time.sleep(1.0)
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

        from camp.group.resolve import central_state_dir

        ws_dir = central_state_dir(group_name, env=env) / "worktrees" / slug

        reconcile_lock_path = lock_path_for(ws_dir)
        probe_fd = open(str(reconcile_lock_path), "w")
        fcntl.flock(probe_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe_fd.fileno(), fcntl.LOCK_UN)
        probe_fd.close()

        guard_path = member_guard_lock_path(ws_dir, member_name)
        guard_fd = open(str(guard_path), "w")
        fcntl.flock(guard_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(guard_fd.fileno(), fcntl.LOCK_UN)
        guard_fd.close()

        mpath = manifest_path_for(group_name, slug, env=env)
        data = read_central_manifest(mpath)
        assert data["members"][0]["tasks"]["npm-ci"]["state"] == "failed", (
            "a crashed retry must not corrupt the persisted task state"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            cmd_setup_group(group, slug, env=env)

        mock_run.assert_called_once()
        data = read_central_manifest(mpath)
        assert data["members"][0]["tasks"]["npm-ci"]["state"] == "ok"

    def test_setup_status_never_calls_cmd_setup_group(self, tmp_path, monkeypatch):
        """camp setup --status stays strictly read-only: no reconcile, no
        worktree add, no manifest write."""
        from camp.cli.lifecycle import _cmd_setup_group_cli
        import camp.provision.lifecycle as lifecycle_mod

        def boom(*args, **kwargs):
            raise AssertionError("cmd_setup_group must not be called for --status")

        monkeypatch.setattr(lifecycle_mod, "cmd_setup_group", boom)

        group = _make_group_config("actgroup", [{"name": "repo_a", "repo_root": "/tmp/fake-repo", "tasks": []}])
        env = _camp_state_env(tmp_path)

        _cmd_setup_group_cli(
            ["--status", "--name", "nonexistent-slug"], group, env, dry_run=False
        )
