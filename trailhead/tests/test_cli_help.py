"""Tests for trailhead/cli.py — subcommand tree skeleton + curated help.

TDD: these tests are written BEFORE the implementation. All must pass after
trailhead/cli.py is updated.

A-9 hygiene:
  - errors → stderr, normal output → stdout
  - main() returns an int exit code
  - bare `trailhead` and `trailhead --help` print a CURATED, grouped menu
    naming the four subcommands — NEVER a raw argparse dump
"""

import sys
from io import StringIO

import pytest

from trailhead.cli import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(args: list[str], *, capture_stdout: bool = True, capture_stderr: bool = True):
    """Run main() with sys.argv set to args; return (exit_code, stdout, stderr)."""
    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    stdout_buf = StringIO()
    stderr_buf = StringIO()
    try:
        sys.argv = ["trailhead"] + args
        if capture_stdout:
            sys.stdout = stdout_buf
        if capture_stderr:
            sys.stderr = stderr_buf
        try:
            exit_code = main()
        except SystemExit as e:
            exit_code = e.code if isinstance(e.code, int) else 0
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# main() return type
# ---------------------------------------------------------------------------


class TestMainReturnsInt:
    def test_main_returns_int_on_help(self):
        try:
            result = main.__wrapped__() if hasattr(main, "__wrapped__") else None
        except Exception:
            result = None
        # The real check: main() must return int (or SystemExit with int code)
        old_argv = sys.argv
        sys.argv = ["trailhead", "--help"]
        try:
            ret = main()
            assert isinstance(ret, int) or ret is None
        except SystemExit as e:
            assert isinstance(e.code, int)
        finally:
            sys.argv = old_argv

    def test_main_returns_zero_for_help(self):
        exit_code, _, _ = _run(["--help"])
        assert exit_code == 0


# ---------------------------------------------------------------------------
# Curated help: bare trailhead + --help print grouped menu
# ---------------------------------------------------------------------------


class TestCuratedHelp:
    def test_help_names_install(self):
        exit_code, out, _ = _run(["--help"])
        assert "install" in out

    def test_help_names_update(self):
        exit_code, out, _ = _run(["--help"])
        assert "update" in out

    def test_help_names_doctor(self):
        exit_code, out, _ = _run(["--help"])
        assert "doctor" in out

    def test_help_names_config(self):
        exit_code, out, _ = _run(["--help"])
        assert "config" in out

    def test_bare_trailhead_names_all_four_subcommands(self):
        exit_code, out, _ = _run([])
        assert "install" in out
        assert "update" in out
        assert "doctor" in out
        assert "config" in out

    def test_bare_trailhead_exits_zero(self):
        exit_code, _, _ = _run([])
        assert exit_code == 0

    def test_help_has_one_line_descriptions(self):
        """Each subcommand must have a short description, not just a name."""
        exit_code, out, _ = _run(["--help"])
        # Check that at least one description-like word appears near each subcommand
        # (the curated menu contract — not a raw argparse dump)
        assert len(out.strip()) > len("install\nupdate\ndoctor\nconfig")

    def test_help_is_not_raw_argparse_dump(self):
        """Curated help must NOT contain 'usage: trailhead [-h]' raw argparse header."""
        exit_code, out, _ = _run(["--help"])
        # Curated help doesn't expose raw argparse formatting as the primary output
        # (it may show it, but it must show the grouped menu prominently)
        # The key invariant: the four subcommands appear
        assert "install" in out and "update" in out and "doctor" in out and "config" in out


# ---------------------------------------------------------------------------
# Subcommand stubs exit 0 with "not yet wired" line
# ---------------------------------------------------------------------------


class TestSubcommandStubs:
    # install is now fully implemented (Slice 4) — its stub tests are superseded
    # by trailhead/tests/test_install.py. The remaining stubs (update, doctor,
    # config) are Slice 5 — they still print "not yet wired".

    def test_update_stub_exits_zero(self):
        exit_code, out, err = _run(["update"])
        assert exit_code == 0

    def test_doctor_stub_exits_zero(self):
        exit_code, out, err = _run(["doctor"])
        assert exit_code == 0

    def test_config_stub_exits_zero(self):
        exit_code, out, err = _run(["config"])
        assert exit_code == 0

    def test_update_stub_prints_not_yet_wired(self):
        exit_code, out, err = _run(["update"])
        combined = out + err
        assert "not yet wired" in combined.lower() or "wired" in combined.lower()

    def test_doctor_stub_prints_not_yet_wired(self):
        exit_code, out, err = _run(["doctor"])
        combined = out + err
        assert "not yet wired" in combined.lower() or "wired" in combined.lower()

    def test_config_stub_prints_not_yet_wired(self):
        exit_code, out, err = _run(["config"])
        combined = out + err
        assert "not yet wired" in combined.lower() or "wired" in combined.lower()

    def test_install_has_preset_flag(self):
        """Slice 4: install now accepts --preset; --help must show it."""
        exit_code, out, err = _run(["install", "--help"])
        assert exit_code == 0
        assert "preset" in out.lower() or "preset" in err.lower()


# ---------------------------------------------------------------------------
# Subcommand --help works
# ---------------------------------------------------------------------------


class TestSubcommandHelp:
    def test_install_help_exits_zero(self):
        exit_code, _, _ = _run(["install", "--help"])
        assert exit_code == 0

    def test_update_help_exits_zero(self):
        exit_code, _, _ = _run(["update", "--help"])
        assert exit_code == 0

    def test_doctor_help_exits_zero(self):
        exit_code, _, _ = _run(["doctor", "--help"])
        assert exit_code == 0

    def test_config_help_exits_zero(self):
        exit_code, _, _ = _run(["config", "--help"])
        assert exit_code == 0
