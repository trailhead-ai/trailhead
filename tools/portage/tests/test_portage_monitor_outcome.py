"""Unit tests for `portage.monitor_outcome` — monitor's outcome-file grammar."""

from __future__ import annotations

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
    get_commit_series,
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


def test_same_strategy_for_different_reasons_does_not_collapse():
    """Two pull requests that landed on the same strategy for different
    reasons stay attributed per repository. This is the silent-degradation
    signal: when a capability lookup starts failing, every affected merge
    falls back to squashing, and a run mixing that fallback with a genuine
    squash is only distinguishable by the reason. Collapsing on strategy
    alone would render the failing run identical to a healthy one.
    """
    degraded_strategy, degraded_reason = resolve_merge_strategy(
        AUTOMATIC_MERGE_METHOD, PERMITTED_STRATEGIES_LOOKUP_FAILED
    )
    healthy_strategy, healthy_reason = resolve_merge_strategy(
        AUTOMATIC_MERGE_METHOD, frozenset({"squash"})
    )
    assert degraded_strategy == healthy_strategy, (
        "fixture no longer exercises the same-strategy case"
    )
    assert degraded_reason != healthy_reason

    result = summarize_strategy_disclosures(
        [
            StrategyDisclosure("1", "member-a", healthy_strategy, healthy_reason),
            StrategyDisclosure("2", "member-b", degraded_strategy, degraded_reason),
        ]
    )

    assert result != f"{healthy_strategy} — {healthy_reason}"
    assert result != f"{degraded_strategy} — {degraded_reason}"
    assert RESOLUTION_REASON_PREFIXES["auto_lookup_failed"] in result
    assert "member-b" in result


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
        "auto_series_dominated",
        "auto_series_not_dominated",
        "auto_series_lookup_failed",
        "auto_series_truncated",
        "auto_lookup_failed",
        "auto_none_permitted",
    ):
        assert RESOLUTION_REASON_PREFIXES[auto_prefix_key] not in summary


def test_no_merged_pull_request_reports_not_applicable():
    assert summarize_strategy_disclosures([]) == "n/a"


