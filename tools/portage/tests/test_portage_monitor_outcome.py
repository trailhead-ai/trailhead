"""Unit tests for `portage.monitor_outcome` — monitor's outcome-file grammar."""

from __future__ import annotations

from pathlib import Path

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from portage.monitor_outcome import (
    MONITOR_OUTCOME_TOKENS,
    parse_monitor_outcome,
    read_monitor_outcome,
)


def test_tokens_are_the_four_terminal_states():
    assert MONITOR_OUTCOME_TOKENS == frozenset({"MERGED", "READY", "BLOCKED", "STOPPED"})


def test_parses_merged_with_no_argument():
    assert parse_monitor_outcome("MERGED") == ("MERGED", "")


def test_parses_ready_with_its_reason():
    assert parse_monitor_outcome("READY awaiting-human-approval") == (
        "READY",
        "awaiting-human-approval",
    )


def test_parses_blocked_with_its_reason():
    assert parse_monitor_outcome("BLOCKED 3 fix cycles without progress") == (
        "BLOCKED",
        "3 fix cycles without progress",
    )


def test_parses_stopped_with_its_reason():
    assert parse_monitor_outcome("STOPPED auto_merge disabled") == (
        "STOPPED",
        "auto_merge disabled",
    )


def test_unrecognized_token_is_refused():
    token, argument = parse_monitor_outcome("FOO bar")
    assert token is None
    assert argument == "FOO bar"


def test_argument_requiring_token_missing_its_argument_is_refused():
    token, argument = parse_monitor_outcome("READY")
    assert token is None
    assert argument == "READY"


def test_only_the_first_line_is_parsed():
    assert parse_monitor_outcome("MERGED\nextra junk after the first line") == ("MERGED", "")


def test_empty_line_is_refused():
    token, argument = parse_monitor_outcome("")
    assert token is None
    assert argument == ""


def test_read_missing_file_synthesizes_a_failure(tmp_path):
    path = tmp_path / "does-not-exist.outcome"
    result = read_monitor_outcome(path)
    token, argument = parse_monitor_outcome(result)
    assert token == "BLOCKED"
    assert "no outcome written" in argument


def test_read_empty_file_synthesizes_a_failure(tmp_path):
    path = tmp_path / "empty.outcome"
    path.write_text("")
    result = read_monitor_outcome(path)
    token, argument = parse_monitor_outcome(result)
    assert token == "BLOCKED"
    assert "empty outcome file" in argument


def test_read_existing_file_returns_its_contents(tmp_path):
    path = tmp_path / "merged.outcome"
    path.write_text("MERGED\n")
    assert read_monitor_outcome(path) == "MERGED\n"
