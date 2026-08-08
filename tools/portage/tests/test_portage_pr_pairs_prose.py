"""Contract pin: `pr_pairs` is 3-field (`repo:pr:member`) across every prose surface.

`updater` produces the `pr_pairs` line; `pull_request/SKILL.md` (the caller) and
`monitor` (the consumer) parse it. All three are prose, so nothing but a pinned
literal keeps them from silently drifting back to a 2-field form the CLI now
loudly refuses (`portage/cli/pr.py::cmd_merge`) — a drift here reads as fine
right up until `merge_order` keying is silently guessed wrong from a repo-path
basename instead of the manifest's real member name.

Every pinned span is asserted as a contiguous substring within one physical
line, per the wrap-safety lesson the ranger sweep's own pin harness encodes
([[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]]).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "portage" / "plugins" / "portage"

UPDATER = _PLUGIN_DIR / "agents" / "updater.md"
MONITOR = _PLUGIN_DIR / "agents" / "monitor.md"
SKILL = _PLUGIN_DIR / "skills" / "pull_request" / "SKILL.md"


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


def test_updater_report_line_spec_emits_three_field_pairs():
    _pin(
        UPDATER,
        "**pr_pairs:** `<repo1_path>:<pr1>:<member1>, <repo2_path>:<pr2>:<member2>, ...`",
        "updater's report-line spec is the sole producer of pr_pairs — a 2-field "
        "regression here silently starves every downstream 3-field consumer.",
    )
    _pin(
        UPDATER,
        "Build a comma-separated list of `repo:pr:member` pairs",
        "the prose instructing the agent what shape to build must itself say 3-field.",
    )


def test_skill_pr_pairs_line_asserts_three_field_shape():
    _pin(
        SKILL,
        "`<repo_path>:<pr_number>:<member_name>`. Parse that line",
        "the skill's caller-side parsing instructions must still expect 3 fields.",
    )


def test_monitor_pr_pairs_input_asserts_three_field_shape():
    _pin(
        MONITOR,
        "`pr_pairs` — comma-separated `<repo_path>:<pr_number>:<member_name>` list",
        "monitor's documented input contract must still expect 3 fields.",
    )