def _group_fixture(tmp_path, *member_names: str):
    """A manifest and group TOML for `member_names`, one worktree each.

    Returns `(manifest_path, toml_path, worktree_paths)`. The TOML enables
    auto-merge and, for more than one member, pins the merge order to the
    order the names were given in.
    """
    import json as _json

    worktrees = []
    members = []
    for name in member_names:
        wt = tmp_path / "wt" / name
        wt.mkdir(parents=True)
        worktrees.append(wt)
        members.append(
            {"name": name, "repo_root": str(tmp_path), "worktree_path": str(wt)}
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(_json.dumps({"schema_version": 1, "members": members}))
    toml = tmp_path / "group.toml"
    body = "[release]\nauto_merge = true\n"
    if len(member_names) > 1:
        order = ", ".join(f"'{name}'" for name in member_names)
        body += f"merge_order = [{order}]\n"
    toml.write_text(body, encoding="utf-8")
    return manifest, toml, worktrees


def _graphql_merge_spy(calls, permitted, *, pr_commits=None):
    """A runner answering just enough gh/git for a merge, per pull request:
    `permitted` (the repository's allowed merge strategies) and
    `pr_commits` (the pull request's commit nodes, see `_commit_node`) are
    both keyed by pr_number, so one stub answers a multi-pull-request run
    where each pull request has its own capabilities and commit series. A
    pull request absent from `pr_commits` gets no `commits` field at all —
    `get_commit_series`'s malformed-shape path.
    """
    import json as _json
    import subprocess

    pr_commits = pr_commits or {}

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        cmd_str = " ".join(cmd)
        if "config" in cmd_str and "user.email" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "dev@example.com\n", "")
        if "remote" in cmd_str and "get-url" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, "git@github.com:acme/api.git\n", "")
        if "graphql" in cmd_str:
            number_tok = next((t for t in cmd if t.startswith("number=")), None)
            pr_number = number_tok.split("=", 1)[1] if number_tok else None
            allowed = permitted.get(pr_number, set())
            nodes = pr_commits.get(pr_number)
            pull_request: dict = {"stackEntry": None}
            if nodes is not None:
                pull_request["commits"] = {"totalCount": len(nodes), "nodes": nodes}
            payload = {
                "data": {
                    "repository": {
                        "mergeCommitAllowed": "merge" in allowed,
                        "squashMergeAllowed": "squash" in allowed,
                        "rebaseMergeAllowed": "rebase" in allowed,
                        "pullRequest": pull_request,
                    }
                }
            }
            return subprocess.CompletedProcess(cmd, 0, _json.dumps(payload), "")
        if "view" in cmd_str and "--json" in cmd_str:
            pr_number = cmd[cmd.index("view") + 1]
            payload = {
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "isDraft": False,
                "headRefName": f"feat-{pr_number}",
            }
            return subprocess.CompletedProcess(cmd, 0, _json.dumps(payload), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


def test_the_line_the_merge_path_actually_prints_parses_via_the_real_parser(
    tmp_path, capsys
):
    """The disclosure line's shape is encoded twice — as a format string in
    `trailhead.vcs.github`'s merge loop, and as this module's parsing regex.
    Bind them by running the real merge path and feeding what it really
    printed through the real parser, so a producer-side reword fails here
    rather than silently yielding an unparseable report field.
    """
    from trailhead.vcs.github import GitHubProvider, PRPair

    manifest, toml, (wt,) = _group_fixture(tmp_path, "api")

    calls: list[list[str]] = []
    provider = GitHubProvider(runner=_graphql_merge_spy(calls, {"11": {"merge", "squash", "rebase"}}))
    provider.pr.merge(
        [PRPair(repo_path=str(wt), pr_number="11", member_name="api")],
        str(manifest),
        toml_path=str(toml),
    )

    err = capsys.readouterr().err
    disclosures = [
        parsed
        for parsed in (parse_strategy_disclosure(line) for line in err.splitlines())
        if parsed is not None
    ]

    assert len(disclosures) == 1, (
        "the merge path printed no line this parser recognises — producer and "
        f"consumer have drifted apart. stderr was:\n{err}"
    )
    expected_strategy, expected_reason = resolve_merge_strategy(
        AUTOMATIC_MERGE_METHOD, frozenset({"merge", "squash", "rebase"})
    )
    assert disclosures[0] == StrategyDisclosure(
        "11", "api", expected_strategy, expected_reason
    )


def _commit_node(message: str, additions: int, deletions: int) -> dict:
    return {
        "commit": {
            "message": message,
            "additions": additions,
            "deletions": deletions,
            "parents": {"totalCount": 1},
        }
    }


def test_series_driven_disclosure_line_parses_via_the_real_parser(tmp_path, capsys):
    """The disclosure line's shape must still hold for the new
    series-driven reason vocabulary (AC10), not only for the
    rebase-permitted case the sibling test covers: a real merge run whose
    repository forbids rebasing and permits both other strategies produces
    a line the real parser reads back intact, reason text included."""
    from trailhead.vcs.github import GitHubProvider, PRPair

    manifest, toml, (wt,) = _group_fixture(tmp_path, "api")

    calls: list[list[str]] = []
    commits = [
        _commit_node("add the parser", 120, 10),
        _commit_node("add the renderer", 140, 20),
    ]
    provider = GitHubProvider(
        runner=_graphql_merge_spy(
            calls, {"12": {"merge", "squash"}}, pr_commits={"12": commits}
        )
    )
    provider.pr.merge(
        [PRPair(repo_path=str(wt), pr_number="12", member_name="api")],
        str(manifest),
        toml_path=str(toml),
    )

    err = capsys.readouterr().err
    disclosures = [
        parsed
        for parsed in (parse_strategy_disclosure(line) for line in err.splitlines())
        if parsed is not None
    ]
    assert len(disclosures) == 1, (
        "the merge path printed no line this parser recognises for the "
        f"series-driven reason — producer and consumer have drifted apart. stderr was:\n{err}"
    )
    series = get_commit_series(str(wt), "12", runner=_graphql_merge_spy(
        [], {"12": {"merge", "squash"}}, pr_commits={"12": commits}
    ))
    expected_strategy, expected_reason = resolve_merge_strategy(
        AUTOMATIC_MERGE_METHOD, frozenset({"merge", "squash"}), series=series
    )
    assert disclosures[0] == StrategyDisclosure("12", "api", expected_strategy, expected_reason)
    assert RESOLUTION_REASON_PREFIXES["auto_series_not_dominated"] in expected_reason


def test_two_pull_requests_one_reading_series_one_not_both_parse_independently(
    tmp_path, capsys
):
    """Two pull requests in one run resolve independently — one reads the
    series (rebase forbidden, two others permitted), one does not (rebase
    permitted) — and the real parser reads both disclosures back correctly
    attributed to their own pull request, over the runner's real command
    log across one genuine two-pull-request merge run."""
    from trailhead.vcs.github import GitHubProvider, PRPair

    manifest, toml, (wt_a, wt_b) = _group_fixture(tmp_path, "api", "web")

    calls: list[list[str]] = []
    commits = [
        _commit_node("add the parser", 120, 10),
        _commit_node("add the renderer", 140, 20),
    ]
    runner = _graphql_merge_spy(
        calls,
        {"21": {"merge", "squash", "rebase"}, "22": {"merge", "squash"}},
        pr_commits={"22": commits},
    )
    provider = GitHubProvider(runner=runner)
    provider.pr.merge(
        [
            PRPair(repo_path=str(wt_a), pr_number="21", member_name="api"),
            PRPair(repo_path=str(wt_b), pr_number="22", member_name="web"),
        ],
        str(manifest),
        toml_path=str(toml),
    )

    err = capsys.readouterr().err
    disclosures = [
        parsed
        for parsed in (parse_strategy_disclosure(line) for line in err.splitlines())
        if parsed is not None
    ]
    assert len(disclosures) == 2
    by_pr = {d.pr_number: d for d in disclosures}
    assert RESOLUTION_REASON_PREFIXES["auto_rebase_permitted"] in by_pr["21"].reason
    assert RESOLUTION_REASON_PREFIXES["auto_series_not_dominated"] in by_pr["22"].reason
    assert by_pr["21"].strategy == "rebase"
    assert by_pr["22"].strategy == "merge"
