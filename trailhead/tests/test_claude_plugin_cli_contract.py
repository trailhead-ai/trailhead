"""Pins the one `claude plugin` CLI behaviour trailhead's wiring depends on.

Registration and per-tool install markers live under the Claude config dir, so a
config dir whose plugins were installed before that keying existed reads as
uninstalled and takes ``wire()``'s ``install_tool`` branch — an install of a
plugin the CLI already has. Unlike ``rewire_tool`` that branch does not uninstall
first, and ``install.py`` turns any ``WireError`` into a failed run before shims
and rulesets are written, so a nonzero exit there would abort the whole install
on the single re-run that migration needs.

The CLI reports "already installed" and exits 0, which is what makes that branch
safe. This test is that claim, checked against the real binary rather than
assumed. It shells out only when ``claude`` is on PATH, and pins
``CLAUDE_CONFIG_DIR`` at a ``tmp_path`` so nothing touches the live install
(Axiom 6).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("claude") is None, reason="the claude CLI is not installed here"
)


def _marketplace(root: Path, tool: str = "lore") -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "trailhead",
                "owner": {"name": "trailhead"},
                "description": "Trailhead-composed plugin marketplace.",
                "plugins": [
                    {"name": tool, "source": f"./plugins/{tool}", "description": "fixture"}
                ],
            }
        )
    )
    plugin = root / "plugins" / tool / ".claude-plugin"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": tool, "description": "fixture", "version": "0.0.1"})
    )
    return root


def _run(args: list[str], config_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_installing_an_already_installed_plugin_exits_zero(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    source = _marketplace(tmp_path / "composed")

    added = _run(
        ["claude", "plugin", "marketplace", "add", "--scope", "user", str(source)], config_dir
    )
    assert added.returncode == 0, added.stderr

    install = ["claude", "plugin", "install", "lore@trailhead", "--scope", "user"]
    first = _run(install, config_dir)
    assert first.returncode == 0, first.stderr

    second = _run(install, config_dir)
    assert second.returncode == 0, second.stderr
    assert "already installed" in second.stdout
