"""Tests for trailhead/update.py — apply-mode `trailhead update` (no `--check`).

Apply mode fast-forwards the stamped checkout, re-wires (`trailhead.install.
wire_all_harnesses`), and refreshes the provenance stamp. It is a true no-op on
any failure short of a completed re-wire: git access is injected via `runner`
so no test ever touches a real git checkout except the rollback test, which
uses a real throwaway git repo under `tmp_path` to assert on actual tree
state rather than mocked calls.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import datetime
from io import StringIO
from pathlib import Path

import pytest

from trailhead import update, update_run
from trailhead.install_config import ResolvedConfig, ResolvedHarness, ResolvedPlugin
from trailhead.provenance import read_stamp
from trailhead.wire import WireError, wire_lock

_OLD_SHA = "a" * 40
_NEW_SHA = "b" * 40
_ORIGIN_URL = "https://example.com/r.git"
_BRANCH = "origin/main"


def _git_sub(argv) -> str:
    """The git subcommand of a `["git", "-C", path, <global flags>, sub, ...]`
    argv, skipping the global flags (`--no-optional-locks`, `-c k=v`) that
    precede it."""
    i = 3
    while argv[i].startswith("-"):
        i += 2 if argv[i] == "-c" else 1
    return argv[i]


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        **os.environ,
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(home),
        # Outpost is unconfigured unless a test writes a config here — never
        # the developer's real outpost config.
        "OUTPOST_CONFIG_DIR": str(tmp_path / "outpost-config"),
    }


def _checkout(tmp_path: Path) -> Path:
    path = tmp_path / "home" / "checkout"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _install_stamp(tmp_path: Path, env: dict[str, str], *, sha: str = _OLD_SHA) -> Path:
    from trailhead import provenance

    checkout = _checkout(tmp_path)
    stamp = {
        "checkout": str(checkout),
        "sha": sha,
        "wired_at": "2026-01-01T00:00:00Z",
        "last_check": None,
    }
    provenance._atomic_write_json(provenance.stamp_path(env=env), stamp)
    return checkout


class _FakeCfg:
    def __init__(self, harnesses=()):
        self.harnesses = list(harnesses)


def _make_runner(
    *,
    status_stdout: str = "",
    status_rc: int = 0,
    fetch_rc: int = 0,
    fetch_stderr: str = "",
    remote_branch_sha: str = _NEW_SHA,
    remote_branch_rc: int = 0,
    ancestor_rc: int = 0,
    remote_is_ancestor_rc: int = 1,
    merge_rc: int = 0,
    merge_stderr: str = "",
    probe_sha: str = _NEW_SHA,
    probe_branch: str = _BRANCH,
    head_sha: str = _OLD_SHA,
    reset_rc: int = 0,
    reset_stderr: str = "",
):
    """A recording git-command stub dispatching on the git subcommand.

    `rev-parse` resolves the tracked upstream branch, that branch's remote
    sha, and HEAD — disambiguated by the revision argument. HEAD is stateful:
    it reads `head_sha` until a fast-forward succeeds and `probe_sha`
    afterwards, so the pre-merge and post-merge reads differ as they do in a
    real checkout.
    """
    calls: list[list[str]] = []
    merged: list[bool] = []

    def runner(args, **kw):
        calls.append(list(args))
        assert isinstance(args, list), f"argv must be a list, not interpolated: {args!r}"
        assert kw.get("shell") is not True, "git must never be invoked with shell=True"
        assert args[0] == "git"
        sub = _git_sub(args)
        if sub == "status":
            return subprocess.CompletedProcess(args, status_rc, stdout=status_stdout, stderr="")
        if sub == "fetch":
            return subprocess.CompletedProcess(args, fetch_rc, stdout="", stderr=fetch_stderr)
        if sub == "rev-parse":
            rev = args[4]
            if rev == "HEAD":
                current = probe_sha if merged else head_sha
                return subprocess.CompletedProcess(args, 0, stdout=current + "\n", stderr="")
            if rev == "--abbrev-ref":
                return subprocess.CompletedProcess(args, 0, stdout=probe_branch + "\n", stderr="")
            # resolving the stamped branch itself
            return subprocess.CompletedProcess(
                args, remote_branch_rc, stdout=(remote_branch_sha + "\n") if remote_branch_rc == 0 else "", stderr=""
            )
        if sub == "merge-base":
            # Two distinct questions share this subcommand: `-- <branch> HEAD`
            # asks whether the remote is already an ancestor of HEAD (nothing
            # to pull); `HEAD -- <branch>` asks the fast-forward question.
            if args[5] == "--":
                return subprocess.CompletedProcess(
                    args, remote_is_ancestor_rc, stdout="", stderr=""
                )
            return subprocess.CompletedProcess(args, ancestor_rc, stdout="", stderr="")
        if sub == "merge":
            if merge_rc == 0:
                merged.append(True)
            return subprocess.CompletedProcess(args, merge_rc, stdout="", stderr=merge_stderr)
        if sub == "reset":
            return subprocess.CompletedProcess(args, reset_rc, stdout="", stderr=reset_stderr)
        raise AssertionError(f"unexpected git invocation: {args}")

    return runner, calls


class TestConsentGate:
    def test_non_interactive_without_yes_refuses_and_mutates_nothing(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner()
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **kw: {})

        exit_code = update.run_update_apply(
            env=env, runner=runner, assume_yes=False, is_tty=lambda: False
        )

        assert exit_code != 0
        assert not calls, f"expected zero git invocations, got {calls}"

    def test_yes_flag_bypasses_the_tty_requirement(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner()
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **kw: {})

        exit_code = update.run_update_apply(
            env=env, runner=runner, assume_yes=True, is_tty=lambda: False
        )

        assert exit_code == 0
        assert calls, "expected git invocations once consent is given"

    def test_interactive_tty_confirmation_accepted_proceeds(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(remote_branch_sha=_NEW_SHA)
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        wire_calls = []
        monkeypatch.setattr(
            update, "wire_all_harnesses", lambda *a, **kw: wire_calls.append(1) or {}
        )
        monkeypatch.setattr(sys, "stdin", StringIO("y\n"))

        exit_code = update.run_update_apply(
            env=env, runner=runner, assume_yes=False, is_tty=lambda: True
        )

        assert exit_code == 0
        assert wire_calls, "an accepted confirmation must proceed to the wire"
        assert any(_git_sub(c) == "fetch" for c in calls)

    def test_interactive_tty_confirmation_declined_aborts_and_mutates_nothing(
        self, tmp_path, monkeypatch
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(remote_branch_sha=_NEW_SHA)
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        wire_calls = []
        monkeypatch.setattr(
            update, "wire_all_harnesses", lambda *a, **kw: wire_calls.append(1) or {}
        )
        monkeypatch.setattr(sys, "stdin", StringIO("n\n"))

        exit_code = update.run_update_apply(
            env=env, runner=runner, assume_yes=False, is_tty=lambda: True
        )

        assert exit_code == 0
        assert not calls, f"a declined confirmation must mutate nothing, got {calls}"
        assert not wire_calls
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OLD_SHA


class TestDirtyCheckout:
    def test_dirty_checkout_refuses_and_leaves_the_stamp_untouched(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(status_stdout=" M some_file.py\n")
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        wire_calls = []
        monkeypatch.setattr(
            update, "wire_all_harnesses", lambda *a, **kw: wire_calls.append(1) or {}
        )

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert exit_code != 0
        assert not any(_git_sub(c) in ("fetch", "merge", "reset") for c in calls)
        assert not wire_calls
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OLD_SHA

    def test_dirty_checkout_error_names_a_recovery_command(self, tmp_path, monkeypatch, capsys):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(status_stdout=" M some_file.py\n")
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **kw: {})

        update.run_update_apply(env=env, runner=runner, assume_yes=True)

        err = capsys.readouterr().err
        assert err.startswith("trailhead: ")
        assert "stash" in err.lower() or "commit" in err.lower()


class TestDiverged:
    def test_diverged_checkout_refuses_and_writes_nothing(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(ancestor_rc=1)
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        wire_calls = []
        monkeypatch.setattr(
            update, "wire_all_harnesses", lambda *a, **kw: wire_calls.append(1) or {}
        )

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert exit_code != 0
        assert not any(_git_sub(c) == "merge" for c in calls)
        assert not wire_calls
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OLD_SHA


class TestDivergenceUnknown:
    @pytest.mark.parametrize(
        "runner_kwargs",
        [{"remote_is_ancestor_rc": 128}, {"ancestor_rc": 128}],
        ids=["first-merge-base-errors", "second-merge-base-errors"],
    )
    def test_a_merge_base_error_is_refused_without_claiming_divergence(
        self, tmp_path, monkeypatch, capsys, runner_kwargs
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(**runner_kwargs)
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **kw: {})

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        err = capsys.readouterr().err
        assert exit_code == 1
        assert not any(_git_sub(c) == "merge" for c in calls)
        assert "could not determine whether" in err
        assert "diverged" not in err
        assert err.count("\n") == 1


class TestAlreadyUpToDate:
    def test_up_to_date_is_a_noop_and_exits_zero_without_changing_the_sha(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(remote_branch_sha=_OLD_SHA)
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        wire_calls = []
        monkeypatch.setattr(
            update, "wire_all_harnesses", lambda *a, **kw: wire_calls.append(1) or {}
        )

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert exit_code == 0
        assert not any(_git_sub(c) == "merge" for c in calls)
        assert not wire_calls
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OLD_SHA


class TestCleanUpgrade:
    def test_clean_behind_checkout_fast_forwards_rewires_and_advances_the_stamp(
        self, tmp_path, monkeypatch
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(remote_branch_sha=_NEW_SHA, probe_sha=_NEW_SHA)
        wire_calls = []
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(
            update, "wire_all_harnesses", lambda *a, **kw: wire_calls.append(1) or {}
        )

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert exit_code == 0
        assert len(wire_calls) == 1
        merge_calls = [c for c in calls if _git_sub(c) == "merge"]
        assert len(merge_calls) == 1
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _NEW_SHA


class TestDryRun:
    def test_dry_run_performs_no_mutation_no_wire_and_no_stamp_write(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(remote_branch_sha=_NEW_SHA)
        wire_calls = []
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(
            update, "wire_all_harnesses", lambda *a, **kw: wire_calls.append(1) or {}
        )

        exit_code = update.run_update_apply(
            env=env, runner=runner, assume_yes=False, dry_run=True, is_tty=lambda: False
        )

        assert exit_code == 0
        assert not any(_git_sub(c) in ("fetch", "merge", "reset") for c in calls)
        assert not wire_calls
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OLD_SHA


class TestWireLock:
    def test_concurrent_lock_holder_blocks_the_upgrade_before_any_git_call(
        self, tmp_path, monkeypatch
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(remote_branch_sha=_NEW_SHA)
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **kw: {})

        with wire_lock(env=env):
            exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert exit_code != 0
        assert not any(_git_sub(c) == "fetch" for c in calls), (
            "the fetch must never run while a concurrent operation holds the wire lock"
        )
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OLD_SHA


class TestStampNeverClaimsAnIncompleteWire:
    def test_wire_failure_leaves_the_stamp_at_the_pre_upgrade_sha(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, calls = _make_runner(remote_branch_sha=_NEW_SHA)
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())

        def _raising_wire(*a, **kw):
            raise WireError(tool="craft", stage="register", cause=RuntimeError("boom"))

        monkeypatch.setattr(update, "wire_all_harnesses", _raising_wire)

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert exit_code != 0
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OLD_SHA


class TestRollbackReportsTruthfully:
    """The rollback branch after a failed re-wire must report exactly what
    actually happened — never a claimed restoration that didn't occur. Two
    distinct failure shapes: the reset succeeds but the retried re-wire still
    fails (partial restore), and the reset itself fails (no restore at
    all)."""

    def test_reset_succeeds_but_rewire_retry_also_fails_reports_partial_restore(
        self, tmp_path, monkeypatch, capsys
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, _ = _make_runner()
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(
            update, "wire_all_harnesses", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert exit_code != 0
        err = capsys.readouterr().err
        assert "rolled" in err.lower()
        assert "could not" in err.lower() or "could NOT" in err
        assert "restore" in err.lower() or "wiring" in err.lower()
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OLD_SHA

    def test_reset_itself_fails_reports_manual_repair(self, tmp_path, monkeypatch, capsys):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, _ = _make_runner(reset_rc=1, reset_stderr="fatal: could not reset")
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(
            update, "wire_all_harnesses", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert exit_code != 0
        err = capsys.readouterr().err
        assert "could not" in err.lower() and "rolled back" in err.lower()
        assert "repair manually" in err.lower() or "reset --hard" in err
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OLD_SHA


class TestErrorHygiene:
    @pytest.mark.parametrize(
        "setup_kwargs",
        [
            {},  # no stamp at all
        ],
    )
    def test_missing_stamp_error_is_clean_no_traceback_no_ansi(
        self, tmp_path, setup_kwargs, capsys
    ):
        env = _env(tmp_path)
        runner, _ = _make_runner()

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        err = capsys.readouterr().err
        assert exit_code != 0
        assert err.startswith("trailhead: ")
        assert "Traceback" not in err
        assert "\x1b" not in err

    def test_rejected_stamp_error_differs_from_the_absent_stamp_error(self, tmp_path, capsys):
        runner, _ = _make_runner()

        env_absent = _env(tmp_path)
        exit_code_absent = update.run_update_apply(env=env_absent, runner=runner, assume_yes=True)
        err_absent = capsys.readouterr().err
        assert exit_code_absent != 0

        rejected_root = tmp_path / "rejected"
        rejected_root.mkdir()
        env_rejected = _env(rejected_root)
        from trailhead import provenance

        checkout = _checkout(rejected_root)
        rejected_stamp = {
            "checkout": str(checkout),
            "sha": "not-a-real-sha",
            "wired_at": "2026-01-01T00:00:00Z",
            "last_check": None,
        }
        provenance._atomic_write_json(provenance.stamp_path(env=env_rejected), rejected_stamp)

        exit_code_rejected = update.run_update_apply(env=env_rejected, runner=runner, assume_yes=True)
        err_rejected = capsys.readouterr().err

        assert exit_code_rejected != 0
        assert err_rejected != err_absent

    def test_dirty_checkout_error_has_no_traceback_no_ansi(self, tmp_path, monkeypatch, capsys):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        runner, _ = _make_runner(status_stdout=" M x\n")
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **kw: {})

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        err = capsys.readouterr().err
        assert exit_code != 0
        assert err.startswith("trailhead: ")
        assert "Traceback" not in err
        assert "\x1b" not in err


# ---------------------------------------------------------------------------
# True no-op on wire failure — real git repo, asserted on actual tree state.
# ---------------------------------------------------------------------------


def _run_git_real(checkout: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(checkout), *args], capture_output=True, text=True, check=True
    )


def _init_real_repo_pair(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """Build a real origin repo + a clone of it one commit behind.

    Returns (origin, checkout, old_sha, new_sha).
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.email", "a@example.com"], check=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.name", "Test"], check=True)
    (origin / "file.txt").write_text("one\n")
    subprocess.run(["git", "-C", str(origin), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(origin), "commit", "-m", "first"], check=True, capture_output=True)
    old_sha = _run_git_real(origin, "rev-parse", "HEAD").stdout.strip()

    checkout = tmp_path / "home" / "checkout"
    checkout.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", str(origin), str(checkout)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.email", "a@example.com"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Test"], check=True)

    (origin / "file.txt").write_text("two\n")
    subprocess.run(["git", "-C", str(origin), "commit", "-am", "second"], check=True, capture_output=True)
    new_sha = _run_git_real(origin, "rev-parse", "HEAD").stdout.strip()

    return origin, checkout, old_sha, new_sha


