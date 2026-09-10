"""Tests for GitHubProvider pr.status / pr.evaluate / pr.merge / ci.checks / ci.wait.

Ports the craft test coverage (test_pr_evaluate.py, test_merge_prs.py) rewritten
against the provider interface. All gh/git calls go through an injected stub
runner — zero network. No hardcoded review-bot login (a passed param).
"""

from __future__ import annotations

import itertools
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from trailhead.vcs import get_provider
from trailhead.vcs.github import (
    PRPair,
    MergeOrderRequiredError,
    MergeConfigError,
    MergeMethodInvalidError,
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
                payload = {
                    "state": "MERGED",
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "isDraft": False,
                    "headRefName": "feat",
                }
            elif status == "DRAFT":
                payload = {
                    "state": "OPEN",
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "isDraft": True,
                    "headRefName": "feat",
                }
            elif status == "BLOCKED":
                payload = {
                    "state": "OPEN",
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "BLOCKED",
                    "isDraft": False,
                    "headRefName": "feat",
                }
            else:
                payload = {
                    "state": "OPEN",
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "isDraft": False,
                    "headRefName": "feat",
                }
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
    p = tmp_path / "group.toml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# pr.status (ports check_pr_status)
# ---------------------------------------------------------------------------


class TestPrStatus:
    def test_done_when_mergeable_and_clean(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_gh_stub(
                {
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "isDraft": False,
                    "reviews": [],
                },
                [],
            ),
        )
        result = provider.pr.status("some/path", "42")
        assert result["mergeable"] == "MERGEABLE"
        assert result["mergeStateStatus"] == "CLEAN"
        assert result["failingChecks"] == []

    def test_bot_reviews_present_when_configured(self) -> None:
        view = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "BLOCKED",
            "isDraft": False,
            "reviews": [
                {"author": {"login": "my-bot"}, "state": "CHANGES_REQUESTED", "body": "fix"},
            ],
        }
        provider = get_provider("github", runner=_make_gh_stub(view, []))
        result = provider.pr.status("some/path", "42", review_bot_login="my-bot")
        assert "botReviews" in result
        assert len(result["botReviews"]) == 1

    def test_wrong_login_filtered(self) -> None:
        view = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "reviews": [
                {"author": {"login": "other-bot"}, "state": "CHANGES_REQUESTED", "body": "x"},
            ],
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
                    cmd,
                    0,
                    json.dumps(
                        {
                            "mergeable": "MERGEABLE",
                            "mergeStateStatus": "CLEAN",
                            "isDraft": False,
                            "reviews": [],
                        }
                    ),
                    "",
                )
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        provider.pr.status("some/path", "42")
        assert calls
        assert all(c[0] in ("gh", "git") for c in calls)

    def test_since_filter_excludes_older_bot_reviews(self) -> None:
        """Bot reviews with submittedAt <= since are filtered out."""
        view = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "reviews": [
                {
                    "author": {"login": "my-bot"},
                    "state": "CHANGES_REQUESTED",
                    "body": "old review",
                    "submittedAt": "2024-01-01T10:00:00Z",
                },
                {
                    "author": {"login": "my-bot"},
                    "state": "APPROVED",
                    "body": "new review",
                    "submittedAt": "2024-06-01T10:00:00Z",
                },
            ],
        }
        provider = get_provider("github", runner=_make_gh_stub(view, []))
        # since is set after the old review but before the new one
        result = provider.pr.status(
            "some/path",
            "42",
            review_bot_login="my-bot",
            since="2024-03-01T00:00:00Z",
        )
        bot_reviews = result.get("botReviews", [])
        assert len(bot_reviews) == 1, f"expected 1 bot review after since filter, got {bot_reviews}"
        assert bot_reviews[0]["state"] == "APPROVED"

    @pytest.mark.parametrize("bad_pr_number", ["--repo=owner/other", "123; rm -rf", "abc"])
    def test_flag_injection_shaped_pr_number_rejected(self, bad_pr_number: str) -> None:
        """A flag-shaped or non-numeric pr_number must never reach gh argv on the read
        path — otherwise gh could parse it as an option (e.g. --repo=) and silently
        redirect the query to a different repo."""

        def _boom(cmd, **kwargs):
            raise AssertionError(f"gh must not be invoked, got: {cmd}")

        provider = get_provider("github", runner=_boom)
        with pytest.raises(InvalidInputError):
            provider.pr.status("some/path", bad_pr_number)


# ---------------------------------------------------------------------------
# ci.checks — annotation fetch integration
# ---------------------------------------------------------------------------


