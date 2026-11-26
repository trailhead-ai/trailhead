"""The docket CLI entry point: help, and the `docket: <message>` error convention."""
from __future__ import annotations

import subprocess
import sys

import pytest

from _docket_cli import CLI, WRAPPER


def _run(argv, exe=None):
    cmd = [str(exe)] if exe else [sys.executable, str(CLI)]
    return subprocess.run([*cmd, *argv], capture_output=True, text=True, timeout=60)


class TestHelp:
    def test_help_prints_usage_and_exits_zero(self):
        proc = _run(["--help"])
        assert proc.returncode == 0, proc.stderr
        assert "usage: docket" in proc.stdout

    @pytest.mark.parametrize("argv", [[], ["report"]])
    def test_bare_invocation_runs_the_report_command(self, monkeypatch, capsys, argv):
        from docket import cli

        monkeypatch.setitem(cli.COMMANDS, "report", cli.Command("r", lambda p: None, lambda args: print("report ran") or 0))
        assert cli.main(argv) == 0
        assert capsys.readouterr().out == "report ran\n"

    def test_the_bin_wrapper_resolves_the_cli_through_a_symlink(self, tmp_path):
        link = tmp_path / "docket-link"
        link.symlink_to(WRAPPER)
        proc = _run(["--help"], exe=link)
        assert proc.returncode == 0, proc.stderr
        assert "usage: docket" in proc.stdout


class TestErrorConvention:
    def test_an_unknown_command_is_a_docket_prefixed_message_without_a_traceback(self):
        proc = _run(["bogus"])
        assert proc.returncode != 0
        assert proc.stderr.startswith("docket: "), proc.stderr
        assert "bogus" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_an_unknown_flag_is_a_docket_prefixed_message_without_a_traceback(self):
        proc = _run(["--nope"])
        assert proc.returncode != 0
        assert proc.stderr.startswith("docket: "), proc.stderr
        assert "Traceback" not in proc.stderr

    @pytest.mark.parametrize("message", ["boom", "the store is unreadable"])
    def test_a_command_raising_docket_error_prints_its_message_and_exits_nonzero(
        self, monkeypatch, capsys, message
    ):
        from docket import cli
        from docket.errors import DocketError

        def run(args):
            raise DocketError(message)

        monkeypatch.setitem(cli.COMMANDS, "explode", cli.Command("raise", lambda p: None, run))
        rc = cli.main(["explode"])
        captured = capsys.readouterr()
        assert rc != 0
        assert captured.err == f"docket: {message}\n"
        assert captured.out == ""

    def test_a_registered_command_runs_with_its_parsed_arguments(self, monkeypatch, capsys):
        from docket import cli

        def add_arguments(parser):
            parser.add_argument("name")

        monkeypatch.setitem(
            cli.COMMANDS,
            "greet",
            cli.Command("say hi", add_arguments, lambda args: print(f"hi {args.name}") or 0),
        )
        assert cli.main(["greet", "ada"]) == 0
        assert capsys.readouterr().out == "hi ada\n"
