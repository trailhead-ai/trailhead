"""CLI dispatch contract tests for the unified `portage` command.

These port the exit-code / delegation / error-message contracts that used to
live in ``test_portage_thin_scripts.py`` (one thin script per file) onto the
single ``portage.cli.dispatch`` router. Each subcommand parses its args and
invokes the matching ``trailhead.vcs`` provider method; the router owns the
JSON output shape and exit codes (0 / 1 / 2) the old scripts owned.

A fake provider is injected (monkeypatching ``get_provider`` in the command
modules) so no network / gh / git is touched. The tests drive the real
``dispatch.main(argv)`` entry point so argparse routing and the handler's
exit code are proven together.
"""

from __future__ import annotations

import json
from pathlib import Path

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.cli import ci as ci_cli
from portage.cli import dispatch
from portage.cli import pr as pr_cli
from portage.cli import repos as repos_cli


class _FakeRepos:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def detect(self, manifest_path):
        self.calls.append(("detect", manifest_path))
        return self.result


class _FakePR:
    def __init__(self):
        self.calls = []
        self.status_result = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "failingChecks": [],
        }
        self.evaluate_result = {"action": "done", "reason": "clean", "details": {}}
        self.merge_result = {"merged": ["a:1"], "failed": {}, "skipped": {}}
        self.sidecar = {"schema_version": 1, "prs": [], "external_tracker": None}
        self.summary_result = {
            "number": 1,
            "title": '<untrusted-content source="pr-metadata">t</untrusted-content>',
            "body": '<untrusted-content source="pr-metadata">b</untrusted-content>',
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [],
            "diff": '<untrusted-content source="pr-diff">d</untrusted-content>',
            "comments": [],
        }

    def status(self, repo_path, pr_number, *, since=None, review_bot_login=None):
        self.calls.append(("status", repo_path, pr_number, since, review_bot_login))
        return self.status_result

    def evaluate(self, status, *, review_bot_login=None, fail_count=0):
        self.calls.append(("evaluate", status, review_bot_login, fail_count))
        return self.evaluate_result

    def merge(self, pr_pairs, manifest_path, *, toml_path=None):
        self.calls.append(("merge", list(pr_pairs), manifest_path, toml_path))
        return self.merge_result

    def summary_inputs(self, repo_path, pr_number):
        self.calls.append(("summary_inputs", repo_path, pr_number))
        return self.summary_result

    def open(self, sidecar_path, prs):
        self.calls.append(("open", str(sidecar_path), prs))

    def read_sidecar(self, sidecar_path):
        self.calls.append(("read_sidecar", str(sidecar_path)))
        return self.sidecar


class _FakeCI:
    def __init__(self):
        self.calls = []
        self.wait_result = {"actionable": {"a:1": {"action": "done"}}, "waiting": {}}

    def wait(self, pr_pairs, *, since=None, timeout=1800, interval=30, review_bot_login=None):
        self.calls.append(("wait", list(pr_pairs), since, timeout, interval, review_bot_login))
        return self.wait_result


class _FakeProvider:
    def __init__(self, repos_result=None):
        self.repos = _FakeRepos(repos_result if repos_result is not None else [])
        self.pr = _FakePR()
        self.ci = _FakeCI()


def _install(monkeypatch, provider):
    """Point every command module's get_provider at the injected fake."""
    for mod in (repos_cli, pr_cli, ci_cli):
        monkeypatch.setattr(mod, "get_provider", lambda *a, **k: provider)


def _make_manifest(tmp_path: Path) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"schema_version": 1, "members": []}))
    return p


# ---------------------------------------------------------------------------
# detect-repos
# ---------------------------------------------------------------------------


class TestDetectRepos:
    def test_delegates_to_repos_detect_and_prints_json(self, tmp_path, monkeypatch, capsys):
        active = [{"repo": "api", "path": "/x", "branch": "feat", "ahead": 1, "dirty": 0}]
        provider = _FakeProvider(repos_result=active)
        _install(monkeypatch, provider)
        manifest = _make_manifest(tmp_path)

        rc = dispatch.main(["detect-repos", "--manifest", str(manifest)])
        assert rc == 0
        assert provider.repos.calls == [("detect", str(manifest))]
        out = json.loads(capsys.readouterr().out)
        assert out == active

    def test_manifest_read_error_exits_2(self, tmp_path, monkeypatch, capsys):
        from trailhead.vcs.github import ManifestReadError

        provider = _FakeProvider()

        def raising_detect(manifest_path):
            raise ManifestReadError("manifest.json: malformed")

        provider.repos.detect = raising_detect
        _install(monkeypatch, provider)
        rc = dispatch.main(["detect-repos", "--manifest", str(tmp_path / "manifest.json")])
        assert rc == 2
        assert "malformed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# check-status