class TestCiChecks:
    def test_annotation_gh_api_call_issued(self) -> None:
        calls: list[list[str]] = []
        view_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "BLOCKED",
            "isDraft": False,
            "reviews": [],
        }
        checks_data = [
            {
                "name": "ci",
                "state": "FAILURE",
                "link": "https://github.com/myorg/myrepo/actions/runs/111/job/222",
            }
        ]
        annotations_data = [
            {
                "path": "src/foo.py",
                "start_line": 10,
                "message": "assertion failed",
                "annotation_level": "failure",
            }
        ]

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
        annotation_calls = [
            c for c in calls if "api" in c and any("annotations" in tok for tok in c)
        ]
        assert annotation_calls
        api_call_str = " ".join(annotation_calls[0])
        assert "check-runs/222" in api_call_str
        assert "myorg/myrepo" in api_call_str
        failing = result["failingChecks"]
        assert len(failing) == 1
        assert failing[0]["annotations"][0]["path"] == "src/foo.py"

    @pytest.mark.parametrize("bad_pr_number", ["--repo=owner/other", "123; rm -rf", "abc"])
    def test_flag_injection_shaped_pr_number_rejected(self, bad_pr_number: str) -> None:
        """ci.checks shares _check_status with pr.status; the digits-only guard must
        cover this entry point too, not just the merge path."""

        def _boom(cmd, **kwargs):
            raise AssertionError(f"gh must not be invoked, got: {cmd}")

        provider = get_provider("github", runner=_boom)
        with pytest.raises(InvalidInputError):
            provider.ci.checks("some/path", bad_pr_number)


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
        status = _pr_payload(
            "MERGEABLE",
            "BLOCKED",
            failing_checks=[
                {
                    "name": "tests",
                    "state": "FAILURE",
                    "link": "https://github.com/o/r/actions/runs/123/job/456",
                    "annotations": [],
                }
            ],
        )
        result = self._provider().pr.evaluate(status)
        assert result["action"] in ("rerun_ci", "fix_ci")

    def test_fix_ci_on_failing_with_annotations(self) -> None:
        status = _pr_payload(
            "MERGEABLE",
            "BLOCKED",
            failing_checks=[
                {
                    "name": "tests",
                    "state": "FAILURE",
                    "link": "https://github.com/o/r/actions/runs/99",
                    "annotations": [{"path": "lib/foo.py", "start_line": 1, "message": "err"}],
                }
            ],
        )
        result = self._provider().pr.evaluate(status, fail_count=0)
        assert result["action"] == "fix_ci"

    def test_wait_on_draft(self) -> None:
        result = self._provider().pr.evaluate(_pr_payload("MERGEABLE", "CLEAN", is_draft=True))
        assert result["action"] == "wait"

    def test_wait_on_ci_pending(self) -> None:
        result = self._provider().pr.evaluate(_pr_payload("UNKNOWN", "PENDING"))
        assert result["action"] == "wait"

    def test_no_bot_configured_ignores_reviews(self) -> None:
        status = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "failingChecks": [],
        }
        result = self._provider().pr.evaluate(status, review_bot_login=None)
        assert result["action"] == "done"

    def test_with_bot_configured_changes_requested_yields_review(self) -> None:
        status = _pr_payload(
            "MERGEABLE", "CLEAN", reviews=[{"state": "CHANGES_REQUESTED", "body": "fix"}]
        )
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
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
                {"name": "beta", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
            ],
        )
        toml = _write_toml(
            tmp_path,
            '[release]\nauto_merge = true\nmerge_order = ["beta", "alpha"]\nmerge_method = "merge"\n',
        )
        merge_calls: list[str] = []

        def stub(cmd, **kwargs):
            if "merge" in cmd and "--merge" in cmd:
                for tok in cmd:
                    if tok not in (
                        "gh",
                        "pr",
                        "merge",
                        "--merge",
                        "--author-email",
                        "test@example.com",
                    ) and not tok.startswith("-"):
                        merge_calls.append(tok)
                        break
                return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
            if "view" in cmd and "--json" in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    json.dumps(
                        {
                            "state": "OPEN",
                            "mergeable": "MERGEABLE",
                            "mergeStateStatus": "CLEAN",
                            "isDraft": False,
                            "headRefName": "feat",
                        }
                    ),
                    "",
                )
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

    def test_multiple_prs_no_merge_order_refuses(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wt_a = tmp_path / "wt" / "alpha"
        wt_b = tmp_path / "wt" / "beta"
        wt_a.mkdir(parents=True)
        wt_b.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
                {"name": "beta", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
            ],
        )
        toml = _write_toml(tmp_path, "[release]\nauto_merge = true\n")
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
        # merge_method is never used on a refused run — the notice announcing
        # its (unused) default must not print here.
        assert capsys.readouterr().err == ""

    def test_merge_order_names_nonexistent_member_raises(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
            ],
        )
        toml = _write_toml(
            tmp_path, '[release]\nauto_merge = true\nmerge_order = ["alpha", "nonexistent"]\n'
        )
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="1", member_name="alpha")]
        with pytest.raises(MergeConfigError) as exc_info:
            provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert "nonexistent" in str(exc_info.value)
        # Same as above: a refused run must not announce a merge method it
        # never used.
        assert capsys.readouterr().err == ""

    def test_partial_merge_pr1_merges_pr2_fails(self, tmp_path: Path) -> None:
        wt_a = tmp_path / "wt" / "alpha"
        wt_b = tmp_path / "wt" / "beta"
        wt_a.mkdir(parents=True)
        wt_b.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
                {"name": "beta", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
            ],
        )
        toml = _write_toml(tmp_path, '[release]\nauto_merge = true\nmerge_order = ["alpha", "beta"]\n')
        provider = get_provider(
            "github",
            runner=_make_pr_stub(
                {"10": "MERGEABLE_CLEAN", "20": "MERGEABLE_CLEAN"}, fail_on={"20"}
            ),
        )
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
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "a", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
                {"name": "b", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
                {"name": "c", "repo_root": str(tmp_path), "worktree_path": str(wt_c)},
            ],
        )
        toml = _write_toml(tmp_path, '[release]\nauto_merge = true\nmerge_order = ["a", "b", "c"]\n')
        provider = get_provider(
            "github",
            runner=_make_pr_stub({"1": "BLOCKED", "2": "MERGEABLE_CLEAN", "3": "MERGEABLE_CLEAN"}),
        )
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
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
            ],
        )
        toml = _write_toml(tmp_path, "[release]\nauto_merge = true\n")
        merge_call_count = [0]

        def stub(cmd, **kwargs):
            if "merge" in cmd and "--merge" in cmd:
                merge_call_count[0] += 1
                return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
            if "view" in cmd and "--json" in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    json.dumps(
                        {
                            "state": "MERGED",
                            "mergeable": "MERGEABLE",
                            "mergeStateStatus": "CLEAN",
                            "isDraft": False,
                            "headRefName": "feat",
                        }
                    ),
                    "",
                )
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
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
            ],
        )
        toml = _write_toml(tmp_path, "[group]\nname='g'\n")
        provider = get_provider("github", runner=lambda cmd, **kw: None)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="abc123", member_name="alpha")]
        with pytest.raises(InvalidInputError):
            provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

    def test_stacked_pr_refused_before_do_merge(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
            ],
        )
        toml = _write_toml(tmp_path, "[release]\nauto_merge = true\n")
        merge_calls: list[str] = []

        def stub(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "config" in cmd and "user.email" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
            if "remote" in cmd and "get-url" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, "git@github.com:acme/alpha.git\n", ""
                )
            if "pr" in cmd and "view" in cmd and "--json" in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    json.dumps(
                        {
                            "state": "OPEN",
                            "mergeable": "MERGEABLE",
                            "mergeStateStatus": "CLEAN",
                            "isDraft": False,
                            "headRefName": "feat",
                        }
                    ),
                    "",
                )
            if "graphql" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    json.dumps(
                        {
                            "data": {
                                "repository": {
                                    "pullRequest": {
                                        "stackEntry": {"stack": {"number": 7, "size": 3}}
                                    }
                                }
                            }
                        }
                    ),
                    "",
                )
            if "pr" in cmd and "merge" in cmd:
                merge_calls.append("merged")
                return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="55", member_name="alpha")]
        result = provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert merge_calls == []
        failed_msg = next(v for k, v in result["failed"].items() if "55" in k)
        assert "55" in failed_msg
        assert "stack" in failed_msg.lower()
        assert result["merged"] == []

    def test_no_stack_signal_merges_as_before(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
            ],
        )
        toml = _write_toml(tmp_path, "[release]\nauto_merge = true\n")
        merge_calls: list[str] = []

        def stub(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "config" in cmd and "user.email" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
            if "remote" in cmd and "get-url" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, "git@github.com:acme/alpha.git\n", ""
                )
            if "pr" in cmd and "view" in cmd and "--json" in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    json.dumps(
                        {
                            "state": "OPEN",
                            "mergeable": "MERGEABLE",
                            "mergeStateStatus": "CLEAN",
                            "isDraft": False,
                            "headRefName": "feat",
                        }
                    ),
                    "",
                )
            if "graphql" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    json.dumps(
                        {
                            "data": {
                                "repository": {
                                    "pullRequest": {"stackEntry": None}
                                }
                            }
                        }
                    ),
                    "",
                )
            if "pr" in cmd and "merge" in cmd:
                merge_calls.append("merged")
                return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="56", member_name="alpha")]
        result = provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert merge_calls == ["merged"]
        assert any("56" in m for m in result["merged"])

    def test_branch_with_leading_dash_skips_delete(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)},
            ],
        )
        toml = _write_toml(tmp_path, "[release]\nauto_merge = true\n")
        delete_calls: list[list[str]] = []

        def stub(cmd, **kwargs):
            if "push" in cmd and "--delete" in cmd:
                delete_calls.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if "config" in cmd and "user.email" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
            if "view" in cmd and "--json" in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    json.dumps(
                        {
                            "state": "OPEN",
                            "mergeable": "MERGEABLE",
                            "mergeStateStatus": "CLEAN",
                            "isDraft": False,
                            "headRefName": "--upload-pack=x",
                        }
                    ),
                    "",
                )
            if "merge" in cmd and "--merge" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="42", member_name="alpha")]
        provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert delete_calls == []


