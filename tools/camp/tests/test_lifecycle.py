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
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