# ---------------------------------------------------------------------------


class TestCheckStatus:
    def test_delegates_to_pr_status_and_prints_json(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)

        rc = dispatch.main(["check-status", str(tmp_path), "42"])
        assert rc == 0
        assert provider.pr.calls[0][:3] == ("status", str(tmp_path), "42")
        out = json.loads(capsys.readouterr().out)
        assert out["mergeable"] == "MERGEABLE"

    def test_not_a_directory_exits_1(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)
        rc = dispatch.main(["check-status", str(tmp_path / "nope"), "42"])
        assert rc == 1
        assert "not a directory" in capsys.readouterr().out

    def test_invalid_pr_number_exits_1_with_clean_error(self, tmp_path, monkeypatch, capsys):
        from trailhead.vcs.github import InvalidInputError

        provider = _FakeProvider()

        def raising_status(repo_path, pr_number, *, since=None, review_bot_login=None):
            raise InvalidInputError(f"pr_number must be all digits, got: {pr_number!r}")

        provider.pr.status = raising_status
        _install(monkeypatch, provider)

        rc = dispatch.main(["check-status", str(tmp_path), "abc"])
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert "must be all digits" in out["error"]


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


class TestSummarize:
    def test_delegates_to_pr_summary_inputs_and_prints_wrapped_json(
        self, tmp_path, monkeypatch, capsys
    ):
        provider = _FakeProvider()
        _install(monkeypatch, provider)

        rc = dispatch.main(["summarize", str(tmp_path), "42"])
        assert rc == 0
        assert provider.pr.calls[0] == ("summary_inputs", str(tmp_path), "42")
        out = json.loads(capsys.readouterr().out)
        assert out["title"].startswith("<untrusted-content")
        assert out["diff"].startswith("<untrusted-content")

    def test_not_a_directory_exits_1(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)
        rc = dispatch.main(["summarize", str(tmp_path / "nope"), "42"])
        assert rc == 1
        assert "not a directory" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# evaluate-status
# ---------------------------------------------------------------------------


class TestEvaluateStatus:
    def test_delegates_status_then_evaluate(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)

        rc = dispatch.main(["evaluate-status", str(tmp_path), "7"])
        assert rc == 0
        kinds = [c[0] for c in provider.pr.calls]
        assert "status" in kinds and "evaluate" in kinds
        out = json.loads(capsys.readouterr().out)
        assert out["action"] == "done"

    def test_invalid_pr_number_exits_1_with_clean_error(self, tmp_path, monkeypatch, capsys):
        from trailhead.vcs.github import InvalidInputError

        provider = _FakeProvider()

        def raising_status(repo_path, pr_number, *, since=None, review_bot_login=None):
            raise InvalidInputError(f"pr_number must be all digits, got: {pr_number!r}")

        provider.pr.status = raising_status
        _install(monkeypatch, provider)

        rc = dispatch.main(["evaluate-status", str(tmp_path), "abc"])
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert "must be all digits" in out["reason"]


# ---------------------------------------------------------------------------
# merge — preserve merge_order gate + BLOCKED message
# ---------------------------------------------------------------------------


class TestMerge:
    def test_delegates_to_pr_merge_and_prints_json(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)
        manifest = _make_manifest(tmp_path)

        rc = dispatch.main(["merge", "--manifest", str(manifest), f"{tmp_path}:1:api"])
        assert rc == 0
        merge_calls = [c for c in provider.pr.calls if c[0] == "merge"]
        assert merge_calls, "merge must delegate to provider.pr.merge"
        out = json.loads(capsys.readouterr().out)
        assert out["merged"] == ["a:1"]

    def test_merge_order_gate_message_preserved(self, tmp_path, monkeypatch, capsys):
        from trailhead.vcs.github import MergeOrderRequiredError

        provider = _FakeProvider()

        def raising_merge(pr_pairs, manifest_path, *, toml_path=None):
            raise MergeOrderRequiredError(
                "refusing to merge 2 PRs with no merge_order declared — "
                "add merge_order = [...] to the [release] block of your group TOML"
            )

        provider.pr.merge = raising_merge
        _install(monkeypatch, provider)
        manifest = _make_manifest(tmp_path)

        rc = dispatch.main(
            ["merge", "--manifest", str(manifest), f"{tmp_path}:1:api", f"{tmp_path}:2:web"]
        )
        assert rc == 2
        assert "merge_order" in capsys.readouterr().err

    def test_bad_pair_format_exits_2(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)
        manifest = _make_manifest(tmp_path)
        rc = dispatch.main(["merge", "--manifest", str(manifest), "no-colon-here"])
        assert rc == 2
        assert not [c for c in provider.pr.calls if c[0] == "merge"]

    def test_two_field_pair_is_loud_refusal_not_basename_backfill(
        self, tmp_path, monkeypatch, capsys
    ):
        provider = _FakeProvider()
        _install(monkeypatch, provider)
        manifest = _make_manifest(tmp_path)

        rc = dispatch.main(["merge", "--manifest", str(manifest), f"{tmp_path}:1"])
        assert rc == 2
        assert not [c for c in provider.pr.calls if c[0] == "merge"]
        assert "member_name" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# wait-for-actionable
