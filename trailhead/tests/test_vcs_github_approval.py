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


#: The stub PR's current head commit, and when it was pushed. An approval
#: signal counts only if it is pinned to this SHA (a review) or postdates this
#: timestamp (a label).
_HEAD_SHA = "headsha1"
_HEAD_DATE = "2026-07-01T00:00:00Z"


def _make_stub(
    reviews: list[dict] | None = None,
    timeline: list[dict] | None = None,
    fail_reviews=False,
    fail_timeline=False,
    fail_pr=False,
    fail_commits=False,
    head_sha: str = _HEAD_SHA,
    head_date: str = _HEAD_DATE,
    commits: list[dict] | None = None,
):
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
        if "api" in cmd_str and "/commits" in cmd_str:
            if fail_commits:
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            payload = commits if commits is not None else [_commit(head_sha, head_date)]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        if "api" in cmd_str and "/pulls/" in cmd_str:
            if fail_pr:
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"head": {"sha": head_sha}}), ""
            )
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    return stub


def _commit(sha: str, date: str) -> dict[str, Any]:
    return {"sha": sha, "commit": {"committer": {"date": date}}}


def _review(
    login: str,
    user_type: str,
    state: str,
    submitted_at: str = "",
    commit_id: str = _HEAD_SHA,
) -> dict[str, Any]:
    return {
        "user": {"login": login, "type": user_type},
        "state": state,
        "submitted_at": submitted_at,
        "commit_id": commit_id,
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
                timeline=[_labeled("review-bot", "Bot", "2026-08-01T00:00:00Z")],
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


class TestApprovalIsPinnedToTheHeadCommit:
    """An approval approves a commit, not a pull request.

    GitHub does not dismiss reviews on a new push unless branch protection
    says so, and it never un-applies a label. Without pinning, the mainline
    attack is trivial: get approved at commit A, push commit B, merge
    whatever B contains. So a review counts only when its `commit_id` is the
    PR's current head, and a label counts only when it was applied after the
    head commit was pushed. Neither is a refusal — the signal exists, it is
    simply stale, and the operator's answer is to re-approve after reviewing
    the new commits.
    """

    def test_approve_at_a_then_push_b_is_stale_not_approved(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[_review("tom", "User", "APPROVED", "2026-08-01T00:00:00Z",
                                 commit_id="commitA")],
                timeline=[],
                head_sha="commitB",
                head_date="2026-08-02T00:00:00Z",
            ),
        )

        result = provider.pr.approval("some/path", "42")

        assert result["approved"] is False
        assert result["stale"] is True
        assert result["source"] == "review"
        assert result["actor"] == "tom"
        assert result["head_sha"] == "commitB"
        assert result["signal_sha"] == "commitA"

    def test_a_review_on_the_head_commit_is_approved(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[_review("tom", "User", "APPROVED", commit_id="commitB")],
                head_sha="commitB",
            ),
        )

        result = provider.pr.approval("some/path", "42")

        assert result["approved"] is True
        assert result["stale"] is False

    def test_a_second_reviewer_at_head_outranks_a_stale_one(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[
                    _review("tom", "User", "APPROVED", "2026-08-01T00:00:00Z",
                            commit_id="commitA"),
                    _review("ada", "User", "APPROVED", "2026-08-03T00:00:00Z",
                            commit_id="commitB"),
                ],
                head_sha="commitB",
            ),
        )

        result = provider.pr.approval("some/path", "42")

        assert result["approved"] is True
        assert result["actor"] == "ada"

    def test_a_label_applied_before_the_head_commit_is_stale(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[],
                timeline=[_labeled("tom", "User", "2026-08-01T00:00:00Z")],
                head_sha="commitB",
                head_date="2026-08-02T00:00:00Z",
            ),
        )

        result = provider.pr.approval("some/path", "42")

        assert result["approved"] is False
        assert result["stale"] is True
        assert result["source"] == "label"
        assert result["actor"] == "tom"

    def test_a_label_applied_after_the_head_commit_is_approved(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[],
                timeline=[_labeled("tom", "User", "2026-08-03T00:00:00Z")],
                head_sha="commitB",
                head_date="2026-08-02T00:00:00Z",
            ),
        )

        result = provider.pr.approval("some/path", "42")

        assert result["approved"] is True
        assert result["stale"] is False

    def test_a_label_applied_in_the_same_second_as_the_push_is_approved(self) -> None:
        # The boundary is inclusive: a label carrying the head commit's own
        # timestamp was not applied before it, and refusing there would make
        # the gate flap on second-granularity timestamps.
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[],
                timeline=[_labeled("tom", "User", "2026-08-02T00:00:00Z")],
                head_sha="commitB",
                head_date="2026-08-02T00:00:00Z",
            ),
        )

        assert provider.pr.approval("some/path", "42")["approved"] is True

    def test_a_stale_review_and_a_fresh_label_is_approved(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[_review("tom", "User", "APPROVED", commit_id="commitA")],
                timeline=[_labeled("tom", "User", "2026-08-03T00:00:00Z")],
                head_sha="commitB",
                head_date="2026-08-02T00:00:00Z",
            ),
        )

        result = provider.pr.approval("some/path", "42")

        assert result["approved"] is True
        assert result["source"] == "label"

    def test_no_signal_at_all_is_not_stale(self) -> None:
        provider = get_provider("github", runner=_make_stub(reviews=[], timeline=[]))

        result = provider.pr.approval("some/path", "42")

        assert result["approved"] is False
        assert result["stale"] is False
        assert result["source"] is None


class TestApprovalApiError:
    def test_reviews_fetch_failure_raises(self) -> None:
        provider = get_provider("github", runner=_make_stub(fail_reviews=True))
        with pytest.raises(RuntimeError):
            provider.pr.approval("some/path", "42")

    def test_timeline_fetch_failure_raises(self) -> None:
        provider = get_provider("github", runner=_make_stub(reviews=[], fail_timeline=True))
        with pytest.raises(RuntimeError):
            provider.pr.approval("some/path", "42")

    def test_head_sha_fetch_failure_raises(self) -> None:
        # Without the head SHA there is nothing to pin an approval to, and a
        # gate that answers "approved" when it could not read the head is the
        # whole vulnerability. Never answered is not the same as no.
        provider = get_provider("github", runner=_make_stub(fail_pr=True))
        with pytest.raises(RuntimeError):
            provider.pr.approval("some/path", "42")

    def test_a_head_commit_missing_from_the_commits_list_raises(self) -> None:
        provider = get_provider(
            "github",
            runner=_make_stub(
                reviews=[],
                timeline=[_labeled("tom", "User", "2026-08-03T00:00:00Z")],
                head_sha="commitB",
                commits=[_commit("someOtherCommit", "2026-08-01T00:00:00Z")],
            ),
        )
        with pytest.raises(RuntimeError):
            provider.pr.approval("some/path", "42")
