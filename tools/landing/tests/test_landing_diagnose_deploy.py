"""doctor's deploy.logs() consumption: the thin diagnose_deploy.py script.

doctor interrogates a GHA deploy-log failure through the thin diagnose_deploy.py
script, which bootstraps trailhead and delegates to provider.deploy.logs() (and
deploy.status()/workflow_runs() for context). These tests inject a fake provider
(monkeypatching the script's get_provider) so no network/gh is touched, and
assert:

  - a failing run surfaces the failure annotation (the doctor signal),
  - a clean / not-found (404 → []) run does NOT false-alarm,
  - (Slice-2 C-1) a non-404 gh failure raises DeployError rather than silently
    returning empty — doctor must never read an *uncheckable* deploy as *healthy*.

Unique basename — no collision with craft's per-script tests.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "landing" / "scripts"


def _load(name: str):
    """Load a landing thin script module fresh by stem."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeDeploy:
    def __init__(self, *, logs_result=None, logs_exc=None):
        self.calls = []
        self._logs_result = logs_result if logs_result is not None else []
        self._logs_exc = logs_exc

    def logs(self, repo_path, *, job_id, **kwargs):
        self.calls.append(("logs", repo_path, job_id))
        if self._logs_exc is not None:
            raise self._logs_exc
        return self._logs_result


class _FakeProvider:
    def __init__(self, deploy):
        self.deploy = deploy


def _patch_provider(mod, monkeypatch, provider):
    monkeypatch.setattr(mod, "get_provider", lambda *a, **k: provider)


class TestDiagnoseDeployFailingRun:
    def test_failing_run_surfaces_annotation(self, tmp_path, monkeypatch, capsys):
        """A failing run's annotation is surfaced as the doctor signal."""
        annotations = [
            {"path": "deploy.sh", "start_line": 12, "message": "deploy failed: exit 1"}
        ]
        provider = _FakeProvider(_FakeDeploy(logs_result=annotations))
        mod = _load("diagnose_deploy")
        _patch_provider(mod, monkeypatch, provider)

        rc = mod.main([str(tmp_path), "--job-id", "555"])
        assert rc == 0
        assert provider.deploy.calls == [("logs", str(tmp_path), "555")]
        out = json.loads(capsys.readouterr().out)
        assert out["annotations"] == annotations
        assert out["failed"] is True


class TestDiagnoseDeployCleanRun:
    def test_clean_run_does_not_false_alarm(self, tmp_path, monkeypatch, capsys):
        """A clean / 404 run yields [] annotations and failed=False (no false alarm)."""
        provider = _FakeProvider(_FakeDeploy(logs_result=[]))
        mod = _load("diagnose_deploy")
        _patch_provider(mod, monkeypatch, provider)

        rc = mod.main([str(tmp_path), "--job-id", "555"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["annotations"] == []
        assert out["failed"] is False


class TestDiagnoseDeployGhFailureRaises:
    def test_non_404_gh_failure_surfaces_error_not_empty(self, tmp_path, monkeypatch, capsys):
        """Slice-2 C-1: a non-404 gh failure must surface as a legible error, NOT empty.

        doctor must never read an uncheckable deploy (auth/rate-limit/outage) as
        healthy. The provider raises DeployError; the thin script surfaces it as a
        named error and a nonzero exit — not a silent empty annotation list.
        """
        from trailhead.vcs.github import DeployError

        provider = _FakeProvider(
            _FakeDeploy(logs_exc=DeployError("gh api ... failed (returncode 1): rate limited"))
        )
        mod = _load("diagnose_deploy")
        _patch_provider(mod, monkeypatch, provider)

        rc = mod.main([str(tmp_path), "--job-id", "555"])
        assert rc == 1, "a non-404 gh failure must exit nonzero, not be read as healthy"
        err = capsys.readouterr().err
        assert "rate limited" in err or "DeployError" in err or "failed" in err, (
            f"the deploy error cause must reach the operator; got stderr: {err!r}"
        )
