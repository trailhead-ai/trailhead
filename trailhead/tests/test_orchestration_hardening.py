"""Orchestration-layer hardening regression tests (post config-driven rewrite).

Surviving invariants after the rewrite (presets/manifest/fetch/update/config were
removed):
  - CLI top-level error guard: a named error → clean 'trailhead: <msg>' on stderr,
    a generic exception → 'trailhead: unexpected error: …'; both exit 1, no traceback.
  - pathint failure is a warning, not a failure: install still exits 0 when a
    harness was wired.
  - the shared wire lock blocks a concurrent install.
  - env=None falls back to os.environ without raising (install + uninstall).
"""

import os
import sys
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch


from trailhead.harness import ClaudeCodeHarness
from trailhead.pathint import ShimDirResult
from trailhead.wire import WireError, wire_lock


def _run_cli(args: list[str]):
    old_argv, old_out, old_err = sys.argv, sys.stdout, sys.stderr
    out, err = StringIO(), StringIO()
    try:
        sys.argv = ["trailhead"] + args
        sys.stdout, sys.stderr = out, err
        from trailhead.cli import main
        try:
            code = main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 0
    finally:
        sys.argv, sys.stdout, sys.stderr = old_argv, old_out, old_err
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# CLI top-level error guard
# ---------------------------------------------------------------------------


class TestCliErrorGuard:
    def test_named_error_produces_clean_stderr(self):
        err = WireError(tool="lore", stage="compose", cause=RuntimeError("boom"))
        with patch("trailhead.cli.run_install", side_effect=err):
            code, out, errtext = _run_cli(["install"])
        assert code == 1
        assert errtext.startswith("trailhead: ")
        assert "Traceback" not in errtext

    def test_generic_exception_produces_clean_stderr(self):
        with patch("trailhead.cli.run_install", side_effect=ValueError("kaboom")):
            code, out, errtext = _run_cli(["install"])
        assert code == 1
        assert "unexpected error" in errtext
        assert "Traceback" not in errtext


# ---------------------------------------------------------------------------
# Hermetic helpers for install
# ---------------------------------------------------------------------------


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {**os.environ, "TRAILHEAD_STATE_DIR": str(tmp_path), "HOME": str(home)}


@contextmanager
def _patched_install(*, detected=True, pathint_exc=None):
    sdr = ShimDirResult(shim_dir=Path("/b"), shims={})
    pathint = patch(
        "trailhead.install.create_shims",
        **({"side_effect": pathint_exc} if pathint_exc else {"return_value": sdr}),
    )
    with patch("trailhead.install.wire"), pathint, patch(
        "trailhead.install.detect_harnesses",
        return_value=([ClaudeCodeHarness()] if detected else []),
    ):
        yield


class TestPathintFailureIsWarning:
    def test_install_exits_zero_when_pathint_fails(self, tmp_path, capsys):
        from trailhead.install import run_install

        with _patched_install(detected=True, pathint_exc=OSError("disk full")):
            rc = run_install(env=_env(tmp_path), quiet=True)
        assert rc == 0
        assert "could not build the CLI shim dir" in capsys.readouterr().err


class TestSharedWireLock:
    def test_install_blocked_when_lock_held(self, tmp_path, capsys):
        from trailhead.install import run_install

        env = _env(tmp_path)
        with _patched_install(detected=True):
            with wire_lock(env=env):  # hold the lock
                rc = run_install(env=env, quiet=True)
        assert rc == 1
        assert "already running" in capsys.readouterr().err


class TestEnvNoneFallback:
    def test_run_install_env_none_does_not_raise(self, tmp_path, monkeypatch):
        from trailhead.install import run_install

        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        with _patched_install(detected=True):
            rc = run_install(quiet=True)  # env=None → os.environ
        assert rc == 0

    def test_run_uninstall_env_none_does_not_raise(self, tmp_path, monkeypatch):
        from trailhead.uninstall import run_uninstall

        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))
        rc = run_uninstall(assume_yes=True)  # nothing installed → 0
        assert rc == 0
