"""Tests for the GitHubProvider deploy surface (Slice 2).

deploy.workflow_runs() / deploy.status() / deploy.logs() — the surface
landing's `doctor` interrogates for a post-merge deploy regression. All gh
calls go through an injected stub runner — zero network.

U2-proven field shapes (live-verified against cli/cli):
  - workflow_runs(): REST `gh api repos/{o}/{r}/actions/runs`, parse `.workflow_runs[]`
    (`id` int, `name`, `status`, `conclusion`, `head_sha`, `created_at`, `html_url`,
    `workflow_id`). NOT `gh run list --json` (it names the id `databaseId`).
  - status(): `gh api .../deployments` then `.../deployments/{id}/statuses`. Zero
    deployments is a VALID steady state → return [], never raise.
  - logs(): `gh api .../check-runs/{job_id}/annotations` filtered to
    `annotation_level=="failure"` → [{path, start_line, message}] + truncation sentinel.

Carried-from-Slice-1 (M-4): the deploy paths must surface a legible in-band error
distinguishing "gh failed (returncode + stderr)" from "gh returned non-JSON/empty".
"""

from __future__ import annotations

import json
import subprocess

import pytest

from trailhead.vcs import get_provider
from trailhead.vcs.github import DeployError


# ---------------------------------------------------------------------------
# Fixtures (shaped like the U2 live samples)
# ---------------------------------------------------------------------------

_RUNS_PAYLOAD = {
    "total_count": 2,
    "workflow_runs": [
        {
            "id": 1001,
            "name": "deploy",
            "status": "completed",
            "conclusion": "success",
            "head_sha": "abc123def456",
            "created_at": "2026-06-10T12:00:00Z",
            "html_url": "https://github.com/o/r/actions/runs/1001",
            "workflow_id": 77,
            "extra_unused_field": "ignored",
        },
        {
            "id": 1002,
            "name": "deploy",
            "status": "in_progress",
            "conclusion": None,
            "head_sha": "def456abc789",
            "created_at": "2026-06-10T13:00:00Z",
            "html_url": "https://github.com/o/r/actions/runs/1002",
            "workflow_id": 77,
        },
    ],
}

_DEPLOYMENTS_PAYLOAD = [
    {"id": 555, "sha": "abc123def456", "environment": "production", "ref": "main"},
]

_STATUSES_PAYLOAD = [
    {
        "id": 9001,
        "state": "success",
        "environment": "production",
        "created_at": "2026-06-10T12:05:00Z",
        "log_url": "https://example.com/logs/9001",
    },
    {
        "id": 9000,
        "state": "in_progress",
        "environment": "production",
        "created_at": "2026-06-10T12:01:00Z",
        "log_url": "",
    },
]

_FAILURE_ANNOTATIONS = [
    {"path": "src/deploy.py", "start_line": 12, "message": "deploy step failed"},
    {"path": "src/deploy.py", "start_line": 40, "message": "rollback triggered"},
]


def _remote_stub_response(cmd: list[str]) -> subprocess.CompletedProcess | None:
    """Resolve the owner/repo lookup that every deploy method makes first."""
    cmd_str = " ".join(cmd)
    if "remote" in cmd_str and "get-url" in cmd_str:
        return subprocess.CompletedProcess(cmd, 0, "git@github.com:myorg/myrepo.git\n", "")
    return None


# ---------------------------------------------------------------------------
# deploy.workflow_runs
# ---------------------------------------------------------------------------