# ---------------------------------------------------------------------------
# pr.merge — merge_method selection ([release].merge_method)
# ---------------------------------------------------------------------------


class TestMergeMethod:
    def _run_merge_capture_argv(
        self, tmp_path: Path, release_toml_body: str
    ) -> list[list[str]]:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [{"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)}],
        )
        toml = _write_toml(tmp_path, release_toml_body)
        merge_argv: list[list[str]] = []
        answer = _make_pr_stub({"7": "MERGEABLE_CLEAN"})

        def stub(cmd, **kwargs):
            if "pr" in cmd and "merge" in cmd:
                merge_argv.append(list(cmd))
            return answer(cmd, **kwargs)

        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="7", member_name="alpha")]
        provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        return merge_argv

    def test_merge_method_squash_flag(self, tmp_path: Path) -> None:
        argv = self._run_merge_capture_argv(
            tmp_path, '[release]\nauto_merge = true\nmerge_method = "squash"\n'
        )
        assert len(argv) == 1
        assert "--squash" in argv[0]
        assert "--merge" not in argv[0]
        assert "--rebase" not in argv[0]

    def test_merge_method_rebase_flag(self, tmp_path: Path) -> None:
        argv = self._run_merge_capture_argv(
            tmp_path, '[release]\nauto_merge = true\nmerge_method = "rebase"\n'
        )
        assert len(argv) == 1
        assert "--rebase" in argv[0]
        assert "--squash" not in argv[0]
        assert "--merge" not in argv[0]
        assert "--author-email" in argv[0]

    def test_merge_method_merge_flag(self, tmp_path: Path) -> None:
        argv = self._run_merge_capture_argv(
            tmp_path, '[release]\nauto_merge = true\nmerge_method = "merge"\n'
        )
        assert len(argv) == 1
        assert "--merge" in argv[0]
        assert "--squash" not in argv[0]
        assert "--rebase" not in argv[0]

    def test_absent_merge_method_names_automatic_selection_and_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An absent key is a readable, well-formed configuration, so it
        gets the automatic-selection notice — not the malformed-input one —
        and that notice names the setting value that restores squashing."""
        argv = self._run_merge_capture_argv(tmp_path, "[release]\nauto_merge = true\n")
        assert len(argv) == 1
        # This stub's PR carries no capability data, so the per-pull-request
        # resolver's lookup-failure fallback picks squash here — a separate
        # concern from the notice text under test.
        assert "--squash" in argv[0]
        err = capsys.readouterr().err
        assert "automatic selection" in err
        assert 'merge_method = "squash"' in err

    def test_automatic_named_explicitly_is_accepted_not_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`merge_method = "automatic"` widens the accepted vocabulary — it
        must resolve rather than raise MergeMethodInvalidError, and it takes
        the same path as an absent key: the per-pull-request resolver picks
        the strategy, and the automatic-selection notice fires the same
        way."""
        argv = self._run_merge_capture_argv(
            tmp_path, '[release]\nauto_merge = true\nmerge_method = "automatic"\n'
        )
        assert len(argv) == 1
        assert "--squash" in argv[0]
        err = capsys.readouterr().err
        assert "automatic selection" in err

    def test_automatic_notice_does_not_claim_squash_is_still_the_default(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The automatic-selection notice must not carry the prior
        default's claim — a run under automatic selection may rebase, so
        the notice cannot say squash is what's happening now."""
        self._run_merge_capture_argv(tmp_path, "[release]\nauto_merge = true\n")
        err = capsys.readouterr().err
        assert "defaulting to squash" not in err

    def test_automatic_notice_restoring_value_round_trips_through_the_loader(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The automatic-selection notice names a setting value that
        restores squashing. Prove that value is genuinely accepted by
        `_load_merge_method` — feeding it back through the loader must
        return the same value, not raise and not resolve to automatic —
        rather than trusting that the literal in the notice text is
        correct."""
        from trailhead.vcs.github import _load_merge_method

        self._run_merge_capture_argv(tmp_path, "[release]\nauto_merge = true\n")
        err = capsys.readouterr().err
        match = re.search(r'merge_method = "([^"]+)"', err)
        assert match is not None, err
        restoring_value = match.group(1)

        round_trip_dir = tmp_path / "round-trip"
        round_trip_dir.mkdir()
        round_trip_toml = _write_toml(
            round_trip_dir, f'[release]\nauto_merge = true\nmerge_method = "{restoring_value}"\n'
        )
        assert _load_merge_method(str(round_trip_toml)) == restoring_value

    def test_malformed_release_table_notice_distinguishable_from_automatic(self) -> None:
        """A malformed configuration resolves to squash, same as before,
        but its notice must read differently from the well-formed-and-
        unset case's — a corrupt file is never mistaken for a deliberate
        choice. `_merge_method_notice` is the function `_merge_prs` calls
        to pick this text; the malformed shape (`None` from
        `_load_merge_method`) cannot be produced through a single static
        TOML file passed to `_merge_prs`, because the same read failure
        that makes `_load_merge_method` return `None` also makes
        `_load_auto_merge` return `False`, which refuses the merge before
        the notice is ever reached — so this exercises the notice-selection
        function directly, the same function the real merge path calls."""
        from trailhead.vcs.github import AUTOMATIC_MERGE_METHOD, _merge_method_notice

        malformed_notice = _merge_method_notice(None)
        automatic_notice = _merge_method_notice(AUTOMATIC_MERGE_METHOD)
        assert malformed_notice is not None
        assert automatic_notice is not None
        assert malformed_notice != automatic_notice
        assert "automatic selection" not in malformed_notice
        assert "could not be read" in malformed_notice
        assert "squash" in malformed_notice

    def test_explicit_strategy_notice_is_none(self) -> None:
        """An explicitly configured strategy gets no notice at all —
        `_merge_method_notice` returns `None` for any concrete strategy."""
        from trailhead.vcs.github import _merge_method_notice

        for method in ("merge", "squash", "rebase"):
            assert _merge_method_notice(method) is None

    def test_merge_result_payload_unaffected_by_notice(self, tmp_path: Path) -> None:
        """The notice goes to stderr; the result payload — what a caller
        eventually serializes to stdout — must parse exactly as before,
        unaffected by which notice (or none) fired."""
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [{"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)}],
        )
        toml = _write_toml(tmp_path, "[release]\nauto_merge = true\n")
        answer = _make_pr_stub({"7": "MERGEABLE_CLEAN"})
        provider = get_provider("github", runner=answer)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="7", member_name="alpha")]

        result = provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

        assert json.loads(json.dumps(result)) == {
            "merged": [f"{wt}:7"],
            "failed": {},
            "skipped": {},
        }

    def test_configured_merge_method_prints_no_notice(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An operator who HAS set merge_method must not be told the key is
        unset — the notice exists only for the absent-key path."""
        argv = self._run_merge_capture_argv(
            tmp_path, '[release]\nauto_merge = true\nmerge_method = "squash"\n'
        )
        assert len(argv) == 1
        err = capsys.readouterr().err
        assert "not set" not in err
        assert "defaulting" not in err

    def test_unrecognized_merge_method_raises_before_any_gh_call(
        self, tmp_path: Path
    ) -> None:
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [{"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)}],
        )
        toml = _write_toml(
            tmp_path, '[release]\nauto_merge = true\nmerge_method = "sqush"\n'
        )
        calls: list[list[str]] = []

        def stub(cmd, **kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="7", member_name="alpha")]
        with pytest.raises(MergeMethodInvalidError) as exc_info:
            provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert calls == []
        msg = str(exc_info.value)
        assert "sqush" in msg
        assert "merge" in msg and "squash" in msg and "rebase" in msg

    def test_non_string_merge_method_raises_before_any_gh_call(
        self, tmp_path: Path
    ) -> None:
        """A TOML value like `merge_method = ["squash"]` reaches the
        `value not in _MERGE_METHOD_FLAGS` membership check as an unhashable
        list, which raises TypeError rather than the contracted
        MergeMethodInvalidError. A non-string value is an invalid value, not
        a crash."""
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [{"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)}],
        )
        toml = _write_toml(
            tmp_path, '[release]\nauto_merge = true\nmerge_method = ["squash"]\n'
        )
        calls: list[list[str]] = []

        def stub(cmd, **kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="7", member_name="alpha")]
        with pytest.raises(MergeMethodInvalidError) as exc_info:
            provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))
        assert calls == []
        msg = str(exc_info.value)
        assert "merge" in msg and "squash" in msg and "rebase" in msg

    def test_well_formed_absent_key_and_malformed_shapes_resolve_differently(
        self, tmp_path: Path
    ) -> None:
        """`_load_merge_method` no longer collapses every unconfigured shape
        to one result. A readable, well-formed [release] table with the key
        absent means automatic selection (AUTOMATIC_MERGE_METHOD) — the
        configuration was understood and asked for nothing specific. Every
        shape where the configuration could not be read or understood at all
        — absent toml_path, missing file, unparseable TOML, non-table
        [release] — still resolves to None, the safe-direction signal. The
        two groups must be distinguishable, not a shared sentinel.
        """
        from trailhead.vcs.github import AUTOMATIC_MERGE_METHOD, _load_merge_method

        # _write_toml always writes to "<tmp_path>/group.toml" — each shape
        # needs its own directory so the three files on disk stay distinct
        # rather than the later writes clobbering the earlier ones.
        unparseable_dir = tmp_path / "unparseable"
        unparseable_dir.mkdir()
        non_table_dir = tmp_path / "non-table"
        non_table_dir.mkdir()
        valid_absent_dir = tmp_path / "valid-absent"
        valid_absent_dir.mkdir()

        unparseable = _write_toml(unparseable_dir, "not valid toml [[[")
        non_table = _write_toml(non_table_dir, "release = 1\n")
        valid_table_absent_key = _write_toml(valid_absent_dir, "[release]\nauto_merge = true\n")

        malformed_results = {
            _load_merge_method(None),
            _load_merge_method(str(tmp_path / "does-not-exist.toml")),
            _load_merge_method(str(unparseable)),
            _load_merge_method(str(non_table)),
        }
        assert malformed_results == {None}
        assert _load_merge_method(str(valid_table_absent_key)) == AUTOMATIC_MERGE_METHOD


