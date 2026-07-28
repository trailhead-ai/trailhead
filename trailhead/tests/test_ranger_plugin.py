"""Contract tests for the wired ``tools/ranger`` plugin skeleton.

``tools/ranger`` is the sixth trailhead plugin and the second CLI-first one
(after ``camp``/``portage``): its product surface is the ``ranger`` binary that
drains a camp group's shaping queue. This file pins the anatomy the installer
depends on — the manifest's ``cli_bin`` declaration, the ``bin/`` → ``cli/``
wrapper pair, and the CLI's error hygiene — so a later edit cannot silently
break discovery or turn a bad verb into a Python traceback.

The sweep behaviour itself lives behind ``ranger.sweep``; that package is
deliberately empty here and grows its own tests alongside the code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from trailhead.capabilities import load_manifest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOL_ROOT = _REPO_ROOT / "tools" / "ranger"
_CAPABILITIES = _TOOL_ROOT / "capabilities.toml"
_PLUGIN_ROOT = _TOOL_ROOT / "plugins" / "ranger"
_PLUGIN_JSON = _PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
_BIN_RANGER = _PLUGIN_ROOT / "bin" / "ranger"
_CLI_RANGER = _PLUGIN_ROOT / "cli" / "ranger"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``cli/ranger`` in a subprocess with a clean PYTHONPATH.

    Dropping PYTHONPATH proves the shim's own ``sys.path`` insert plus
    ``_bootstrap`` reach the ``ranger`` package and ``trailhead.paths`` on a
    bare checkout — the state a freshly cloned install starts in.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(_CLI_RANGER), *args],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Anatomy: CLI-bearing plugin modelled on portage
# ---------------------------------------------------------------------------


class TestPluginAnatomy:
    def test_manifest_names_ranger_and_declares_the_cli(self):
        m = load_manifest(_CAPABILITIES)
        assert m.tool_name == "ranger"
        assert m.cli_bin == "bin/ranger"
        assert (m.plugin_root / m.cli_bin).is_file()

    def test_manifest_declares_no_always_on_set_yet(self):
        m = load_manifest(_CAPABILITIES)
        assert m.base == []
        assert m.hooks_json is None

    def test_plugin_json_names_ranger(self):
        data = json.loads(_PLUGIN_JSON.read_text())
        assert data.get("name") == "ranger"
        assert data.get("description", "").strip() != ""

    def test_no_per_tool_marketplace_json(self):
        """The single-marketplace convention: no per-tool marketplace.json."""
        assert not (_TOOL_ROOT / ".claude-plugin" / "marketplace.json").exists(), (
            "trailhead uses a single root marketplace; tools/ranger must not carry "
            "its own .claude-plugin/marketplace.json"
        )

    def test_sweep_package_is_present_and_empty(self):
        """Sweep behaviour lands later; the package exists so imports resolve."""
        sweep = _PLUGIN_ROOT / "ranger" / "sweep"
        assert (sweep / "__init__.py").is_file()
        assert sorted(p.name for p in sweep.glob("*.py")) == ["__init__.py"]


# ---------------------------------------------------------------------------
# bin/ranger PATH wrapper
# ---------------------------------------------------------------------------


class TestBinWrapper:
    def test_wrapper_is_executable(self):
        assert os.access(_BIN_RANGER, os.X_OK), f"{_BIN_RANGER} must be executable"

    def test_wrapper_prefers_claude_plugin_root(self):
        text = _BIN_RANGER.read_text()
        assert '"${CLAUDE_PLUGIN_ROOT}/cli/ranger"' in text

    def test_wrapper_avoids_gnu_readlink(self):
        """macOS ships BSD readlink — `readlink -f` would break the fallback.

        Comment lines are excluded: the wrapper documents the constraint in
        prose, and matching that prose would pass the test for the wrong reason.
        """
        code = [
            ln for ln in _BIN_RANGER.read_text().splitlines() if not ln.lstrip().startswith("#")
        ]
        assert not any("readlink -f" in ln for ln in code)

    def test_wrapper_resolves_the_cli_without_the_env_var(self, tmp_path):
        """The self-relative fallback survives being reached through a symlink."""
        link = tmp_path / "ranger"
        link.symlink_to(_BIN_RANGER)
        env = dict(os.environ)
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [str(link), "--help"], env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# CLI error hygiene: `ranger: <msg>` on stderr, nonzero, never a traceback
# ---------------------------------------------------------------------------


class TestCliErrorHygiene:
    def test_help_exits_zero(self):
        result = _run_cli("--help")
        assert result.returncode == 0, result.stderr
        assert "ranger" in result.stdout

    def test_unknown_verb_exits_nonzero(self):
        assert _run_cli("definitely-not-a-verb").returncode != 0

    def test_unknown_verb_names_the_tool_on_stderr(self):
        result = _run_cli("definitely-not-a-verb")
        assert result.stderr.strip(), "an unknown verb must explain itself on stderr"
        assert "ranger:" in result.stderr

    def test_unknown_verb_prints_no_traceback(self):
        result = _run_cli("definitely-not-a-verb")
        assert "Traceback" not in result.stderr
