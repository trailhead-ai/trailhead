"""Contract pin: the merge-policy prose surfaces.

`[release] auto_merge = true` is the operator's standing authorization to
merge: monitor merges a `done` PR without gating on human approval. With
`auto_merge` unset/false, nothing merges automatically — `portage merge`
refuses fail-closed and the operator merges by hand. The C3 council pin
survives in narrowed form: no drain, portage, or dispatched-agent component
ever applies the approval signal (the signal no longer gates portage's
merges, but branch protection or an operator's own review may still consume
it). These are prose-only invariants (nothing but agent adherence enforces
them at runtime), so a pinned literal is the only thing keeping them from
silently drifting.

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


def test_monitor_pins_auto_merge_as_the_merge_gate():
    _pin(
        MONITOR,
        "`[release] auto_merge = true` in the group TOML is the operator's standing authorization to merge.",
        "auto_merge carrying the merge authorization is the load-bearing policy statement.",
    )
    _pin(
        MONITOR,
        "monitor does not gate on human approval",
        "with auto_merge enabled, the human-approval gate must be explicitly absent, "
        "not merely undocumented — otherwise the old gate silently reasserts itself.",
    )


def test_monitor_pins_the_fail_closed_disabled_branch():
    _pin(
        MONITOR,
        "nothing merges automatically",
        "auto_merge unset/false must keep today's fail-closed behavior: no automatic "
        "merge of any kind; the operator merges by hand.",
    )


def test_monitor_never_applies_the_approval_signal_itself():
    _pin(
        MONITOR,
        "Monitor never applies the approval signal itself.",
        "the narrowed C3 pin — the signal no longer gates portage's merges, but no "
        "drain/portage/agent component ever fabricates it.",
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


def test_operator_rituals_pin_the_auto_merge_policy():
    rituals = (
        _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger" / "skills" / "execute"
        / "operator-rituals.md"
    )
    _pin(
        rituals,
        "monitor merges a `done` PR without any human-approval check",
        "the operator rituals must describe the same merge policy monitor enforces, or "
        "the operator is sent hunting for an approval gate that no longer exists.",
    )


@pytest.mark.parametrize("path", [UPDATER, GREEN_DRIVER])
def test_pr_touching_agent_docs_never_apply_the_approval_signal(path):
    _pin(
        path,
        "Don't apply the `human-approved` label or post an approving review on any PR",
        "every PR-touching portage agent doc must carry the never-apply-the-signal prohibition, "
        "not just monitor.",
    )


def test_updater_pins_the_gpgsig_presence_preflight_rule():
    _pin(
        UPDATER,
        "confirm the commit carries a `gpgsig` header via",
        "the preflight must check signature presence via the gpgsig header directly, "
        "not a local-verification proxy.",
    )
    _pin(
        UPDATER,
        "Never use `git log --pretty=%G?` for",
        "the prohibition on %G? must stay verbatim — it read as a signature check but is "
        "a local-verifiability check, which false-negates properly signed commits.",
    )
    _pin(
        UPDATER,
        "so it reports `N`/`E` even for properly signed commits",
        "the false-negative rationale must stay pinned — it's what makes the %G? "
        "prohibition non-optional rather than a style preference.",
    )
