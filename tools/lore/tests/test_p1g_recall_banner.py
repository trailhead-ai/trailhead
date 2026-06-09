"""P1-G tests: recall-off banner and RECALL_CLASSIFIER_ENABLED flag.

All fixtures use SYNTHETIC vocabulary per the public-repo fixture discipline
axiom.

Test contract (all must fail before the implementation, pass after):
- With RECALL_CLASSIFIER_ENABLED = False → render_vault_index output CONTAINS
  the recall-off banner.
- With RECALL_CLASSIFIER_ENABLED = True → banner is SUPPRESSED, independent of
  hooks.json presence or absence (the FLAG drives it, not hook-file detection).
- docs/DEGRADATION.md exists and names the classifier entry.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from conftest import PLUGIN_ROOT, REPO_ROOT, SCRIPTS_DIR, load_script


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config_fresh():
    """Load config module freshly (bypasses cached module state)."""
    name = "config"
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal synthetic vault."""
    vault = tmp_path / "vault"
    for d in ("subsystems", "deferred", "dead-ends", "lessons", "sessions",
              "collaboration", "tools"):
        (vault / d).mkdir(parents=True)
    return vault


def _render_index(vault: Path, *, flag_enabled: bool) -> str:
    """Call render_vault_index with the flag patched to the given value."""
    sessions = load_script("sessions")
    config = _load_config_fresh()
    # Patch the flag on the config module that sessions imported at load time
    # by patching the attribute sessions references directly.
    config.RECALL_CLASSIFIER_ENABLED = flag_enabled
    sessions.config.RECALL_CLASSIFIER_ENABLED = flag_enabled
    return sessions.render_vault_index(
        vault=vault,
        worktree_name="synth-worktree",
        project="synth-project",
        session_note=None,
        session_created=False,
    )


# ---------------------------------------------------------------------------
# RECALL_CLASSIFIER_ENABLED flag exists in config module
# ---------------------------------------------------------------------------

class TestConfigModule:
    def test_recall_classifier_enabled_exists(self):
        """config module exposes RECALL_CLASSIFIER_ENABLED."""
        config = _load_config_fresh()
        assert hasattr(config, "RECALL_CLASSIFIER_ENABLED")

    def test_recall_classifier_enabled_default_false(self):
        """RECALL_CLASSIFIER_ENABLED defaults to False (classifier is deferred)."""
        config = _load_config_fresh()
        assert config.RECALL_CLASSIFIER_ENABLED is False


# ---------------------------------------------------------------------------
# Banner presence when flag is False
# ---------------------------------------------------------------------------

class TestRecallBannerWhenDisabled:
    def test_banner_present_when_flag_false(self, tmp_path):
        """With RECALL_CLASSIFIER_ENABLED=False, banner appears in output."""
        vault = _make_vault(tmp_path)
        result = _render_index(vault, flag_enabled=False)
        assert "Mid-conversation subsystem recall is not active" in result

    def test_banner_mentions_classifier_deferred(self, tmp_path):
        """Banner explicitly says the classifier is deferred."""
        vault = _make_vault(tmp_path)
        result = _render_index(vault, flag_enabled=False)
        assert "classifier deferred" in result

    def test_banner_mentions_sessionstart_recall(self, tmp_path):
        """Banner explains that branch/keyword recall fires at SessionStart only."""
        vault = _make_vault(tmp_path)
        result = _render_index(vault, flag_enabled=False)
        assert "SessionStart" in result

    def test_banner_present_with_empty_vault(self, tmp_path):
        """Banner appears even when vault has no notes (empty dirs)."""
        vault = _make_vault(tmp_path)
        result = _render_index(vault, flag_enabled=False)
        assert "Mid-conversation subsystem recall is not active" in result

    def test_banner_present_with_no_hooks_json(self, tmp_path):
        """Banner driven by flag, not hooks.json — absent hooks.json has no effect."""
        vault = _make_vault(tmp_path)
        # Confirm no hooks.json anywhere near vault
        assert not (tmp_path / "hooks.json").exists()
        result = _render_index(vault, flag_enabled=False)
        assert "Mid-conversation subsystem recall is not active" in result


