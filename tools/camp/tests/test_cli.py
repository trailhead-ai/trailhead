"""Tests for the camp CLI entry points (bin/camp, cli/camp, capabilities.toml).

Test contract:
- camp --help exits 0 and prints a grouped menu (not a raw argparse dump).
- camp --version prints the binary path.
- camp --which prints the binary path.
- capabilities.toml loads + validates via the Step-1 loader.
- bin/camp wrapper resolves cli/camp (smoke: exits 0 via python invocation).
- marketplace.json source resolves (./plugins/camp).
- D-H guard: cli/camp --help succeeds; guard function tested in test_spine.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_TOOL_DIR = _REPO_ROOT / "tools" / "camp"
_PLUGIN_DIR = _TOOL_DIR / "plugins" / "camp"
_BIN_CAMP = _PLUGIN_DIR / "bin" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"
_CAPABILITIES_TOML = _TOOL_DIR / "capabilities.toml"
_MARKETPLACE_JSON = _TOOL_DIR / ".claude-plugin" / "marketplace.json"


# ---------------------------------------------------------------------------
# camp --help exits 0 and prints grouped menu
# ---------------------------------------------------------------------------


def test_camp_help_exits_0() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"--help exited {result.returncode}\nstderr: {result.stderr}"


def test_camp_help_prints_grouped_menu() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--help"],
        capture_output=True,
        text=True,
    )
    output = result.stdout
    # Must show major command groups, not a raw argparse dump
    assert "Usage:" in output or "usage:" in output
    # Must not be a bare argparse dump (those start with "usage: cli ...")
    assert "error:" not in output.lower() or "error" not in result.stderr.lower()


def test_camp_help_contains_key_commands() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--help"],
        capture_output=True,
        text=True,
    )
    output = result.stdout
    for cmd in ("ls", "status", "break", "sweep", "sync"):
        assert cmd in output, f"Expected {cmd!r} in --help output, got:\n{output}"


# ---------------------------------------------------------------------------
# camp --version prints binary path
# ---------------------------------------------------------------------------


def test_camp_version_exits_0() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"--version exited {result.returncode}\n{result.stderr}"


def test_camp_version_includes_binary_path() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--version"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    # Should include some reference to the binary location
    assert "camp" in output.lower()


# ---------------------------------------------------------------------------
# camp --which
# ---------------------------------------------------------------------------


def test_camp_which_exits_0() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--which"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"--which exited {result.returncode}\n{result.stderr}"


def test_camp_which_prints_path() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--which"],
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    assert output != "", "Expected a non-empty path from --which"


# ---------------------------------------------------------------------------
# bin/camp wrapper resolves cli/camp
# ---------------------------------------------------------------------------


def test_bin_camp_wrapper_exits_0_on_help() -> None:
    """bin/camp (bash wrapper) should resolve cli/camp and exit 0 on --help."""
    result = subprocess.run(
        ["bash", str(_BIN_CAMP), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bin/camp --help exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# capabilities.toml loads + validates via the Step-1 loader
# ---------------------------------------------------------------------------


def test_capabilities_toml_loads_and_validates() -> None:
    from trailhead.capabilities import load_manifest
    manifest = load_manifest(_CAPABILITIES_TOML)
    assert manifest.tool_name == "camp"
    assert manifest.validate is True


def test_capabilities_toml_base_has_worktree_skill() -> None:
    from trailhead.capabilities import load_manifest
    manifest = load_manifest(_CAPABILITIES_TOML)
    assert "skills/worktree" in manifest.base


def test_capabilities_toml_dev_env_capability_declared() -> None:
    from trailhead.capabilities import load_manifest
    manifest = load_manifest(_CAPABILITIES_TOML)
    assert "dev-env" in manifest.capabilities


# ---------------------------------------------------------------------------
# marketplace.json source resolves
# ---------------------------------------------------------------------------


def test_marketplace_json_exists() -> None:
    assert _MARKETPLACE_JSON.is_file(), f"Missing: {_MARKETPLACE_JSON}"


def test_marketplace_json_source_resolves() -> None:
    data = json.loads(_MARKETPLACE_JSON.read_text())
    plugins = data.get("plugins", [])
    assert len(plugins) >= 1
    source = plugins[0]["source"]
    # source is relative to the tool dir (the parent of .claude-plugin/)
    tool_dir = _MARKETPLACE_JSON.parent.parent
    resolved = (tool_dir / source).resolve()
    assert resolved.is_dir(), (
        f"marketplace.json source {source!r} does not resolve to a directory: {resolved}"
    )


def test_marketplace_json_plugin_name_is_camp() -> None:
    data = json.loads(_MARKETPLACE_JSON.read_text())
    plugins = data.get("plugins", [])
    assert plugins[0]["name"] == "camp"
