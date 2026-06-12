"""Tests for GitHubProvider pr.status / pr.evaluate / pr.merge / ci.checks / ci.wait.

Ports the forge test coverage (test_pr_evaluate.py, test_merge_prs.py) rewritten
against the provider interface. All gh/git calls go through an injected stub
runner — zero network. No hardcoded review-bot login (a passed param).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from trailhead.vcs import get_provider
from trailhead.vcs.github import (
    PRPair,
    MergeOrderRequiredError,
    MergeConfigError,
    InvalidInputError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pr_payload(
    mergeable: str = "MERGEABLE",
    merge_state: str = "CLEAN",
    is_draft: bool = False,
    failing_checks: list[dict] | None = None,
    reviews: list[dict] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mergeable": mergeable,
        "mergeStateStatus": merge_state,
        "isDraft": is_draft,
        "failingChecks": failing_checks or [],
    }
    if reviews is not None:
        result["botReviews"] = reviews
    return result


def _make_gh_stub(view_payload: dict, checks_payload: list[dict] | None = None):
    def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        cmd_str = " ".join(cmd)
        if "pr" in cmd_str and "checks" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(checks_payload or []), "")
        if "pr" in cmd_str and "view" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(view_payload), "")
        if "remote" in cmd_str and "get-url" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "[]", "")
    return stub


def _make_pr_stub(pr_statuses: dict[str, str], fail_on: set[str] | None = None):
    fail_on = fail_on or set()

    def _token_after(cmd: list[str], keyword: str) -> str | None:
        try:
            idx = cmd.index(keyword)
            return cmd[idx + 1] if idx + 1 < len(cmd) else None
        except ValueError:
            return None

    def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        cmd_str = " ".join(cmd)
        if "git" in cmd_str and "config" in cmd_str and "user.email" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
        if "gh" in cmd_str and "pr" in cmd_str and "view" in cmd_str and "--json" in cmd_str:
            pr_number = _token_after(cmd, "view")
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
        if "gh" in cmd_str and "pr" in cmd_str and "merge" in cmd_str:
            pr_number = _token_after(cmd, "merge")
            if pr_number in fail_on:
                return subprocess.CompletedProcess(cmd, 1, "", "merge failed")
            return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
        if "git" in cmd_str and "push" in cmd_str and "delete" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return stub


def _write_manifest(tmp_path: Path, members: list[dict]) -> Path:
    path = tmp_path / "camp-state" / "grp" / "worktrees" / "feat" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": 1, "group": "grp", "slug": "feat",
            "branch": "worktree-feat", "members": members}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "group.toml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# pr.status (ports check_pr_status)
# ---------------------------------------------------------------------------


class TestPrStatus:
    def test_done_when_mergeable_and_clean(self) -> None:
        provider = get_provider("github", runner=_make_gh_stub(
            {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False, "reviews": []}, []))
        result = provider.pr.status("some/path", "42")
        assert result["mergeable"] == "MERGEABLE"
        assert result["mergeStateStatus"] == "CLEAN"
        assert result["failingChecks"] == []

    def test_bot_reviews_present_when_configured(self) -> None:
        view = {
            "mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED", "isDraft": False,
            "reviews": [{"author": {"login": "my-bot"}, "state": "CHANGES_REQUESTED", "body": "fix"}],
        }
        provider = get_provider("github", runner=_make_gh_stub(view, []))
        result = provider.pr.status("some/path", "42", review_bot_login="my-bot")
        assert "botReviews" in result
        assert len(result["botReviews"]) == 1

    def test_wrong_login_filtered(self) -> None:
        view = {
            "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False,
            "reviews": [{"author": {"login": "other-bot"}, "state": "CHANGES_REQUESTED", "body": "x"}],
        }
        provider = get_provider("github", runner=_make_gh_stub(view, []))
        result = provider.pr.status("some/path", "42", review_bot_login="my-review-bot")
        assert result.get("botReviews", []) == []

    def test_status_routes_through_runner_no_shell_string(self) -> None:
        calls: list[list[str]] = []

        def stub(cmd, **kwargs):
            assert isinstance(cmd, list), "cmd must be list-form (shell=False)"
            calls.append(cmd)
            cmd_str = " ".join(cmd)
            if "pr" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd, 0, json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False, "reviews": []}), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        provider.pr.status("some/path", "42")
        assert calls
        assert all(c[0] in ("gh", "git") for c in calls)


# ---------------------------------------------------------------------------
# ci.checks — annotation fetch integration
# ---------------------------------------------------------------------------


class TestCiChecks:
    def test_annotation_gh_api_call_issued(self) -> None:
        calls: list[list[str]] = []
        view_data = {"mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED", "isDraft": False, "reviews": []}
        checks_data = [{"name": "ci", "state": "FAILURE",
                        "link": "https://github.com/myorg/myrepo/actions/runs/111/job/222"}]
        annotations_data = [{"path": "src/foo.py", "start_line": 10, "message": "assertion failed",
                             "annotation_level": "failure"}]

        def stub(cmd, **kwargs):
            calls.append(list(cmd))
            cmd_str = " ".join(cmd)
            if "remote" in cmd_str and "get-url" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "git@github.com:myorg/myrepo.git\n", "")
            if "pr" in cmd_str and "checks" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(checks_data), "")
            if "pr" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(view_data), "")
            if "api" in cmd_str and "annotations" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(annotations_data), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        result = provider.ci.checks("some/path", "42")
        annotation_calls = [c for c in calls if "api" in c and any("annotations" in tok for tok in c)]
        assert annotation_calls
        api_call_str = " ".join(annotation_calls[0])
        assert "check-runs/222" in api_call_str
        assert "myorg/myrepo" in api_call_str
        failing = result["failingChecks"]
        assert len(failing) == 1
        assert failing[0]["annotations"][0]["path"] == "src/foo.py"


# ---------------------------------------------------------------------------
# pr.evaluate (ports pr_evaluate_status)
# ---------------------------------------------------------------------------


class TestPrEvaluate:
    def _provider(self):
        return get_provider("github", runner=lambda cmd, **kw: None)

    def test_done_on_mergeable_clean(self) -> None:
        result = self._provider().pr.evaluate(_pr_payload("MERGEABLE", "CLEAN"))
        assert result["action"] == "done"

    def test_rebase_on_conflicting(self) -> None:
        result = self._provider().pr.evaluate(_pr_payload("CONFLICTING", "DIRTY"))
        assert result["action"] == "rebase"

    def test_rerun_ci_on_failing_no_annotations(self) -> None:
        status = _pr_payload("MERGEABLE", "BLOCKED", failing_checks=[
            {"name": "tests", "state": "FAILURE",
             "link": "https://github.com/o/r/actions/runs/123/job/456", "annotations": []}])
        result = self._provider().pr.evaluate(status)
        assert result["action"] in ("rerun_ci", "fix_ci")

    def test_fix_ci_on_failing_with_annotations(self) -> None:
        status = _pr_payload("MERGEABLE", "BLOCKED", failing_checks=[
            {"name": "tests", "state": "FAILURE", "link": "https://github.com/o/r/actions/runs/99",
             "annotations": [{"path": "lib/foo.py", "start_line": 1, "message": "err"}]}])
        result = self._provider().pr.evaluate(status, fail_count=0)
        assert result["action"] == "fix_ci"

    def test_wait_on_draft(self) -> None:
        result = self._provider().pr.evaluate(_pr_payload("MERGEABLE", "CLEAN", is_draft=True))
        assert result["action"] == "wait"

    def test_wait_on_ci_pending(self) -> None:
        result = self._provider().pr.evaluate(_pr_payload("UNKNOWN", "PENDING"))
        assert result["action"] == "wait"

    def test_no_bot_configured_ignores_reviews(self) -> None:
        status = {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False, "failingChecks": []}
        result = self._provider().pr.evaluate(status, review_bot_login=None)
        assert result["action"] == "done"

    def test_with_bot_configured_changes_requested_yields_review(self) -> None:
        status = _pr_payload("MERGEABLE", "CLEAN", reviews=[{"state": "CHANGES_REQUESTED", "body": "fix"}])
        result = self._provider().pr.evaluate(status, review_bot_login="my-review-bot")
        assert result["action"] == "review"


# ---------------------------------------------------------------------------
# pr.merge (ports merge_prs)
# ---------------------------------------------------------------------------


class TestPrMerge:
    def test_toml_merge_order_respected(self, tmp_path: Path) -> None:
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

        def stub(cmd, **kwargs):
            if "merge" in cmd and "--merge" in cmd:
                for tok in cmd:
                    if tok not in ("gh", "pr", "merge", "--merge", "--author-email", "test@example.com") and not tok.startswith("-"):
                        merge_calls.append(tok)
                        break
                return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
            if "view" in cmd and "--json" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({
                    "state": "OPEN", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                    "isDraft": False, "headRefName": "feat"}), "")
            if "config" in cmd and "user.email" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        provider = get_provider("github", runner=stub)
        pr_pairs = [
            PRPair(repo_path=str(wt_a), pr_number="10", member_name="alpha"),
            PRPair(repo_path=str(wt_b), pr_number="20", member_name="beta"),
        ]
        provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert merge_calls.index("20") < merge_calls.index("10")

    def test_multiple_prs_no_merge_order_refuses(self, tmp_path: Path) -> None:
        wt_a = tmp_path / "wt" / "alpha"
        wt_b = tmp_path / "wt" / "beta"
        wt_a.mkdir(parents=True)
        wt_b.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
            {"name": "beta", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
        ])
        toml = _write_toml(tmp_path, "[group]\nname = 'grp'\n")
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        pr_pairs = [
            PRPair(repo_path=str(wt_a), pr_number="1", member_name="alpha"),
            PRPair(repo_path=str(wt_b), pr_number="2", member_name="beta"),
        ]
        with pytest.raises(MergeOrderRequiredError) as exc_info:
            provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        msg = str(exc_info.value)
        assert "merge_order" in msg
        assert "[release]" in msg

    def test_merge_order_names_nonexistent_member_raises(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
        ])
        toml = _write_toml(tmp_path, '[release]\nmerge_order = ["alpha", "nonexistent"]\n')
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="1", member_name="alpha")]
        with pytest.raises(MergeConfigError) as exc_info:
            provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert "nonexistent" in str(exc_info.value)

    def test_partial_merge_pr1_merges_pr2_fails(self, tmp_path: Path) -> None:
        wt_a = tmp_path / "wt" / "alpha"
        wt_b = tmp_path / "wt" / "beta"
        wt_a.mkdir(parents=True)
        wt_b.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
            {"name": "beta", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
        ])
        toml = _write_toml(tmp_path, '[release]\nmerge_order = ["alpha", "beta"]\n')
        provider = get_provider("github", runner=_make_pr_stub(
            {"10": "MERGEABLE_CLEAN", "20": "MERGEABLE_CLEAN"}, fail_on={"20"}))
        pr_pairs = [
            PRPair(repo_path=str(wt_a), pr_number="10", member_name="alpha"),
            PRPair(repo_path=str(wt_b), pr_number="20", member_name="beta"),
        ]
        result = provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert any("10" in m for m in result["merged"])
        assert any("20" in k for k in result["failed"])
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
        toml = _write_toml(tmp_path, '[release]\nmerge_order = ["a", "b", "c"]\n')
        provider = get_provider("github", runner=_make_pr_stub(
            {"1": "BLOCKED", "2": "MERGEABLE_CLEAN", "3": "MERGEABLE_CLEAN"}))
        pr_pairs = [
            PRPair(repo_path=str(wt_a), pr_number="1", member_name="a"),
            PRPair(repo_path=str(wt_b), pr_number="2", member_name="b"),
            PRPair(repo_path=str(wt_c), pr_number="3", member_name="c"),
        ]
        result = provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert result["merged"] == []
        assert len(result["failed"]) == 1
        assert len(result["skipped"]) == 2

    def test_already_merged_pr_skipped(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
        ])
        toml = _write_toml(tmp_path, "[group]\nname = 'grp'\n")
        merge_call_count = [0]

        def stub(cmd, **kwargs):
            if "merge" in cmd and "--merge" in cmd:
                merge_call_count[0] += 1
                return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
            if "view" in cmd and "--json" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({
                    "state": "MERGED", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                    "isDraft": False, "headRefName": "feat"}), "")
            if "config" in cmd and "user.email" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="99", member_name="alpha")]
        result = provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert merge_call_count[0] == 0
        assert any("99" in k for k in result["skipped"])

    def test_pr_number_non_numeric_rejected(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
        ])
        toml = _write_toml(tmp_path, "[group]\nname='g'\n")
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="abc123", member_name="alpha")]
        with pytest.raises(InvalidInputError):
            provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

    def test_branch_with_leading_dash_skips_delete(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(tmp_path, [
            {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
        ])
        toml = _write_toml(tmp_path, "[group]\nname='g'\n")
        delete_calls: list[list[str]] = []

        def stub(cmd, **kwargs):
            if "push" in cmd and "--delete" in cmd:
                delete_calls.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if "config" in cmd and "user.email" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
            if "view" in cmd and "--json" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({
                    "state": "OPEN", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                    "isDraft": False, "headRefName": "--upload-pack=x"}), "")
            if "merge" in cmd and "--merge" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="42", member_name="alpha")]
        provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert delete_calls == []


# ---------------------------------------------------------------------------
# ci.wait (ports wait_for_actionable)
# ---------------------------------------------------------------------------


class TestCiWait:
    def test_returns_immediately_when_actionable(self) -> None:
        view_data = {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False, "reviews": []}

        def stub(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "checks" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            if "pr" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(view_data), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        result = provider.ci.wait([("some/path", "42")], timeout=5, interval=1)
        assert "42" in str(result["actionable"])
        assert result.get("timeout") is not True

    def test_times_out_when_always_waiting(self) -> None:
        draft_view = {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": True, "reviews": []}

        def stub(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "checks" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            if "pr" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(draft_view), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        result = provider.ci.wait([("some/path", "1")], timeout=2, interval=1)
        assert result.get("timeout") is True
