"""EPHEMERAL assumption probe — delete before merge.

Captures the assumption behind
task/read-the-strategies-a-repository-permits-without-a-second-round-trip:
that GitHub's GraphQL Repository object exposes mergeCommitAllowed /
squashMergeAllowed / rebaseMergeAllowed, and that they are selectable in the
same query the existing _STACK_ENTRY_QUERY issues (repository -> pullRequest).

Requires network access and an authenticated `gh` CLI (uses the ambient
`gh auth status` session) against the real trailhead-ai/trailhead repo. Not
suitable for the regular offline suite — run standalone, then delete.
"""

import json
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("gh") is None, reason="gh CLI not available"
)

_COMBINED_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) {"
    " repository(owner: $owner, name: $name) {"
    "  mergeCommitAllowed"
    "  squashMergeAllowed"
    "  rebaseMergeAllowed"
    "  pullRequest(number: $number) { stackEntry { stack { number size } } }"
    " }"
    "}"
)


def _run_graphql(owner: str, name: str, number: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={_COMBINED_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_merge_method_fields_coselectable_with_pull_request_field():
    """The three permitted-merge-method booleans and the existing
    pullRequest{stackEntry} selection both resolve in one round trip."""
    result = _run_graphql("trailhead-ai", "trailhead", 206)
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    repo = body["data"]["repository"]
    assert isinstance(repo["mergeCommitAllowed"], bool)
    assert isinstance(repo["squashMergeAllowed"], bool)
    assert isinstance(repo["rebaseMergeAllowed"], bool)
    # existing field is still returned correctly alongside the new ones
    assert "stackEntry" in repo["pullRequest"]


def test_nonexistent_pr_number_nulls_only_the_failing_subselection():
    """A PR-number error nulls pullRequest but NOT the sibling
    merge-method fields on the same repository object — partial failure,
    not whole-object failure. gh's own exit code is nonzero even though
    the merge-method fields are present in stdout."""
    result = _run_graphql("trailhead-ai", "trailhead", 999999999)
    assert result.returncode != 0  # gh surfaces the GraphQL error as failure
    body = json.loads(result.stdout)
    assert body.get("errors"), "expected a GraphQL errors array"
    repo = body["data"]["repository"]
    assert repo["pullRequest"] is None
    assert isinstance(repo["mergeCommitAllowed"], bool)
    assert isinstance(repo["squashMergeAllowed"], bool)
    assert isinstance(repo["rebaseMergeAllowed"], bool)


def test_unresolvable_repository_nulls_the_whole_repository_object():
    """When the repository itself can't be resolved, `repository` is
    null outright (not merely its sub-selections) — this IS
    distinguishable from the PR-not-found case above."""
    result = _run_graphql("trailhead-ai", "this-repo-does-not-exist-xyz123", 1)
    assert result.returncode != 0
    body = json.loads(result.stdout)
    assert body.get("errors")
    assert body["data"]["repository"] is None