class TestWorkflowRuns:
    def test_parses_runs_into_documented_shape(self) -> None:
        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, json.dumps(_RUNS_PAYLOAD), "")

        provider = get_provider("github", runner=stub)
        runs = provider.deploy.workflow_runs("some/path")

        assert len(runs) == 2
        first = runs[0]
        assert first["id"] == 1001
        assert isinstance(first["id"], int)
        assert first["name"] == "deploy"
        assert first["status"] == "completed"
        assert first["conclusion"] == "success"
        assert first["head_sha"] == "abc123def456"
        assert first["created_at"] == "2026-06-10T12:00:00Z"
        assert first["html_url"] == "https://github.com/o/r/actions/runs/1001"
        assert first["workflow_id"] == 77
        # only the documented keys are surfaced
        assert "extra_unused_field" not in first

    def test_in_progress_run_has_none_conclusion(self) -> None:
        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, json.dumps(_RUNS_PAYLOAD), "")

        provider = get_provider("github", runner=stub)
        runs = provider.deploy.workflow_runs("some/path")
        assert runs[1]["conclusion"] is None
        assert runs[1]["status"] == "in_progress"

    def test_queries_rest_actions_runs_not_run_list(self) -> None:
        """U2 correction: must use REST actions/runs (id), never `gh run list` (databaseId)."""
        calls: list[list[str]] = []

        def stub(cmd, **kw):
            calls.append(list(cmd))
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, json.dumps(_RUNS_PAYLOAD), "")

        provider = get_provider("github", runner=stub)
        provider.deploy.workflow_runs("some/path")

        api_calls = [c for c in calls if "api" in c]
        assert api_calls, "expected a `gh api` call"
        api_str = " ".join(api_calls[0])
        assert "actions/runs" in api_str
        assert "myorg/myrepo" in api_str
        # never the `gh run list` path
        assert not any("run" in c and "list" in c for c in calls)

    def test_status_filter_forwarded_to_query(self) -> None:
        calls: list[list[str]] = []

        def stub(cmd, **kw):
            calls.append(list(cmd))
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, json.dumps(_RUNS_PAYLOAD), "")

        provider = get_provider("github", runner=stub)
        provider.deploy.workflow_runs("some/path", status="completed", per_page=5)

        api_calls = [c for c in calls if "api" in c]
        api_str = " ".join(api_calls[0])
        assert "status=completed" in api_str
        assert "per_page=5" in api_str

    def test_empty_runs_returns_empty_list(self) -> None:
        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"total_count": 0, "workflow_runs": []}), ""
            )

        provider = get_provider("github", runner=stub)
        assert provider.deploy.workflow_runs("some/path") == []

    def test_all_calls_list_form(self) -> None:
        def stub(cmd, **kw):
            assert isinstance(cmd, list), "cmd must be list-form (shell=False)"
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, json.dumps(_RUNS_PAYLOAD), "")

        provider = get_provider("github", runner=stub)
        provider.deploy.workflow_runs("some/path")


# ---------------------------------------------------------------------------
# deploy.status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_parses_latest_status_per_deployment(self) -> None:
        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            cmd_str = " ".join(cmd)
            if "statuses" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(_STATUSES_PAYLOAD), "")
            if "deployments" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(_DEPLOYMENTS_PAYLOAD), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        statuses = provider.deploy.status("some/path")

        assert len(statuses) == 1
        s = statuses[0]
        assert s["id"] == 555
        assert s["sha"] == "abc123def456"
        assert s["state"] == "success"
        assert s["environment"] == "production"
        assert s["created_at"] == "2026-06-10T12:05:00Z"
        assert s["log_url"] == "https://example.com/logs/9001"

    def test_zero_deployments_returns_empty_list_no_raise(self) -> None:
        """U2: zero deployments is a VALID steady state — return [], never raise."""

        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            cmd_str = " ".join(cmd)
            if "deployments" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        assert provider.deploy.status("some/path") == []

    def test_deployment_with_no_statuses_yields_empty_state(self) -> None:
        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            cmd_str = " ".join(cmd)
            if "statuses" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            if "deployments" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(_DEPLOYMENTS_PAYLOAD), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        statuses = provider.deploy.status("some/path")
        assert len(statuses) == 1
        assert statuses[0]["state"] is None
        assert statuses[0]["id"] == 555

    def test_all_calls_list_form(self) -> None:
        def stub(cmd, **kw):
            assert isinstance(cmd, list), "cmd must be list-form (shell=False)"
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            cmd_str = " ".join(cmd)
            if "statuses" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(_STATUSES_PAYLOAD), "")
            if "deployments" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(_DEPLOYMENTS_PAYLOAD), "")
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        provider.deploy.status("some/path")


# ---------------------------------------------------------------------------
# deploy.logs — the doctor signal
# ---------------------------------------------------------------------------


