"""Tests for lore's static agent-ruleset content.

``lore/config/agent_ruleset.py`` renders lore's user-level ruleset: the
write-prohibition rules (minus the stale per-project multi-rules-file "Drift
caveat") plus a short disposition primer. The content is STATIC — no
computed/per-session state — so two renders must be byte-identical.

The write-prohibition block must name ``/lore:flush``, must not reference a
non-existent ``/checkpoint`` command, and must teach ``lore session candidate``
as the mid-task capture path.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


def _render():
    return load_script("lore.config.agent_ruleset").RULESET_CONTENT


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
        "RULESET_CONTENT must name /lore:flush (the finalization skill)"
    )


def test_does_not_reference_checkpoint():
    """The rules file must not reference a non-existent /checkpoint command.

    The mid-task capture path is `lore session candidate` (continuous); the
    evaluation+finalization path is /lore:flush.
    """
    content = _render()
    assert "/checkpoint" not in content, (
        "RULESET_CONTENT must NOT reference /checkpoint — no such command exists; "
        "the capture path is `lore session candidate` + /lore:flush"
    )


def test_teaches_lore_session_candidate_as_capture_path():
    """The rules file must teach `lore session candidate` as the mid-task capture path.

    Capture is continuous (candidate); flush evaluates.
    """
    content = _render()
    assert "lore session candidate" in content, (
        "RULESET_CONTENT must teach `lore session candidate` as the mid-task "
        "capture path"
    )