def _real_runner(args, **kw):
    return subprocess.run(args, **kw)


def _git(checkout: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(checkout), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


def _dirty(checkout: Path) -> None:
    (checkout / "file.txt").write_text("uncommitted\n")


def _commit_locally(checkout: Path) -> None:
    (checkout / "local.txt").write_text("mine\n")
    _git(checkout, "add", "local.txt")
    _git(checkout, "commit", "-m", "local")


def _catch_up(checkout: Path) -> None:
    _git(checkout, "fetch", "origin")
    _git(checkout, "merge", "--ff-only", "origin/main")


class TestTrueNoOpOnWireFailure:
    def test_rollback_restores_checkout_sha_and_prior_wiring_on_wire_failure(
        self, tmp_path, monkeypatch, capsys
    ):
        env = _env(tmp_path)
        origin, checkout, old_sha, new_sha = _init_real_repo_pair(tmp_path)
        from trailhead import provenance

        stamp = {
            "checkout": str(checkout),
            "sha": old_sha,
            "wired_at": "2026-01-01T00:00:00Z",
            "last_check": None,
        }
        provenance._atomic_write_json(provenance.stamp_path(env=env), stamp)

        marker = tmp_path / "wired-for-sha.txt"
        wire_call_shas: list[str] = []

        def _fake_wire(cfg, *, env, runner=None, quiet=False, as_json=False):
            head = _run_git_real(checkout, "rev-parse", "HEAD").stdout.strip()
            wire_call_shas.append(head)
            if head == new_sha:
                raise WireError(tool="craft", stage="register", cause=RuntimeError("boom"))
            marker.write_text(head)
            return {}

        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", _fake_wire)

        exit_code = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert exit_code != 0
        current_head = _run_git_real(checkout, "rev-parse", "HEAD").stdout.strip()
        assert current_head == old_sha, "checkout must be rolled back to the pre-upgrade sha"
        assert wire_call_shas == [new_sha, old_sha], "wire must be retried once against the reverted checkout"
        assert marker.exists() and marker.read_text() == old_sha, "prior wiring must be restored"

        err = capsys.readouterr().err
        assert err.startswith("trailhead: ")
        assert "boom" in err or "craft" in err
        assert "trailhead update" in err or "re-run" in err.lower()

        stamp_after = read_stamp(env=env)
        assert stamp_after["sha"] == old_sha


# ---------------------------------------------------------------------------
# A genuinely FAILING `claude plugin install` (nonzero exit, not a raising
# stub) must still be detected and trigger rollback — exercising the real
# `wire_all_harnesses` -> `wire()` -> `ClaudeCodeHarness.install_tool` path,
# never a patched-out `wire_all_harnesses`.
# ---------------------------------------------------------------------------


def _real_git_and_stubbed_claude_runner(*, fail_install: bool):
    """Real git subprocess calls; `claude plugin ...` calls are stubbed to a
    genuinely failing (or succeeding) CompletedProcess — never a raise — so
    this exercises the harness's own returncode check, not exception handling.
    """

    def runner(args, **kw):
        if args[0] == "git":
            return subprocess.run(args, **kw)
        if args[0] == "claude":
            if "install" in args and fail_install:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    return runner


class TestRealFailingWireTriggersRollback:
    def test_a_genuinely_failing_claude_plugin_install_rolls_back(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        env["TRAILHEAD_CLAUDE_DIR"] = str(tmp_path / "claude-dir")
        origin, checkout, old_sha, new_sha = _init_real_repo_pair(tmp_path)
        from trailhead import provenance

        stamp = {
            "checkout": str(checkout),
            "sha": old_sha,
            "wired_at": "2026-01-01T00:00:00Z",
            "last_check": None,
        }
        provenance._atomic_write_json(provenance.stamp_path(env=env), stamp)

        cfg = ResolvedConfig(
            cli_flags={},
            harnesses=[ResolvedHarness(name="claude_code", plugins=[ResolvedPlugin(name="camp")])],
        )
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: cfg)

        runner = _real_git_and_stubbed_claude_runner(fail_install=True)

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert exit_code != 0
        current_head = _run_git_real(checkout, "rev-parse", "HEAD").stdout.strip()
        assert current_head == old_sha, "a real failing wire must still roll the checkout back"
        stamp_after = read_stamp(env=env)
        assert stamp_after["sha"] == old_sha, "the stamp must never advance past an install that failed"

    def test_a_genuinely_succeeding_claude_plugin_install_advances_the_stamp(
        self, tmp_path, monkeypatch
    ):
        env = _env(tmp_path)
        env["TRAILHEAD_CLAUDE_DIR"] = str(tmp_path / "claude-dir")
        origin, checkout, old_sha, new_sha = _init_real_repo_pair(tmp_path)
        from trailhead import provenance

        stamp = {
            "checkout": str(checkout),
            "sha": old_sha,
            "wired_at": "2026-01-01T00:00:00Z",
            "last_check": None,
        }
        provenance._atomic_write_json(provenance.stamp_path(env=env), stamp)

        cfg = ResolvedConfig(
            cli_flags={},
            harnesses=[ResolvedHarness(name="claude_code", plugins=[ResolvedPlugin(name="camp")])],
        )
        monkeypatch.setattr(update, "resolve_config_for_env", lambda env: cfg)

        runner = _real_git_and_stubbed_claude_runner(fail_install=False)

        exit_code = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert exit_code == 0
        current_head = _run_git_real(checkout, "rev-parse", "HEAD").stdout.strip()
        assert current_head == new_sha
        stamp_after = read_stamp(env=env)
        assert stamp_after["sha"] == new_sha


class TestApplyDerivesBranchAndReWiresAStaleInstall:
    """Apply mode reads the tracked branch from the checkout, never from the
    stamp, and treats a checkout that is current but wired from an older sha
    as work to do rather than a no-op."""

    def _stamp(self, tmp_path, env, *, sha=_OLD_SHA):
        from trailhead import provenance

        checkout = _checkout(tmp_path)
        provenance._atomic_write_json(
            provenance.stamp_path(env=env),
            {
                "checkout": str(checkout),
                "sha": sha,
                "wired_at": "2026-01-01T00:00:00Z",
                "last_check": None,
            },
        )
        return checkout

    def _runner(self, *, head=_NEW_SHA, remote=_NEW_SHA, branch=_BRANCH):
        calls: list[list[str]] = []

        def runner(args, **kw):
            calls.append(list(args))
            sub = _git_sub(args)
            if sub == "status":
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if sub == "fetch":
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if sub == "rev-parse":
                if args[4] == "--abbrev-ref":
                    return subprocess.CompletedProcess(args, 0, stdout=branch + "\n", stderr="")
                if args[4] == "HEAD":
                    return subprocess.CompletedProcess(args, 0, stdout=head + "\n", stderr="")
                return subprocess.CompletedProcess(args, 0, stdout=remote + "\n", stderr="")
            if sub in ("merge-base", "merge"):
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            raise AssertionError(f"unexpected git invocation: {args}")

        return runner, calls

    def test_origin_url_is_never_probed(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        self._stamp(tmp_path, env)
        runner, calls = self._runner()
        monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **k: None)

        update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert not any(_git_sub(c) == "remote" for c in calls)

    def test_current_checkout_with_a_stale_stamp_re_wires(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        self._stamp(tmp_path, env, sha=_OLD_SHA)
        runner, calls = self._runner(head=_NEW_SHA, remote=_NEW_SHA)
        wired = []
        monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **k: wired.append(1))

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 0
        assert wired == [1]
        assert not any(_git_sub(c) == "merge" for c in calls)
        assert read_stamp(env=env)["sha"] == _NEW_SHA

    def test_everything_level_is_a_true_no_op(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        self._stamp(tmp_path, env, sha=_NEW_SHA)
        runner, calls = self._runner(head=_NEW_SHA, remote=_NEW_SHA)
        wired = []
        monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **k: wired.append(1))

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 0
        assert wired == []
        assert not any(_git_sub(c) in ("merge", "merge-base") for c in calls)


class TestCheckoutAheadOfItsRemote:
    """A checkout carrying local commits is ahead of its tracked branch, not
    diverged from it. There is nothing to fast-forward, so a stale install on
    top of one must still re-wire rather than be refused with a merge command
    that would do nothing."""

    def _stamp(self, tmp_path, env, *, sha):
        from trailhead import provenance

        checkout = _checkout(tmp_path)
        provenance._atomic_write_json(
            provenance.stamp_path(env=env),
            {
                "checkout": str(checkout),
                "sha": sha,
                "wired_at": "2026-01-01T00:00:00Z",
                "last_check": None,
            },
        )
        return checkout

    def _runner(self, *, head, remote, remote_is_ancestor_of_head=True):
        calls: list[list[str]] = []

        def runner(args, **kw):
            calls.append(list(args))
            sub = _git_sub(args)
            if sub == "status":
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if sub == "fetch":
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if sub == "rev-parse":
                if args[4] == "--abbrev-ref":
                    return subprocess.CompletedProcess(args, 0, stdout=_BRANCH + "\n", stderr="")
                if args[4] == "HEAD":
                    return subprocess.CompletedProcess(args, 0, stdout=head + "\n", stderr="")
                return subprocess.CompletedProcess(args, 0, stdout=remote + "\n", stderr="")
            if sub == "merge-base":
                # `<branch> .. HEAD` asks "is the remote an ancestor of HEAD";
                # `HEAD .. <branch>` asks the fast-forward question.
                asks_remote_is_ancestor = args[5] == "--" and args[6] == _BRANCH
                if asks_remote_is_ancestor:
                    return subprocess.CompletedProcess(
                        args, 0 if remote_is_ancestor_of_head else 1, stdout="", stderr=""
                    )
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
            raise AssertionError(f"unexpected git invocation: {args}")

        return runner, calls

    def test_stale_install_on_an_ahead_checkout_re_wires(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        self._stamp(tmp_path, env, sha=_OLD_SHA)
        runner, calls = self._runner(head=_NEW_SHA, remote=_OLD_SHA)
        wired = []
        monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **k: wired.append(1))

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 0
        assert wired == [1]
        assert not any(_git_sub(c) == "merge" for c in calls)

    def test_level_install_on_an_ahead_checkout_is_a_no_op(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        self._stamp(tmp_path, env, sha=_NEW_SHA)
        runner, calls = self._runner(head=_NEW_SHA, remote=_OLD_SHA)
        wired = []
        monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **k: wired.append(1))

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 0
        assert wired == []

    def test_a_genuinely_diverged_checkout_is_still_refused(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        self._stamp(tmp_path, env, sha=_OLD_SHA)
        runner, _ = self._runner(
            head=_NEW_SHA, remote="c" * 40, remote_is_ancestor_of_head=False
        )
        monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())
        monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **k: None)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1