class TestLogs:
    def test_failure_annotations_surfaced_on_failing_run(self) -> None:
        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, json.dumps(_FAILURE_ANNOTATIONS), "")

        provider = get_provider("github", runner=stub)
        annotations = provider.deploy.logs("some/path", job_id="222")

        assert len(annotations) == 2
        assert annotations[0]["path"] == "src/deploy.py"
        assert annotations[0]["start_line"] == 12
        assert annotations[0]["message"] == "deploy step failed"

    def test_clean_run_returns_empty_list_no_false_alarm(self) -> None:
        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        provider = get_provider("github", runner=stub)
        assert provider.deploy.logs("some/path", job_id="222") == []

    def test_not_found_run_returns_empty_list(self) -> None:
        """A 404 from gh (nonzero + Not-Found stderr) on annotations is no-false-alarm:
        empty list."""

        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 1, "", "gh: Not Found (HTTP 404)")

        provider = get_provider("github", runner=stub)
        assert provider.deploy.logs("some/path", job_id="999") == []

    def test_non_404_nonzero_raises_deploy_error(self) -> None:
        """A non-404 nonzero (e.g. 403 rate-limit) raises DeployError — never returns []."""

        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 1, "", "gh: API rate limit exceeded (HTTP 403)")

        provider = get_provider("github", runner=stub)
        with pytest.raises(DeployError):
            provider.deploy.logs("some/path", job_id="999")

    def test_truncation_sentinel_appended_past_cap(self) -> None:
        many = [{"path": f"f{i}.py", "start_line": i, "message": f"err {i}"} for i in range(15)]

        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, json.dumps(many), "")

        provider = get_provider("github", runner=stub)
        annotations = provider.deploy.logs("some/path", job_id="222", max_annotations=10)

        assert len(annotations) == 11
        assert annotations[-1] == {"truncated": True, "total": 15}

    def test_issues_check_runs_annotations_api_call(self) -> None:
        calls: list[list[str]] = []

        def stub(cmd, **kw):
            calls.append(list(cmd))
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, json.dumps(_FAILURE_ANNOTATIONS), "")

        provider = get_provider("github", runner=stub)
        provider.deploy.logs("some/path", job_id="222")

        api_calls = [c for c in calls if "api" in c]
        assert api_calls
        api_str = " ".join(api_calls[0])
        assert "check-runs/222/annotations" in api_str
        assert "myorg/myrepo" in api_str
        # the jq filter is a list element, not a shell pipe — no unguarded | outside of jq select
        assert "-q" in api_calls[0]
        assert not any("|" in tok and "select" not in tok for tok in api_calls[0])

    def test_all_calls_list_form(self) -> None:
        def stub(cmd, **kw):
            assert isinstance(cmd, list), "cmd must be list-form (shell=False)"
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, json.dumps(_FAILURE_ANNOTATIONS), "")

        provider = get_provider("github", runner=stub)
        provider.deploy.logs("some/path", job_id="222")


# ---------------------------------------------------------------------------
# M-4: legible in-band error distinguishing gh-failure from non-JSON/empty
# ---------------------------------------------------------------------------


class TestDeployErrorCause:
    def test_gh_nonzero_surfaces_returncode_and_stderr(self) -> None:
        """gh exiting nonzero on the runs query → DeployError naming returncode + stderr."""

        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 2, "", "gh: API rate limit exceeded")

        provider = get_provider("github", runner=stub)
        with pytest.raises(DeployError) as exc_info:
            provider.deploy.workflow_runs("some/path")
        msg = str(exc_info.value)
        assert "2" in msg, "returncode not surfaced"
        assert "rate limit" in msg, "stderr not surfaced"

    def test_non_json_surfaces_distinct_cause(self) -> None:
        """gh exiting zero but with non-JSON stdout → DeployError, distinct from the
        nonzero case."""

        def stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, "not json at all", "")

        provider = get_provider("github", runner=stub)
        with pytest.raises(DeployError) as exc_info:
            provider.deploy.workflow_runs("some/path")
        msg = str(exc_info.value).lower()
        assert "json" in msg, "non-JSON cause not surfaced"

    def test_two_branches_are_distinguishable(self) -> None:
        """The nonzero-exit message and the non-JSON message must differ (M-4)."""

        def nonzero_stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 2, "", "boom")

        def nonjson_stub(cmd, **kw):
            resp = _remote_stub_response(cmd)
            if resp is not None:
                return resp
            return subprocess.CompletedProcess(cmd, 0, "garbage", "")

        p1 = get_provider("github", runner=nonzero_stub)
        p2 = get_provider("github", runner=nonjson_stub)

        with pytest.raises(DeployError) as e1:
            p1.deploy.workflow_runs("some/path")
        with pytest.raises(DeployError) as e2:
            p2.deploy.workflow_runs("some/path")

        assert str(e1.value) != str(e2.value)
