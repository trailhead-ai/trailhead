"""Tests for trailhead/update.py — apply-mode `trailhead update` (no `--check`).

Apply mode fast-forwards the stamped checkout, re-wires (`trailhead.install.
wire_all_harnesses`), and refreshes the provenance stamp. It is a true no-op on
any failure short of a completed re-wire: git access is injected via `runner`
so no test ever touches a real git checkout except the rollback test, which
uses a real throwaway git repo under `tmp_path` to assert on actual tree
state rather than mocked calls.
"""

from __future__ import annotations

import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from trailhead import update
from trailhead.install_config import ResolvedConfig, ResolvedHarness, ResolvedPlugin
from trailhead.provenance import read_stamp
from trailhead.wire import WireError, wire_lock

_OLD_SHA = "a" * 40
_NEW_SHA = "b" * 40
_ORIGIN_URL = "https://example.com/r.git"
_BRANCH = "origin/main"


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        **os.environ,
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(home),
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
        sub = args[3]
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
        assert any(c[3] == "fetch" for c in calls)

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
        assert not any(c[3] in ("fetch", "merge", "reset") for c in calls)
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
        assert not any(c[3] == "merge" for c in calls)
        assert not wire_calls
        stamp = read_stamp(env=env)
        assert stamp["sha"] == _OLD_SHA


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
        assert not any(c[3] == "merge" for c in calls)
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
        merge_calls = [c for c in calls if c[3] == "merge"]
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
        assert not any(c[3] in ("fetch", "merge", "reset") for c in calls)
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
        assert not any(c[3] == "fetch" for c in calls), (
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
            sub = args[3]
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

        assert not any(c[3] == "remote" for c in calls)

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
        assert not any(c[3] == "merge" for c in calls)
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
        assert not any(c[3] in ("merge", "merge-base") for c in calls)


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
            sub = args[3]
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
        assert not any(c[3] == "merge" for c in calls)

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