# ---------------------------------------------------------------------------
# Banner suppressed when flag is True
# ---------------------------------------------------------------------------

class TestRecallBannerWhenEnabled:
    def test_banner_absent_when_flag_true(self, tmp_path):
        """With RECALL_CLASSIFIER_ENABLED=True, banner is suppressed."""
        vault = _make_vault(tmp_path)
        result = _render_index(vault, flag_enabled=True)
        assert "Mid-conversation subsystem recall is not active" not in result

    def test_banner_absent_flag_true_no_hooks_json(self, tmp_path):
        """Flag=True suppresses banner regardless of hooks.json absence."""
        vault = _make_vault(tmp_path)
        # Confirm no hooks.json
        assert not (tmp_path / "hooks.json").exists()
        result = _render_index(vault, flag_enabled=True)
        assert "Mid-conversation subsystem recall is not active" not in result

    def test_banner_absent_flag_true_with_fake_hooks_json(self, tmp_path):
        """Flag=True suppresses banner; a hooks.json present does not affect outcome."""
        vault = _make_vault(tmp_path)
        # Write a synthetic hooks.json (empty array) — should have zero effect
        (tmp_path / "hooks.json").write_text("[]")
        result = _render_index(vault, flag_enabled=True)
        assert "Mid-conversation subsystem recall is not active" not in result

    def test_banner_absent_flag_true_with_usersubmit_hook(self, tmp_path):
        """Flag=True suppresses banner even when a UserPromptSubmit hook exists.

        This is the key assertion that the banner is FLAG-driven, not
        hook-presence-driven.
        """
        vault = _make_vault(tmp_path)
        hooks_json = tmp_path / "hooks.json"
        hooks_json.write_text(
            '[{"matcher": "UserPromptSubmit", "hooks": [{"type": "command", "command": "echo synth"}]}]'
        )
        result = _render_index(vault, flag_enabled=True)
        assert "Mid-conversation subsystem recall is not active" not in result

    def test_banner_absent_flag_false_with_usersubmit_hook(self, tmp_path):
        """Flag=False shows banner even when a UserPromptSubmit hook is present.

        Confirms hook-presence cannot suppress the banner — only the flag can.
        """
        vault = _make_vault(tmp_path)
        hooks_json = tmp_path / "hooks.json"
        hooks_json.write_text(
            '[{"matcher": "UserPromptSubmit", "hooks": [{"type": "command", "command": "echo synth"}]}]'
        )
        result = _render_index(vault, flag_enabled=False)
        assert "Mid-conversation subsystem recall is not active" in result


# ---------------------------------------------------------------------------
# Degradation ledger doc existence
# ---------------------------------------------------------------------------

class TestDegradationLedger:
    def test_degradation_md_exists(self):
        """docs/DEGRADATION.md exists at the repo root docs/ directory."""
        doc = REPO_ROOT / "docs" / "DEGRADATION.md"
        assert doc.exists(), f"Expected {doc} to exist"

    def test_degradation_md_names_classifier(self):
        """DEGRADATION.md contains the classifier-deferred entry."""
        doc = REPO_ROOT / "docs" / "DEGRADATION.md"
        text = doc.read_text()
        assert "classifier" in text.lower()

    def test_degradation_md_names_recall(self):
        """DEGRADATION.md describes what is degraded: mid-conversation recall."""
        doc = REPO_ROOT / "docs" / "DEGRADATION.md"
        text = doc.read_text()
        assert "recall" in text.lower()

    def test_degradation_md_names_flag(self):
        """DEGRADATION.md tells adopters how to turn it back on: the flag."""
        doc = REPO_ROOT / "docs" / "DEGRADATION.md"
        text = doc.read_text()
        assert "RECALL_CLASSIFIER_ENABLED" in text
