"""Content contract for `plugins/forge/skills/pickup/SKILL.md`.

The council folded several non-negotiable user-facing guarantees into the
pickup ritual. These are prose contracts the orchestrating SKILL.md must carry
verbatim enough that a future edit can't silently drop them:

  - no-rebase announcement (Advocate) — surfaced as the FIRST output line.
  - which-backend declaration (Advocate C5).
  - symmetric degradation read from ~/.forge/handoffs/ (Advocate C4).
  - empty-state clear message (Advocate Important).
  - 3-state lore detection (same probe as handoff).
  - resume flips shelved -> active via the lore resume subcommand.

Also guards the structural shape: it must NOT re-introduce the orchestrator-repo
machinery the dev ritual deliberately drops (sibling enum, worktree path shape).
"""
from __future__ import annotations

from pathlib import Path

import pytest

SKILL = (
    Path(__file__).parent.parent
    / "plugins" / "forge" / "skills" / "pickup" / "SKILL.md"
)


@pytest.fixture(scope="module")
def text() -> str:
    return SKILL.read_text()


def test_no_rebase_announcement_present(text: str):
    """The 'NOT restored/rebased' staleness warning must be present."""
    assert "NOT restored/rebased" in text
    assert "restore your working tree yourself" in text


def test_no_rebase_is_first_output_line(text: str):
    """The no-rebase line must be surfaced BEFORE the mechanics.

    It must precede the which-backend detection and the Step 3 hint-surfacing
    instructions so the user sees the staleness caveat first. (The descriptive
    intro paragraph above it is the skill's own preamble, not user output.)"""
    no_rebase_idx = text.index("NOT restored/rebased")
    # It precedes the lore detection mechanics and the surfacing step.
    assert no_rebase_idx < text.index("command -v lore")
    assert no_rebase_idx < text.index("Surface the hints")
    # It is the first thing the skill instructs the model to emit.
    assert text.index("Announce FIRST") < text.index("Which backend")


def test_which_backend_declaration_present(text: str):
    assert "Searching lore vault at" in text
    assert "reading local forge handoff at" in text


def test_lore_detection_uses_three_state_probe(text: str):
    assert "command -v lore" in text
    assert "lore stats" in text
    assert "LORE_VAULT" in text


def test_symmetric_degraded_read_location(text: str):
    """Degraded read uses the SAME location handoff wrote to (out of repo)."""
    assert "~/.forge/handoffs/" in text


def test_resume_flips_via_lore_resume(text: str):
    """The working path flips the note back to active via `lore resume`."""
    assert "lore resume" in text
    assert "lore shelved" in text


def test_empty_state_clear_message(text: str):
    """Nothing shelved AND no forge file -> a clear nothing-to-resume message."""
    assert "nothing to resume" in text.lower()


def test_invokes_helper_script(text: str):
    """The deterministic logic is delegated to the helper, not re-implemented."""
    assert "pickup_resume.py" in text


def test_no_sibling_enum_or_worktree_shape(text: str):
    forbidden = [
        ".claude/worktrees/",
        "mobile-overview",
        "infra-cdk-overview",
    ]
    for token in forbidden:
        assert token not in text, f"SKILL.md must not contain {token!r}"


def test_no_placeholder_deferral_language(text: str):
    """The Slice-3 placeholder deferral note must be gone (body replaced)."""
    assert "built in a following slice" not in text
