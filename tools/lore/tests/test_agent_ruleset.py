"""Tests for lore's static agent-ruleset content (Slice 2 + Slice 5).

``scripts/agent_ruleset.py`` renders lore's user-level ruleset: the
write-prohibition rules (minus the stale per-project multi-rules-file "Drift
caveat") plus a short disposition primer. The content is STATIC — no
computed/per-session state — so two renders must be byte-identical.

Slice 5 additions: the write-prohibition block must name ``/lore:flush``
(not the retired ``/lore:finish``), must not reference ``/checkpoint``, and
must teach ``lore session candidate`` as the mid-task capture path.
"""
from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


def _render():
    return load_script("agent_ruleset").RULESET_CONTENT


def test_two_renders_are_byte_identical():
    assert _render() == _render()


def test_contains_write_prohibition_rules():
    content = _render()
    assert "Lore vault — mandatory write rules" in content
    assert "**only** via the `lore` CLI" in content
    # The Bash/shell-redirection prohibition (the guardrail gap) must survive.
    assert "`> file`, `>> file`, `tee`, `sed -i`, `cp`, `mv`" in content


def test_drops_stale_drift_caveat():
    # The old "Drift caveat" described the now-deleted --local/multi-rules-file model.
    assert "Drift caveat" not in _render()


def test_primer_body_is_at_most_20_lines():
    primer = load_script("agent_ruleset").PRIMER
    assert len(primer.splitlines()) <= 20


def test_primer_names_the_three_entry_commands():
    primer = load_script("agent_ruleset").PRIMER
    assert "lore search" in primer
    assert "lore record" in primer
    assert "lore session" in primer


# ---------------------------------------------------------------------------
# Slice 5 — /lore:flush + lore session candidate (replaces /checkpoint)
# ---------------------------------------------------------------------------

def test_names_lore_flush_skill_not_lore_finish():
    """The write-prohibition block must name /lore:flush, NOT the retired /lore:finish.

    Slice 4 renamed the finish skill to flush; the rules file must follow.
    """
    content = _render()
    assert "/lore:flush" in content, (
        "RULESET_CONTENT must name /lore:flush (the current finalization skill)"
    )
    assert "/lore:finish" not in content, (
        "RULESET_CONTENT must NOT name /lore:finish — it was renamed to /lore:flush (Slice 4)"
    )


def test_does_not_reference_checkpoint():
    """/checkpoint was deleted in Slice 4; the rules file must not teach it.

    The mid-task capture path is `lore session candidate` (continuous); the
    evaluation+finalization path is /lore:flush.
    """
    content = _render()
    assert "/checkpoint" not in content, (
        "RULESET_CONTENT must NOT reference /checkpoint — it was deleted in Slice 4; "
        "the substitute is `lore session candidate` + /lore:flush"
    )


def test_teaches_lore_session_candidate_as_capture_path():
    """The rules file must teach `lore session candidate` as the mid-task capture path.

    Council/Advocate (Slice 5): /checkpoint removal left no taught "save progress"
    path — this line closes the gap.  Capture is continuous (candidate); flush
    evaluates.
    """
    content = _render()
    assert "lore session candidate" in content, (
        "RULESET_CONTENT must teach `lore session candidate` as the mid-task capture "
        "path that replaces /checkpoint (Council/Advocate, Slice 5)"
    )