# ---------------------------------------------------------------------------
# Outpost — a configured outpost checkout is upgraded after the install
# ---------------------------------------------------------------------------

_OUTPOST_OLD = "c" * 40
_OUTPOST_NEW = "d" * 40


def _outpost_checkout(tmp_path: Path) -> Path:
    path = tmp_path / "home" / "outpost"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _configure_outpost(env: dict[str, str], checkout: Path | str) -> None:
    cfg = Path(env["OUTPOST_CONFIG_DIR"])
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.toml").write_text(f'checkout = "{checkout}"\n')


def _outpost_runner(
    outpost_checkout: Path,
    *,
    trailhead_kw: dict | None = None,
    status_stdout: str = "",
    head: str = _OUTPOST_OLD,
    remote: str = _OUTPOST_NEW,
    remote_is_ancestor_rc: int = 1,
    ancestor_rc: int = 0,
    merge_rc: int = 0,
    reset_rc: int = 0,
):
    """Route git calls by target checkout: the install's go to `_make_runner`,
    outpost's to a stateful stub whose HEAD moves on a successful merge and
    back on a successful reset."""
    trailhead_runner, _ = _make_runner(**(trailhead_kw or {}))
    calls: list[list[str]] = []
    state = {"head": head}

    def runner(args, **kw):
        calls.append(list(args))
        assert isinstance(args, list)
        assert kw.get("shell") is not True
        if args[2] != str(outpost_checkout):
            return trailhead_runner(args, **kw)
        sub = _git_sub(args)
        if sub == "status":
            return subprocess.CompletedProcess(args, 0, stdout=status_stdout, stderr="")
        if sub == "fetch":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if sub == "rev-parse":
            if args[4] == "--abbrev-ref":
                return subprocess.CompletedProcess(args, 0, stdout=_BRANCH + "\n", stderr="")
            if args[4] == "HEAD":
                return subprocess.CompletedProcess(args, 0, stdout=state["head"] + "\n", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout=remote + "\n", stderr="")
        if sub == "merge-base":
            if args[5] == "--":
                return subprocess.CompletedProcess(args, remote_is_ancestor_rc, stdout="", stderr="")
            return subprocess.CompletedProcess(args, ancestor_rc, stdout="", stderr="")
        if sub == "merge":
            if merge_rc == 0:
                state["head"] = remote
            return subprocess.CompletedProcess(args, merge_rc, stdout="", stderr="merge boom")
        if sub == "reset":
            if reset_rc == 0:
                state["head"] = args[-1]
            return subprocess.CompletedProcess(args, reset_rc, stdout="", stderr="reset boom")
        raise AssertionError(f"unexpected git invocation against outpost: {args}")

    return runner, calls, state


class _OutpostSpy:
    """Records the outpost lifecycle steps apply drives, in order, and the
    outpost HEAD each ran against. `fail` names steps that raise, and on which
    call (1-based) — so a rollback's retry can be made to succeed or fail."""

    def __init__(self, monkeypatch, state, *, running=False, fail=None):
        self.steps: list[tuple[str, str]] = []
        self._fail = fail or {}
        self._counts: dict[str, int] = {}

        def _step(name):
            def run(*a, **kw):
                self.steps.append((name, state["head"]))
                self._counts[name] = self._counts.get(name, 0) + 1
                if self._counts[name] in self._fail.get(name, ()):
                    raise update.OutpostLifecycleError(f"{name} exploded")
                return 0

            return run

        monkeypatch.setattr(update.outpost_lifecycle, "install_dependencies", _step("deps"))
        monkeypatch.setattr(update.outpost_lifecycle, "build", _step("build"))
        monkeypatch.setattr(update.outpost_lifecycle, "restart", _step("restart"))
        managed = {"pid": 4242, "checkout": "/outpost"} if running else None
        monkeypatch.setattr(update.outpost_lifecycle, "managed_outpost", lambda *a, **k: managed)

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.steps]


