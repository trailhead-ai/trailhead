"""Tests for lore's resident agent-ruleset content.

``lore/config/agent_ruleset.py`` renders lore's user-level ruleset via
``render_ruleset_content()``: the write-prohibition rules (minus the stale
per-project multi-rules-file "Drift caveat"), a short disposition primer, and
a generated command reference. The content is deterministic — computed from
the live CLI parser on every call, but two calls always return byte-identical
output, since it is byte-stability (not literal source-level staticness) that
the whole-file drift compare (``user_ruleset_status``) actually requires.

The write-prohibition block must name ``/lore:flush``, must not reference a
non-existent ``/checkpoint`` command, and must teach ``lore session candidate``
as the mid-task capture path.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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

def test_render_contains_prohibition_primer_and_command_reference():
    mod = load_script("lore.config.agent_ruleset")
    content = mod.render_ruleset_content()
    assert mod._WRITE_PROHIBITION in content
    assert mod.PRIMER in content
    assert "## Lore command reference (generated)" in content


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
    """Only CALLING render_ruleset_content() may load lore.cli.dispatch.

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


# ---------------------------------------------------------------------------
# Fail-closed: an enrichment failure must never take down the write-prohibition.
# ---------------------------------------------------------------------------

def test_render_falls_back_when_build_reference_fails(monkeypatch, capsys):
    mod = load_script("lore.config.agent_ruleset")
    from lore.config import command_reference as command_reference_mod

    def _boom(parser):
        raise RuntimeError("boom")

    monkeypatch.setattr(command_reference_mod, "build_reference", _boom)
    content = mod.render_ruleset_content()

    assert content == f"{mod._WRITE_PROHIBITION}\n{mod.PRIMER}", (
        "on enrichment failure, content must fall back to just the "
        "write-prohibition + primer, with no invocation-reference block"
    )
    assert content.index(mod._WRITE_PROHIBITION) == 0

    captured = capsys.readouterr()
    assert "command-reference generation failed" in captured.err


def test_render_falls_back_when_build_parser_fails(monkeypatch, capsys):
    mod = load_script("lore.config.agent_ruleset")
    import lore.cli.dispatch as dispatch_mod

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(dispatch_mod, "build_parser", _boom)
    content = mod.render_ruleset_content()

    assert content == f"{mod._WRITE_PROHIBITION}\n{mod.PRIMER}"
    captured = capsys.readouterr()
    assert "command-reference generation failed" in captured.err
