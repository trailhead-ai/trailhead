"""Tests for lore's resident agent-ruleset content.

``lore/config/agent_ruleset.py`` renders lore's user-level ruleset via
``render_ruleset_content()``: the write-prohibition rules (minus the stale
per-project multi-rules-file "Drift caveat") plus a short disposition primer —
nothing else. The content is static and byte-identical across calls.

The write-prohibition block must name ``/lore:flush``, must not reference a
non-existent ``/checkpoint`` command, and must teach ``lore session candidate``
as the mid-task capture path.
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


def test_contains_write_prohibition_rules():
    content = _render()
    assert "Lore vault — mandatory write rules" in content
    assert "**only** via the `lore` CLI" in content
    # The Bash/shell-redirection prohibition (the guardrail gap) must survive.
    assert "`> file`, `>> file`, `tee`, `sed -i`, `cp`, `mv`" in content


def test_write_prohibition_carves_out_the_sites_zone():
    """The prohibition must scope itself to record trees and name the one
    subtree that is directly writable, or an agent reading it will refuse the
    publish flow the guard hook and the deny rules both allow.
    """
    prohibition = load_script("lore.config.agent_ruleset")._WRITE_PROHIBITION
    assert "sites/" in prohibition, (
        "the write-prohibition block must name the sites zone as its carve-out"
    )
    assert "outpost:publish-site" in prohibition, (
        "the carve-out must point at the skill that owns the publish convention"
    )
    assert "nested" in prohibition and ".git" in prohibition, (
        "the carve-out must warn against creating a nested .git inside the "
        "sites zone — it corrupts the vault's own sync"
    )


def test_sites_carve_out_does_not_weaken_the_record_prohibition():
    """The carve-out is a scope statement, not a loophole: the CLI-only rule for
    records and the Bash-gap note must both survive beside it."""
    content = _render()
    assert "**only** via the `lore` CLI" in content
    assert "opaque to Bash-mediated writes" in content


def test_primer_names_the_three_entry_commands():
    primer = load_script("lore.config.agent_ruleset").PRIMER
    assert "lore search" in primer
    assert "lore record" in primer
    assert "lore session" in primer


# ---------------------------------------------------------------------------
# /lore:flush + lore session candidate (mid-task capture)
# ---------------------------------------------------------------------------

def test_names_lore_flush_skill():
    """The write-prohibition block names the /lore:flush finalization skill."""
    content = _render()
    assert "/lore:flush" in content, (
        "render_ruleset_content() must name /lore:flush (the finalization skill)"
    )


def test_does_not_reference_checkpoint():
    """The rules file must not reference a non-existent /checkpoint command.

    The mid-task capture path is `lore session candidate` (continuous); the
    evaluation+finalization path is /lore:flush.
    """
    content = _render()
    assert "/checkpoint" not in content, (
        "render_ruleset_content() must NOT reference /checkpoint — no such "
        "command exists; the capture path is `lore session candidate` + "
        "/lore:flush"
    )


def test_teaches_lore_session_candidate_as_capture_path():
    """The rules file must teach `lore session candidate` as the mid-task capture path.

    Capture is continuous (candidate); flush evaluates.
    """
    content = _render()
    assert "lore session candidate" in content, (
        "render_ruleset_content() must teach `lore session candidate` as the "
        "mid-task capture path"
    )


# ---------------------------------------------------------------------------
# render_ruleset_content(): integration, guardrail-first ordering, determinism
# ---------------------------------------------------------------------------

def test_render_is_exactly_prohibition_plus_primer():
    mod = load_script("lore.config.agent_ruleset")
    content = mod.render_ruleset_content()
    assert content == f"{mod._WRITE_PROHIBITION}\n{mod.PRIMER}"


def test_write_prohibition_is_first_in_the_rendered_content():
    """A future reorder must not be able to silently demote the guardrail."""
    mod = load_script("lore.config.agent_ruleset")
    content = mod.render_ruleset_content()
    assert content.index(mod._WRITE_PROHIBITION) == 0


def test_two_renders_are_byte_identical():
    mod = load_script("lore.config.agent_ruleset")
    assert mod.render_ruleset_content() == mod.render_ruleset_content()


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
