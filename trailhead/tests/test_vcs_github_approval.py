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


def _review(login: str, user_type: str, state: str, submitted_at: str = "") -> dict[str, Any]:
    return {
        "user": {"login": login, "type": user_type},
        "state": state,
        "submitted_at": submitted_at,
    }


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


class TestApprovalReviewRecency:
    """A reviewer's *latest* decisive review is the one that counts.

    GitHub's reviews API returns every review a PR ever collected, so an
    approval a reviewer later withdrew is still in the list. Evaluating each
    reviewer's latest decisive state mirrors the label path's
    last-event-wins rule; without it a `CHANGES_REQUESTED` or a dismissal is
    silently outranked by the stale `APPROVED` that preceded it, and the
    merge gate opens on an approval its author has already taken back.
    """

    def test_later_changes_requested_supersedes_an_earlier_approval(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[
                    _review("tom", "User", "APPROVED", "2026-08-01T00:00:00Z"),
                    _review("tom", "User", "CHANGES_REQUESTED", "2026-08-02T00:00:00Z"),
                ],
                timeline=[],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is False

    def test_later_approval_supersedes_an_earlier_changes_requested(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[
                    _review("tom", "User", "CHANGES_REQUESTED", "2026-08-01T00:00:00Z"),
                    _review("tom", "User", "APPROVED", "2026-08-02T00:00:00Z"),
                ],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is True
        assert result["actor"] == "tom"

    def test_a_dismissed_approval_no_longer_counts(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[
                    _review("tom", "User", "APPROVED", "2026-08-01T00:00:00Z"),
                    _review("tom", "User", "DISMISSED", "2026-08-02T00:00:00Z"),
                ],
                timeline=[],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is False

    def test_another_reviewers_standing_approval_still_counts(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[
                    _review("tom", "User", "APPROVED", "2026-08-01T00:00:00Z"),
                    _review("tom", "User", "CHANGES_REQUESTED", "2026-08-02T00:00:00Z"),
                    _review("ada", "User", "APPROVED", "2026-08-03T00:00:00Z"),
                ],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is True
        assert result["actor"] == "ada"

    def test_a_later_comment_does_not_withdraw_an_approval(self) -> None:
        # COMMENTED is not a decisive state — GitHub keeps the standing
        # approval when the same reviewer later leaves a plain comment.
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[
                    _review("tom", "User", "APPROVED", "2026-08-01T00:00:00Z"),
                    _review("tom", "User", "COMMENTED", "2026-08-02T00:00:00Z"),
                ],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is True

    def test_out_of_order_reviews_are_sorted_by_submitted_at(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[
                    _review("tom", "User", "CHANGES_REQUESTED", "2026-08-02T00:00:00Z"),
                    _review("tom", "User", "APPROVED", "2026-08-01T00:00:00Z"),
                ],
                timeline=[],
            ),
        )
        result = provider.pr.approval("some/path", "42")
        assert result["approved"] is False


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