# ---------------------------------------------------------------------------


class TestWaitForActionable:
    def test_delegates_to_ci_wait_and_prints_json(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)

        rc = dispatch.main(["wait-for-actionable", f"{tmp_path}:1"])
        assert rc == 0
        assert provider.ci.calls[0][0] == "wait"
        out = json.loads(capsys.readouterr().out)
        assert "actionable" in out

    def test_timeout_exits_1(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        provider.ci.wait_result = {"timeout": True, "elapsed_seconds": 1800}
        _install(monkeypatch, provider)
        rc = dispatch.main(["wait-for-actionable", f"{tmp_path}:1"])
        assert rc == 1

    def test_bad_pair_format_exits_2_with_clean_message(self, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)
        rc = dispatch.main(["wait-for-actionable", "no-colon-here"])
        assert rc == 2
        assert provider.ci.calls == []
        assert "no-colon-here" in capsys.readouterr().err

    def test_non_digit_pr_number_exits_2_with_clean_message(self, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)
        rc = dispatch.main(["wait-for-actionable", "some/path:--repo=owner/other"])
        assert rc == 2
        assert provider.ci.calls == []
        assert "--repo=owner/other" in capsys.readouterr().err

    def test_accepts_three_field_pairs(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)

        rc = dispatch.main(["wait-for-actionable", f"{tmp_path}:1:api"])
        assert rc == 0
        assert provider.ci.calls[0][0] == "wait"
        out = json.loads(capsys.readouterr().out)
        assert "actionable" in out


# ---------------------------------------------------------------------------
# sidecar — record/read via provider.pr
# ---------------------------------------------------------------------------


class TestSidecar:
    def test_write_delegates_to_pr_open(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)
        sidecar = tmp_path / "prs.json"

        rc = dispatch.main(
            [
                "sidecar",
                "write",
                "--sidecar",
                str(sidecar),
                "--pr",
                "api:1:https://github.com/o/api/pull/1:feat",
            ]
        )
        assert rc == 0
        open_calls = [c for c in provider.pr.calls if c[0] == "open"]
        assert open_calls, "sidecar write must delegate to provider.pr.open"
        _, _, prs = open_calls[0]
        assert prs == [
            {
                "repo": "api",
                "pr_number": "1",
                "url": "https://github.com/o/api/pull/1",
                "branch": "feat",
            }
        ]

    def test_write_bad_token_exits_2(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        _install(monkeypatch, provider)
        sidecar = tmp_path / "prs.json"
        rc = dispatch.main(
            ["sidecar", "write", "--sidecar", str(sidecar), "--pr", "not-enough-fields"]
        )
        assert rc == 2
        assert not [c for c in provider.pr.calls if c[0] == "open"]

    def test_read_delegates_to_pr_read_sidecar(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        provider.pr.sidecar = {
            "schema_version": 1,
            "prs": [{"repo": "api", "pr_number": "1", "url": "u", "branch": "b"}],
            "external_tracker": None,
        }
        _install(monkeypatch, provider)
        sidecar = tmp_path / "prs.json"

        rc = dispatch.main(["sidecar", "read", "--sidecar", str(sidecar)])
        assert rc == 0
        read_calls = [c for c in provider.pr.calls if c[0] == "read_sidecar"]
        assert read_calls, "sidecar read must delegate to provider.pr.read_sidecar"
        out = json.loads(capsys.readouterr().out)
        assert out["prs"][0]["repo"] == "api"