# ---------------------------------------------------------------------------
# get_permitted_merge_strategies (folded into _STACK_ENTRY_QUERY)
# ---------------------------------------------------------------------------


def _graphql_stub(
    repository: dict | None,
    *,
    returncode: int = 0,
    errors: list[dict] | None = None,
    remote_url: str = "git@github.com:acme/alpha.git",
    call_log: list[list[str]] | None = None,
):
    """Stub runner for a repository -> pullRequest{stackEntry} GraphQL call.

    `repository` is the raw `data.repository` object the fake response
    carries; passing None simulates a whole-object null (unresolvable
    owner/repo). `returncode`/`errors` simulate `gh api graphql` exiting
    non-zero on a GraphQL error entry while the body still carries data.
    """

    def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        if call_log is not None:
            call_log.append(list(cmd))
        cmd_str = " ".join(cmd)
        if "remote" in cmd_str and "get-url" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, remote_url, "")
        if "graphql" in cmd_str:
            body: dict[str, Any] = {"data": {"repository": repository}}
            if errors is not None:
                body["errors"] = errors
            return subprocess.CompletedProcess(cmd, returncode, json.dumps(body), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return stub


class TestPermittedMergeStrategies:
    def test_repository_permitting_all_three_returns_all_three(self) -> None:
        from trailhead.vcs.github import get_permitted_merge_strategies

        stub = _graphql_stub(
            {
                "mergeCommitAllowed": True,
                "squashMergeAllowed": True,
                "rebaseMergeAllowed": True,
                "pullRequest": {"stackEntry": None},
            }
        )
        result = get_permitted_merge_strategies("/repo", "1", runner=stub)
        assert result == frozenset({"merge", "squash", "rebase"})

    def test_repository_permitting_exactly_one_returns_exactly_that_one(self) -> None:
        from trailhead.vcs.github import get_permitted_merge_strategies

        stub = _graphql_stub(
            {
                "mergeCommitAllowed": False,
                "squashMergeAllowed": False,
                "rebaseMergeAllowed": True,
                "pullRequest": {"stackEntry": None},
            }
        )
        result = get_permitted_merge_strategies("/repo", "2", runner=stub)
        assert result == frozenset({"rebase"})

    def test_unresolvable_repository_returns_lookup_failed_not_empty_set(self) -> None:
        """A whole-object null `repository` (owner/name unresolvable) is the
        real "could not ask" signal — it must not read as "permits none"."""
        from trailhead.vcs.github import (
            PERMITTED_STRATEGIES_LOOKUP_FAILED,
            get_permitted_merge_strategies,
        )

        stub = _graphql_stub(None, returncode=1, errors=[{"type": "NOT_FOUND"}])
        result = get_permitted_merge_strategies("/repo", "3", runner=stub)
        assert result == PERMITTED_STRATEGIES_LOOKUP_FAILED
        assert result != frozenset()

    def test_partial_failure_on_unrelated_field_still_returns_capabilities(self) -> None:
        """`gh api graphql` exits non-zero on ANY GraphQL error entry, even
        one scoped to an unrelated sub-selection (e.g. a bad PR number
        nulling `pullRequest`). The capability fields on the same
        `repository` object are unaffected and must still be read —
        routing through `_gh` (which discards stdout on non-zero exit)
        would wrongly report this as a lookup failure."""
        from trailhead.vcs.github import get_permitted_merge_strategies

        stub = _graphql_stub(
            {
                "mergeCommitAllowed": True,
                "squashMergeAllowed": True,
                "rebaseMergeAllowed": False,
                "pullRequest": None,
            },
            returncode=1,
            errors=[{"type": "NOT_FOUND", "path": ["repository", "pullRequest"]}],
        )
        result = get_permitted_merge_strategies("/repo", "999999999", runner=stub)
        assert result == frozenset({"merge", "squash"})

    def test_malformed_capability_fields_return_lookup_failed(self) -> None:
        """A repository object present but missing (or mistyped) capability
        fields is an unexpected shape — it must not raise into the merge
        loop, and must not read as "permits none"."""
        from trailhead.vcs.github import (
            PERMITTED_STRATEGIES_LOOKUP_FAILED,
            get_permitted_merge_strategies,
        )

        stub = _graphql_stub({"pullRequest": {"stackEntry": None}})
        result = get_permitted_merge_strategies("/repo", "4", runner=stub)
        assert result == PERMITTED_STRATEGIES_LOOKUP_FAILED

    def test_existing_pull_request_field_still_returned_correctly(self) -> None:
        """Folding the capability fields into the query must not break the
        existing `_get_stack_entry` read of `pullRequest { stackEntry }`."""
        from trailhead.vcs.github import _get_stack_entry

        stub = _graphql_stub(
            {
                "mergeCommitAllowed": True,
                "squashMergeAllowed": True,
                "rebaseMergeAllowed": True,
                "pullRequest": {"stackEntry": {"stack": {"number": 9, "size": 2}}},
            }
        )
        result = _get_stack_entry("/repo", "5", runner=stub)
        assert result == {"number": 9, "size": 2}

    def test_one_fetch_serves_both_stack_entry_and_permitted_strategies(self) -> None:
        """Obtaining BOTH the stack-entry signal and the permitted strategies
        for one pull request must cost exactly one query — the merge loop is
        exactly the caller that needs both. A stub call log, not a
        per-function count, is what proves the pair shares a single fetch:
        counting either reader alone is true by construction (each contains
        exactly one `rp.run`) and cannot catch two independent fetches."""
        from trailhead.vcs.github import _get_stack_entry, get_permitted_merge_strategies

        calls: list[list[str]] = []
        stub = _graphql_stub(
            {
                "mergeCommitAllowed": True,
                "squashMergeAllowed": True,
                "rebaseMergeAllowed": True,
                "pullRequest": {"stackEntry": {"stack": {"number": 9, "size": 2}}},
            },
            call_log=calls,
        )
        cache: dict = {}
        stack = _get_stack_entry("/repo", "6", runner=stub, cache=cache)
        strategies = get_permitted_merge_strategies("/repo", "6", runner=stub, cache=cache)

        graphql_calls = [c for c in calls if "graphql" in " ".join(c)]
        assert len(graphql_calls) == 1
        assert stack == {"number": 9, "size": 2}
        assert strategies == frozenset({"merge", "squash", "rebase"})


# ---------------------------------------------------------------------------
# resolve_merge_strategy / describe_merge_refusal
# ---------------------------------------------------------------------------


class TestResolveMergeStrategy:
    def test_explicit_configured_strategy_wins_with_no_capability_read_consulted(self) -> None:
        from trailhead.vcs.github import PERMITTED_STRATEGIES_LOOKUP_FAILED, resolve_merge_strategy

        strategy, reason = resolve_merge_strategy("merge", PERMITTED_STRATEGIES_LOOKUP_FAILED)
        assert strategy == "merge"
        assert reason

    def test_automatic_with_rebase_permitted_resolves_to_rebase(self) -> None:
        from trailhead.vcs.github import AUTOMATIC_MERGE_METHOD, resolve_merge_strategy

        strategy, reason = resolve_merge_strategy(
            AUTOMATIC_MERGE_METHOD, frozenset({"rebase", "squash"})
        )
        assert strategy == "rebase"
        assert reason

    def test_automatic_with_rebase_forbidden_and_exactly_one_other_permitted(self) -> None:
        from trailhead.vcs.github import AUTOMATIC_MERGE_METHOD, resolve_merge_strategy

        strategy, reason = resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset({"squash"}))
        assert strategy == "squash"
        assert reason

        strategy, reason = resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset({"merge"}))
        assert strategy == "merge"
        assert reason

    def test_automatic_with_rebase_forbidden_and_both_others_permitted_resolves_to_interim_squash(
        self,
    ) -> None:
        """Interim rule: with rebasing forbidden and more than one other
        strategy permitted, this slice resolves to squashing. A later change
        replaces this with a rule driven by the commit series' shape; this
        test names the rule as interim so that change reads as intended
        rather than as a regression."""
        from trailhead.vcs.github import AUTOMATIC_MERGE_METHOD, resolve_merge_strategy

        strategy, reason = resolve_merge_strategy(
            AUTOMATIC_MERGE_METHOD, frozenset({"merge", "squash"})
        )
        assert strategy == "squash"
        assert reason

    def test_automatic_with_lookup_failed_resolves_to_squash_distinguishable_from_interim(
        self,
    ) -> None:
        from trailhead.vcs.github import (
            AUTOMATIC_MERGE_METHOD,
            PERMITTED_STRATEGIES_LOOKUP_FAILED,
            resolve_merge_strategy,
        )

        lookup_strategy, lookup_reason = resolve_merge_strategy(
            AUTOMATIC_MERGE_METHOD, PERMITTED_STRATEGIES_LOOKUP_FAILED
        )
        interim_strategy, interim_reason = resolve_merge_strategy(
            AUTOMATIC_MERGE_METHOD, frozenset({"merge", "squash"})
        )
        assert lookup_strategy == "squash"
        assert interim_strategy == "squash"
        assert lookup_reason != interim_reason

    def test_never_returns_a_strategy_absent_from_the_permitted_set(self) -> None:
        """Enumerated from the module's own strategy vocabulary
        (`_MERGE_METHOD_FLAGS`) rather than a hand-typed list of cases — every
        subset of it, derived by power set, is exercised.

        The membership assertion is deliberately scoped to the NON-EMPTY
        subsets, and `checked_nonempty` pins that exactly those ran. Membership
        is unsatisfiable for the empty set: the resolver is total, so it must
        return some strategy, and every strategy is absent from an empty
        permitted set. That case is asserted instead by
        `test_no_permitted_strategy_resolves_to_squash_with_its_own_reason`,
        which pins both the returned strategy and its distinct reason. Every
        subset still gets the non-empty-reason assertion above."""
        from trailhead.vcs.github import (
            AUTOMATIC_MERGE_METHOD,
            _MERGE_METHOD_FLAGS,
            resolve_merge_strategy,
        )

        vocabulary = sorted(_MERGE_METHOD_FLAGS)
        checked_nonempty = 0
        for size in range(len(vocabulary) + 1):
            for combo in itertools.combinations(vocabulary, size):
                permitted = frozenset(combo)
                strategy, reason = resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, permitted)
                assert reason
                if permitted:
                    assert strategy in permitted
                    checked_nonempty += 1
        assert checked_nonempty == 2 ** len(vocabulary) - 1

    def test_no_permitted_strategy_resolves_to_squash_with_its_own_reason(self) -> None:
        """A repository reporting no permitted strategy still resolves, because
        the resolver is total. It cannot satisfy membership — every strategy is
        absent from an empty set — so it returns the safe direction and says so
        under a reason distinct from every other squashing outcome, letting a
        reader tell "permits nothing" from a lookup failure or the interim rule.
        """
        from trailhead.vcs.github import (
            AUTOMATIC_MERGE_METHOD,
            PERMITTED_STRATEGIES_LOOKUP_FAILED,
            resolve_merge_strategy,
        )

        strategy, reason = resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset())

        assert strategy == "squash"
        _, lookup_failed_reason = resolve_merge_strategy(
            AUTOMATIC_MERGE_METHOD, PERMITTED_STRATEGIES_LOOKUP_FAILED
        )
        _, interim_reason = resolve_merge_strategy(
            AUTOMATIC_MERGE_METHOD, frozenset({"merge", "squash"})
        )
        assert reason.split(":", 1)[0] not in {
            lookup_failed_reason.split(":", 1)[0],
            interim_reason.split(":", 1)[0],
        }

    def test_reason_prefixes_are_pinned_and_pairwise_distinct(self) -> None:
        """Prefixes are read back from the resolver and refusal-formatter
        actually running each named cause, then checked against the module's
        own `RESOLUTION_REASON_PREFIXES` registry — derived, not retyped."""
        from trailhead.vcs.github import (
            AUTOMATIC_MERGE_METHOD,
            PERMITTED_STRATEGIES_LOOKUP_FAILED,
            RESOLUTION_REASON_PREFIXES,
            describe_merge_refusal,
            resolve_merge_strategy,
        )

        outcomes = [
            resolve_merge_strategy("rebase", PERMITTED_STRATEGIES_LOOKUP_FAILED)[1],
            resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset({"rebase"}))[1],
            resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset({"squash"}))[1],
            resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset({"merge", "squash"}))[1],
            resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, PERMITTED_STRATEGIES_LOOKUP_FAILED)[1],
            resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset())[1],
            describe_merge_refusal("rebase", "some provider refusal text"),
        ]
        prefixes = [reason.split(":", 1)[0] for reason in outcomes]

        assert len(set(prefixes)) == len(prefixes)
        assert set(prefixes) <= set(RESOLUTION_REASON_PREFIXES.values())
        assert len(set(RESOLUTION_REASON_PREFIXES.values())) == len(RESOLUTION_REASON_PREFIXES)


