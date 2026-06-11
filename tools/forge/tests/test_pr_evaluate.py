"""Tests for the PR actionability evaluator trio (D-3, U-3, B-3).

Scripts: pr_evaluate_status.py + check_pr_status.py + wait_for_actionable.py
Re-homed from zenith github-pr/scripts/. Cortana-zh stripped; review_bot_login
is an optional TOML config (D-3), inert by default.

Contract:
  - No review_bot_login configured → evaluator is CI-only (no bot review path).
  - With review_bot_login configured → a matching bot comment yields 'review'.
  - done / rebase / fix_ci / rerun_ci / wait from CI state (no bot).
  - check_pr_status.py called by pr_evaluate_status.py via injectable runner (B-3).
  - wait_for_actionable.py polls pr_evaluate_status.py via injectable runner.
  - No hardcoded 'cortana' / 'cortana-zh' in any re-homed file.

Import pattern: sys.path.insert(SCRIPTS_DIR).
All gh calls stubbed — no real gh, no network.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pr_evaluate_status as pev  # noqa: E402
import check_pr_status as cps  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — build stub gh responses
# ---------------------------------------------------------------------------

def _pr_gh_view_payload(
    mergeable: str = "MERGEABLE",
    merge_state: str = "CLEAN",
    is_draft: bool = False,
    reviews: list[dict] | None = None,
) -> dict[str, Any]:
    """Payload for 'gh pr view --json mergeable,mergeStateStatus,isDraft,reviews'."""
    return {
        "mergeable": mergeable,
        "mergeStateStatus": merge_state,
        "isDraft": is_draft,
        "reviews": reviews or [],
    }


def _pr_payload(
    mergeable: str = "MERGEABLE",
    merge_state: str = "CLEAN",
    is_draft: bool = False,
    failing_checks: list[dict] | None = None,
    reviews: list[dict] | None = None,
) -> dict[str, Any]:
    """Payload matching the output of check_pr_status.check_status()."""
    result: dict[str, Any] = {
        "mergeable": mergeable,
        "mergeStateStatus": merge_state,
        "isDraft": is_draft,
        "failingChecks": failing_checks or [],
    }
    if reviews is not None:
        result["botReviews"] = reviews
    return result


def _make_gh_stub(
    view_payload: dict,
    checks_payload: list[dict] | None = None,
) -> callable:
    """Stub for gh pr view and gh pr checks calls (separate payloads)."""
    def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        cmd_str = " ".join(cmd)
        if "pr" in cmd_str and "checks" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(checks_payload or []), "")
        if "pr" in cmd_str and "view" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(view_payload), "")
        # git remote get-url origin
        if "remote" in cmd_str and "get-url" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "[]", "")
    return stub


def _make_check_stub(payload: dict) -> callable:
    """Stub that returns a fixed check_pr_status.check_status() output.

    This stub makes check_status return the payload directly by making all gh
    calls return a consistent structure.
    """
    view_data = {
        "mergeable": payload.get("mergeable", "MERGEABLE"),
        "mergeStateStatus": payload.get("mergeStateStatus", "CLEAN"),
        "isDraft": payload.get("isDraft", False),
        "reviews": [
            {"author": {"login": r.get("login", "")}, **{k: v for k, v in r.items() if k != "login"}}
            for r in payload.get("botReviews", [])
        ],
    }
    checks_data = payload.get("failingChecks", [])
    return _make_gh_stub(view_data, checks_data)


# ---------------------------------------------------------------------------
# check_pr_status tests (the innermost script)
# ---------------------------------------------------------------------------


class TestCheckPrStatus:
    def test_done_when_mergeable_and_clean(self) -> None:
        payload = _pr_payload("MERGEABLE", "CLEAN")
        stub = _make_check_stub(payload)
        result = cps.check_status("some/path", "42", runner=stub)
        assert result["mergeable"] == "MERGEABLE"
        assert result["mergeStateStatus"] == "CLEAN"
        assert result["failingChecks"] == []

    def test_no_cortana_key_in_output(self) -> None:
        payload = _pr_payload("MERGEABLE", "CLEAN")
        stub = _make_check_stub(payload)
        result = cps.check_status("some/path", "42", runner=stub)
        assert "cortanaReviews" not in result
        assert "cortana" not in json.dumps(result).lower()

    def test_bot_reviews_key_present_when_configured(self) -> None:
        view_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "BLOCKED",
            "isDraft": False,
            "reviews": [
                {"author": {"login": "my-bot"}, "state": "CHANGES_REQUESTED", "body": "fix this"},
            ],
        }
        stub = _make_gh_stub(view_data, [])
        result = cps.check_status("some/path", "42", runner=stub, review_bot_login="my-bot")
        assert "botReviews" in result
        assert len(result["botReviews"]) == 1


# ---------------------------------------------------------------------------
# pr_evaluate_status tests (the middle layer)
# ---------------------------------------------------------------------------


class TestPrEvaluateStatus:
    def test_done_on_mergeable_clean(self) -> None:
        status = _pr_payload("MERGEABLE", "CLEAN")
        result = pev.evaluate(status, review_bot_login=None)
        assert result["action"] == "done"

    def test_rebase_on_conflicting(self) -> None:
        status = _pr_payload("CONFLICTING", "DIRTY")
        result = pev.evaluate(status, review_bot_login=None)
        assert result["action"] == "rebase"

    def test_rerun_ci_on_failing_no_annotations(self) -> None:
        status = _pr_payload(
            "MERGEABLE", "BLOCKED",
            failing_checks=[{"name": "tests", "state": "FAILURE", "link": "https://github.com/o/r/actions/runs/123/job/456", "annotations": []}],
        )
        result = pev.evaluate(status, review_bot_login=None)
        assert result["action"] in ("rerun_ci", "fix_ci")

    def test_fix_ci_on_failing_with_annotations(self) -> None:
        status = _pr_payload(
            "MERGEABLE", "BLOCKED",
            failing_checks=[{
                "name": "tests", "state": "FAILURE",
                "link": "https://github.com/o/r/actions/runs/99",
                "annotations": [{"path": "lib/foo.py", "start_line": 1, "message": "err"}],
            }],
        )
        result = pev.evaluate(status, review_bot_login=None, fail_count=0)
        assert result["action"] == "fix_ci"

    def test_wait_on_draft(self) -> None:
        status = _pr_payload("MERGEABLE", "CLEAN", is_draft=True)
        result = pev.evaluate(status, review_bot_login=None)
        assert result["action"] == "wait"

    def test_wait_on_ci_pending(self) -> None:
        status = _pr_payload("UNKNOWN", "PENDING")
        result = pev.evaluate(status, review_bot_login=None)
        assert result["action"] == "wait"

    # U-3: no review_bot_login → CI-only, bot comments don't yield 'review'
    def test_no_bot_configured_ignores_reviews(self) -> None:
        # botReviews absent (no review_bot_login) → CI says CLEAN → done
        status = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "failingChecks": [],
            # no botReviews key (because review_bot_login was None in check_status)
        }
        result = pev.evaluate(status, review_bot_login=None)
        assert result["action"] == "done"

    # U-3: with review_bot_login configured → bot comment yields 'review'
    def test_with_bot_configured_changes_requested_yields_review(self) -> None:
        # botReviews is pre-filtered by check_status to the configured bot's reviews
        status = _pr_payload(
            "MERGEABLE", "CLEAN",
            reviews=[{"state": "CHANGES_REQUESTED", "body": "fix this"}],
        )
        result = pev.evaluate(status, review_bot_login="my-review-bot")
        assert result["action"] == "review"

    def test_with_bot_configured_wrong_login_filtered_by_check_status(self) -> None:
        """check_status filters reviews to only the configured bot — wrong login absent."""
        view_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "reviews": [
                {"author": {"login": "other-bot"}, "state": "CHANGES_REQUESTED", "body": "x"},
            ],
        }
        stub = _make_gh_stub(view_data, [])
        result = cps.check_status("some/path", "42", runner=stub, review_bot_login="my-review-bot")
        # other-bot not in botReviews because login doesn't match
        assert result.get("botReviews", []) == []

    def test_no_cortana_in_source(self) -> None:
        src = SCRIPTS_DIR / "pr_evaluate_status.py"
        text = src.read_text()
        assert "cortana" not in text.lower()

    def test_no_cortana_in_check_status_source(self) -> None:
        src = SCRIPTS_DIR / "check_pr_status.py"
        text = src.read_text()
        assert "cortana" not in text.lower()


# ---------------------------------------------------------------------------
# Runner injection: B-3 — pr_evaluate_status calls check via injectable runner
# ---------------------------------------------------------------------------


class TestPrEvaluateRunnerInjection:
    def test_stub_receives_check_subcommand(self) -> None:
        """The evaluate pipeline calls the check script via the injectable runner."""
        calls: list[list[str]] = []
        view_data = {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False, "reviews": []}

        def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
            calls.append(list(cmd))
            cmd_str = " ".join(cmd)
            if "pr" in cmd_str and "checks" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            if "pr" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(view_data), "")
            if "remote" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        pev.evaluate_with_check(
            repo_path="some/path",
            pr_number="77",
            review_bot_login=None,
            runner=stub,
        )
        # At least one call should contain "gh" for the pr view/checks calls
        combined = " ".join(" ".join(c) for c in calls)
        assert "gh" in combined


# ---------------------------------------------------------------------------
# wait_for_actionable tests
# ---------------------------------------------------------------------------


class TestWaitForActionable:
    def test_returns_immediately_when_actionable(self) -> None:
        import wait_for_actionable as wfa

        view_data = {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False, "reviews": []}

        def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
            cmd_str = " ".join(cmd)
            if "checks" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            if "pr" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(view_data), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        result = wfa.wait(
            pr_pairs=[("some/path", "42")],
            review_bot_login=None,
            timeout=5,
            interval=1,
            runner=stub,
        )
        assert "42" in str(result["actionable"])
        assert result.get("timeout") is not True

    def test_times_out_when_always_waiting(self) -> None:
        import wait_for_actionable as wfa

        draft_view = {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": True, "reviews": []}

        def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
            cmd_str = " ".join(cmd)
            if "checks" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            if "pr" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(draft_view), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        result = wfa.wait(
            pr_pairs=[("some/path", "1")],
            review_bot_login=None,
            timeout=2,
            interval=1,
            runner=stub,
        )
        assert result.get("timeout") is True

    def test_no_cortana_in_source(self) -> None:
        src = SCRIPTS_DIR / "wait_for_actionable.py"
        text = src.read_text()
        assert "cortana" not in text.lower()
