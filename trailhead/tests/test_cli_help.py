"""Tests for trailhead/cli.py — subcommand tree + curated help.

The CLI exposes four commands: install / uninstall / doctor / update.

Output hygiene: bare `trailhead` and `--help` print a curated grouped menu, never a
raw argparse dump; main() returns an int exit code.
"""

import sys
from io import StringIO


def _run(args: list[str]):
    """Run main() with sys.argv set to args; return (exit_code, stdout, stderr)."""
    old_argv, old_stdout, old_stderr = sys.argv, sys.stdout, sys.stderr
    stdout_buf, stderr_buf = StringIO(), StringIO()
    try:
        sys.argv = ["trailhead"] + args
        sys.stdout, sys.stderr = stdout_buf, stderr_buf
        from trailhead.cli import main

        try:
            exit_code = main()
        except SystemExit as e:
            exit_code = e.code if isinstance(e.code, int) else 0
    finally:
        sys.argv, sys.stdout, sys.stderr = old_argv, old_stdout, old_stderr
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


class TestCuratedHelp:
    def test_bare_trailhead_exits_zero(self):
        assert _run([])[0] == 0

    def test_help_exits_zero(self):
        assert _run(["--help"])[0] == 0

class TestSubcommandHelp:
    def test_doctor_help_exits_zero(self):
        assert _run(["doctor", "--help"])[0] == 0


class TestDoctorRuns:
    def test_doctor_exits_zero_with_empty_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))
        ec, out, _ = _run(["doctor"])
        assert ec == 0
        assert "doctor" in out.lower()


class TestShellenv:
    def test_shellenv_zsh_prints_exports(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))
        ec, out, _ = _run(["shellenv", "--shell", "zsh"])
        assert ec == 0
        assert "export TRAILHEAD_ROOT=" in out
        assert "export PATH=" in out

    def test_shellenv_fish(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / "state"))
        ec, out, _ = _run(["shellenv", "--shell", "fish"])
        assert ec == 0
        assert "fish_add_path" in out
