"""Contract pin: the human-approval merge gate's prose surfaces.

`monitor` gates `portage merge` on `portage approvals`; absent approval it must
hold and report `ready-awaiting-human-approval`, never merge. Council C3 pin:
no drain, portage, or dispatched-agent component ever applies the approval
signal — the signal is human-applied only. These are prose-only invariants
(nothing but agent adherence enforces them at runtime), so a pinned literal is
the only thing keeping them from silently drifting.

Every pinned span is asserted as a contiguous substring within one physical
line, per the wrap-safety lesson the ranger sweep's own pin harness encodes
([[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]]).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PORTAGE_AGENTS = _REPO_ROOT / "tools" / "portage" / "plugins" / "portage" / "agents"

MONITOR = _PORTAGE_AGENTS / "monitor.md"
UPDATER = _PORTAGE_AGENTS / "updater.md"
GREEN_DRIVER = _PORTAGE_AGENTS / "green-driver.md"


def _pin(path: Path, phrase: str, why: str) -> None:
    """Assert *phrase* appears inside a single physical line of *path*."""
    text = path.read_text()
    if any(phrase in line for line in text.splitlines()):
        return
    if phrase in " ".join(text.split()):
        pytest.fail(
            f"{path.name}: the pinned span {phrase!r} is present but straddles a line "
            f"wrap — keep it on one physical line. {why}"
        )
    pytest.fail(f"{path.name}: missing the pinned span {phrase!r}. {why}")


def test_monitor_gates_merge_on_approvals_check():
    _pin(
        MONITOR,
        "Before calling `portage merge` on any PR, check its human-authored approval signal:",
        "the merge gate must run before every merge call, not just be documented nearby.",
    )
    _pin(
        MONITOR,
        "Monitor never merges a PR without a passing `portage approvals` check.",
        "the never-merge-without-approval rule must be pinned verbatim so it can't erode.",
    )


def test_monitor_holds_and_reports_awaiting_human_approval():
    _pin(
        MONITOR,
        "report it as `ready-awaiting-human-approval`",
        "absent approval, monitor must hold and surface this exact token, never merge.",
    )


def test_monitor_treats_a_stale_approval_as_its_own_outcome():
    # An approval approves a commit. GitHub dismisses neither a review nor a
    # label on a push, so without this the mainline attack is: get approved at
    # commit A, push commit B, merge B.
    _pin(
        MONITOR,
        "**The approval is pinned to the commit it was given on.**",
        "the commit-pinning rule is what makes the gate mean anything after a push.",
    )
    _pin(
        MONITOR,
        "`ready-awaiting-human-approval` with a **(stale)** note",
        "stale is a distinct outcome from never-approved; the operator's remedy differs "
        "(re-approve after reviewing the new commits, not go find an approver).",
    )
    _pin(
        MONITOR,
        'exits 1 with `"stale": true`',
        "the exit contract must be pinned: stale stays inside the not-approved family, so "
        "a caller gating on exit 0 holds either way.",
    )


def test_operator_rituals_tell_the_operator_to_re_approve_after_a_fix_cycle():
    rituals = (
        _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger" / "skills" / "execute"
        / "operator-rituals.md"
    )
    _pin(
        rituals,
        "after every fix cycle on an approved PR, re-approve it",
        "a fix cycle pushes new commits, which makes the standing approval stale by "
        "construction — the operator has to be told that re-approval is now part of the "
        "ritual, or every fixed PR silently stalls at the gate.",
    )


def test_monitor_never_applies_the_approval_signal_itself():
    _pin(
        MONITOR,
        "Monitor never applies the approval signal itself.",
        "the C3 council pin — no drain/portage/agent component ever applies the signal.",
    )
    _pin(
        MONITOR,
        "no drain, portage, or dispatched-agent component",
        "the prohibition's scope (drain/portage/dispatched-agent) must stay verbatim.",
    )


def test_monitor_anti_pattern_pins_the_prohibition_and_the_manual_bypass_residual():
    _pin(
        MONITOR,
        "Don't apply the `human-approved` label or post an approving review yourself",
        "the anti-patterns list must carry the same prohibition, not just the prose above.",
    )
    _pin(
        MONITOR,
        "that residual is accepted as risk",
        "the manual-bypass weakness (operator-credential self-approval) must be documented.",
    )


@pytest.mark.parametrize("path", [UPDATER, GREEN_DRIVER])
def test_pr_touching_agent_docs_never_apply_the_approval_signal(path):
    _pin(
        path,
        "Don't apply the `human-approved` label or post an approving review on any PR",
        "every PR-touching portage agent doc must carry the never-apply-the-signal prohibition, "
        "not just monitor.",
    )
