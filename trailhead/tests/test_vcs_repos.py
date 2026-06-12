"""Tests for GitHubProvider.repos.detect() + pr.open() (sidecar).

repos.detect() ports detect_repos.py: consumes camp's manifest.json, returns
active repos via the injected runner. camp stays the membership source of truth.

pr.open() ports release_prs_sidecar.py: the prs.json sidecar read/write that
records opened/stacked PRs alongside the camp manifest.

All git/gh calls go through an injected stub runner — zero network.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from trailhead.vcs import get_provider
from trailhead.vcs.github import ManifestReadError, SidecarError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(tmp_path: Path, members: list[dict]) -> Path:
    path = (
        tmp_path / "camp-state" / "test-group" / "worktrees" / "my-feat" / "manifest.json"
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


def _active_member_stub(branch="worktree-my-feat", ahead="1", dirty=" M file.py\n"):
    import subprocess

    def stub(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        if "rev-parse" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, branch + "\n", "")
        if "rev-list" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, ahead + "\n", "")
        if "status" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, dirty, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return stub


# ---------------------------------------------------------------------------
# repos.detect()
# ---------------------------------------------------------------------------


class TestReposDetect:
    @pytest.fixture()
    def manifest_path(self, tmp_path: Path) -> Path:
        for name in ("alpha", "beta"):
            wt = tmp_path / "worktrees" / name
            wt.mkdir(parents=True)
            (wt / ".git").mkdir()
        return _make_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path / "alpha"),
             "worktree_path": str(tmp_path / "worktrees" / "alpha")},
            {"name": "beta", "repo_root": str(tmp_path / "beta"),
             "worktree_path": str(tmp_path / "worktrees" / "beta")},
        ])

    def test_both_members_reported(self, manifest_path: Path) -> None:
        provider = get_provider("github", runner=_active_member_stub())
        result = provider.repos.detect(str(manifest_path))
        assert len(result) == 2
        assert {r["repo"] for r in result} == {"alpha", "beta"}

    def test_member_shape_has_required_fields(self, manifest_path: Path) -> None:
        provider = get_provider("github", runner=_active_member_stub(ahead="2"))
        result = provider.repos.detect(str(manifest_path))
        for entry in result:
            for field in ("repo", "path", "branch", "ahead", "dirty"):
                assert field in entry

    def test_path_comes_from_worktree_path(self, manifest_path: Path, tmp_path: Path) -> None:
        provider = get_provider("github", runner=_active_member_stub())
        result = provider.repos.detect(str(manifest_path))
        paths = {r["path"] for r in result}
        assert str(tmp_path / "worktrees" / "alpha") in paths
        assert str(tmp_path / "worktrees" / "beta") in paths

    def test_member_on_main_excluded(self, tmp_path: Path) -> None:
        wt = tmp_path / "worktrees" / "alpha"
        wt.mkdir(parents=True)
        (wt / ".git").mkdir()
        manifest = _make_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path / "alpha"), "worktree_path": str(wt)},
        ])
        import subprocess

        def stub(cmd, **kwargs):
            if "rev-parse" in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, 0, "main\n", "")
            return subprocess.CompletedProcess(cmd, 0, "0\n", "")

        provider = get_provider("github", runner=stub)
        assert provider.repos.detect(str(manifest)) == []

    def test_missing_worktree_excluded_not_error(self, tmp_path: Path) -> None:
        wt = tmp_path / "worktrees" / "alpha"
        wt.mkdir(parents=True)
        (wt / ".git").mkdir()
        manifest = _make_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path / "alpha"), "worktree_path": str(wt)},
            {"name": "beta", "repo_root": str(tmp_path / "beta"),
             "worktree_path": str(tmp_path / "DOES_NOT_EXIST" / "beta")},
        ])
        provider = get_provider("github", runner=_active_member_stub())
        result = provider.repos.detect(str(manifest))
        assert len(result) == 1
        assert result[0]["repo"] == "alpha"

    def test_no_zenith_exclusion(self, tmp_path: Path) -> None:
        wt = tmp_path / "worktrees" / "zenith"
        wt.mkdir(parents=True)
        (wt / ".git").mkdir()
        manifest = _make_manifest(tmp_path, [
            {"name": "zenith", "repo_root": str(tmp_path / "zenith"), "worktree_path": str(wt)},
        ])
        provider = get_provider("github", runner=_active_member_stub(branch="feature-branch"))
        result = provider.repos.detect(str(manifest))
        assert [r["repo"] for r in result] == ["zenith"]

    def test_missing_manifest_raises_named_error(self, tmp_path: Path) -> None:
        absent = str(tmp_path / "no-such" / "manifest.json")
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        with pytest.raises(ManifestReadError) as exc_info:
            provider.repos.detect(absent)
        assert absent in str(exc_info.value)

    def test_malformed_manifest_raises_named_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json !!!")
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        with pytest.raises(ManifestReadError) as exc_info:
            provider.repos.detect(str(bad))
        assert str(bad) in str(exc_info.value)

    def test_empty_members_returns_empty_list(self, tmp_path: Path) -> None:
        """M-3: a valid manifest with members absent or empty returns [] — not an error."""
        manifest_absent = tmp_path / "absent_members.json"
        manifest_absent.write_text(
            json.dumps({"schema_version": 1, "group": "grp", "slug": "feat"}),
            encoding="utf-8",
        )
        manifest_empty = tmp_path / "empty_members.json"
        manifest_empty.write_text(
            json.dumps({"schema_version": 1, "group": "grp", "slug": "feat", "members": []}),
            encoding="utf-8",
        )
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        assert provider.repos.detect(str(manifest_absent)) == []
        assert provider.repos.detect(str(manifest_empty)) == []

    def test_detect_routes_through_injected_runner(self, manifest_path: Path) -> None:
        """Every git call is captured by the stub — none uses a shell string."""
        import subprocess
        calls: list[list[str]] = []

        def stub(cmd, **kwargs):
            calls.append(cmd)
            assert isinstance(cmd, list), "cmd must be list-form (shell=False)"
            cmd_str = " ".join(cmd)
            if "rev-parse" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "worktree-my-feat\n", "")
            if "rev-list" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "1\n", "")
            if "status" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, " M x\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        provider = get_provider("github", runner=stub)
        provider.repos.detect(str(manifest_path))
        assert calls, "no git call routed through the runner"
        assert all(c[0] == "git" for c in calls)


# ---------------------------------------------------------------------------
# pr.open() — sidecar round-trip (ports release_prs_sidecar.py)
# ---------------------------------------------------------------------------


class TestPrOpenSidecar:
    def test_write_then_read_roundtrips_prs(self, tmp_path: Path) -> None:
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        path = tmp_path / "prs.json"
        prs = [
            {"repo": "alpha", "pr_number": "42", "url": "https://gh.com/42", "branch": "feat"},
            {"repo": "beta", "pr_number": "7", "url": "https://gh.com/7", "branch": "feat"},
        ]
        provider.pr.open(path, prs)
        result = provider.pr.status_sidecar(path)
        assert result["prs"] == prs

    def test_schema_version_and_external_tracker(self, tmp_path: Path) -> None:
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        path = tmp_path / "prs.json"
        provider.pr.open(path, [])
        result = provider.pr.status_sidecar(path)
        assert result["schema_version"] == 1
        assert result["external_tracker"] is None

    def test_file_mode_is_0600(self, tmp_path: Path) -> None:
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        path = tmp_path / "prs.json"
        provider.pr.open(path, [])
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_overwrite_is_atomic(self, tmp_path: Path) -> None:
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        path = tmp_path / "prs.json"
        provider.pr.open(path, [{"repo": "a", "pr_number": "1", "url": "u", "branch": "b"}])
        provider.pr.open(path, [{"repo": "x", "pr_number": "99", "url": "v", "branch": "c"}])
        result = provider.pr.status_sidecar(path)
        assert len(result["prs"]) == 1
        assert result["prs"][0]["repo"] == "x"

    def test_missing_sidecar_raises_named_error(self, tmp_path: Path) -> None:
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        absent = tmp_path / "nonexistent.json"
        with pytest.raises(SidecarError) as exc_info:
            provider.pr.status_sidecar(absent)
        assert str(absent) in str(exc_info.value)

    def test_malformed_sidecar_raises_named_error(self, tmp_path: Path) -> None:
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json !!!")
        with pytest.raises(SidecarError) as exc_info:
            provider.pr.status_sidecar(bad)
        assert str(bad) in str(exc_info.value)
