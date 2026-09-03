"""Contract pin: monitor's machine-readable completion channel.

`monitor` optionally takes an `outcome_file` path from its dispatcher and, on
reaching a terminal state, writes exactly one line naming that state, so a
caller that cannot wait on monitor's prose reply can poll for its result
instead. These are prose-only invariants (nothing but agent
adherence enforces them at runtime), so a pinned literal is the only thing
keeping them from silently drifting.

Every pinned span is asserted as a contiguous substring within one physical
line, per the wrap-safety lesson at
[[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]].
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PORTAGE_AGENTS = _REPO_ROOT / "tools" / "portage" / "plugins" / "portage" / "agents"
_PORTAGE_SKILLS = _REPO_ROOT / "tools" / "portage" / "plugins" / "portage" / "skills"

MONITOR = _PORTAGE_AGENTS / "monitor.md"
PULL_REQUEST_SKILL = _PORTAGE_SKILLS / "pull_request" / "SKILL.md"


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


def test_monitor_names_the_outcome_file_input_parameter():
    _pin(
        MONITOR,
        "`outcome_file`",
        "the outcome-file input parameter must be named so a dispatcher knows what to pass.",
    )


def test_monitor_writes_exactly_one_line():
    _pin(
        MONITOR,
        "writes exactly one line",
        "the exactly-one-line write contract must be pinned — a multi-line write breaks a "
        "caller that reads the file with a single read.",
    )


def test_monitor_outcome_grammar_is_pinned():
    _pin(MONITOR, "`MERGED`", "the MERGED token must be pinned.")
    _pin(MONITOR, "`READY <reason>`", "the READY <reason> token must be pinned.")
    _pin(MONITOR, "`BLOCKED <reason>`", "the BLOCKED <reason> token must be pinned.")
    _pin(MONITOR, "`STOPPED <reason>`", "the STOPPED <reason> token must be pinned.")


def test_monitor_writes_outcome_only_at_terminal_state():
    _pin(
        MONITOR,
        "writes the outcome file only once it reaches a terminal state",
        "the write-at-terminal-state-only contract must be pinned — a mid-loop write would "
        "let a caller observe a non-final result.",
    )


def test_monitor_uses_the_callers_path_verbatim_with_no_mkdir():
    _pin(
        MONITOR,
        "uses the path verbatim and never creates its parent directory",
        "the caller pre-creates the outcome-file directory (mode 0700) — monitor "
        "must not mkdir it.",
    )


def test_pull_request_skill_documents_the_outcome_file_dispatch_param():
    _pin(
        PULL_REQUEST_SKILL,
        "outcome_file",
        "the dispatch contract must document how a caller hands monitor an outcome-file path.",
    )