class TestDescribeMergeRefusal:
    def test_names_the_strategy_and_carries_the_refusal_verbatim(self) -> None:
        from trailhead.vcs.github import RESOLUTION_REASON_PREFIXES, describe_merge_refusal

        described = describe_merge_refusal("rebase", "GraphQL: merge blocked")
        prefix = RESOLUTION_REASON_PREFIXES["merge_refused"]
        assert described == f"{prefix}: rebase refused — GraphQL: merge blocked"

    def test_never_attributes_a_cause_the_refusal_text_did_not_name(self) -> None:
        """The refusal text is opaque and may not name a branch rule at
        all — the formatter must pass it through verbatim rather than
        inventing an explanation, for a refusal that names nothing."""
        from trailhead.vcs.github import RESOLUTION_REASON_PREFIXES, describe_merge_refusal

        described = describe_merge_refusal("squash", "Pull Request is not mergeable")
        prefix = RESOLUTION_REASON_PREFIXES["merge_refused"]
        assert described == f"{prefix}: squash refused — Pull Request is not mergeable"

    def test_names_exactly_the_strategy_it_was_given_never_a_different_one(self) -> None:
        """The resolver could not have predicted this refusal; the formatter
        never substitutes a different strategy for the one that was
        actually attempted and refused."""
        from trailhead.vcs.github import describe_merge_refusal

        for strategy in ("merge", "squash", "rebase"):
            described = describe_merge_refusal(strategy, "refused")
            body = described.split(": ", 1)[1]
            assert strategy in body
            for other in ("merge", "squash", "rebase"):
                if other != strategy:
                    assert other not in body


