"""Tests for detect_repos.py — de-zenithed camp-manifest-driven repo detector.

Contract (B-1, R-7, S-4):
  - Reads active repos from camp manifest members[], NOT .workspace-manifest.json.
  - Returns {repo, path, branch, ahead, dirty} per member with work.
  - manifest path is an explicit CLI arg; parsed via stdlib json (not camp module).
  - A missing worktree_path → no entry in output, exit 0 (graceful degrade, R-7).
  - No KNOWN_SIBLINGS, no zenith-exclusion, no hardcoded repo names.
  - All git calls go through the injectable runner stub.

Fixture pattern: write the camp manifest via the REAL write_central_manifest
under CAMP_STATE_DIR+HOME env override (same as assumption-prover test).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import detect_repos as dr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    readme = path / "README.md"
    readme.write_text("init\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
        check=True, capture_output=True,
    )


def _make_manifest(tmp_path: Path, members: list[dict]) -> Path:
    """Write a synthetic camp schema-v1 manifest directly (stdlib only, B-1).

    Writes to tmp_path/camp-state/test-group/worktrees/my-feat/manifest.json —
    the same layout camp.manifest.py uses — but via stdlib json directly to
    avoid pulling in trailhead.paths through manifest_path_for/central_state_dir.
    """
    path = (
        tmp_path
        / "camp-state"
        / "test-group"
        / "worktrees"
        / "my-feat"
        / "manifest.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "group": "test-group",
        "slug": "my-feat",
        "branch": "worktree-my-feat",
        "members": members,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _stub_for_member(name: str, branch: str, ahead: int = 1, dirty: int = 0):
    """Build a runner stub that answers git queries for a single member."""
    dirty_lines = "\n".join(" M file.py" for _ in range(dirty)) if dirty else ""

    def stub(cmd: list[str], **kwargs):
        cmd_str = " ".join(cmd)
        if "rev-parse" in cmd_str and "abbrev-ref" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, branch + "\n", "")
        if "rev-list" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, str(ahead) + "\n", "")
        if "status" in cmd_str and "--porcelain" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, dirty_lines, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return stub


# ---------------------------------------------------------------------------
# Core test: 2-member manifest → both members reported
# ---------------------------------------------------------------------------


class TestDetectReposTwoMembers:
    @pytest.fixture()
    def manifest_path(self, tmp_path: Path) -> Path:
        wt_a = tmp_path / "worktrees" / "alpha"
        wt_a.mkdir(parents=True)
        (wt_a / ".git").mkdir()
        wt_b = tmp_path / "worktrees" / "beta"
        wt_b.mkdir(parents=True)
        (wt_b / ".git").mkdir()
        return _make_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path / "alpha"), "worktree_path": str(wt_a)},
            {"name": "beta", "repo_root": str(tmp_path / "beta"), "worktree_path": str(wt_b)},
        ])

    def test_both_members_reported(self, manifest_path: Path) -> None:
        calls: list[tuple[str, list[str]]] = []

        def stub(cmd: list[str], **kwargs):
            calls.append((kwargs.get("cwd", ""), cmd))
            cmd_str = " ".join(cmd)
            if "rev-parse" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "worktree-my-feat\n", "")
            if "rev-list" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "1\n", "")
            if "status" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, " M file.py\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = dr.detect_repos(str(manifest_path), runner=stub)
        assert len(result) == 2
        names = {r["repo"] for r in result}
        assert names == {"alpha", "beta"}

    def test_member_shape_has_required_fields(self, manifest_path: Path) -> None:
        def stub(cmd: list[str], **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "worktree-my-feat\n", "")
            if "rev-list" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "2\n", "")
            if "status" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, " M a\n M b\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = dr.detect_repos(str(manifest_path), runner=stub)
        for entry in result:
            assert "repo" in entry
            assert "path" in entry
            assert "branch" in entry
            assert "ahead" in entry
            assert "dirty" in entry

    def test_path_comes_from_worktree_path(self, manifest_path: Path, tmp_path: Path) -> None:
        def stub(cmd: list[str], **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "worktree-my-feat\n", "")
            if "rev-list" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "1\n", "")
            if "status" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, " M x\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = dr.detect_repos(str(manifest_path), runner=stub)
        paths = {r["path"] for r in result}
        expected_alpha = str(tmp_path / "worktrees" / "alpha")
        expected_beta = str(tmp_path / "worktrees" / "beta")
        assert expected_alpha in paths
        assert expected_beta in paths

    def test_workspace_manifest_json_never_touched(
        self, manifest_path: Path, tmp_path: Path
    ) -> None:
        """The zenith .workspace-manifest.json path must never be read."""
        zenith_manifest = tmp_path / ".workspace-manifest.json"
        zenith_manifest.write_text('{"repos": []}')

        def stub(cmd: list[str], **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "worktree-my-feat\n", "")
            if "rev-list" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "1\n", "")
            if "status" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, " M x\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        dr.detect_repos(str(manifest_path), runner=stub)
        # The zenith manifest was never modified (no additional writes)
        content = zenith_manifest.read_text()
        assert content == '{"repos": []}'


# ---------------------------------------------------------------------------
# Members on main / no work → excluded from output
# ---------------------------------------------------------------------------


class TestDetectReposFiltering:
    @pytest.fixture()
    def manifest_path(self, tmp_path: Path) -> Path:
        wt = tmp_path / "worktrees" / "alpha"
        wt.mkdir(parents=True)
        (wt / ".git").mkdir()
        return _make_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path / "alpha"), "worktree_path": str(wt)},
        ])

    def test_member_on_main_excluded(self, manifest_path: Path) -> None:
        def stub(cmd: list[str], **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "main\n", "")
            return subprocess.CompletedProcess(cmd, 0, "0\n", "")

        result = dr.detect_repos(str(manifest_path), runner=stub)
        assert result == []

    def test_member_on_feature_branch_no_work_excluded(self, manifest_path: Path) -> None:
        def stub(cmd: list[str], **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "worktree-feat\n", "")
            if "rev-list" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "0\n", "")
            if "status" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = dr.detect_repos(str(manifest_path), runner=stub)
        assert result == []


# ---------------------------------------------------------------------------
# R-7: missing worktree_path → graceful degrade (no entry, exit 0)
# ---------------------------------------------------------------------------


class TestDetectReposMissingWorktreePath:
    def test_missing_worktree_excluded_not_error(self, tmp_path: Path) -> None:
        """A member whose worktree_path does not exist → excluded, exit 0."""
        wt_exists = tmp_path / "worktrees" / "alpha"
        wt_exists.mkdir(parents=True)
        (wt_exists / ".git").mkdir()

        manifest_path = _make_manifest(tmp_path, [
            {
                "name": "alpha",
                "repo_root": str(tmp_path / "alpha"),
                "worktree_path": str(wt_exists),
            },
            {
                "name": "beta",
                "repo_root": str(tmp_path / "beta"),
                "worktree_path": str(tmp_path / "DOES_NOT_EXIST" / "beta"),
            },
        ])

        def stub(cmd: list[str], **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "worktree-feat\n", "")
            if "rev-list" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "1\n", "")
            if "status" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, " M x\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = dr.detect_repos(str(manifest_path), runner=stub)
        # Only alpha reported; beta (missing wt) silently absent
        assert len(result) == 1
        assert result[0]["repo"] == "alpha"

    def test_all_members_missing_returns_empty_list(self, tmp_path: Path) -> None:
        manifest_path = _make_manifest(tmp_path, [
            {
                "name": "alpha",
                "repo_root": str(tmp_path / "alpha"),
                "worktree_path": str(tmp_path / "NO_SUCH" / "alpha"),
            },
        ])
        result = dr.detect_repos(str(manifest_path), runner=lambda cmd, **kw: None)
        assert result == []


# ---------------------------------------------------------------------------
# Malformed/missing manifest → named error
# ---------------------------------------------------------------------------


class TestDetectReposManifestErrors:
    def test_missing_manifest_raises_named_error(self, tmp_path: Path) -> None:
        absent = str(tmp_path / "no-such" / "manifest.json")
        with pytest.raises(dr.ManifestReadError) as exc_info:
            dr.detect_repos(absent, runner=lambda cmd, **kw: None)
        assert absent in str(exc_info.value)

    def test_malformed_manifest_raises_named_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json !!!")
        with pytest.raises(dr.ManifestReadError) as exc_info:
            dr.detect_repos(str(bad), runner=lambda cmd, **kw: None)
        assert str(bad) in str(exc_info.value)


# ---------------------------------------------------------------------------
# No KNOWN_SIBLINGS / no zenith-exclusion
# ---------------------------------------------------------------------------


class TestDetectReposNoHardcodes:
    def test_no_zenith_exclusion(self, tmp_path: Path) -> None:
        """A member named 'zenith' must appear in output (no exclusion)."""
        wt = tmp_path / "worktrees" / "zenith"
        wt.mkdir(parents=True)
        (wt / ".git").mkdir()
        manifest_path = _make_manifest(tmp_path, [
            {"name": "zenith", "repo_root": str(tmp_path / "zenith"), "worktree_path": str(wt)},
        ])

        def stub(cmd: list[str], **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "feature-branch\n", "")
            if "rev-list" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "1\n", "")
            if "status" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, " M x\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = dr.detect_repos(str(manifest_path), runner=stub)
        assert len(result) == 1
        assert result[0]["repo"] == "zenith"

    def test_arbitrary_member_name_accepted(self, tmp_path: Path) -> None:
        """Any member name works — not restricted to known sibling names."""
        wt = tmp_path / "worktrees" / "my-custom-service"
        wt.mkdir(parents=True)
        (wt / ".git").mkdir()
        manifest_path = _make_manifest(tmp_path, [
            {
                "name": "my-custom-service",
                "repo_root": str(tmp_path / "svc"),
                "worktree_path": str(wt),
            },
        ])

        def stub(cmd: list[str], **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "feature\n", "")
            if "rev-list" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "1\n", "")
            if "status" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, " M x\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        result = dr.detect_repos(str(manifest_path), runner=stub)
        assert result[0]["repo"] == "my-custom-service"


# ---------------------------------------------------------------------------
# Shared manifest_read module: single ManifestReadError (Item 7)
# ---------------------------------------------------------------------------


class TestSharedManifestRead:
    def test_detect_repos_uses_shared_manifest_read_error(self) -> None:
        """detect_repos.ManifestReadError is the same class as manifest_read.ManifestReadError."""
        import manifest_read as mr
        assert dr.ManifestReadError is mr.ManifestReadError, (
            "detect_repos must re-export manifest_read.ManifestReadError, "
            "not define its own"
        )

    def test_manifest_read_module_exists(self) -> None:
        """manifest_read module is importable from SCRIPTS_DIR."""
        import manifest_read as mr
        assert hasattr(mr, "ManifestReadError")
        assert hasattr(mr, "load_manifest")
