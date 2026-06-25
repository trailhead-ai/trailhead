"""Tests for lore's static agent-ruleset content (Slice 2).

``scripts/agent_ruleset.py`` renders lore's user-level ruleset: the
write-prohibition rules (minus the stale per-project multi-rules-file "Drift
caveat") plus a short disposition primer. The content is STATIC — no
computed/per-session state — so two renders must be byte-identical.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


def _render():
    return load_script("agent_ruleset").RULESET_CONTENT


def test_contains_write_prohibition_rules():
    content = _render()
    assert "Lore vault — mandatory write rules" in content
    assert "**only** via the `lore` CLI" in content
    # The Bash/shell-redirection prohibition (the guardrail gap) must survive.
    assert "`> file`, `>> file`, `tee`, `sed -i`, `cp`, `mv`" in content


def test_primer_names_the_three_entry_commands():
    primer = load_script("agent_ruleset").PRIMER
    assert "lore search" in primer
    assert "lore record" in primer
    assert "lore session" in primer