# ---------------------------------------------------------------------------
# pr.merge — per-pull-request strategy resolution + disclosure (wiring)
# ---------------------------------------------------------------------------


def _make_capability_stub(
    *,
    capabilities: dict[str, dict[str, bool]] | None = None,
    null_repos: set[str] | None = None,
    fail_merge_repos: set[str] | None = None,
    call_log: list[list[str]] | None = None,
):
    """Stub keyed by repo directory basename -> permitted-merge-method
    booleans. `pr view` always reports mergeable/clean/non-draft/no-stack;
    `graphql` returns the repository object folding those booleans in
    alongside a null `stackEntry`, or a null repository for any basename in
    `null_repos` (an unresolvable repository — the capability lookup's
    failure signal); `pr merge` fails for any repo basename named in
    `fail_merge_repos`.
    """
    capabilities = capabilities or {}
    null_repos = null_repos or set()
    fail_merge_repos = fail_merge_repos or set()
    default_caps = {
        "mergeCommitAllowed": True,
        "squashMergeAllowed": True,
        "rebaseMergeAllowed": True,
    }

    def stub(cmd, **kwargs) -> subprocess.CompletedProcess:
        if call_log is not None:
            call_log.append(list(cmd))
        cmd_str = " ".join(cmd)
        cwd = kwargs.get("cwd") or ""
        name = Path(cwd).name
        if "config" in cmd and "user.email" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "test@example.com\n", "")
        if "remote" in cmd and "get-url" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, f"git@github.com:acme/{name}.git\n", ""
            )
        if "pr" in cmd and "view" in cmd and "--json" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps(
                    {
                        "state": "OPEN",
                        "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                        "isDraft": False,
                        "headRefName": "feat",
                    }
                ),
                "",
            )
        if "graphql" in cmd_str:
            if name in null_repos:
                repository: dict | None = None
            else:
                repository = {
                    **capabilities.get(name, default_caps),
                    "pullRequest": {"stackEntry": None},
                }
            body = {"data": {"repository": repository}}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(body), "")
        if "pr" in cmd and "merge" in cmd:
            if name in fail_merge_repos:
                return subprocess.CompletedProcess(cmd, 1, "", "GraphQL: merge blocked")
            return subprocess.CompletedProcess(cmd, 0, "merged\n", "")
        if "push" in cmd and "--delete" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return stub


