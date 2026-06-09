"""P3B2-2b tests: bin/lore PATH wrapper — delegation correctness.

TDD: tests written before implementation. All fixtures are SYNTHETIC
(invented vocabulary, no real vault/session names).

Covers:
- bin/lore exists and is executable
- bin/lore --help produces byte-identical stdout + exit code to python3 cli/lore --help
- bin/lore stats delegates correctly (byte-identical output + exit code)
- bin/lore with invalid subcommand forwards the CLI's non-zero exit code
- invoking bin/lore from an arbitrary cwd (e.g. /tmp) still resolves cli/lore
  (location-independence — not just relative to plugin dir)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

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
        capture_output=True, text=True, env=full_env, cwd=cwd,
    )


def run_cli_direct(args, env=None, cwd=None):
    """Run the Python CLI directly (python3 cli/lore) for reference comparison."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, cwd=cwd,
    )


def _make_vault(tmp_path: Path) -> Path:
    """Scaffold a minimal synthetic vault for stats tests."""
    vault = tmp_path / "synth-vault"
    vault.mkdir()
    for d in ("sessions", "deferred", "subsystems", "decisions", "dead-ends",
              "lessons", "radar", "collaboration", "specs", "plans", "designs",
              "inbox", "briefings", "reviews", "gotchas", "audits", "tools",
              "templates"):
        (vault / d).mkdir()
    (vault / "sessions" / "2099-01-01-test-alpha.md").write_text(
        "---\ntype: session\nstatus: active\n---\n\n# Alpha session\n"
    )
    return vault


# ---- existence + permissions ------------------------------------------------

def test_bin_lore_exists():
    assert BIN_PATH.exists(), f"bin/lore not found at {BIN_PATH}"


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


# ---- delegation correctness: stats ------------------------------------------

def test_bin_stats_exit_code_matches_cli(tmp_path):
    vault = _make_vault(tmp_path)
    env = {"LORE_VAULT": str(vault)}
    bin_result = run_bin(["stats"], env=env)
    cli_result = run_cli_direct(["stats"], env=env)
    assert bin_result.returncode == cli_result.returncode


def test_bin_stats_stdout_matches_cli(tmp_path):
    vault = _make_vault(tmp_path)
    env = {"LORE_VAULT": str(vault)}
    bin_result = run_bin(["stats"], env=env)
    cli_result = run_cli_direct(["stats"], env=env)
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


def test_bin_stats_from_tmp_cwd(tmp_path):
    """bin/lore stats resolves correctly when cwd is unrelated to the plugin dir."""
    vault = _make_vault(tmp_path)
    env = {"LORE_VAULT": str(vault)}
    bin_result = run_bin(["stats"], env=env, cwd="/tmp")
    cli_result = run_cli_direct(["stats"], env=env)
    assert bin_result.returncode == cli_result.returncode
    assert bin_result.stdout == cli_result.stdout


# ---- no machine-specific paths baked in -------------------------------------

def test_bin_wrapper_no_hardcoded_absolute_paths():
    """The wrapper must not contain any hardcoded absolute paths."""
    content = BIN_PATH.read_text()
    # Should not contain /Users/<anything> or /home/<anything>
    assert "/Users/" not in content, "bin/lore contains a hardcoded /Users/... path"
    assert "/home/" not in content, "bin/lore contains a hardcoded /home/... path"
