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
