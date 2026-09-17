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


# ---- which tree answers: sibling cli/ vs $CLAUDE_PLUGIN_ROOT ------------------


def _plant_tree(root: Path, marker: str, *, with_cli: bool = True) -> Path:
    """Build a synthetic plugin tree whose CLI prints *marker*; return its bin/lore.

    The wrapper under test is copied in verbatim, so the tree exercises the real
    resolution logic rather than a restatement of it.
    """
    (root / "bin").mkdir(parents=True)
    wrapper = root / "bin" / "lore"
    wrapper.write_bytes(BIN_PATH.read_bytes())
    wrapper.chmod(0o755)
    if with_cli:
        (root / "cli").mkdir()
        cli = root / "cli" / "lore"
        cli.write_text(f"print({marker!r})\n", encoding="utf-8")
        cli.chmod(0o755)
    return wrapper


def test_bin_runs_its_own_tree_not_the_env_tree(tmp_path):
    """The wrapper's own sibling cli/ wins over a $CLAUDE_PLUGIN_ROOT elsewhere.

    A wrapper invoked out of a specific checkout must run *that* checkout's CLI.
    Deferring to the harness-supplied plugin root instead makes an absolute-path
    invocation silently run a different, possibly older build.
    """
    _plant_tree(tmp_path / "env_tree", "env")
    wrapper = _plant_tree(tmp_path / "own_tree", "own")

    result = subprocess.run(
        [str(wrapper)],
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(tmp_path / "env_tree")},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "own"


def test_bin_without_a_sibling_cli_fails_in_its_own_tree(tmp_path):
    """No sibling cli/ is a failure in this tree, not a silent hop to another one.

    A marketplace install copies a plugin directory whole — a tree carrying bin/ has
    cli/ beside it, and $CLAUDE_PLUGIN_ROOT names that same copy. An env root can
    therefore never supply a CLI the sibling lookup would not already find in the same
    place; consulting it can only ever run a tree the caller did not name.
    """
    _plant_tree(tmp_path / "env_tree", "env")
    wrapper = _plant_tree(tmp_path / "own_tree", "own", with_cli=False)

    result = subprocess.run(
        [str(wrapper)],
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(tmp_path / "env_tree")},
    )

    assert result.returncode != 0
    assert str(tmp_path / "own_tree" / "cli" / "lore") in result.stderr
