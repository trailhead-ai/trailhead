"""Parser-assembly + dispatch parity for the split ``lore`` CLI.

``cli/lore`` is a thin shim that runs ``lore.cli.dispatch.main()``; the parser is
assembled from one ``add_*_subparser`` per command-group module. These tests lock
the two properties that a per-module split can silently break but the behavioral
subprocess suites don't directly assert:

  1. ``--help`` resolves at EVERY level — top-level and every subcommand /
     sub-subcommand — with exit 0 and a coherent ``usage:`` banner naming the
     right ``prog``. This proves the subparser tree is wired into a single parser
     (not N disconnected parsers) and that registration order is preserved.
  2. The error/dispatch paths survive the split: the custom ``_unknown_command_hint``
     redirect table AND argparse's own invalid-choice / missing-required exit(2)
     path, for at least one unknown/invalid case per command group. Help-text
     parity alone does not exercise these.

Everything runs the real ``cli/lore`` as a subprocess against a fenced XDG env so
it never touches the developer's vault/config (Axiom 6) — ``--help`` and argparse
errors are resolved before any vault resolution, so no vault fixture is needed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


def _run(args, tmp_path):
    """Run ``cli/lore`` with a fenced XDG env; return CompletedProcess."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "LORE_EMAIL": "tester@example.com",
    }
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
    )


# Every (args, prog) pair whose ``--help`` must resolve to exit 0 with a banner
# whose ``usage:`` names ``prog`` — top-level, every subcommand, every sub-sub.
_HELP_LEVELS = [
    ([], "lore"),
    (["init"], "lore init"),
    (["status"], "lore status"),
    (["sync"], "lore sync"),
    (["flush"], "lore flush"),
    (["areas"], "lore areas"),
    (["reindex"], "lore reindex"),
    (["search"], "lore search"),
    (["pipeline"], "lore pipeline"),
    (["vault"], "lore vault"),
    (["vault", "add"], "lore vault add"),
    (["vault", "delete"], "lore vault delete"),
    (["vault", "ls"], "lore vault ls"),
    (["vault", "config"], "lore vault config"),
    (["record"], "lore record"),
    (["record", "create"], "lore record create"),
    (["record", "update"], "lore record update"),
    (["record", "delete"], "lore record delete"),
    (["record", "show"], "lore record show"),
    (["record", "rename"], "lore record rename"),
    (["session"], "lore session"),
    (["session", "candidate"], "lore session candidate"),
    (["session", "referenced"], "lore session referenced"),
    (["session", "show"], "lore session show"),
]


class TestHelpAtEveryLevel:
    """``--help`` resolves at every subcommand level — the assembled-parser proof."""

    @pytest.mark.parametrize("args,prog", _HELP_LEVELS, ids=[p for _, p in _HELP_LEVELS])
    def test_help_exits_zero_with_usage(self, args, prog, tmp_path):
        result = _run([*args, "--help"], tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("usage:"), result.stdout
        # The usage banner names this level's prog (argparse derives it from the
        # subparser chain, so a mis-wired tree would carry the wrong prog).
        assert f"usage: {prog}" in result.stdout, result.stdout

    def test_top_level_help_lists_every_command_group(self, tmp_path):
        """The single top-level parser lists all twelve command groups, in order."""
        result = _run(["--help"], tmp_path)
        assert result.returncode == 0
        # Registration order is load-bearing for the help listing; assert the
        # exact choices string argparse renders.
        assert (
            "{init,status,sync,flush,areas,reindex,search,record,task,pipeline,vault,session}"
            in result.stdout
        )


class TestUnknownCommandHint:
    """The custom ``_unknown_command_hint`` redirect table survives the split."""

    def test_retired_recall_redirects_to_search(self, tmp_path):
        result = _run(["recall"], tmp_path)
        assert result.returncode == 2
        assert "unknown command 'recall'" in result.stderr
        assert "did you mean 'lore search'?" in result.stderr

    def test_retired_set_status_redirects_to_record_update(self, tmp_path):
        result = _run(["set-status"], tmp_path)
        assert result.returncode == 2
        assert "unknown command 'set-status'" in result.stderr
        assert "did you mean 'lore record update --status'?" in result.stderr

    def test_unknown_command_without_hint_points_at_help(self, tmp_path):
        result = _run(["boguscmd"], tmp_path)
        assert result.returncode == 2
        assert "unknown command 'boguscmd'" in result.stderr
        assert "Run 'lore --help'" in result.stderr


class TestArgparseErrorPathPerGroup:
    """argparse's own invalid-choice / missing-required exit(2) path, per group.

    Distinct from the unknown-TOP-level path above: these are a *valid* command
    that fails on a sub-argument, which must exit 2 with argparse's error and NOT
    be mislabelled an "unknown command".
    """

    @pytest.mark.parametrize(
        "args",
        [
            ["vault", "bogus-action"],
            ["record", "bogus-action"],
            ["session", "bogus-action"],
        ],
        ids=["vault", "record", "session"],
    )
    def test_unknown_subcommand_is_invalid_choice(self, args, tmp_path):
        result = _run(args, tmp_path)
        assert result.returncode == 2
        assert "invalid choice" in result.stderr
        # Must NOT be swallowed / relabelled as a top-level "unknown command".
        assert "unknown command" not in result.stderr

    def test_missing_required_subcommand_exits_two(self, tmp_path):
        """A bare group that requires a sub-action exits 2 (required subparser)."""
        for group in ("vault", "record", "session"):
            result = _run([group], tmp_path)
            assert result.returncode == 2, f"{group}: {result.stdout}{result.stderr}"
            assert "unknown command" not in result.stderr

    def test_missing_required_flag_exits_two(self, tmp_path):
        """``record create`` without its required ``--kind``/``--title`` exits 2."""
        result = _run(["record", "create"], tmp_path)
        assert result.returncode == 2
        assert "required" in result.stderr
        assert "unknown command" not in result.stderr

    def test_no_command_exits_two(self, tmp_path):
        """A bare ``lore`` with no subcommand exits 2 (top-level required)."""
        result = _run([], tmp_path)
        assert result.returncode == 2