def _outpost_calls(calls, outpost_checkout: Path):
    return [c for c in calls if c[2] == str(outpost_checkout)]


def _patch_wire(monkeypatch) -> list[int]:
    wired: list[int] = []
    monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())
    monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **k: wired.append(1))
    return wired


class TestOutpostUpgrade:
    def test_unconfigured_outpost_is_never_touched(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        runner, calls, state = _outpost_runner(
            outpost, trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA}
        )
        spy = _OutpostSpy(monkeypatch, state)
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 0
        assert _outpost_calls(calls, outpost) == []
        assert spy.steps == []

    def test_behind_outpost_is_fast_forwarded_installed_and_built_when_not_running(
        self, tmp_path, monkeypatch
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, calls, state = _outpost_runner(
            outpost, trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA}
        )
        spy = _OutpostSpy(monkeypatch, state, running=False)
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 0
        assert [_git_sub(c) for c in _outpost_calls(calls, outpost)].count("merge") == 1
        assert spy.steps == [("deps", _OUTPOST_NEW), ("build", _OUTPOST_NEW)]
        assert read_stamp(env=env)["sha"] == _NEW_SHA

    def test_a_running_daemon_is_restarted_rather_than_just_built(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, _calls, state = _outpost_runner(
            outpost, trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA}
        )
        spy = _OutpostSpy(monkeypatch, state, running=True)
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 0
        assert spy.names == ["deps", "restart"]

    def test_outpost_is_upgraded_even_when_the_install_is_already_current(
        self, tmp_path, monkeypatch
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env, sha=_OLD_SHA)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, calls, state = _outpost_runner(
            outpost,
            trailhead_kw={"remote_branch_sha": _OLD_SHA, "head_sha": _OLD_SHA},
        )
        spy = _OutpostSpy(monkeypatch, state)
        wired = _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 0
        assert wired == []
        assert spy.names == ["deps", "build"]
        assert state["head"] == _OUTPOST_NEW

    def test_a_level_outpost_is_left_alone(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, calls, state = _outpost_runner(
            outpost,
            trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA},
            head=_OUTPOST_NEW,
            remote=_OUTPOST_NEW,
        )
        spy = _OutpostSpy(monkeypatch, state)
        monkeypatch.setattr(update.outpost_lifecycle, "built_sha", lambda *a, **k: _OUTPOST_NEW)
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 0
        assert "merge" not in [_git_sub(c) for c in _outpost_calls(calls, outpost)]
        assert spy.steps == []

    def test_a_dirty_outpost_refuses_the_whole_upgrade_before_any_mutation(
        self, tmp_path, monkeypatch, capsys
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, calls, state = _outpost_runner(
            outpost,
            trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA},
            status_stdout=" M server/index.ts\n",
        )
        spy = _OutpostSpy(monkeypatch, state)
        wired = _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1
        assert not any(_git_sub(c) in ("fetch", "merge", "reset") for c in calls)
        assert wired == [] and spy.steps == []
        assert read_stamp(env=env)["sha"] == _OLD_SHA
        err = capsys.readouterr().err
        assert err.startswith("trailhead: ")
        assert str(outpost) in err

    def test_an_unusable_outpost_config_refuses_before_any_git_runs(
        self, tmp_path, monkeypatch, capsys
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        _configure_outpost(env, "relative/outpost")
        runner, calls, state = _outpost_runner(_outpost_checkout(tmp_path))
        _OutpostSpy(monkeypatch, state)
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1
        assert calls == []
        assert "absolute" in capsys.readouterr().err

    def test_a_diverged_outpost_keeps_the_install_upgrade_and_reports_failure(
        self, tmp_path, monkeypatch, capsys
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, calls, state = _outpost_runner(
            outpost,
            trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA},
            ancestor_rc=1,
        )
        spy = _OutpostSpy(monkeypatch, state)
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1
        assert read_stamp(env=env)["sha"] == _NEW_SHA
        assert "merge" not in [_git_sub(c) for c in _outpost_calls(calls, outpost)]
        assert spy.steps == []
        err = capsys.readouterr().err
        assert "diverged" in err and str(outpost) in err

    def test_a_failed_build_rolls_outpost_back_and_rebuilds_the_prior_version(
        self, tmp_path, monkeypatch, capsys
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, calls, state = _outpost_runner(
            outpost, trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA}
        )
        spy = _OutpostSpy(monkeypatch, state, fail={"build": (1,)})
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1
        assert state["head"] == _OUTPOST_OLD
        assert spy.steps == [
            ("deps", _OUTPOST_NEW),
            ("build", _OUTPOST_NEW),
            ("deps", _OUTPOST_OLD),
            ("build", _OUTPOST_OLD),
        ]
        assert read_stamp(env=env)["sha"] == _NEW_SHA, "the install upgrade is kept"
        err = capsys.readouterr().err
        assert "build exploded" in err
        assert "restored" in err

    def test_a_failed_restart_rolls_back_and_restarts_the_prior_version(
        self, tmp_path, monkeypatch
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, _calls, state = _outpost_runner(
            outpost, trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA}
        )
        spy = _OutpostSpy(monkeypatch, state, running=True, fail={"restart": (1,)})
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1
        assert spy.steps[-2:] == [("deps", _OUTPOST_OLD), ("restart", _OUTPOST_OLD)]

    def test_a_rollback_whose_rebuild_also_fails_says_so(self, tmp_path, monkeypatch, capsys):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, _calls, state = _outpost_runner(
            outpost, trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA}
        )
        _OutpostSpy(monkeypatch, state, fail={"build": (1, 2)})
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1
        assert state["head"] == _OUTPOST_OLD
        err = capsys.readouterr().err
        assert "could NOT be rebuilt" in err
        assert "restored" not in err

    def test_a_rollback_whose_reset_fails_names_the_manual_repair(
        self, tmp_path, monkeypatch, capsys
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, _calls, state = _outpost_runner(
            outpost,
            trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA},
            reset_rc=1,
        )
        spy = _OutpostSpy(monkeypatch, state, fail={"deps": (1,)})
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1
        assert spy.names == ["deps"], "nothing is rebuilt on top of a failed reset"
        err = capsys.readouterr().err
        assert f"git -C {outpost} reset --hard {_OUTPOST_OLD}" in err

    def test_dry_run_names_outpost_and_mutates_nothing(self, tmp_path, monkeypatch, capsys):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, calls, state = _outpost_runner(outpost)
        spy = _OutpostSpy(monkeypatch, state)
        wired = _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=runner, dry_run=True)

        assert rc == 0
        assert not any(_git_sub(c) in ("fetch", "merge", "reset") for c in calls)
        assert wired == [] and spy.steps == []
        assert str(outpost) in capsys.readouterr().out

    def test_the_confirmation_prompt_names_the_outpost_checkout(
        self, tmp_path, monkeypatch, capsys
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, calls, state = _outpost_runner(outpost)
        _OutpostSpy(monkeypatch, state)
        _patch_wire(monkeypatch)
        monkeypatch.setattr(sys, "stdin", StringIO("n\n"))

        rc = update.run_update_apply(env=env, runner=runner, is_tty=lambda: True)

        assert rc == 0
        assert calls == []
        assert str(outpost) in capsys.readouterr().out


class TestRealOutpostRollback:
    def test_a_failed_outpost_build_restores_the_real_checkout(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _origin, checkout, old_sha, new_sha = _init_real_repo_pair(tmp_path)
        from trailhead import provenance

        provenance._atomic_write_json(
            provenance.stamp_path(env=env),
            {"checkout": str(checkout), "sha": new_sha, "wired_at": "2026-01-01T00:00:00Z", "last_check": None},
        )
        _run_git_real(checkout, "merge", "--ff-only", "origin/main")

        outpost_root = tmp_path / "outpost-repos"
        outpost_root.mkdir()
        _o_origin, outpost, o_old, o_new = _init_real_repo_pair(outpost_root)
        _configure_outpost(env, outpost)

        built_at: list[str] = []

        def _build(*a, **kw):
            head = _run_git_real(outpost, "rev-parse", "HEAD").stdout.strip()
            built_at.append(head)
            if head == o_new:
                raise update.OutpostLifecycleError("tsc failed")

        monkeypatch.setattr(update.outpost_lifecycle, "install_dependencies", lambda *a, **k: None)
        monkeypatch.setattr(update.outpost_lifecycle, "build", _build)
        monkeypatch.setattr(update.outpost_lifecycle, "managed_outpost", lambda *a, **k: None)
        _patch_wire(monkeypatch)

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert rc == 1
        assert _run_git_real(outpost, "rev-parse", "HEAD").stdout.strip() == o_old
        assert built_at == [o_new, o_old]


def _real_outpost_world(tmp_path: Path, monkeypatch, *, outpost_behind: bool):
    """A level, stamped real install plus a real outpost checkout (behind its
    origin, or caught up to it), configured as the outpost checkout. Returns
    (env, outpost, old_sha, new_sha)."""
    env = _env(tmp_path)
    env["OUTPOST_STATE_DIR"] = str(tmp_path / "outpost-state")
    _origin, checkout, _old, new_sha = _init_real_repo_pair(tmp_path)
    _install_stamp(tmp_path, env, sha=new_sha)
    _catch_up(checkout)

    outpost_root = tmp_path / "outpost-repos"
    outpost_root.mkdir()
    _o_origin, outpost, o_old, o_new = _init_real_repo_pair(outpost_root)
    if not outpost_behind:
        _catch_up(outpost)
    _configure_outpost(env, outpost)
    monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())
    monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **k: {})
    return env, outpost, o_old, o_new


class _Steps:
    """Records the outpost lifecycle steps apply drives, against real repos."""

    def __init__(self, monkeypatch, *, managed: bool):
        self.names: list[str] = []
        self.real_build = update.outpost_lifecycle.build
        for name in ("install_dependencies", "build", "restart"):
            monkeypatch.setattr(
                update.outpost_lifecycle,
                name,
                lambda *a, _n={"install_dependencies": "deps"}.get(name, name), **k: self.names.append(_n),
            )
        info = {"pid": 4242, "checkout": "/outpost"} if managed else None
        monkeypatch.setattr(update.outpost_lifecycle, "managed_outpost", lambda *a, **k: info)


