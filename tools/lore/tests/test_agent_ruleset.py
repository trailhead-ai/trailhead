"""Tests for lore's resident agent-ruleset seam.

``lore/config/agent_ruleset.py`` renders lore's user-level ruleset via
``render_ruleset_content()``. What that text *says* is authored prose with no
behaviour to observe — asserting a phrase appears in it proves only that someone
typed it — so this suite tests the two things about the module that do vary:
that importing it does not drag in the CLI dispatch layer, and that the harness
seam reads the rendered content back as current, stale, or missing depending on
what is on disk.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


def _render():
    return load_script("lore.config.agent_ruleset").render_ruleset_content()


# ---------------------------------------------------------------------------
# /lore:flush + lore session candidate (mid-task capture)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# render_ruleset_content(): integration, guardrail-first ordering, determinism
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# No import cycle: importing the module must never load the CLI parser.
# ---------------------------------------------------------------------------

def test_importing_agent_ruleset_does_not_import_cli_dispatch():
    """Importing this config-layer module must never pull in ``lore.cli``.

    Runs in a fresh subprocess so sys.modules pollution from earlier tests in
    this same process (which may already have imported lore.cli.dispatch)
    can't mask a real cycle or fake a clean result.
    """
    code = (
        f"import sys; sys.path.insert(0, {str(PLUGIN_ROOT)!r})\n"
        "import lore.config.agent_ruleset\n"
        "print('lore.cli.dispatch' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", (
        f"importing lore.config.agent_ruleset must not eagerly import "
        f"lore.cli.dispatch; stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Drift via the seam: current -> stale -> missing, through the real harness.
# ---------------------------------------------------------------------------

def test_drift_via_the_seam_current_stale_missing(tmp_path):
    pytest.importorskip(
        "trailhead",
        reason="requires the trailhead package on sys.path; lore's suite is "
        "also run standalone (tools/lore/pyproject.toml testpaths) without it",
    )
    from trailhead.harness.claude_code import ClaudeCodeHarness

    content = _render()

    harness = ClaudeCodeHarness()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    env = {"TRAILHEAD_CLAUDE_DIR": str(home / ".claude")}

    assert harness.user_ruleset_status("probe", content, env=env) == "missing"

    harness.install_user_ruleset("probe", content, env=env)
    assert harness.user_ruleset_status("probe", content, env=env) == "current"

    path = harness.user_ruleset_path("probe", env=env)
    path.write_text(path.read_text() + "\nmutated\n")
    assert harness.user_ruleset_status("probe", content, env=env) == "stale"
