"""Tests for ranger.sweep.names — the shared shell-safe name allowlist.

Test contract:
- The allowlist anchors on end-of-string (`\\Z`), not `$`, so a name carrying
  a trailing newline is refused rather than slipping through `$`'s
  before-a-trailing-newline exception.
- A vault-name refusal's message carries a remedy — lore accepts vault names
  (e.g. containing a space) that this allowlist refuses, so without a remedy
  clause such a vault would be permanently un-sweepable with no guidance.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from ranger.sweep import names  # noqa: E402


def test_rejects_a_name_with_a_trailing_newline():
    """`$` matches just before a trailing newline; `\\Z` must not."""
    with pytest.raises(ValueError):
        names.validate_shell_safe_name("prod\n", what="vault name")


def test_accepts_a_name_without_a_trailing_newline():
    names.validate_shell_safe_name("prod", what="vault name")


def test_vault_name_refusal_carries_a_remedy():
    """lore accepts vault names (e.g. with a space) this allowlist refuses,
    so the refusal must tell the operator how to make the vault sweepable."""
    with pytest.raises(ValueError) as exc_info:
        names.validate_shell_safe_name("prod name", what="vault name")

    assert "lore vault" in str(exc_info.value)
