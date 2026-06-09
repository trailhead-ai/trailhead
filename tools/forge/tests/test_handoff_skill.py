"""Content contract for `plugins/forge/skills/handoff/SKILL.md`.

The council folded several non-negotiable user-facing guarantees into the
handoff ritual. These are prose contracts the orchestrating SKILL.md must carry
verbatim enough that a future edit can't silently drop them:

  - code-safety announcement (Advocate C1) — surfaced FIRST.
  - which-backend declaration (Advocate C5).
  - resolved-vault-path confirmation after a clean handoff (Reliability C1).
  - mid-failure handling (Reliability C2).
  - pickup discoverability (Advocate C2).
  - empty-hints guard (Advocate Important).
  - the out-of-repo degraded location (Security C1).

Also guards the structural shape: it must NOT re-introduce the orchestrator-repo
machinery the dev ritual deliberately drops (sibling enum, worktree path shape,
stack-specific build commands).
"""
from __future__ import annotations

from pathlib import Path

import pytest

SKILL = (
    Path(__file__).parent.parent
    / "plugins" / "forge" / "skills" / "handoff" / "SKILL.md"
)


@pytest.fixture(scope="module")
def text() -> str:
    return SKILL.read_text()


def test_code_safety_announcement_present_and_first(text: str):
    """The 'does NOT commit or push your code' safety line must be present and
    surfaced before the mechanics — it's the first thing the user sees."""
    assert "does NOT commit or push your code" in text
    safety_idx = text.index("does NOT commit or push your code")
    # It precedes the git-capture mechanics.
    assert safety_idx < text.index("git status --porcelain")


def test_which_backend_declaration_present(text: str):
    assert "Using lore vault at" in text
    assert "writing local forge handoff at" in text


def test_resolved_vault_path_confirmation_present(text: str):
    """After a clean `lore handoff`, the skill echoes the resolved $LORE_VAULT
    so a wrong/shadow vault can't silently misdirect (Reliability C1)."""
    assert "$LORE_VAULT" in text


def test_mid_failure_handling_present(text: str):
    assert "handoff FAILED" in text
    assert "is NOT shelved" in text


def test_pickup_discoverability_present(text: str):
    assert "/forge:pickup" in text


def test_empty_hints_guard_present(text: str):
    assert "next-action" in text or "next action" in text
    assert "blocker" in text


def test_degraded_path_is_out_of_repo(text: str):
    assert "~/.forge/handoffs/" in text


def test_lore_detection_uses_three_state_probe(text: str):
    assert "command -v lore" in text
    assert "lore stats" in text
    assert "LORE_VAULT" in text


def test_no_sibling_enum_or_worktree_shape(text: str):
    """The generic dev ritual must not re-introduce orchestrator-specific
    sibling-repo enumeration or worktree path-shape orchestration."""
    forbidden = [
        ".claude/worktrees/",
        "mobile-overview",
        "infra-cdk-overview",
    ]
    for token in forbidden:
        assert token not in text, f"SKILL.md must not contain {token!r}"


def test_invokes_helper_script(text: str):
    """The deterministic logic is delegated to the helper, not re-implemented."""
    assert "handoff_capture.py" in text


def test_working_path_uses_pickup_hints_file_flag(text: str):
    """The working-backend path must pass hints atomically via --pickup-hints-file,
    not via the broken 'lore patch … --section' two-step (P3B2-3 fix).
    The old pattern was broken: lore patch takes section as a positional arg
    (no --section flag), and $SESSION_NOTE was never resolved."""
    assert "--pickup-hints-file" in text
    # The broken two-step must not exist in the skill.
    assert "--section" not in text
    assert "$SESSION_NOTE" not in text


def test_working_path_no_separate_lore_patch_step(text: str):
    """No standalone `lore patch` call — hints go through lore handoff atomically."""
    # lore patch (with a space, as a command) must not appear as a separate step.
    assert "lore patch" not in text
