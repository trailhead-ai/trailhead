"""Unit tests for `portage.monitor_outcome` — monitor's outcome-file grammar."""

from __future__ import annotations

import re

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from portage.monitor_outcome import (
    StrategyDisclosure,
    parse_monitor_outcome,
    parse_strategy_disclosure,
    read_monitor_outcome,
    summarize_strategy_disclosures,
)

from trailhead.vcs.github import (
    AUTOMATIC_MERGE_METHOD,
    PERMITTED_STRATEGIES_LOOKUP_FAILED,
    RESOLUTION_REASON_PREFIXES,
    resolve_merge_strategy,
)


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


# ---------------------------------------------------------------------------
# Strategy disclosure — parses `portage merge`'s per-pull-request disclosure
# line and aggregates it into monitor's report `Strategy:` field.
# ---------------------------------------------------------------------------


def test_parses_a_strategy_disclosure_line():
    strategy, reason = resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset({"rebase"}))
    line = f"portage merge: PR #7 (trailhead): strategy '{strategy}' — {reason}"
    assert parse_strategy_disclosure(line) == StrategyDisclosure("7", "trailhead", strategy, reason)


def test_unrecognized_line_is_refused_by_the_strategy_parser():
    assert parse_strategy_disclosure("not a disclosure line at all") is None


def test_uniform_run_reports_strategy_once():
    strategy, reason = resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset({"rebase"}))
    disclosures = [
        StrategyDisclosure("1", "member-a", strategy, reason),
        StrategyDisclosure("2", "member-b", strategy, reason),
    ]
    assert summarize_strategy_disclosures(disclosures) == f"{strategy} — {reason}"


def test_mixed_run_attributes_each_strategy_to_its_repository():
    rebase_strategy, rebase_reason = resolve_merge_strategy(
        AUTOMATIC_MERGE_METHOD, frozenset({"rebase"})
    )
    squash_strategy, squash_reason = resolve_merge_strategy(
        AUTOMATIC_MERGE_METHOD, frozenset({"squash"})
    )
    disclosures = [
        StrategyDisclosure("1", "member-a", rebase_strategy, rebase_reason),
        StrategyDisclosure("2", "member-b", squash_strategy, squash_reason),
    ]
    result = summarize_strategy_disclosures(disclosures)
    assert result == (
        f"member-a={rebase_strategy} ({rebase_reason}), "
        f"member-b={squash_strategy} ({squash_reason})"
    )
    # A single summary line naming one strategy for a mixed run is a failure.
    assert result != f"{rebase_strategy} — {rebase_reason}"
    assert result != f"{squash_strategy} — {squash_reason}"


def test_lookup_failure_reads_distinctly_from_a_run_where_every_lookup_succeeded():
    failed_strategy, failed_reason = resolve_merge_strategy(
        AUTOMATIC_MERGE_METHOD, PERMITTED_STRATEGIES_LOOKUP_FAILED
    )
    succeeded_strategy, succeeded_reason = resolve_merge_strategy(
        AUTOMATIC_MERGE_METHOD, frozenset({"squash"})
    )
    failed_summary = summarize_strategy_disclosures(
        [StrategyDisclosure("1", "member-a", failed_strategy, failed_reason)]
    )
    succeeded_summary = summarize_strategy_disclosures(
        [StrategyDisclosure("1", "member-a", succeeded_strategy, succeeded_reason)]
    )
    assert failed_summary != succeeded_summary
    assert RESOLUTION_REASON_PREFIXES["auto_lookup_failed"] in failed_summary
    assert RESOLUTION_REASON_PREFIXES["auto_lookup_failed"] not in succeeded_summary


def test_explicit_configuration_is_reported_without_implying_selection():
    strategy, reason = resolve_merge_strategy("rebase", frozenset())
    summary = summarize_strategy_disclosures(
        [StrategyDisclosure("1", "member-a", strategy, reason)]
    )
    assert summary.startswith(f"{strategy} — {RESOLUTION_REASON_PREFIXES['explicit_configured']}")
    for auto_prefix_key in (
        "auto_rebase_permitted",
        "auto_sole_permitted",
        "auto_interim_squash",
        "auto_lookup_failed",
        "auto_none_permitted",
    ):
        assert RESOLUTION_REASON_PREFIXES[auto_prefix_key] not in summary


def test_no_merged_pull_request_reports_not_applicable():
    assert summarize_strategy_disclosures([]) == "n/a"


# ---------------------------------------------------------------------------
# monitor.md's Strategy-disclosure prose is the INPUT here: its documented
# line/field shapes are extracted and fed to the real parser/summarizer, so a
# doc that drifts from what the code actually accepts fails here rather than
# on an agent's first attempt to follow it. Nothing below asserts that a
# sentence appears in the document.
# ---------------------------------------------------------------------------

_MONITOR_MD = _portage_cli.PLUGIN_ROOT / "agents" / "monitor.md"


def _monitor_md_text() -> str:
    return _MONITOR_MD.read_text(encoding="utf-8")


def _documented_disclosure_template() -> str:
    match = re.search(
        r"`portage merge: PR #<pr_number> \(<member_name>\): strategy '<strategy>' — <reason>`",
        _monitor_md_text(),
    )
    assert match, "monitor.md must document the per-PR strategy disclosure line's shape"
    return match.group(0).strip("`")


def _documented_uniform_template() -> str:
    match = re.search(r"once: `(<strategy> — <reason>)`", _monitor_md_text())
    assert match, "monitor.md must document the uniform-run Strategy field shape"
    return match.group(1)


def _documented_mixed_template() -> str:
    match = re.search(
        r"comma-separated: `(<member_name>=<strategy> \(<reason>\))`", _monitor_md_text()
    )
    assert match, "monitor.md must document the mixed-run per-repository Strategy field shape"
    return match.group(1)


def test_documented_disclosure_line_shape_parses_via_the_real_resolver():
    strategy, reason = resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset({"rebase"}))
    line = (
        _documented_disclosure_template()
        .replace("<pr_number>", "42")
        .replace("<member_name>", "trailhead-ai.github.io")
        .replace("<strategy>", strategy)
        .replace("<reason>", reason)
    )
    assert parse_strategy_disclosure(line) == StrategyDisclosure(
        "42", "trailhead-ai.github.io", strategy, reason
    )


def test_documented_uniform_shape_matches_the_real_summary():
    strategy, reason = resolve_merge_strategy(AUTOMATIC_MERGE_METHOD, frozenset({"rebase"}))
    disclosures = [
        StrategyDisclosure("1", "member-a", strategy, reason),
        StrategyDisclosure("2", "member-b", strategy, reason),
    ]
    expected = (
        _documented_uniform_template().replace("<strategy>", strategy).replace("<reason>", reason)
    )
    assert summarize_strategy_disclosures(disclosures) == expected


def test_documented_mixed_shape_matches_the_real_summary():
    rebase_strategy, rebase_reason = resolve_merge_strategy(
        AUTOMATIC_MERGE_METHOD, frozenset({"rebase"})
    )
    squash_strategy, squash_reason = resolve_merge_strategy(
        AUTOMATIC_MERGE_METHOD, frozenset({"squash"})
    )
    disclosures = [
        StrategyDisclosure("1", "member-a", rebase_strategy, rebase_reason),
        StrategyDisclosure("2", "member-b", squash_strategy, squash_reason),
    ]
    template = _documented_mixed_template()
    expected = ", ".join(
        template.replace("<member_name>", d.member_name)
        .replace("<strategy>", d.strategy)
        .replace("<reason>", d.reason)
        for d in disclosures
    )
    assert summarize_strategy_disclosures(disclosures) == expected
