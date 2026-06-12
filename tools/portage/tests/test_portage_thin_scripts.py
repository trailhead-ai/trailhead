"""Thin-script delegation tests: each portage script calls trailhead.vcs and
reproduces the forge CLI's argv + output shape.

The portage scripts are thin: bootstrap → from trailhead.vcs import get_provider
→ call the matching provider method → print the same JSON / exit code the old
forge script did. These tests inject a fake provider (monkeypatching the script's
get_provider) so no network/gh/git is touched, and assert:

  - the right provider method is invoked with the right args (delegation), and
  - the CLI prints the same JSON shape and returns the same exit code.

Scripts under test (CLI contract ported verbatim from forge):
  detect_repos.py        → provider.repos.detect(manifest)
  check_pr_status.py     → provider.pr.status(...)
  pr_evaluate_status.py  → provider.pr.evaluate(provider.pr.status(...))
  merge_prs.py           → provider.pr.merge(pairs, manifest, toml=...)  [R-6 gate]
  wait_for_actionable.py → provider.ci.wait(pairs, ...)
  release_prs_sidecar.py → provider.pr.open(...) / provider.pr.read_sidecar(...)

Unique basename — no collision with forge's per-script tests.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "portage" / "scripts"


def _load(name: str):
    """Load a portage thin script module fresh by stem."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        self.status_result = {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                              "isDraft": False, "failingChecks": []}
        self.evaluate_result = {"action": "done", "reason": "clean", "details": {}}
        self.merge_result = {"merged": ["a:1"], "failed": {}, "skipped": {}}
        self.sidecar = {"schema_version": 1, "prs": [], "external_tracker": None}

    def status(self, repo_path, pr_number, *, since=None, review_bot_login=None):
        self.calls.append(("status", repo_path, pr_number, since, review_bot_login))
        return self.status_result

    def evaluate(self, status, *, review_bot_login=None, fail_count=0):
        self.calls.append(("evaluate", status, review_bot_login, fail_count))
        return self.evaluate_result

    def merge(self, pr_pairs, manifest_path, *, toml_path=None):
        self.calls.append(("merge", list(pr_pairs), manifest_path, toml_path))
        return self.merge_result

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


def _patch_provider(mod, monkeypatch, provider):
    monkeypatch.setattr(mod, "get_provider", lambda *a, **k: provider)


def _make_manifest(tmp_path: Path) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"schema_version": 1, "members": []}))
    return p


# ---------------------------------------------------------------------------
# detect_repos.py
# ---------------------------------------------------------------------------


class TestDetectRepos:
    def test_delegates_to_repos_detect_and_prints_json(self, tmp_path, monkeypatch, capsys):
        active = [{"repo": "api", "path": "/x", "branch": "feat", "ahead": 1, "dirty": 0}]
        provider = _FakeProvider(repos_result=active)
        mod = _load("detect_repos")
        _patch_provider(mod, monkeypatch, provider)
        manifest = _make_manifest(tmp_path)

        rc = mod.main(["--manifest", str(manifest)])
        assert rc == 0
        assert provider.repos.calls == [("detect", str(manifest))]
        out = json.loads(capsys.readouterr().out)
        assert out == active


# ---------------------------------------------------------------------------
# check_pr_status.py
# ---------------------------------------------------------------------------


