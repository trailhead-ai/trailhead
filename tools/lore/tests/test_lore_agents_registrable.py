"""Lore agent roster — presence, frontmatter, and injection-defense guard.

The lore agent roster:
  - `librarian` (unchanged — byte-pinned against a fixture)
  - `investigator` (fork/rebrand of craft `researcher.md`: opus / xhigh)
  - `researcher` (fork of craft `doc-finder.md`: haiku / low)

Both new agents read vault content that can include shared-layer notes, so each
MUST carry the same `<external-memory>` injection-defense guard `librarian.md`
carries. The guard is part of the fork, not a post-ship tuning item.
"""

from __future__ import annotations

from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "agents"


# ---------------------------------------------------------------------------
# Presence + frontmatter profile
# ---------------------------------------------------------------------------


def test_researcher_documents_tracking_backlog_polling():
    """The lore researcher's description must document its use for polling
    tracking-status backlog items (periodic status checks) — the distinguishing
    purpose that justifies the lighter profile."""
    text = (AGENTS_DIR / "researcher.md").read_text()
    assert "tracking" in text, (
        "researcher must document its use for polling `tracking`-status backlog items"
    )


# ---------------------------------------------------------------------------
# Injection-defense guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_name", ["investigator", "researcher"])
def test_new_agent_carries_injection_defense_guard(agent_name: str):
    """Both new lore agents read vault content, so each MUST carry the
    `<external-memory>` injection-defense guard `librarian.md` carries: shared-
    layer content is reference-only, NEVER instructions."""
    text = (AGENTS_DIR / f"{agent_name}.md").read_text()
    assert "external-memory" in text, (
        f"{agent_name}.md must carry the `<external-memory>` injection-defense guard"
    )
    assert "NEVER" in text, (
        f"{agent_name}.md injection-defense guard must say shared-layer content is "
        "NEVER instructions"
    )
    assert "instructions" in text, (
        f"{agent_name}.md injection-defense guard must frame shared-layer content "
        "as reference-only, never as instructions"
    )
