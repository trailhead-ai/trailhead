"""bin/lore PATH wrapper — delegation correctness.

All fixtures are SYNTHETIC (invented vocabulary, no real vault/session names).

Covers:
- bin/lore exists and is executable
- bin/lore --help produces byte-identical stdout + exit code to python3 cli/lore --help
- bin/lore with invalid subcommand forwards the CLI's non-zero exit code
- invoking bin/lore from an arbitrary cwd (e.g. /tmp) still resolves cli/lore
  (location-independence — not just relative to plugin dir)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
BIN_PATH = PLUGIN_ROOT / "bin" / "lore"


def run_bin(args, env=None, cwd=None):
    """Run the bin/lore wrapper directly (as a subprocess, not via python3)."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [str(BIN_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=cwd,
    )


def run_cli_direct(args, env=None, cwd=None):
    """Run the Python CLI directly (python3 cli/lore) for reference comparison."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=cwd,
    )


# ---- existence + permissions ------------------------------------------------


def test_bin_lore_is_executable():
    assert os.access(str(BIN_PATH), os.X_OK), "bin/lore is not executable"


# ---- delegation correctness: --help -----------------------------------------


def test_bin_help_exit_code_matches_cli():
    bin_result = run_bin(["--help"])
    cli_result = run_cli_direct(["--help"])
    assert bin_result.returncode == cli_result.returncode


def test_bin_help_stdout_matches_cli():
    bin_result = run_bin(["--help"])
    cli_result = run_cli_direct(["--help"])
    assert bin_result.stdout == cli_result.stdout


# ---- non-zero exit code forwarding ------------------------------------------


def test_bin_invalid_subcommand_forwards_nonzero():
    result = run_bin(["__invalid_subcommand_that_does_not_exist__"])
    assert result.returncode != 0


def test_bin_invalid_subcommand_exit_code_matches_cli():
    bin_result = run_bin(["__invalid_subcommand_that_does_not_exist__"])
    cli_result = run_cli_direct(["__invalid_subcommand_that_does_not_exist__"])
    assert bin_result.returncode == cli_result.returncode


# ---- location-independence: different cwd -----------------------------------


def test_bin_help_from_tmp_cwd():
    """bin/lore resolves cli/lore even when invoked from /tmp."""
    bin_result = run_bin(["--help"], cwd="/tmp")
    assert bin_result.returncode == 0
    assert "lore" in bin_result.stdout.lower()