class TestCheckPrStatus:
    def test_delegates_to_pr_status_and_prints_json(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        mod = _load("check_pr_status")
        _patch_provider(mod, monkeypatch, provider)
        repo = tmp_path  # must be a directory (CLI guard)

        rc = mod.main([str(repo), "42"])
        assert rc == 0
        assert provider.pr.calls[0][:3] == ("status", str(repo), "42")
        out = json.loads(capsys.readouterr().out)
        assert out["mergeable"] == "MERGEABLE"

    def test_not_a_directory_exits_1(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        mod = _load("check_pr_status")
        _patch_provider(mod, monkeypatch, provider)
        rc = mod.main([str(tmp_path / "nope"), "42"])
        assert rc == 1
        assert "not a directory" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# pr_evaluate_status.py
# ---------------------------------------------------------------------------


class TestPrEvaluate:
    def test_delegates_status_then_evaluate(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        mod = _load("pr_evaluate_status")
        _patch_provider(mod, monkeypatch, provider)

        rc = mod.main([str(tmp_path), "7"])
        assert rc == 0
        kinds = [c[0] for c in provider.pr.calls]
        assert "status" in kinds and "evaluate" in kinds
        out = json.loads(capsys.readouterr().out)
        assert out["action"] == "done"


# ---------------------------------------------------------------------------
# merge_prs.py — preserve R-6 gate + BLOCKED message
# ---------------------------------------------------------------------------


class TestMergePrs:
    def test_delegates_to_pr_merge_and_prints_json(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        mod = _load("merge_prs")
        _patch_provider(mod, monkeypatch, provider)
        manifest = _make_manifest(tmp_path)

        rc = mod.main(["--manifest", str(manifest), f"{tmp_path}:1:api"])
        assert rc == 0
        merge_calls = [c for c in provider.pr.calls if c[0] == "merge"]
        assert merge_calls, "merge_prs.py must delegate to provider.pr.merge"
        out = json.loads(capsys.readouterr().out)
        assert out["merged"] == ["a:1"]

    def test_r6_gate_message_preserved(self, tmp_path, monkeypatch, capsys):
        """>1 PR with no --toml → provider.pr.merge raises MergeOrderRequiredError;
        the CLI must surface the BLOCKED-style named error and exit 2."""
        from trailhead.vcs.github import MergeOrderRequiredError

        provider = _FakeProvider()

        def raising_merge(pr_pairs, manifest_path, *, toml_path=None):
            raise MergeOrderRequiredError(
                "refusing to merge 2 PRs with no merge_order declared — "
                "add merge_order = [...] to the [release] block of your group TOML"
            )

        provider.pr.merge = raising_merge
        mod = _load("merge_prs")
        _patch_provider(mod, monkeypatch, provider)
        manifest = _make_manifest(tmp_path)

        rc = mod.main(["--manifest", str(manifest), f"{tmp_path}:1:api", f"{tmp_path}:2:web"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "merge_order" in err

    def test_bad_pair_format_exits_2(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        mod = _load("merge_prs")
        _patch_provider(mod, monkeypatch, provider)
        manifest = _make_manifest(tmp_path)
        rc = mod.main(["--manifest", str(manifest), "no-colon-here"])
        assert rc == 2


# ---------------------------------------------------------------------------
# wait_for_actionable.py
# ---------------------------------------------------------------------------


class TestWaitForActionable:
    def test_delegates_to_ci_wait_and_prints_json(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        mod = _load("wait_for_actionable")
        _patch_provider(mod, monkeypatch, provider)

        rc = mod.main([f"{tmp_path}:1"])
        assert rc == 0
        assert provider.ci.calls[0][0] == "wait"
        out = json.loads(capsys.readouterr().out)
        assert "actionable" in out

    def test_timeout_exits_1(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        provider.ci.wait_result = {"timeout": True, "elapsed_seconds": 1800}
        mod = _load("wait_for_actionable")
        _patch_provider(mod, monkeypatch, provider)
        rc = mod.main([f"{tmp_path}:1"])
        assert rc == 1


# ---------------------------------------------------------------------------
# release_prs_sidecar.py — record/read via provider.pr
# ---------------------------------------------------------------------------


class TestSidecar:
    def test_write_delegates_to_pr_open(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        mod = _load("release_prs_sidecar")
        _patch_provider(mod, monkeypatch, provider)
        sidecar = tmp_path / "prs.json"

        rc = mod.main([
            "write", "--sidecar", str(sidecar),
            "--pr", "api:1:https://github.com/o/api/pull/1:feat",
        ])
        assert rc == 0
        open_calls = [c for c in provider.pr.calls if c[0] == "open"]
        assert open_calls, "write subcommand must delegate to provider.pr.open"
        _, _, prs = open_calls[0]
        assert prs == [{"repo": "api", "pr_number": "1",
                        "url": "https://github.com/o/api/pull/1", "branch": "feat"}]

    def test_read_delegates_to_pr_read_sidecar(self, tmp_path, monkeypatch, capsys):
        provider = _FakeProvider()
        provider.pr.sidecar = {"schema_version": 1,
                               "prs": [{"repo": "api", "pr_number": "1",
                                        "url": "u", "branch": "b"}],
                               "external_tracker": None}
        mod = _load("release_prs_sidecar")
        _patch_provider(mod, monkeypatch, provider)
        sidecar = tmp_path / "prs.json"

        rc = mod.main(["read", "--sidecar", str(sidecar)])
        assert rc == 0
        read_calls = [c for c in provider.pr.calls if c[0] == "read_sidecar"]
        assert read_calls, "read subcommand must delegate to provider.pr.read_sidecar"
        out = json.loads(capsys.readouterr().out)
        assert out["prs"][0]["repo"] == "api"
