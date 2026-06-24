"""Tests for trailhead.vcs.runner — the injectable runner seam (R-1, S-4).

Copied from craft's runner_protocol.py contract. Proves:

  R-1a: the stub receives the EXACT gh/git subcommand + args the caller
        constructed — no args dropped, no shell interpolation.
  R-1b: the no-runner production path works against a real tmp_path git repo
        (git init + commit) to catch env={}-style blindspots.
  S-4:  a pr_number or branch containing shell metacharacters (';', '&&',
        '$(...)') is passed LITERALLY — no subshell spawned.
  SHELL_FALSE invariant sentinel is True.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from trailhead.vcs import runner as rp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_init_repo(path: Path) -> None:
    """Create a minimal git repo with one commit under path."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    readme = path / "README.md"
    readme.write_text("init\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# R-1a: stub receives the exact args
# ---------------------------------------------------------------------------


class TestRunnerStub:
    def test_stub_records_single_call(self) -> None:
        calls: list[list[str]] = []

        def stub(cmd: list[str], **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        rp.run(["gh", "pr", "view", "42", "--json", "state"], runner=stub)
        assert calls == [["gh", "pr", "view", "42", "--json", "state"]]

    def test_stub_records_multiple_calls_in_order(self) -> None:
        calls: list[list[str]] = []

        def stub(cmd: list[str], **kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        rp.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], runner=stub)
        rp.run(["git", "status", "--porcelain"], runner=stub)

        assert calls[0] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]
        assert calls[1] == ["git", "status", "--porcelain"]

    def test_stub_return_value_propagated(self) -> None:
        def stub(cmd: list[str], **kwargs):
            return subprocess.CompletedProcess(cmd, returncode=7, stdout="out", stderr="err")

        result = rp.run(["gh", "pr", "merge", "1", "--merge"], runner=stub)
        assert result.returncode == 7
        assert result.stdout == "out"
        assert result.stderr == "err"

    def test_stub_receives_cwd_kwarg(self) -> None:
        captured_kwargs: list[dict] = []

        def stub(cmd: list[str], **kwargs):
            captured_kwargs.append(kwargs)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        rp.run(["git", "status"], cwd="/some/path", runner=stub)
        assert captured_kwargs[0].get("cwd") == "/some/path"


# ---------------------------------------------------------------------------
# R-1b: the production path (no runner) works on a real git repo
# ---------------------------------------------------------------------------


class TestRunnerProduction:
    def test_real_git_rev_parse_on_tmp_repo(self, tmp_path: Path) -> None:
        repo = tmp_path / "my-repo"
        _git_init_repo(repo)
        result = rp.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo))
        assert result.returncode == 0
        assert result.stdout.strip() in ("main", "master")

    def test_production_path_inherits_env(self, tmp_path: Path) -> None:
        """Env is inherited (not {}) — git can find system objects."""
        repo = tmp_path / "env-repo"
        _git_init_repo(repo)
        result = rp.run(["git", "log", "--oneline"], cwd=str(repo))
        assert result.returncode == 0
        assert "init" in result.stdout


# ---------------------------------------------------------------------------
# S-4: shell metacharacters in args are passed literally — no subshell
# ---------------------------------------------------------------------------


class TestShellSafety:
    def test_metachar_in_pr_number_passed_literally(self) -> None:
        calls: list[list[str]] = []

        def stub(cmd: list[str], **kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="{}", stderr="")

        rp.run(["gh", "pr", "view", "42; echo PWNED", "--json", "state"], runner=stub)
        assert calls[0][3] == "42; echo PWNED", (
            "shell metachar must be a literal arg, not interpreted"
        )

    def test_metachar_branch_name_passed_literally(self) -> None:
        calls: list[list[str]] = []

        def stub(cmd: list[str], **kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        branch = "feature/$(id)"
        rp.run(["git", "push", "origin", branch], runner=stub)
        assert calls[0][3] == branch

    def test_no_shell_flag_is_false(self) -> None:
        assert rp.SHELL_FALSE is True, "runner.SHELL_FALSE must be True (documents shell=False)"


# ---------------------------------------------------------------------------
# Timeout: default runner passes a timeout to subprocess
# ---------------------------------------------------------------------------


class TestRunnerTimeout:
    def test_default_runner_passes_timeout_kwarg(self, tmp_path: Path) -> None:
        captured: list[dict] = []

        import subprocess as _sp

        original_run = _sp.run

        def spy(*args, **kwargs):
            captured.append(kwargs)
            return original_run(*args, **kwargs)

        import unittest.mock as mock

        repo = tmp_path / "repo"
        _git_init_repo(repo)
        with mock.patch("subprocess.run", side_effect=spy):
            rp._default_runner(["git", "rev-parse", "HEAD"], cwd=str(repo))

        assert captured, "subprocess.run was not called"
        assert "timeout" in captured[0], "_default_runner must pass timeout= to subprocess.run"

    def test_rp_run_forwards_timeout_to_stub(self) -> None:
        captured_kwargs: list[dict] = []

        def stub(cmd: list[str], **kwargs):
            captured_kwargs.append(kwargs)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        rp.run(["git", "status"], runner=stub, timeout=99)
        assert captured_kwargs[0].get("timeout") == 99
