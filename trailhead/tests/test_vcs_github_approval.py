"""Tests for GitHubProvider pr.approval — the human-approval merge gate check.

Answers "does this PR carry a human-authored approval signal?": an approving
review by a `User` (non-bot) reviewer, OR (self-authored PRs — the drain's
normal case, since GitHub 422s self-approval) the `human-approved` label
applied by a `User` actor per the timeline API. All gh/git calls go through
an injected stub runner — zero network.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from trailhead.vcs import get_provider


def _make_stub(reviews: list[dict] | None = None, timeline: list[dict] | None = None, fail_reviews=False, fail_timeline=False):
    def stub(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        cmd_str = " ".join(cmd)
        if "remote" in cmd_str and "get-url" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "git@github.com:acme/widgets.git\n", "")
        if "api" in cmd_str and "/reviews" in cmd_str:
            if fail_reviews:
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(reviews or []), "")
        if "api" in cmd_str and "/timeline" in cmd_str:
            if fail_timeline:
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(timeline or []), "")
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    return stub


def _review(login: str, user_type: str, state: str) -> dict[str, Any]:
    return {"user": {"login": login, "type": user_type}, "state": state}


def _labeled(actor_login: str, actor_type: str, created_at: str, app=None, name="human-approved") -> dict[str, Any]:
    return {
        "event": "labeled",
        "label": {"name": name},
        "actor": {"login": actor_login, "type": actor_type},
        "created_at": created_at,
        "performed_via_github_app": app,
    }


def _unlabeled(actor_login: str, actor_type: str, created_at: str, name="human-approved") -> dict[str, Any]:
    return {
        "event": "unlabeled",
        "label": {"name": name},
        "actor": {"login": actor_login, "type": actor_type},
        "created_at": created_at,
        "performed_via_github_app": None,
    }


class TestApprovalReviewPath:
    def test_human_approving_review_is_approved(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(reviews=[_review("tom", "User", "APPROVED")]),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is True
        assert result["source"] == "review"

    def test_bot_authored_review_is_not_approved(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(reviews=[_review("dependabot[bot]", "Bot", "APPROVED")]),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is False

    def test_no_reviews_and_no_label_is_not_approved(self) -> None:
        provider = get_provider("github", runner=_make_stub(reviews=[], timeline=[]))
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is False
        assert result["source"] is None


class TestApprovalLabelPath:
    def test_label_applied_by_user_actor_is_approved(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[],
                timeline=[_labeled("tom", "User", "2026-08-01T00:00:00Z")],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is True
        assert result["source"] == "label"
        assert result["actor"] == "tom"

    def test_label_applied_by_bot_actor_is_not_approved(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[],
                timeline=[_labeled("ranger-bot", "Bot", "2026-08-01T00:00:00Z")],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is False

    def test_label_applied_via_github_app_is_not_approved(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[],
                timeline=[
                    _labeled("tom", "User", "2026-08-01T00:00:00Z", app={"id": 1, "slug": "some-app"})
                ],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is False

    def test_last_event_wins_when_label_reapplied(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[],
                timeline=[
                    _labeled("tom", "User", "2026-08-01T00:00:00Z"),
                    _unlabeled("tom", "User", "2026-08-02T00:00:00Z"),
                ],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is False

    def test_out_of_order_events_sorted_by_created_at(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[],
                timeline=[
                    _unlabeled("tom", "User", "2026-08-01T00:00:00Z"),
                    _labeled("tom", "User", "2026-08-02T00:00:00Z"),
                ],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is True


class TestApprovalApiError:
    def test_reviews_fetch_failure_raises(self) -> None:
        provider = get_provider("github", runner=_make_stub(fail_reviews=True))
        with pytest.raises(RuntimeError):
            provider.pr.approval("some/path", "42")

    def test_timeline_fetch_failure_raises(self) -> None:
        provider = get_provider("github", runner=_make_stub(reviews=[], fail_timeline=True))
        with pytest.raises(RuntimeError):
            provider.pr.approval("some/path", "42")