def _stamp_build_at_head(env, outpost) -> None:
    update.outpost_lifecycle.build(env=env, build_cmd=[sys.executable, "-c", "pass"])


class TestOutpostRestartFollowsOwnership:
    def test_behind_with_a_managed_daemon_running_restarts(self, tmp_path, monkeypatch):
        env, _outpost, _o_old, _o_new = _real_outpost_world(tmp_path, monkeypatch, outpost_behind=True)
        steps = _Steps(monkeypatch, managed=True)

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert rc == 0
        assert steps.names == ["deps", "restart"]

    def test_behind_with_a_foreign_listener_on_the_port_builds_without_restarting(
        self, tmp_path, monkeypatch
    ):
        env, _outpost, _o_old, _o_new = _real_outpost_world(tmp_path, monkeypatch, outpost_behind=True)
        steps = _Steps(monkeypatch, managed=False)
        # The real ownership predicate decides here: no supervisor entry under
        # the isolated HOME and no pidfile, while a development copy answers
        # /health on the daemon port. The probe is faked rather than binding
        # 7313, which a live Outpost on the host may already hold.
        monkeypatch.setattr(update.outpost_lifecycle, "managed_outpost", self._real_managed_outpost)
        monkeypatch.setattr(
            update.outpost_lifecycle, "_probe_health", lambda port, timeout: {"status": "ok"}
        )

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert rc == 0
        assert steps.names == ["deps", "build"]

    _real_managed_outpost = staticmethod(update.outpost_lifecycle.managed_outpost)

    def test_behind_with_nothing_running_builds_without_starting_anything(self, tmp_path, monkeypatch):
        env, _outpost, _o_old, _o_new = _real_outpost_world(tmp_path, monkeypatch, outpost_behind=True)
        steps = _Steps(monkeypatch, managed=False)

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert rc == 0
        assert steps.names == ["deps", "build"]


class TestOutpostBuildStamp:
    def test_level_with_the_remote_and_a_matching_stamp_is_current_and_builds_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        env, outpost, _o_old, _o_new = _real_outpost_world(tmp_path, monkeypatch, outpost_behind=False)
        _stamp_build_at_head(env, outpost)
        steps = _Steps(monkeypatch, managed=True)
        capsys.readouterr()

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert rc == 0
        assert steps.names == []
        assert "outpost already up to date" in capsys.readouterr().out

    def test_level_with_a_different_stamp_rebuilds_and_reports_an_advance(
        self, tmp_path, monkeypatch, capsys
    ):
        env, outpost, o_old, _o_new = _real_outpost_world(tmp_path, monkeypatch, outpost_behind=False)
        _stamp_build_at_head(env, outpost)
        _run_git_real(outpost, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "local")
        steps = _Steps(monkeypatch, managed=False)
        capsys.readouterr()

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        out = capsys.readouterr().out
        assert rc == 0
        assert steps.names == ["deps", "build"]
        assert "outpost already up to date" not in out
        assert "rebuilt outpost" in out

    def test_level_with_a_different_stamp_restarts_when_the_managed_daemon_is_running(
        self, tmp_path, monkeypatch
    ):
        env, outpost, _o_old, _o_new = _real_outpost_world(tmp_path, monkeypatch, outpost_behind=False)
        _stamp_build_at_head(env, outpost)
        _run_git_real(outpost, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "local")
        steps = _Steps(monkeypatch, managed=True)

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert rc == 0
        assert steps.names == ["deps", "restart"]

    def test_level_with_no_stamp_rebuilds(self, tmp_path, monkeypatch, capsys):
        env, _outpost, _o_old, _o_new = _real_outpost_world(tmp_path, monkeypatch, outpost_behind=False)
        steps = _Steps(monkeypatch, managed=False)

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert rc == 0
        assert steps.names == ["deps", "build"]
        assert "rebuilt outpost" in capsys.readouterr().out

    def test_a_stale_rebuild_that_fails_is_reported_and_leaves_the_checkout_alone(
        self, tmp_path, monkeypatch, capsys
    ):
        env, outpost, o_old, _o_new = _real_outpost_world(tmp_path, monkeypatch, outpost_behind=False)
        head = _run_git_real(outpost, "rev-parse", "HEAD").stdout.strip()
        _Steps(monkeypatch, managed=False)

        def _boom(*a, **k):
            raise update.OutpostLifecycleError("tsc failed")

        monkeypatch.setattr(update.outpost_lifecycle, "build", _boom)

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert rc == 1
        assert _run_git_real(outpost, "rev-parse", "HEAD").stdout.strip() == head
        assert "tsc failed" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("restore_works", "outcome", "daemon_up"),
        [(True, "failed_restored", True), (False, "failed_not_restored", False)],
        ids=["the-restore-brings-the-daemon-back", "the-restore-fails-too"],
    )
    def test_a_stale_rebuild_whose_restart_fails_after_stopping_the_daemon_restores_before_claiming_nothing_changed(
        self, tmp_path, monkeypatch, capsys, restore_works, outcome, daemon_up
    ):
        env, outpost, _o_old, _o_new = _real_outpost_world(tmp_path, monkeypatch, outpost_behind=False)
        head = _run_git_real(outpost, "rev-parse", "HEAD").stdout.strip()
        state = {"daemon_up": True, "restarts": 0}
        monkeypatch.setattr(update.outpost_lifecycle, "install_dependencies", lambda *a, **k: None)
        monkeypatch.setattr(update.outpost_lifecycle, "managed_outpost", lambda *a, **k: {"pid": 1, "checkout": str(outpost)})

        def _restart(*a, **k):
            # The build has succeeded; the managed daemon is stopped; the new one never answers.
            state["restarts"] += 1
            state["daemon_up"] = False
            if state["restarts"] == 1 or not restore_works:
                raise update.OutpostLifecycleError("/health never answered")
            state["daemon_up"] = True

        monkeypatch.setattr(update.outpost_lifecycle, "restart", _restart)

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True, run_id=RUN_ID)

        assert rc == 1
        assert state["restarts"] == 2, "one restore is attempted at the unchanged checkout"
        assert state["daemon_up"] is daemon_up
        assert _read_record(tmp_path)["outcome"] == outcome
        assert _run_git_real(outpost, "rev-parse", "HEAD").stdout.strip() == head
        err = capsys.readouterr().err
        assert "/health never answered" in err
        assert ("could NOT be restored" in err) is (not restore_works)

    def test_an_advance_records_its_build_so_the_next_update_is_current(self, tmp_path, monkeypatch, capsys):
        env, _outpost, _o_old, o_new = _real_outpost_world(tmp_path, monkeypatch, outpost_behind=True)
        monkeypatch.setattr(update.outpost_lifecycle, "DEFAULT_INSTALL_CMD", [sys.executable, "-c", "pass"])
        monkeypatch.setattr(update.outpost_lifecycle, "DEFAULT_BUILD_CMD", [sys.executable, "-c", "pass"])
        monkeypatch.setattr(update.outpost_lifecycle, "managed_outpost", lambda *a, **k: None)

        assert update.run_update_apply(env=env, runner=_real_runner, assume_yes=True) == 0
        assert update.outpost_lifecycle.built_sha(env=env) == o_new
        capsys.readouterr()

        assert update.run_update_apply(env=env, runner=_real_runner, assume_yes=True) == 0
        assert "outpost already up to date" in capsys.readouterr().out


class TestOutpostWaitsOnTheInstall:
    def test_a_failed_install_upgrade_never_touches_outpost(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        runner, calls, state = _outpost_runner(
            outpost, trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA}
        )
        spy = _OutpostSpy(monkeypatch, state)
        monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())

        def _failing_wire(*a, **k):
            raise WireError(tool="craft", stage="register", cause=RuntimeError("boom"))

        monkeypatch.setattr(update, "wire_all_harnesses", _failing_wire)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1
        assert not any(_git_sub(c) in ("fetch", "merge", "reset") for c in _outpost_calls(calls, outpost))
        assert spy.steps == []

    def test_an_upstreamless_outpost_names_the_config_escape_hatch(
        self, tmp_path, monkeypatch, capsys
    ):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        inner, calls, state = _outpost_runner(outpost)
        _OutpostSpy(monkeypatch, state)
        wired = _patch_wire(monkeypatch)

        def runner(args, **kw):
            if args[2] == str(outpost) and _git_sub(args) == "rev-parse" and args[4] == "--abbrev-ref":
                calls.append(list(args))
                return subprocess.CompletedProcess(args, 128, stdout="", stderr="no upstream")
            return inner(args, **kw)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1
        assert wired == []
        assert not any(_git_sub(c) in ("fetch", "merge") for c in calls)
        assert "'checkout' key in the outpost config" in capsys.readouterr().err

    def test_an_outpost_fetch_failure_keeps_the_install_upgrade(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        _install_stamp(tmp_path, env)
        outpost = _outpost_checkout(tmp_path)
        _configure_outpost(env, outpost)
        inner, _calls, state = _outpost_runner(
            outpost, trailhead_kw={"remote_branch_sha": _NEW_SHA, "probe_sha": _NEW_SHA}
        )
        spy = _OutpostSpy(monkeypatch, state)
        _patch_wire(monkeypatch)

        def runner(args, **kw):
            if args[2] == str(outpost) and _git_sub(args) == "fetch":
                return subprocess.CompletedProcess(args, 128, stdout="", stderr="denied")
            return inner(args, **kw)

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        assert rc == 1
        assert read_stamp(env=env)["sha"] == _NEW_SHA
        assert spy.steps == []


# ---------------------------------------------------------------------------
# Real repos — apply refuses exactly what the check's apply_preflight predicts
# ---------------------------------------------------------------------------


def _ahead_only(checkout: Path) -> None:
    _catch_up(checkout)
    _commit_locally(checkout)


def _dirty_and_diverged(checkout: Path) -> None:
    _commit_locally(checkout)
    _dirty(checkout)


def _real_install_behind(tmp_path: Path, *, setup=None):
    """A real install checkout one commit behind its origin, stamped at its
    own old sha (so the install is stale even when the checkout is level),
    with *setup* applied to the checkout. Returns (env, checkout)."""
    env = _env(tmp_path)
    _origin, checkout, old_sha, _new = _init_real_repo_pair(tmp_path)
    _install_stamp(tmp_path, env, sha=old_sha)
    if setup is not None:
        setup(checkout)
    return env, checkout


def _apply_on_real_repo(tmp_path: Path, monkeypatch, setup):
    env, checkout = _real_install_behind(tmp_path, setup=setup)
    monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())
    monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **k: {})
    return env, checkout


