"""Tests for merge_prs.py — de-zenithed camp-group PR merger.

Contract (D-2/R-2/R-6/A-1/S-4):
  - D-2 order: group-TOML merge_order if present, else manifest member order.
  - R-6 safety gate: >1 PR + no merge_order → refuse with named error (A-1 message).
  - R-6: merge_order naming a non-existent manifest member → named error.
  - R-2: PR1 merges, PR2 fails → merged=[pr1], failed={pr2:…}, skipped, nonzero exit.
  - R-2 retry-safe: an already-MERGED PR is skipped, not re-merged/failed.
  - No hardcoded platform/mobile/infra order.
  - All gh/git calls go through the injectable runner stub.
  - manifest path + group TOML path as explicit CLI args (B-1).
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

import merge_prs as mp  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_manifest(tmp_path: Path, members: list[dict]) -> Path:
    """Write a minimal camp manifest with the given members."""
    path = tmp_path / "camp-state" / "grp" / "worktrees" / "feat" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "group": "grp",
        "slug": "feat",
        "branch": "worktree-feat",
        "members": members,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_toml(tmp_path: Path, content: str) -> Path:
    """Write a group TOML file under tmp_path."""
    p = tmp_path / "group.toml"
    p.write_text(content, encoding="utf-8")
    return p


def _make_pr_stub(
    pr_statuses: dict[str, str],
    fail_on: set[str] | None = None,
) -> callable:
    """Build a runner stub for gh/git PR operations.

    pr_statuses maps pr_number → 'MERGEABLE_CLEAN' | 'MERGED' | 'DRAFT' | 'BLOCKED'.
    fail_on: set of pr_numbers whose merge command returns nonzero.
    """
    fail_on = fail_on or set()

    def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        cmd_str = " ".join(cmd)

        # git config user.email
        if "git" in cmd_str and "config" in cmd_str and "user.email" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")

        # gh pr view <pr_number> --json ...
        if "gh" in cmd_str and "pr" in cmd_str and "view" in cmd_str and "--json" in cmd_str:
            pr_number = None
            for tok in cmd:
                try:
                    pr_number = int(tok)
                    pr_number = str(tok)
                    break
                except ValueError:
                    continue
            if pr_number is None:
                # Try to find it another way
                for i, tok in enumerate(cmd):
                    if tok not in ("gh", "pr", "view", "--json", "--repo-override") and not tok.startswith("-"):
                        if "," not in tok:
                            pr_number = tok
                            break

            status = pr_statuses.get(str(pr_number), "MERGEABLE_CLEAN")
            if status == "MERGED":
                payload = {"state": "MERGED", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False, "headRefName": "feat"}
            elif status == "DRAFT":
                payload = {"state": "OPEN", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": True, "headRefName": "feat"}
            elif status == "BLOCKED":
                payload = {"state": "OPEN", "mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED", "isDraft": False, "headRefName": "feat"}
            else:
                payload = {"state": "OPEN", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False, "headRefName": "feat"}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

        # gh pr merge <pr_number>
        if "gh" in cmd_str and "pr" in cmd_str and "merge" in cmd_str:
            pr_number = None
            for tok in cmd:
                if tok not in ("gh", "pr", "merge", "--merge", "--author-email", "test@example.com") and not tok.startswith("-"):
                    pr_number = tok
                    break
            if pr_number in fail_on:
                return subprocess.CompletedProcess(cmd, 1, "", "merge failed")
            return subprocess.CompletedProcess(cmd, 0, "merged\n", "")

        # git push --delete (branch cleanup)
        if "git" in cmd_str and "push" in cmd_str and "delete" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "", "")

        return subprocess.CompletedProcess(cmd, 0, "", "")

    return stub


# ---------------------------------------------------------------------------
# D-2 merge order: group-TOML merge_order honored
# ---------------------------------------------------------------------------


class TestMergeOrder:
    def test_toml_merge_order_respected(self, tmp_path: Path) -> None:
        """When merge_order is declared, PRs merge in that order."""
        wt_a = tmp_path / "wt" / "alpha"
        wt_b = tmp_path / "wt" / "beta"
        wt_a.mkdir(parents=True)
        wt_b.mkdir(parents=True)

        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
            {"name": "beta", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
        ])
        toml = _write_toml(tmp_path, '[release]\nmerge_order = ["beta", "alpha"]\n')

        merge_calls: list[str] = []

        def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
            if "merge" in cmd and "--merge" in cmd:
                for tok in cmd:
                    if tok not in ("gh", "pr", "merge", "--merge", "--author-email", "test@example.com") and not tok.startswith("-"):
                        merge_calls.append(tok)
                        break
                return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
            if "view" in cmd and "--json" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({
                    "state": "OPEN", "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN", "isDraft": False, "headRefName": "feat",
                }), "")
            if "config" in cmd and "user.email" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        pr_pairs = [
            mp.PRPair(repo_path=str(wt_a), pr_number="10", member_name="alpha"),
            mp.PRPair(repo_path=str(wt_b), pr_number="20", member_name="beta"),
        ]
        result = mp.merge_prs(
            pr_pairs=pr_pairs,
            manifest_path=str(manifest),
            toml_path=str(toml),
            runner=stub,
        )
        # beta (20) should merge before alpha (10)
        assert merge_calls.index("20") < merge_calls.index("10"), (
            f"expected beta(20) before alpha(10), got order: {merge_calls}"
        )

    def test_single_pr_no_toml_merge_order_proceeds(self, tmp_path: Path) -> None:
        """A single PR group with no merge_order declared → merges fine."""
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
        ])
        toml = _write_toml(tmp_path, "[group]\nname = 'grp'\n")  # no [release] block

        stub = _make_pr_stub({"42": "MERGEABLE_CLEAN"})
        pr_pairs = [mp.PRPair(repo_path=str(wt), pr_number="42", member_name="alpha")]
        result = mp.merge_prs(
            pr_pairs=pr_pairs,
            manifest_path=str(manifest),
            toml_path=str(toml),
            runner=stub,
        )
        assert "42" in " ".join(result["merged"])
        assert result["failed"] == {}

    def test_no_hardcoded_platform_mobile_infra(self) -> None:
        """grep: no hardcoded platform/mobile-app/platform-infra in source."""
        src = SCRIPTS_DIR / "merge_prs.py"
        text = src.read_text()
        assert "platform-infra" not in text
        assert "mobile-app" not in text
        # 'platform' might appear in comments but should not be a list element
        import re
        matches = re.findall(r'[\[\(]["\']platform["\']', text)
        assert not matches, f"hardcoded 'platform' in list context found: {matches}"


# ---------------------------------------------------------------------------
# R-6 safety gate: >1 PR + no merge_order → refuse
# ---------------------------------------------------------------------------


class TestMergeOrderSafetyGate:
    def test_multiple_prs_no_merge_order_refuses(self, tmp_path: Path) -> None:
        wt_a = tmp_path / "wt" / "alpha"
        wt_b = tmp_path / "wt" / "beta"
        wt_a.mkdir(parents=True)
        wt_b.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
            {"name": "beta", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
        ])
        toml = _write_toml(tmp_path, "[group]\nname = 'grp'\n")  # no [release] block

        pr_pairs = [
            mp.PRPair(repo_path=str(wt_a), pr_number="1", member_name="alpha"),
            mp.PRPair(repo_path=str(wt_b), pr_number="2", member_name="beta"),
        ]
        with pytest.raises(mp.MergeOrderRequiredError) as exc_info:
            mp.merge_prs(
                pr_pairs=pr_pairs,
                manifest_path=str(manifest),
                toml_path=str(toml),
                runner=lambda cmd, **kw: None,
            )
        # A-1: the error message names the fix
        msg = str(exc_info.value)
        assert "merge_order" in msg
        assert "[release]" in msg

    def test_merge_order_names_nonexistent_member_raises(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
        ])
        toml = _write_toml(
            tmp_path,
            '[release]\nmerge_order = ["alpha", "nonexistent"]\n',
        )
        pr_pairs = [mp.PRPair(repo_path=str(wt), pr_number="1", member_name="alpha")]
        with pytest.raises(mp.MergeConfigError) as exc_info:
            mp.merge_prs(
                pr_pairs=pr_pairs,
                manifest_path=str(manifest),
                toml_path=str(toml),
                runner=lambda cmd, **kw: None,
            )
        assert "nonexistent" in str(exc_info.value)


# ---------------------------------------------------------------------------
# R-2: partial-merge state + retry safety
# ---------------------------------------------------------------------------


class TestPartialMerge:
    def test_pr1_merges_pr2_fails_partial_result(self, tmp_path: Path) -> None:
        wt_a = tmp_path / "wt" / "alpha"
        wt_b = tmp_path / "wt" / "beta"
        wt_a.mkdir(parents=True)
        wt_b.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
            {"name": "beta", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
        ])
        toml = _write_toml(
            tmp_path,
            '[release]\nmerge_order = ["alpha", "beta"]\n',
        )

        stub = _make_pr_stub({"10": "MERGEABLE_CLEAN", "20": "MERGEABLE_CLEAN"}, fail_on={"20"})
        pr_pairs = [
            mp.PRPair(repo_path=str(wt_a), pr_number="10", member_name="alpha"),
            mp.PRPair(repo_path=str(wt_b), pr_number="20", member_name="beta"),
        ]
        result = mp.merge_prs(
            pr_pairs=pr_pairs,
            manifest_path=str(manifest),
            toml_path=str(toml),
            runner=stub,
        )
        assert any("10" in m for m in result["merged"]), "pr1 (10) should be in merged"
        assert any("20" in k for k in result["failed"]), "pr2 (20) should be in failed"
        assert result["skipped"] == {}

    def test_stop_on_first_failure_skips_remaining(self, tmp_path: Path) -> None:
        wt_a = tmp_path / "wt" / "a"
        wt_b = tmp_path / "wt" / "b"
        wt_c = tmp_path / "wt" / "c"
        for p in (wt_a, wt_b, wt_c):
            p.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "a", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
            {"name": "b", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
            {"name": "c", "repo_root": str(tmp_path), "worktree_path": str(wt_c)},
        ])
        toml = _write_toml(
            tmp_path,
            '[release]\nmerge_order = ["a", "b", "c"]\n',
        )

        stub = _make_pr_stub(
            {"1": "BLOCKED", "2": "MERGEABLE_CLEAN", "3": "MERGEABLE_CLEAN"},
        )
        pr_pairs = [
            mp.PRPair(repo_path=str(wt_a), pr_number="1", member_name="a"),
            mp.PRPair(repo_path=str(wt_b), pr_number="2", member_name="b"),
            mp.PRPair(repo_path=str(wt_c), pr_number="3", member_name="c"),
        ]
        result = mp.merge_prs(
            pr_pairs=pr_pairs,
            manifest_path=str(manifest),
            toml_path=str(toml),
            runner=stub,
        )
        assert result["merged"] == []
        assert len(result["failed"]) == 1
        assert len(result["skipped"]) == 2

    def test_already_merged_pr_skipped_not_re_merged(self, tmp_path: Path) -> None:
        """A PR already MERGED on a retry invocation → skipped, not re-merged."""
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
        ])
        toml = _write_toml(tmp_path, "[group]\nname = 'grp'\n")

        merge_call_count = [0]

        def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
            if "merge" in cmd and "--merge" in cmd:
                merge_call_count[0] += 1
                return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
            if "view" in cmd and "--json" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({
                    "state": "MERGED", "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN", "isDraft": False, "headRefName": "feat",
                }), "")
            if "config" in cmd and "user.email" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        pr_pairs = [mp.PRPair(repo_path=str(wt), pr_number="99", member_name="alpha")]
        result = mp.merge_prs(
            pr_pairs=pr_pairs,
            manifest_path=str(manifest),
            toml_path=str(toml),
            runner=stub,
        )
        assert merge_call_count[0] == 0, "should not call gh pr merge on an already-merged PR"
        assert any("99" in k for k in result["skipped"])
        assert "already" in list(result["skipped"].values())[0].lower()


# ---------------------------------------------------------------------------
# Manifest errors
# ---------------------------------------------------------------------------


class TestMergePrsManifestErrors:
    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        toml = _write_toml(tmp_path, "[group]\nname='g'\n")
        with pytest.raises(mp.ManifestReadError):
            mp.merge_prs(
                pr_pairs=[mp.PRPair("path", "1", "a")],
                manifest_path=str(tmp_path / "nonexistent.json"),
                toml_path=str(toml),
                runner=lambda cmd, **kw: None,
            )

    def test_malformed_manifest_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        toml = _write_toml(tmp_path, "[group]\nname='g'\n")
        with pytest.raises(mp.ManifestReadError):
            mp.merge_prs(
                pr_pairs=[mp.PRPair("path", "1", "a")],
                manifest_path=str(bad),
                toml_path=str(toml),
                runner=lambda cmd, **kw: None,
            )