class TestMergeLoopResolvesPerPullRequest:
    def _setup_two_repos(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        wt_a = tmp_path / "wt" / "alpha"
        wt_b = tmp_path / "wt" / "beta"
        wt_a.mkdir(parents=True)
        wt_b.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [
                {"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt_a)},
                {"name": "beta", "repo_root": str(tmp_path), "worktree_path": str(wt_b)},
            ],
        )
        return manifest, wt_a, wt_b

    def test_two_pull_requests_resolve_independently_per_repository(
        self, tmp_path: Path
    ) -> None:
        """`alpha` permits rebasing, `beta` forbids it and permits only
        squash — under automatic selection the group no longer shares one
        strategy; each pull request merges with the strategy its own
        repository permits."""
        manifest, wt_a, wt_b = self._setup_two_repos(tmp_path)
        toml = _write_toml(
            tmp_path, "[release]\nauto_merge = true\nmerge_order = ['alpha', 'beta']\n"
        )
        call_log: list[list[str]] = []
        stub = _make_capability_stub(
            capabilities={
                "alpha": {
                    "mergeCommitAllowed": True,
                    "squashMergeAllowed": True,
                    "rebaseMergeAllowed": True,
                },
                "beta": {
                    "mergeCommitAllowed": False,
                    "squashMergeAllowed": True,
                    "rebaseMergeAllowed": False,
                },
            },
            call_log=call_log,
        )
        provider = get_provider("github", runner=stub)
        pr_pairs = [
            PRPair(repo_path=str(wt_a), pr_number="1", member_name="alpha"),
            PRPair(repo_path=str(wt_b), pr_number="2", member_name="beta"),
        ]
        result = provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

        merge_argvs = [c for c in call_log if "pr" in c and "merge" in c]
        assert len(merge_argvs) == 2
        assert "--rebase" in merge_argvs[0]
        assert "--squash" in merge_argvs[1]
        assert set(result["merged"]) == {f"{wt_a}:1", f"{wt_b}:2"}

    def test_disclosure_precedes_each_merge_call_not_batched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The resolved strategy is disclosed on stderr immediately before
        that pull request's own merge call — not after, and not once for
        the whole run."""
        import sys as sys_module

        manifest, wt_a, wt_b = self._setup_two_repos(tmp_path)
        toml = _write_toml(
            tmp_path, "[release]\nauto_merge = true\nmerge_order = ['alpha', 'beta']\n"
        )
        events: list[tuple[str, str]] = []

        class _RecordingStderr:
            def write(self, s: str) -> int:
                if "strategy '" in s:
                    events.append(("disclosure", s))
                return len(s)

            def flush(self) -> None:
                pass

        monkeypatch.setattr(sys_module, "stderr", _RecordingStderr())

        def stub(cmd, **kwargs):
            result = _make_capability_stub()(cmd, **kwargs)
            if "pr" in cmd and "merge" in cmd:
                cwd = kwargs.get("cwd") or ""
                events.append(("merge", Path(cwd).name))
            return result

        provider = get_provider("github", runner=stub)
        pr_pairs = [
            PRPair(repo_path=str(wt_a), pr_number="1", member_name="alpha"),
            PRPair(repo_path=str(wt_b), pr_number="2", member_name="beta"),
        ]
        provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

        merge_indices = [i for i, e in enumerate(events) if e[0] == "merge"]
        assert len(merge_indices) == 2
        for merge_index in merge_indices:
            # a disclosure event immediately precedes each merge event
            assert events[merge_index - 1][0] == "disclosure"
        # not batched: a disclosure for the second PR does not appear before
        # the first PR's own merge call
        first_merge_index = merge_indices[0]
        disclosures_before_first_merge = [
            e for e in events[:first_merge_index] if e[0] == "disclosure"
        ]
        assert len(disclosures_before_first_merge) == 1

    def test_lookup_failure_disclosure_differs_from_other_reasons(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A merge whose strategy came from a failed capability lookup
        discloses that cause, distinguishable from a merge whose strategy
        was resolved for any other reason."""
        from trailhead.vcs.github import RESOLUTION_REASON_PREFIXES

        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [{"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)}],
        )
        toml = _write_toml(tmp_path, "[release]\nauto_merge = true\n")

        stub = _make_capability_stub(null_repos={"alpha"})
        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="9", member_name="alpha")]
        provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

        disclosure = next(
            line
            for line in capsys.readouterr().err.splitlines()
            if RESOLUTION_REASON_PREFIXES["auto_lookup_failed"] in line
        )
        assert RESOLUTION_REASON_PREFIXES["auto_rebase_permitted"] not in disclosure
        assert RESOLUTION_REASON_PREFIXES["auto_sole_permitted"] not in disclosure
        assert RESOLUTION_REASON_PREFIXES["auto_interim_squash"] not in disclosure

    def test_explicit_configured_strategy_performs_no_capability_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A group with an explicit `merge_method` performs no capability
        read at all — the resolver never inspects `permitted` for a
        concretely-configured strategy, so the merge loop must not even
        issue the lookup for that case."""
        import trailhead.vcs.github as gh_module

        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [{"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)}],
        )
        toml = _write_toml(
            tmp_path, '[release]\nauto_merge = true\nmerge_method = "rebase"\n'
        )
        calls: list[tuple] = []
        original = gh_module.get_permitted_merge_strategies

        def _spy(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(gh_module, "get_permitted_merge_strategies", _spy)

        stub = _make_capability_stub()
        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="7", member_name="alpha")]
        result = provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

        assert calls == []
        assert any("7" in m for m in result["merged"])

    def test_one_repository_query_per_pull_request(self, tmp_path: Path) -> None:
        """The whole merge path issues exactly one repository (graphql)
        query per pull request under automatic selection — the capability
        read and the stack-entry read share the fetch a shared cache
        provides, scoped per pull request rather than collapsed to one
        query for the whole invocation."""
        manifest, wt_a, wt_b = self._setup_two_repos(tmp_path)
        toml = _write_toml(
            tmp_path, "[release]\nauto_merge = true\nmerge_order = ['alpha', 'beta']\n"
        )
        call_log: list[list[str]] = []
        stub = _make_capability_stub(call_log=call_log)
        provider = get_provider("github", runner=stub)
        pr_pairs = [
            PRPair(repo_path=str(wt_a), pr_number="1", member_name="alpha"),
            PRPair(repo_path=str(wt_b), pr_number="2", member_name="beta"),
        ]
        provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

        graphql_calls = [c for c in call_log if "graphql" in " ".join(c)]
        assert len(graphql_calls) == 2

    def test_refusal_recorded_as_failure_with_no_second_merge_attempt(
        self, tmp_path: Path
    ) -> None:
        """A merge refused at merge time is recorded as a failure via
        `describe_merge_refusal`, not retried with a different strategy —
        exactly one merge call is made for that pull request."""
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [{"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)}],
        )
        toml = _write_toml(
            tmp_path, '[release]\nauto_merge = true\nmerge_method = "rebase"\n'
        )
        call_log: list[list[str]] = []
        stub = _make_capability_stub(fail_merge_repos={"alpha"}, call_log=call_log)
        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="7", member_name="alpha")]
        result = provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

        merge_argvs = [c for c in call_log if "pr" in c and "merge" in c]
        assert len(merge_argvs) == 1
        failed_msg = next(v for k, v in result["failed"].items() if "7" in k)
        assert failed_msg == "merge-refused: rebase refused — GraphQL: merge blocked"
        assert result["merged"] == []

    def test_refusal_does_not_raise_and_skips_the_remainder(
        self, tmp_path: Path
    ) -> None:
        """No unhandled exception escapes the merge path on a refusal, and
        the remainder of the merge order is skipped exactly as any other
        mid-order failure skips it."""
        manifest, wt_a, wt_b = self._setup_two_repos(tmp_path)
        toml = _write_toml(
            tmp_path, "[release]\nauto_merge = true\nmerge_order = ['alpha', 'beta']\n"
        )
        stub = _make_capability_stub(fail_merge_repos={"alpha"})
        provider = get_provider("github", runner=stub)
        pr_pairs = [
            PRPair(repo_path=str(wt_a), pr_number="1", member_name="alpha"),
            PRPair(repo_path=str(wt_b), pr_number="2", member_name="beta"),
        ]
        result = provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

        assert result["merged"] == []
        assert any("1" in k for k in result["failed"])
        skip_msg = next(v for k, v in result["skipped"].items() if "2" in k)
        assert "blocked by" in skip_msg

    def test_refusal_message_reaches_the_diagnostic_stream(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The refusal message is not merely formatted and discarded — it
        reaches stderr, the same operator-visible surface the disclosure
        itself uses."""
        wt = tmp_path / "wt" / "alpha"
        wt.mkdir(parents=True)
        manifest = _write_manifest(
            tmp_path,
            [{"name": "alpha", "repo_root": str(tmp_path), "worktree_path": str(wt)}],
        )
        toml = _write_toml(
            tmp_path, '[release]\nauto_merge = true\nmerge_method = "squash"\n'
        )
        stub = _make_capability_stub(fail_merge_repos={"alpha"})
        provider = get_provider("github", runner=stub)
        pr_pairs = [PRPair(repo_path=str(wt), pr_number="7", member_name="alpha")]
        provider.pr.merge(pr_pairs, str(manifest), toml_path=str(toml))

        err = capsys.readouterr().err
        assert "merge-refused: squash refused — GraphQL: merge blocked" in err


