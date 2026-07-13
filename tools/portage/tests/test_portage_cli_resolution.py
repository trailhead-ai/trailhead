"""bin/portage → cli/portage resolves symlink-safely (macOS, no GNU readlink -f).

The PATH wrapper must find its sibling ``cli/portage`` shim even when invoked
through a symlink from an arbitrary directory — Claude Code adds the plugin's
``bin/`` to PATH and users may symlink it. This drives the real wrapper through
a symlink and asserts the CLI actually runs (help exits 0; a bad manifest exits
2 through the full shim → dispatch → provider path), proving the self-relative
resolution branch works without ``CLAUDE_PLUGIN_ROOT`` set.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _portage_cli  # noqa: F401  (defines PLUGIN_ROOT)

_BIN_PORTAGE = _portage_cli.PLUGIN_ROOT / "bin" / "portage"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    # Strip CLAUDE_PLUGIN_ROOT so the self-relative (symlink-walk) branch is the
    # one under test, not the env short-circuit.
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)


class TestBinResolvesThroughSymlink:
    def test_bin_exists_and_is_executable(self):
        assert _BIN_PORTAGE.is_file(), f"{_BIN_PORTAGE} missing"
        assert os.access(_BIN_PORTAGE, os.X_OK), f"{_BIN_PORTAGE} not executable"

    def test_help_through_symlink_exits_zero(self, tmp_path):
        link = tmp_path / "portage"
        link.symlink_to(_BIN_PORTAGE)
        result = _run([str(link), "--help"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # The resolved shim reached the real dispatch and listed its subcommands.
        assert "detect-repos" in result.stdout and "merge" in result.stdout

    def test_command_through_symlink_reaches_provider(self, tmp_path):
        link = tmp_path / "portage"
        link.symlink_to(_BIN_PORTAGE)
        missing = tmp_path / "nope" / "manifest.json"
        result = _run([str(link), "detect-repos", "--manifest", str(missing)], cwd=tmp_path)
        # Full path exercised: wrapper → cli/portage shim → dispatch → provider.
        assert result.returncode == 2, f"{result.returncode}\n{result.stdout}\n{result.stderr}"
        assert str(missing) in result.stderr


class TestCliShimResolvesPackage:
    def test_cli_shim_runs_directly(self, tmp_path):
        """cli/portage run directly (as bin/portage execs it) resolves the package."""
        cli = _portage_cli.PLUGIN_ROOT / "cli" / "portage"
        result = _run([sys.executable, str(cli), "--help"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "wait-for-actionable" in result.stdout
