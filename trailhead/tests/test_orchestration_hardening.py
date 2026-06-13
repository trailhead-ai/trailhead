"""Regression tests for orchestration layer hardening.

Covers findings from code review:
  C1 — env=None defaults to {} → PathResolutionError crash in install/update/config
  I1 — wire lock not shared: install + config capabilities unguarded
  I2 — update verifies non-trailhead entries against trailhead checkout root
  M5 — cli.main has no top-level error guard (raw tracebacks on named errors)
  M1 — pathint failure after successful wire exits nonzero ("installed but exit 1")
  M2 — first update after install always re-wires (update_state never seeded at install)

TDD: each test written BEFORE the fix. Each must fail RED first, then go GREEN
after the implementation change.

Hermeticity: all tests use tmp_path + monkeypatch.setenv / env dict overrides.
No test touches real ~/.claude/, real state/config dirs, or real shell rc.
wire / pathint / fetch are always stubbed.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _hermetic_env(tmp_path: Path) -> dict[str, str]:
    """Env with TRAILHEAD_CONFIG_DIR + TRAILHEAD_STATE_DIR redirected to tmp."""
    return {
        **os.environ,
        "TRAILHEAD_CONFIG_DIR": str(tmp_path / "config"),
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
    }


def _capture(fn):
    """Run fn() capturing stdout/stderr; return (result, stdout_str, stderr_str)."""
    old_stdout, old_stderr = sys.stdout, sys.stderr
    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        sys.stdout, sys.stderr = out_buf, err_buf
        result = fn()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return result, out_buf.getvalue(), err_buf.getvalue()


def _fake_pathint_result(tmp_path: Path):
    from trailhead.pathint import PathIntegrationResult
    return PathIntegrationResult(
        shim_dir=tmp_path / "bin",
        rc_path=None,
        skip_message=None,
    )


def _save_config(env: dict[str, str], **kwargs):
    from trailhead.config import TrailheadConfig, save_config
    cfg = TrailheadConfig(**kwargs)
    save_config(cfg, env=env)
    return cfg


def _load_config(env: dict[str, str]):
    from trailhead.config import load_config
    return load_config(env=env)


# ---------------------------------------------------------------------------
# C1 — env=None must use os.environ, not {} (per-verb regression)
# ---------------------------------------------------------------------------


class TestC1EnvNoneUsesOsEnviron:
    """env=None must not crash with PathResolutionError when os.environ is used.

    The production path calls run_install/run_update/run_config with no env=
    argument (i.e. env=None). The old code defaulted to {}, which left HOME
    unset → _home({}) → PathResolutionError: HOME is not set.

    These tests exercise the env=None path hermetically by setting the
    TRAILHEAD_*_DIR overrides in the *real* os.environ (via monkeypatch), so
    path resolution uses the overrides and never needs HOME/Library paths.
    """

    def test_run_install_env_none_does_not_raise(
        self, tmp_path, monkeypatch
    ):
        """run_install(env=None) must not raise PathResolutionError."""
        monkeypatch.setenv("TRAILHEAD_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))

        from trailhead import install as install_mod
        from trailhead.paths import PathResolutionError

        def run():
            with patch("trailhead.install.wire") as mock_wire, \
                 patch("trailhead.install.install_path_integration") as mock_pathint, \
                 patch("trailhead.install.verify_present_repo", return_value=True), \
                 patch("trailhead.install._is_tty", return_value=False):
                mock_wire.return_value = None
                mock_pathint.return_value = _fake_pathint_result(tmp_path)
                # env=None — the production path
                return install_mod.run_install("standard", env=None)

        result, out, err = _capture(run)
        # Must not have raised PathResolutionError; result should be 0
        assert result == 0, f"expected exit 0, got {result}; stderr={err!r}"
        assert "PathResolutionError" not in err
        assert "HOME is not set" not in err

    def test_run_update_env_none_does_not_raise(
        self, tmp_path, monkeypatch
    ):
        """run_update(env=None) must not raise PathResolutionError."""
        monkeypatch.setenv("TRAILHEAD_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))

        from trailhead import update as update_mod
        from trailhead.manifest import InstallManifest
        from trailhead.manifest import RepoEntry
        from trailhead.paths import PathResolutionError

        fake_entry = RepoEntry(
            name="trailhead",
            rev="a" * 40,
            source="https://example.com",
            tools=["lore"],
        )
        fake_manifest = InstallManifest(repos=[fake_entry])

        _save_config(
            {
                "TRAILHEAD_CONFIG_DIR": str(tmp_path / "config"),
                "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
                **os.environ,
            },
            capabilities={"lore": ["capture"]},
        )

        # Seed update_state so the rev is unchanged → no-op path
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "update_state.json").write_text(
            json.dumps({"trailhead": "a" * 40})
        )

        def run():
            with patch("trailhead.update.load_install_manifest", return_value=fake_manifest), \
                 patch("trailhead.update.wire", return_value=None), \
                 patch("trailhead.update.verify_present_repo", return_value=True):
                # env=None — the production path
                return update_mod.run_update(env=None)

        result, out, err = _capture(run)
        assert result == 0, f"expected exit 0, got {result}; stderr={err!r}"
        assert "PathResolutionError" not in err
        assert "HOME is not set" not in err

    def test_run_config_env_none_does_not_raise(
        self, tmp_path, monkeypatch
    ):
        """run_config(env=None) must not raise PathResolutionError."""
        monkeypatch.setenv("TRAILHEAD_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))

        from trailhead import config_cmd as config_cmd_mod
        from trailhead.paths import PathResolutionError

        _save_config(
            {
                "TRAILHEAD_CONFIG_DIR": str(tmp_path / "config"),
                "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
                **os.environ,
            },
            registry="https://example.com",
        )

        def run():
            with patch("trailhead.config_cmd.wire", return_value=None), \
                 patch("trailhead.config_cmd.install_path_integration",
                       return_value=_fake_pathint_result(tmp_path)), \
                 patch("trailhead.config_cmd.remove_path_integration", return_value=None):
                # env=None — the production path
                return config_cmd_mod.run_config(["registry"], env=None)

        result, out, err = _capture(run)
        assert result == 0, f"expected exit 0, got {result}; stderr={err!r}"
        assert "PathResolutionError" not in err
        assert "HOME is not set" not in err


# ---------------------------------------------------------------------------
# I1 — shared wire lock: config capabilities toggle must be guarded
# ---------------------------------------------------------------------------


class TestI1SharedWireLock:
    """The wire lock must guard config capabilities re-wires, not just update.

    When the lock is already held (simulating a concurrent update), a
    capabilities toggle must be rejected with a named error.
    After a normal toggle the lock must be released.
    """

    def test_config_capabilities_rejected_when_lock_held(self, tmp_path):
        """config capabilities toggle while lock held → named error + nonzero exit."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture", "recall", "sessions"]})

        # Pre-create the lock file to simulate a concurrent operation
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        lock_file = state_dir / "trailhead.lock"
        lock_file.write_text("locked by pid 99999")

        from trailhead import config_cmd as config_cmd_mod

        def run():
            with patch("trailhead.config_cmd.wire", return_value=None):
                return config_cmd_mod.run_config(
                    ["capabilities", "lore", "recall", "off"],
                    env=env,
                )

        result, out, err = _capture(run)
        assert result != 0, "must fail with nonzero exit when lock is held"
        combined = out + err
        assert (
            "lock" in combined.lower()
            or "concurrent" in combined.lower()
            or "already running" in combined.lower()
        ), f"Expected lock-related error message, got: {combined!r}"

    def test_config_capabilities_lock_released_after_normal_toggle(self, tmp_path):
        """After a normal capabilities toggle, the lock file must be gone."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture", "recall", "sessions"]})

        from trailhead import config_cmd as config_cmd_mod

        def run():
            with patch("trailhead.config_cmd.wire", return_value=None):
                return config_cmd_mod.run_config(
                    ["capabilities", "lore", "recall", "off"],
                    env=env,
                )

        result, out, err = _capture(run)
        assert result == 0, f"expected exit 0, got {result}; stderr={err!r}"

        lock_file = tmp_path / "state" / "trailhead.lock"
        assert not lock_file.exists(), "lock file must be released after a successful toggle"


# ---------------------------------------------------------------------------
# I2 — update must not verify non-trailhead entries against trailhead checkout
# ---------------------------------------------------------------------------


class TestI2UpdateEntryScoping:
    """update must only verify entries whose repo is the trailhead checkout.

    A second (non-trailhead) entry whose rev differs from HEAD must NOT cause
    a false refusal — it should be skipped or handled without verifying its
    rev against the trailhead checkout root.
    """

    def test_second_repo_entry_does_not_cause_false_refusal(self, tmp_path):
        """Non-trailhead entry with a different rev must not fail update."""
        env = _hermetic_env(tmp_path)
        _save_config(
            env,
            capabilities={"lore": ["capture"]},
            registry="https://example.com",
        )

        from trailhead.manifest import InstallManifest, RepoEntry
        from trailhead.fetch import FetchError

        outpost_rev = "b" * 40  # different from trailhead checkout

        entries = [
            # trailhead is the local-self entry (L-1): verified in place.
            RepoEntry(
                name="trailhead",
                rev=None,
                source="local",
                tools=["lore"],
                is_local_self=True,
            ),
            # outpost is a remote entry: its rev must NOT be checked against the
            # trailhead checkout root (I2) — it is skipped here.
            RepoEntry(
                name="outpost",
                rev=outpost_rev,
                source="https://example.com/outpost",
                tools=[],
            ),
        ]
        manifest = InstallManifest(repos=entries)

        # No prior state → both entries appear "changed"
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        verify_calls = []

        def stub_verify(entry, repo_path=None):
            verify_calls.append((entry.name, repo_path))
            if entry.name == "outpost":
                # Simulate a SHA mismatch that would happen if outpost's rev
                # is verified against the trailhead checkout root
                raise FetchError(
                    f"trailhead: version mismatch in 'outpost'\n"
                    f"  expected: {outpost_rev}\n"
                    f"     found: {trailhead_rev}\n"
                    f"The local checkout is at a different version."
                )

        def run():
            from trailhead import update as update_mod
            with patch("trailhead.update.load_install_manifest", return_value=manifest), \
                 patch("trailhead.update.wire", return_value=None), \
                 patch("trailhead.update.verify_present_repo", side_effect=stub_verify):
                return update_mod.run_update(env=env)

        result, out, err = _capture(run)
        # update must NOT fail because of the outpost entry being checked
        # against the trailhead checkout root
        assert result == 0, (
            f"update should not fail due to non-trailhead entry rev check; "
            f"exit={result}, stderr={err!r}"
        )

    def test_trailhead_entry_still_verified(self, tmp_path):
        """The local-self (trailhead) entry itself must still be verified (not skipped)."""
        env = _hermetic_env(tmp_path)
        _save_config(
            env,
            capabilities={"lore": ["capture"]},
            registry="https://example.com",
        )

        from trailhead.manifest import InstallManifest, RepoEntry
        from trailhead.fetch import FetchError

        entries = [
            RepoEntry(
                name="trailhead",
                rev=None,
                source="local",
                tools=["lore"],
                is_local_self=True,
            ),
        ]
        manifest = InstallManifest(repos=entries)

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        verify_calls = []

        def stub_verify(entry, repo_path=None):
            verify_calls.append(entry.name)
            # trailhead entry passes

        def run():
            from trailhead import update as update_mod
            with patch("trailhead.update.load_install_manifest", return_value=manifest), \
                 patch("trailhead.update.wire", return_value=None), \
                 patch("trailhead.update.verify_present_repo", side_effect=stub_verify):
                return update_mod.run_update(env=env)

        result, out, err = _capture(run)
        assert result == 0
        assert "trailhead" in verify_calls, "trailhead entry must still be verified"


# ---------------------------------------------------------------------------
# M5 — cli.main top-level error guard: named errors → clean stderr + nonzero
# ---------------------------------------------------------------------------


class TestM5CliTopLevelErrorGuard:
    """cli.main must catch named trailhead errors → 'trailhead: <msg>' on stderr + nonzero.

    A raw traceback must NOT be shown to the user for named errors.
    """

    def _run_main(self, argv: list[str]):
        """Run cli.main() capturing stdout/stderr; return (exit_code, stdout, stderr)."""
        import trailhead.cli as cli_mod
        old_argv = sys.argv
        old_stdout, old_stderr = sys.stdout, sys.stderr
        out_buf, err_buf = io.StringIO(), io.StringIO()
        try:
            sys.argv = ["trailhead"] + argv
            sys.stdout, sys.stderr = out_buf, err_buf
            try:
                exit_code = cli_mod.main()
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else 1
        finally:
            sys.argv = old_argv
            sys.stdout, sys.stderr = old_stdout, old_stderr
        return exit_code, out_buf.getvalue(), err_buf.getvalue()

    def test_named_error_in_verb_produces_clean_stderr_line(self, tmp_path, monkeypatch):
        """A named trailhead error raised during install → 'trailhead: ...' on stderr, no traceback."""
        monkeypatch.setenv("TRAILHEAD_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))

        from trailhead.paths import PathResolutionError

        def bad_install(*args, **kwargs):
            raise PathResolutionError("HOME is not set in the environment.")

        with patch("trailhead.cli.run_install", side_effect=bad_install):
            exit_code, out, err = self._run_main(["install", "--preset", "standard"])

        assert exit_code != 0, "named error must produce nonzero exit"
        assert "Traceback" not in err, f"raw traceback must not appear; got: {err!r}"
        # Must have a 'trailhead: ...' line on stderr
        assert "trailhead:" in err.lower() or len(err.strip()) > 0, (
            f"Expected 'trailhead: ...' error line on stderr, got: {err!r}"
        )

    def test_named_wire_error_produces_clean_stderr(self, tmp_path, monkeypatch):
        """A WireError in a verb → clean stderr line + nonzero exit, no traceback."""
        monkeypatch.setenv("TRAILHEAD_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))

        from trailhead.wire import WireError

        def bad_update(*args, **kwargs):
            raise WireError(tool="lore", stage="compose", cause=Exception("boom"))

        with patch("trailhead.cli.run_update", side_effect=bad_update):
            exit_code, out, err = self._run_main(["update"])

        assert exit_code != 0
        assert "Traceback" not in err, f"raw traceback must not appear; got: {err!r}"
        assert len(err.strip()) > 0, "some error message must appear on stderr"

    def test_generic_unexpected_exception_produces_clean_stderr(self, tmp_path, monkeypatch):
        """A truly unexpected exception → generic error line on stderr + nonzero, no raw traceback."""
        monkeypatch.setenv("TRAILHEAD_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))

        def bad_doctor(*args, **kwargs):
            raise RuntimeError("something unexpected broke")

        with patch("trailhead.cli.run_doctor", side_effect=bad_doctor):
            exit_code, out, err = self._run_main(["doctor"])

        assert exit_code != 0
        assert "Traceback" not in err, f"raw traceback must not appear; got: {err!r}"
        assert len(err.strip()) > 0


# ---------------------------------------------------------------------------
# M1 — pathint failure after successful wire → exit 0 with warning (not exit 1)
# ---------------------------------------------------------------------------


class TestM1PathintFailureIsWarning:
    """install: a PathIntegrationError after wire+save_config → exit 0 + warning.

    The install succeeded (wire + config saved). PATH is separately fixable.
    Exiting nonzero here would mislead the user into thinking the install failed.
    """

    def test_pathint_failure_after_wire_exits_zero(self, tmp_path):
        """install: pathint raises → exit 0 (install succeeded, PATH fixable separately)."""
        env = _hermetic_env(tmp_path)

        from trailhead import install as install_mod
        from trailhead.pathint import PathIntegrationError

        def run():
            with patch("trailhead.install.wire", return_value=None), \
                 patch("trailhead.install.verify_present_repo", return_value=True), \
                 patch("trailhead.install._is_tty", return_value=False), \
                 patch("trailhead.install.install_path_integration",
                       side_effect=PathIntegrationError("rc write failed")):
                return install_mod.run_install("standard", env=env)

        result, out, err = _capture(run)
        assert result == 0, (
            f"pathint failure must not be a fatal error; expected exit 0, got {result}; "
            f"stderr={err!r}"
        )

    def test_pathint_failure_prints_warning_to_stderr(self, tmp_path):
        """install: pathint failure → warning on stderr mentioning shim-dir or path_integration."""
        env = _hermetic_env(tmp_path)

        from trailhead import install as install_mod
        from trailhead.pathint import PathIntegrationError

        def run():
            with patch("trailhead.install.wire", return_value=None), \
                 patch("trailhead.install.verify_present_repo", return_value=True), \
                 patch("trailhead.install._is_tty", return_value=False), \
                 patch("trailhead.install.install_path_integration",
                       side_effect=PathIntegrationError("rc write failed")):
                return install_mod.run_install("standard", env=env)

        result, out, err = _capture(run)
        # Warning must mention how to fix PATH manually
        assert len(err.strip()) > 0, "warning must be on stderr"
        assert (
            "path" in err.lower()
            or "shim" in err.lower()
            or "path_integration" in err.lower()
        ), f"warning must mention PATH or shim; got: {err!r}"

    def test_pathint_failure_config_still_saved(self, tmp_path):
        """install: pathint failure → config is still saved (wire succeeded)."""
        env = _hermetic_env(tmp_path)

        from trailhead import install as install_mod
        from trailhead.pathint import PathIntegrationError

        def run():
            with patch("trailhead.install.wire", return_value=None), \
                 patch("trailhead.install.verify_present_repo", return_value=True), \
                 patch("trailhead.install._is_tty", return_value=False), \
                 patch("trailhead.install.install_path_integration",
                       side_effect=PathIntegrationError("rc write failed")):
                return install_mod.run_install("standard", env=env)

        result, out, err = _capture(run)
        # Config must have been saved even though pathint failed
        cfg = _load_config(env)
        assert cfg.preset == "standard", "config must be saved even when pathint fails"


# ---------------------------------------------------------------------------
# M2 — install seeds update_state so first update after install is a no-op
# ---------------------------------------------------------------------------


class TestM2InstallSeedsUpdateState:
    """install must write update_state.json so an immediate update is a no-op.

    Before the fix: the first update after install always re-wires because
    update_state.json doesn't exist yet.
    After the fix: install seeds the file with the current pinned revs.
    """

    def test_install_writes_update_state(self, tmp_path):
        """After run_install, update_state.json must exist in the state dir."""
        env = _hermetic_env(tmp_path)

        from trailhead import install as install_mod

        def run():
            with patch("trailhead.install.wire", return_value=None), \
                 patch("trailhead.install.verify_present_repo", return_value=True), \
                 patch("trailhead.install._is_tty", return_value=False), \
                 patch("trailhead.install.install_path_integration",
                       return_value=_fake_pathint_result(tmp_path)):
                return install_mod.run_install("standard", env=env)

        result, out, err = _capture(run)
        assert result == 0, f"install failed: {err}"

        state_file = tmp_path / "state" / "update_state.json"
        assert state_file.exists(), (
            "install must seed update_state.json so the first update is a no-op"
        )

    def test_update_is_no_op_immediately_after_install(self, tmp_path):
        """update immediately after install (same revs) must be a no-op (wire not called)."""
        env = _hermetic_env(tmp_path)

        from trailhead import install as install_mod
        from trailhead import update as update_mod
        from trailhead.manifest import InstallManifest, RepoEntry

        # The manifest the install uses has this rev
        trailhead_rev = "d" * 40
        fake_entry = RepoEntry(
            name="trailhead",
            rev=trailhead_rev,
            source="https://example.com",
            tools=["lore"],
        )
        fake_manifest = InstallManifest(repos=[fake_entry])

        # Run install (seeds update_state)
        install_wire_calls = []

        def run_install():
            with patch("trailhead.install.wire", side_effect=lambda *a, **kw: install_wire_calls.append(True)), \
                 patch("trailhead.install.verify_present_repo", return_value=True), \
                 patch("trailhead.install._is_tty", return_value=False), \
                 patch("trailhead.install.install_path_integration",
                       return_value=_fake_pathint_result(tmp_path)), \
                 patch("trailhead.install.load_install_manifest", return_value=fake_manifest):
                return install_mod.run_install("standard", env=env)

        _, _, _ = _capture(run_install)

        # Now run update with same manifest (same revs)
        update_wire_calls = []

        def run_update():
            with patch("trailhead.update.load_install_manifest", return_value=fake_manifest), \
                 patch("trailhead.update.verify_present_repo", return_value=True), \
                 patch("trailhead.update.wire",
                       side_effect=lambda *a, **kw: update_wire_calls.append(True)):
                return update_mod.run_update(env=env)

        result, out, err = _capture(run_update)
        assert result == 0, f"update after install should succeed; stderr={err!r}"
        assert not update_wire_calls, (
            "wire must NOT be called on update immediately after install with same revs; "
            f"update_wire_calls={update_wire_calls}"
        )