# ---------------------------------------------------------------------------
# ci.wait (ports wait_for_actionable)
# ---------------------------------------------------------------------------


class TestCiWait:
    def test_returns_immediately_when_actionable(self) -> None:
        view_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "reviews": [],
        }

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
        draft_view = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "isDraft": True,
            "reviews": [],
        }

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

    def test_total_slept_does_not_exceed_timeout(self) -> None:
        """The loop must never sleep past timeout (no overshoot on terminal iteration)."""
        draft_view = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "isDraft": True,
            "reviews": [],
        }
        slept: list[float] = []

        def fake_sleep(secs: float) -> None:
            slept.append(secs)

        def stub(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "checks" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            if "pr" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(draft_view), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        import trailhead.vcs.github as gh_module

        original_sleep = gh_module.time.sleep
        gh_module.time.sleep = fake_sleep  # type: ignore[method-assign]
        try:
            provider = get_provider("github", runner=stub)
            provider.ci.wait([("some/path", "1")], timeout=100, interval=30)
        finally:
            gh_module.time.sleep = original_sleep

        assert slept, "expected at least one sleep call"
        assert sum(slept) <= 100, (
            f"total slept ({sum(slept)}) exceeded timeout (100); slept={slept}"
        )

    def test_wait_routes_evaluate_through_pr_surface(self) -> None:
        """ci.wait must call self._pr.evaluate so a subclass override is honoured."""
        from trailhead.vcs.github import _GitHubCI, _GitHubPR

        evaluate_calls: list[dict] = []

        class _CapturingPR(_GitHubPR):
            def evaluate(self, status, *, review_bot_login=None, fail_count=0):
                evaluate_calls.append({"status": status, "review_bot_login": review_bot_login})
                # Return actionable immediately so the loop exits after one iteration.
                return {"action": "done", "reason": "captured"}

        view_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "reviews": [],
        }

        def stub(cmd, **kw):
            cmd_str = " ".join(cmd)
            if "checks" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            if "pr" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(view_data), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        pr = _CapturingPR(stub)
        ci = _GitHubCI(stub, pr)
        result = ci.wait([("some/path", "42")], timeout=60, interval=5)

        assert evaluate_calls, "ci.wait did not route through self._pr.evaluate"
        assert result.get("timeout") is not True