class TestApplyRefusesRealRepos:
    def test_a_real_dirty_checkout_is_refused_with_the_existing_message(
        self, tmp_path, monkeypatch, capsys
    ):
        env, checkout = _apply_on_real_repo(tmp_path, monkeypatch, _dirty)
        head = _run_git_real(checkout, "rev-parse", "HEAD").stdout

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert rc == 1
        err = capsys.readouterr().err
        assert f"trailhead: refusing to upgrade — {checkout} has uncommitted changes." in err
        assert "Commit or stash them, then re-run: trailhead update" in err
        assert _run_git_real(checkout, "rev-parse", "HEAD").stdout == head

    def test_a_real_diverged_checkout_is_refused_with_the_existing_message(
        self, tmp_path, monkeypatch, capsys
    ):
        env, checkout = _apply_on_real_repo(tmp_path, monkeypatch, _commit_locally)
        head = _run_git_real(checkout, "rev-parse", "HEAD").stdout

        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert rc == 1
        err = capsys.readouterr().err
        assert f"trailhead: refusing to upgrade — {checkout}'s HEAD has diverged from origin/main" in err
        assert "cannot be fast-forwarded" in err
        assert _run_git_real(checkout, "rev-parse", "HEAD").stdout == head

    @pytest.mark.parametrize(
        "setup, refusal, apply_says",
        [
            (lambda checkout: None, None, None),
            (_ahead_only, None, None),
            (_dirty, "local_changes", "has uncommitted changes"),
            (_commit_locally, "diverged", "has diverged from origin/main"),
            (_dirty_and_diverged, "local_changes", "has uncommitted changes"),
        ],
        ids=["behind-only", "ahead-only", "dirty", "diverged", "dirty-and-diverged"],
    )
    def test_the_check_verdict_equals_what_apply_does_on_the_same_repo(
        self, tmp_path, monkeypatch, capsys, setup, refusal, apply_says
    ):
        env, _checkout_path = _apply_on_real_repo(tmp_path, monkeypatch, setup)

        preflight = update.check_for_update(env=env, runner=_real_runner)["apply_preflight"]
        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        err = capsys.readouterr().err
        if refusal is None:
            assert preflight == {"verdict": "clear", "refusal": None}
            assert rc == 0, err
        else:
            assert preflight == {"verdict": "refused", "refusal": refusal}
            assert rc == 1
            assert apply_says in err

    def test_no_upstream_is_refused_by_both_the_preflight_and_apply(
        self, tmp_path, monkeypatch, capsys
    ):
        env, checkout = _apply_on_real_repo(
            tmp_path, monkeypatch, lambda c: _git(c, "branch", "--unset-upstream")
        )

        preflight = update._apply_preflight(checkout, env=env, runner=_real_runner, timeout=10)
        rc = update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)

        assert preflight == {"verdict": "refused", "refusal": "no_upstream"}
        assert rc == 1
        assert "could not resolve the tracked upstream branch" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The result record: every apply that holds the run lock records how it ended.
#
# Exit-path audit of run_update_apply / _apply_after_consent. Each row is one
# way the apply can end and the record it must leave; the rows are the
# parametrisation below (ids in brackets), produced on real throwaway repos
# with real failures.
#
#   no record (never held the lock, or took nothing):
#     missing stamp, unusable outpost config, malformed --run-id, TTY decline,
#     non-interactive refusal, dry run, lock contention  (TestNoRecordIsWritten)
#   refused:
#     [install-no-upstream] [outpost-no-upstream] [install-git-error]
#     [install-dirty] [outpost-dirty] [install-status-unreadable]
#     [install-diverged] [install-divergence-unknown]
#     [outpost-diverged-install-current] [outpost-divergence-unknown-install-current]
#   failed_restored (nothing changed on this machine):
#     [wire-lock-held] [config-resolve-error] [install-fetch-fails]
#     [install-merge-blocked] [install-rewire-fails-rolled-back]
#     [outpost-fetch-fails-install-current]
#     [outpost-merge-blocked-install-current]
#     [outpost-build-fails-install-current] [outpost-stale-rebuild-fails-install-current]
#     [interrupt-before-any-change]
#   outpost_restored (install advanced, outpost left as it was):
#     [outpost-diverged-install-advanced] [outpost-fetch-fails-install-advanced]
#     [outpost-merge-blocked-install-advanced]
#     [outpost-build-fails-install-advanced] [outpost-stale-rebuild-fails-install-advanced]
#   failed_not_restored:
#     [install-rewire-and-reset-fail] [install-rewire-fails-twice]
#     [outpost-rollback-rebuild-fails] [outpost-reset-fails]
#     [outpost-stale-rebuild-and-restore-fail-install-current]
#     [interrupt-after-a-change] [unexpected-exception-after-a-change]
#     [unexpected-exception-after-rewiring-a-level-checkout] [interrupt-during-a-stale-rebuild]
#   updated:
#     [install-advanced] [install-rewired-checkout-level] [outpost-advanced-install-current]
#     [outpost-stale-rebuild-install-current] [readiness-check-raises]
#   already_current:
#     [nothing-behind-no-outpost] [nothing-behind-outpost-built]
#   SIGTERM / SIGKILL to a real apply process: TestRealSignals
# ---------------------------------------------------------------------------

RUN_ID = "c" * 32
RUN_OLD = "d" * 32
_OK_OUTCOMES = ("updated", "already_current")


def _result_file(tmp_path: Path) -> Path:
    return tmp_path / "state" / "update-result.json"


def _lock_file(tmp_path: Path) -> Path:
    return tmp_path / "state" / "update-run.lock"


def _read_record(tmp_path: Path) -> dict:
    return json.loads(_result_file(tmp_path).read_text())


def _last_result(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(update_run.outpost_lifecycle, "managed_outpost", lambda *a, **k: None)
    return update_run.build_status(env=_env(tmp_path))["last_result"]


def _stub_npm(tmp_path: Path, monkeypatch, *, fail_on=(), lock: Path | None = None) -> Path:
    """A real `npm` executable on PATH that exits 1 on the listed call numbers
    (or on every call with ``"all"``), optionally taking a git index lock
    first. Returns the call-counter file."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    counter = tmp_path / "npm-calls"
    fail = "all" if fail_on == "all" else " ".join(str(n) for n in fail_on)
    take_lock = f'touch "{lock}"' if lock is not None else ":"
    script = bin_dir / "npm"
    script.write_text(
        "#!/bin/sh\n"
        f'n=$(( $(cat "{counter}" 2>/dev/null || echo 0) + 1 ))\n'
        f'echo $n > "{counter}"\n'
        f'case " {fail} " in *" all "*|*" $n "*) {take_lock}; echo "stub npm failing" >&2; exit 1;; esac\n'
        "exit 0\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return counter


class _World:
    pass


def _world(tmp_path: Path, monkeypatch, *, install: str, outpost: str | None, wire_fail=()):
    """Real install checkout and (optionally) real Outpost checkout.

    install: "behind" (checkout behind its origin, stamped old), "stale"
    (checkout level, install stamped at an older sha), "current".
    outpost: None (unconfigured), "behind", "built" (level, build stamp
    matches), "unbuilt" (level, no build stamp).
    wire_fail: call numbers of wire_all_harnesses that raise ("all" for every
    call); a raise leaves `.git/index.lock` in the checkout when
    ``w.wire_locks_checkout``.
    """
    w = _World()
    env = _env(tmp_path)
    env["OUTPOST_STATE_DIR"] = str(tmp_path / "outpost-state")
    _origin, checkout, old_sha, new_sha = _init_real_repo_pair(tmp_path)
    if install == "behind" or install == "stale":
        _install_stamp(tmp_path, env, sha=old_sha)
    else:
        _install_stamp(tmp_path, env, sha=new_sha)
    if install != "behind":
        _catch_up(checkout)
    w.env, w.checkout, w.old_sha, w.new_sha = env, checkout, old_sha, new_sha
    w.outpost = None
    if outpost is not None:
        root = tmp_path / "outpost-repos"
        root.mkdir()
        _oo, w.outpost, w.o_old, w.o_new = _init_real_repo_pair(root)
        if outpost != "behind":
            _catch_up(w.outpost)
        _configure_outpost(env, w.outpost)
        if outpost == "built":
            update.outpost_lifecycle.build(env=env, build_cmd=[sys.executable, "-c", "pass"])
    w.wire_calls = []
    w.wire_locks_checkout = False

    def _wire(cfg, *a, **k):
        w.wire_calls.append(1)
        if wire_fail == "all" or len(w.wire_calls) in wire_fail:
            if w.wire_locks_checkout:
                (w.checkout / ".git" / "index.lock").write_text("")
            raise WireError(tool="craft", stage="register", cause=RuntimeError("boom"))
        return {}

    monkeypatch.setattr(update, "resolve_config_for_env", lambda e: _FakeCfg())
    monkeypatch.setattr(update, "wire_all_harnesses", _wire)
    monkeypatch.setattr(update.outpost_lifecycle, "managed_outpost", lambda *a, **k: None)
    w.npm = _stub_npm(tmp_path, monkeypatch)
    return w


def _lock_repo(repo: Path) -> None:
    (repo / ".git" / "index.lock").write_text("")


def _merge_base_fails_runner(args, **kw):
    if "merge-base" in args:
        return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal: boom")
    return subprocess.run(args, **kw)


def _fetch_fails(repo: Path, tmp_path: Path) -> None:
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "gone"))


def _interrupt(exc: BaseException):
    def raiser(*a, **k):
        raise exc

    return raiser


def _r(id, *, install, outpost=None, outcome, refusal=None, setup=None, **kw):
    return pytest.param(
        dict(install=install, outpost=outpost, outcome=outcome, refusal=refusal, setup=setup, **kw),
        id=id,
    )


def _corrupt_head(w, tmp_path, monkeypatch, stack):
    (w.checkout / ".git" / "HEAD").write_text("garbage\n")


_ROWS = [
    # --- refused ---------------------------------------------------------
    _r("install-no-upstream", install="behind", outcome="refused", refusal="no_upstream",
       setup=lambda w, t, m, s: _git(w.checkout, "branch", "--unset-upstream")),
    _r("outpost-no-upstream", install="behind", outpost="behind", outcome="refused", refusal="no_upstream",
       setup=lambda w, t, m, s: _git(w.outpost, "branch", "--unset-upstream")),
    _r("install-git-error", install="behind", outcome="refused", refusal="unknown", setup=_corrupt_head),
    _r("install-dirty", install="behind", outcome="refused", refusal="local_changes",
       setup=lambda w, t, m, s: _dirty(w.checkout)),
    _r("outpost-dirty", install="behind", outpost="behind", outcome="refused", refusal="local_changes",
       setup=lambda w, t, m, s: _dirty(w.outpost)),
    _r("install-status-unreadable", install="behind", outcome="refused", refusal="unknown",
       setup=lambda w, t, m, s: (w.checkout / ".git" / "index").write_bytes(b"garbage")),
    _r("install-diverged", install="behind", outcome="refused", refusal="diverged",
       setup=lambda w, t, m, s: _commit_locally(w.checkout)),
    _r("install-divergence-unknown", install="behind", outcome="refused", refusal="unknown",
       runner=_merge_base_fails_runner),
    _r("outpost-diverged-install-current", install="current", outpost="behind", outcome="refused",
       refusal="diverged", setup=lambda w, t, m, s: _commit_locally(w.outpost)),
    _r("outpost-divergence-unknown-install-current", install="current", outpost="behind",
       outcome="refused", refusal="unknown", runner=_merge_base_fails_runner),
    # --- failed_restored -------------------------------------------------
    _r("wire-lock-held", install="behind", outcome="failed_restored",
       setup=lambda w, t, m, s: s.enter_context(wire_lock(env=w.env))),
    _r("config-resolve-error", install="behind", outcome="failed_restored", raises=RuntimeError,
       setup=lambda w, t, m, s: m.setattr(update, "resolve_config_for_env", _interrupt(RuntimeError("bad cfg")))),
    _r("install-fetch-fails", install="behind", outcome="failed_restored",
       setup=lambda w, t, m, s: _fetch_fails(w.checkout, t)),
    _r("install-merge-blocked", install="behind", outcome="failed_restored",
       setup=lambda w, t, m, s: _lock_repo(w.checkout)),
    _r("install-rewire-fails-rolled-back", install="behind", outcome="failed_restored", wire_fail=(1,)),
    _r("outpost-fetch-fails-install-current", install="current", outpost="behind", outcome="failed_restored",
       setup=lambda w, t, m, s: _fetch_fails(w.outpost, t)),
    _r("outpost-merge-blocked-install-current", install="current", outpost="behind",
       outcome="failed_restored", setup=lambda w, t, m, s: _lock_repo(w.outpost)),
    _r("outpost-build-fails-install-current", install="current", outpost="behind",
       outcome="failed_restored", npm_fail=(2,)),
    _r("outpost-stale-rebuild-fails-install-current", install="current", outpost="unbuilt",
       outcome="failed_restored", npm_fail=(1,)),
    _r("interrupt-before-any-change", install="behind", outcome="failed_restored", raises=KeyboardInterrupt,
       setup=lambda w, t, m, s: m.setattr(update, "resolve_config_for_env", _interrupt(KeyboardInterrupt()))),
    # --- outpost_restored ------------------------------------------------
    _r("outpost-diverged-install-advanced", install="behind", outpost="behind", outcome="outpost_restored",
       setup=lambda w, t, m, s: _commit_locally(w.outpost)),
    _r("outpost-fetch-fails-install-advanced", install="behind", outpost="behind", outcome="outpost_restored",
       setup=lambda w, t, m, s: _fetch_fails(w.outpost, t)),
    _r("outpost-merge-blocked-install-advanced", install="behind", outpost="behind",
       outcome="outpost_restored", setup=lambda w, t, m, s: _lock_repo(w.outpost)),
    _r("outpost-build-fails-install-advanced", install="behind", outpost="behind",
       outcome="outpost_restored", npm_fail=(2,)),
    _r("outpost-stale-rebuild-fails-install-advanced", install="behind", outpost="unbuilt",
       outcome="outpost_restored", npm_fail=(1,)),
    # --- failed_not_restored ---------------------------------------------
    _r("install-rewire-and-reset-fail", install="behind", outcome="failed_not_restored", wire_fail=(1,),
       setup=lambda w, t, m, s: setattr(w, "wire_locks_checkout", True)),
    _r("install-rewire-fails-twice", install="behind", outcome="failed_not_restored", wire_fail="all"),
    _r("outpost-rollback-rebuild-fails", install="behind", outpost="behind",
       outcome="failed_not_restored", npm_fail="all"),
    _r("outpost-stale-rebuild-and-restore-fail-install-current", install="current", outpost="unbuilt",
       outcome="failed_not_restored", npm_fail="all"),
    _r("outpost-reset-fails", install="behind", outpost="behind", outcome="failed_not_restored",
       npm_fail=(1,), npm_lock="outpost"),
    _r("interrupt-after-a-change", install="behind", outpost="behind", outcome="failed_not_restored",
       raises=KeyboardInterrupt,
       setup=lambda w, t, m, s: m.setattr(update.outpost_lifecycle, "install_dependencies", _interrupt(KeyboardInterrupt()))),
    _r("unexpected-exception-after-a-change", install="behind", outcome="failed_not_restored",
       raises=RuntimeError,
       setup=lambda w, t, m, s: m.setattr(update, "write_stamp", _interrupt(RuntimeError("stamp exploded")))),
    _r("unexpected-exception-after-rewiring-a-level-checkout", install="stale", outcome="failed_not_restored",
       raises=RuntimeError,
       setup=lambda w, t, m, s: m.setattr(update, "write_stamp", _interrupt(RuntimeError("stamp exploded")))),
    _r("interrupt-during-a-stale-rebuild", install="current", outpost="unbuilt", outcome="failed_not_restored",
       raises=KeyboardInterrupt,
       setup=lambda w, t, m, s: m.setattr(update.outpost_lifecycle, "install_dependencies", _interrupt(KeyboardInterrupt()))),
    # --- updated ---------------------------------------------------------
    _r("install-advanced", install="behind", outcome="updated"),
    _r("install-rewired-checkout-level", install="stale", outcome="updated"),
    _r("outpost-advanced-install-current", install="current", outpost="behind", outcome="updated"),
    _r("outpost-stale-rebuild-install-current", install="current", outpost="unbuilt", outcome="updated"),
    _r("readiness-check-raises", install="behind", outcome="updated",
       setup=lambda w, t, m, s: m.setattr(update.doctor, "build_readiness", _interrupt(RuntimeError("doctor down")))),
    # --- already_current -------------------------------------------------
    _r("nothing-behind-no-outpost", install="current", outcome="already_current"),
    _r("nothing-behind-outpost-built", install="current", outpost="built", outcome="already_current"),
]


class TestEveryExitPathRecordsHowItEnded:
    @pytest.mark.parametrize("row", _ROWS)
    def test_the_record_and_status_carry_the_outcome(self, tmp_path, monkeypatch, row):
        w = _world(tmp_path, monkeypatch, install=row["install"], outpost=row["outpost"],
                   wire_fail=row.get("wire_fail", ()))
        if row.get("npm_fail"):
            lock = (w.outpost / ".git" / "index.lock") if row.get("npm_lock") == "outpost" else None
            w.npm = _stub_npm(tmp_path, monkeypatch, fail_on=row["npm_fail"], lock=lock)
        with ExitStack() as stack:
            if row["setup"] is not None:
                row["setup"](w, tmp_path, monkeypatch, stack)
            runner = row.get("runner", _real_runner)
            call = lambda: update.run_update_apply(  # noqa: E731
                env=w.env, runner=runner, assume_yes=True, run_id=RUN_ID
            )
            if row.get("raises"):
                with pytest.raises(row["raises"]):
                    call()
                rc = None
            else:
                rc = call()

        rec = _read_record(tmp_path)
        assert rec["outcome"] == row["outcome"]
        assert rec["refusal"] == row["refusal"]
        assert rec["result_schema_version"] == 1
        assert rec["run_id"] == RUN_ID
        assert datetime.strptime(rec["finished_at"], "%Y-%m-%dT%H:%M:%SZ")
        assert set(rec) == {"result_schema_version", "run_id", "finished_at", "outcome", "refusal"}
        if rc is not None:
            assert rc == (0 if row["outcome"] in _OK_OUTCOMES else 1)
        assert update_run.current_holder(env=w.env) is None, "the lock is released on every exit path"
        assert _last_result(tmp_path, monkeypatch) == {
            k: rec[k] for k in ("run_id", "outcome", "refusal", "finished_at")
        }


class TestNoRecordIsWritten:
    """Runs that never held the run lock leave no record, and a previous
    record survives them untouched."""

    @staticmethod
    def _previous_record(tmp_path: Path) -> str:
        path = _result_file(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            {"result_schema_version": 1, "run_id": RUN_OLD, "finished_at": "2026-01-01T00:00:00Z",
             "outcome": "updated", "refusal": None}
        )
        path.write_text(text)
        return text

    def _run(self, name, w, monkeypatch, tmp_path):
        env = w.env
        if name == "tty-decline":
            monkeypatch.setattr(sys, "stdin", StringIO("n\n"))
            return update.run_update_apply(env=env, runner=_real_runner, is_tty=lambda: True)
        if name == "non-interactive-refusal":
            return update.run_update_apply(env=env, runner=_real_runner, is_tty=lambda: False)
        if name == "dry-run":
            return update.run_update_apply(env=env, runner=_real_runner, dry_run=True)
        if name == "malformed-run-id":
            return update.run_update_apply(env=env, runner=_real_runner, assume_yes=True, run_id="nope")
        if name == "unusable-outpost-config":
            (Path(env["OUTPOST_CONFIG_DIR"]) / "config.toml").write_text("checkout = [not toml")
            return update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)
        if name == "missing-stamp":
            from trailhead import provenance

            provenance.stamp_path(env=env).unlink()
            return update.run_update_apply(env=env, runner=_real_runner, assume_yes=True)
        raise AssertionError(name)

    @pytest.mark.parametrize(
        "name",
        ["tty-decline", "non-interactive-refusal", "dry-run", "malformed-run-id",
         "unusable-outpost-config", "missing-stamp"],
    )
    @pytest.mark.parametrize("had_previous", [False, True], ids=["no-previous", "previous-record"])
    def test_the_run_writes_nothing_and_keeps_the_previous_record(
        self, tmp_path, monkeypatch, name, had_previous
    ):
        w = _world(tmp_path, monkeypatch, install="behind", outpost="behind")
        previous = self._previous_record(tmp_path) if had_previous else None

        self._run(name, w, monkeypatch, tmp_path)

        if had_previous:
            assert _result_file(tmp_path).read_text() == previous
        else:
            assert not _result_file(tmp_path).exists()


class TestSigtermHandlerIsScopedToTheApply:
    def test_the_previous_sigterm_handler_is_back_after_the_apply(self, tmp_path, monkeypatch):
        w = _world(tmp_path, monkeypatch, install="behind", outpost=None)
        sentinel = lambda signum, frame: None  # noqa: E731
        previous = signal.signal(signal.SIGTERM, sentinel)
        try:
            assert update.run_update_apply(env=w.env, runner=_real_runner, assume_yes=True) == 0
            assert signal.getsignal(signal.SIGTERM) is sentinel
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_the_handler_is_back_after_a_refused_apply_too(self, tmp_path, monkeypatch):
        w = _world(tmp_path, monkeypatch, install="behind", outpost=None)
        _dirty(w.checkout)
        sentinel = lambda signum, frame: None  # noqa: E731
        previous = signal.signal(signal.SIGTERM, sentinel)
        try:
            assert update.run_update_apply(env=w.env, runner=_real_runner, assume_yes=True) == 1
            assert signal.getsignal(signal.SIGTERM) is sentinel
        finally:
            signal.signal(signal.SIGTERM, previous)


class TestRecordFile:
    def test_the_record_is_owner_only_and_replaced_through_a_temp_file_in_its_directory(
        self, tmp_path, monkeypatch
    ):
        w = _world(tmp_path, monkeypatch, install="behind", outpost=None)
        result = _result_file(tmp_path)
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text("{}")
        os.chmod(result, 0o644)
        replaced: list[tuple[str, str]] = []
        real_replace = os.replace

        def spy(src, dst, *a, **k):
            replaced.append((str(src), str(dst)))
            return real_replace(src, dst, *a, **k)

        monkeypatch.setattr(os, "replace", spy)
        old_umask = os.umask(0)
        try:
            rc = update.run_update_apply(env=w.env, runner=_real_runner, assume_yes=True, run_id=RUN_ID)
        finally:
            os.umask(old_umask)

        assert rc == 0
        assert (result.stat().st_mode & 0o777) == 0o600
        ours = [(s, d) for s, d in replaced if d == str(result)]
        assert len(ours) == 1
        src, dst = ours[0]
        assert Path(src).parent == result.parent and src != dst
        assert not Path(src).exists()
        assert _read_record(tmp_path)["run_id"] == RUN_ID

    def test_a_later_run_replaces_the_previous_record(self, tmp_path, monkeypatch):
        w = _world(tmp_path, monkeypatch, install="behind", outpost=None)
        assert update.run_update_apply(env=w.env, runner=_real_runner, assume_yes=True, run_id=RUN_OLD) == 0
        assert _read_record(tmp_path)["outcome"] == "updated"
        assert update.run_update_apply(env=w.env, runner=_real_runner, assume_yes=True, run_id=RUN_ID) == 0
        rec = _read_record(tmp_path)
        assert (rec["run_id"], rec["outcome"]) == (RUN_ID, "already_current")


# ---------------------------------------------------------------------------
# A real apply process, signalled mid-run.
# ---------------------------------------------------------------------------

_APPLY_DRIVER = """
import sys
from trailhead import cli, update, outpost_lifecycle
class _Cfg:
    harnesses = []
update.resolve_config_for_env = lambda e: _Cfg()
update.wire_all_harnesses = lambda *a, **k: {}
outpost_lifecycle.managed_outpost = lambda *a, **k: None
run_id = sys.argv[1]
sys.argv = ["trailhead", "update", "--yes", "--run-id", run_id]
sys.exit(cli.main())
"""


class _ApplyProcess:
    """`trailhead update --yes --run-id <id>` as a real process, blocked at a
    real seam: a `sleep` standing in for `git fetch`'s upload-pack, or for
    `npm`. Every process it caused is reaped on close."""

    def __init__(self, tmp_path: Path, w, *, block: str):
        self.tmp_path = tmp_path
        self.marker = tmp_path / "blocked.pid"
        blocker = tmp_path / "block.sh"
        blocker.write_text(f'#!/bin/sh\necho $$ > "{self.marker}"\nexec sleep 120\n')
        blocker.chmod(0o755)
        env = dict(w.env)
        if block == "fetch":
            _git(w.checkout, "config", "remote.origin.uploadpack", str(blocker))
        else:
            bin_dir = tmp_path / "proc-bin"
            bin_dir.mkdir()
            shim = bin_dir / "npm"
            shim.write_text(f'#!/bin/sh\nexec "{blocker}"\n')
            shim.chmod(0o755)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        self.log = (tmp_path / "apply.log").open("w")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _APPLY_DRIVER, RUN_ID],
            env=env, stdout=self.log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        while not self.marker.exists() or not self.marker.read_text().strip():
            assert self.proc.poll() is None, f"apply exited early: {(tmp_path / 'apply.log').read_text()}"
            assert time.monotonic() < deadline, "apply never reached the blocking seam"
            time.sleep(0.05)
        self.blocked_pid = int(self.marker.read_text())

    def signal_and_wait(self, sig) -> int:
        self.proc.send_signal(sig)
        return self.proc.wait(timeout=30)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=10)
        self.log.close()
        try:
            os.kill(self.blocked_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.fixture()
def apply_process():
    made: list[_ApplyProcess] = []

    def make(tmp_path, w, *, block):
        p = _ApplyProcess(tmp_path, w, block=block)
        made.append(p)
        return p

    yield make
    for p in made:
        p.close()


class TestRealSignals:
    def test_sigterm_after_a_change_began_records_failed_not_restored_and_frees_the_lock(
        self, tmp_path, monkeypatch, apply_process
    ):
        w = _world(tmp_path, monkeypatch, install="current", outpost="behind")
        p = apply_process(tmp_path, w, block="npm")

        assert update_run.current_holder(env=w.env)["run_id"] == RUN_ID
        assert p.signal_and_wait(signal.SIGTERM) == 143

        rec = _read_record(tmp_path)
        assert rec["outcome"] == "failed_not_restored"
        assert rec["refusal"] is None
        assert rec["run_id"] == RUN_ID
        assert update_run.current_holder(env=w.env) is None
        assert _last_result(tmp_path, monkeypatch)["outcome"] == "failed_not_restored"
        assert _run_git_real(w.outpost, "rev-parse", "HEAD").stdout.strip() == w.o_new

    def test_sigterm_before_any_change_records_failed_restored(
        self, tmp_path, monkeypatch, apply_process
    ):
        w = _world(tmp_path, monkeypatch, install="behind", outpost=None)
        p = apply_process(tmp_path, w, block="fetch")

        assert p.signal_and_wait(signal.SIGTERM) == 143

        rec = _read_record(tmp_path)
        assert (rec["outcome"], rec["run_id"]) == ("failed_restored", RUN_ID)
        assert update_run.current_holder(env=w.env) is None
        assert _run_git_real(w.checkout, "rev-parse", "HEAD").stdout.strip() == w.old_sha

    @pytest.mark.parametrize("older", [False, True], ids=["no-record", "older-record"])
    def test_sigkill_after_the_lock_reads_interrupted_until_the_next_run_replaces_it(
        self, tmp_path, monkeypatch, apply_process, older
    ):
        w = _world(tmp_path, monkeypatch, install="behind", outpost=None)
        old_record = {"result_schema_version": 1, "run_id": RUN_OLD, "finished_at": "2026-01-01T00:00:00Z",
                      "outcome": "updated", "refusal": None}
        if older:
            _result_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
            _result_file(tmp_path).write_text(json.dumps(old_record))
        p = apply_process(tmp_path, w, block="fetch")

        held = _last_result(tmp_path, monkeypatch)
        assert held == ({k: old_record[k] for k in ("run_id", "outcome", "refusal", "finished_at")} if older else None), (
            "a held lock never reads as interrupted"
        )

        p.signal_and_wait(signal.SIGKILL)

        assert update_run.current_holder(env=w.env) is None
        assert _last_result(tmp_path, monkeypatch) == {
            "run_id": RUN_ID, "outcome": "interrupted", "refusal": None, "finished_at": None,
        }
        on_disk = json.loads(_result_file(tmp_path).read_text()) if older else None
        assert on_disk == (old_record if older else None), "interrupted is derived, never written"
        if not older:
            assert not _result_file(tmp_path).exists()

        _git(w.checkout, "config", "--unset", "remote.origin.uploadpack")
        # The wire lock has no liveness: a killed apply leaves it behind and the
        # next run names it for the operator to remove.
        (tmp_path / "state" / "trailhead.lock").unlink()
        assert update.run_update_apply(env=w.env, runner=_real_runner, assume_yes=True, run_id=RUN_OLD) == 0
        last = _last_result(tmp_path, monkeypatch)
        assert (last["run_id"], last["outcome"]) == (RUN_OLD, "updated")
