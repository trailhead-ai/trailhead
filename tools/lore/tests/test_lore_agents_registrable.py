"""Lore agent roster — presence, frontmatter, and injection-defense guard.

S6 Slice 3 establishes the lore agent roster:
  - `librarian` (unchanged — byte-pinned against a pre-Slice-3 fixture)
  - `investigator` (fork/rebrand of craft `researcher.md`: opus / xhigh)
  - `researcher` (fork of craft `doc-finder.md`: haiku / low)

Both new agents read vault content that can include shared-layer notes, so each
MUST carry the same `<external-memory>` injection-defense guard `librarian.md`
carries. The guard is part of the fork, not a post-ship tuning item.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "agents"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# The pinned pre-Slice-3 snapshot of librarian.md. Slice 3 must NOT modify
# librarian.md; the unchanged-assertion compares the live file against THIS
# fixture (not against itself — so it is not tautological, and an S5 edit to
# agent-rules can't silently move the basis).
_LIBRARIAN_PIN = FIXTURES_DIR / "librarian_pre_slice3.md"


def _frontmatter(agent_md: Path) -> str:
    text = agent_md.read_text()
    assert text.startswith("---\n"), (
        f"{agent_md.name} must open with a `---` frontmatter block"
    )
    end = text.find("\n---", 3)
    assert end > 0, f"{agent_md.name} frontmatter block is not closed"
    return text[3:end]


def _field(frontmatter: str, field: str) -> str | None:
    for ln in frontmatter.splitlines():
        if ln.strip().startswith(f"{field}:"):
            return ln.split(":", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# Presence + frontmatter profile (KU2)
# ---------------------------------------------------------------------------

def test_investigator_agent_present_with_profile():
    """investigator exists with valid frontmatter; inherits craft researcher's
    profile verbatim (opus / xhigh) per KU2."""
    agent = AGENTS_DIR / "investigator.md"
    assert agent.exists(), f"{agent} must exist (Slice 3)"
    fm = _frontmatter(agent)
    assert _field(fm, "name") == "investigator", (
        "investigator frontmatter name: must be 'investigator'"
    )
    assert _field(fm, "model") == "opus", (
        "investigator must inherit craft researcher's model: opus (KU2)"
    )
    assert _field(fm, "effort") == "xhigh", (
        "investigator must inherit craft researcher's effort: xhigh (KU2)"
    )
    assert _field(fm, "description"), "investigator must carry a non-empty description:"


def test_researcher_agent_present_with_profile():
    """researcher exists with valid frontmatter; inherits craft doc-finder's
    lighter profile verbatim (haiku / low) per KU2."""
    agent = AGENTS_DIR / "researcher.md"
    assert agent.exists(), f"{agent} must exist (Slice 3)"
    fm = _frontmatter(agent)
    assert _field(fm, "name") == "researcher", (
        "researcher frontmatter name: must be 'researcher'"
    )
    assert _field(fm, "model") == "haiku", (
        "researcher must inherit craft doc-finder's model: haiku (KU2)"
    )
    assert _field(fm, "effort") == "low", (
        "researcher must inherit craft doc-finder's effort: low (KU2)"
    )
    assert _field(fm, "description"), "researcher must carry a non-empty description:"


def test_researcher_documents_tracking_backlog_polling():
    """The lore researcher's description must document its use for polling
    tracking-status backlog items (periodic status checks) — the distinguishing
    purpose that justifies the lighter profile (Slice 3)."""
    text = (AGENTS_DIR / "researcher.md").read_text()
    assert "tracking" in text, (
        "researcher must document its use for polling `tracking`-status backlog items"
    )


# ---------------------------------------------------------------------------
# Injection-defense guard (council Important — Security)
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


# ---------------------------------------------------------------------------
# librarian byte-pin (council Important — non-tautological baseline)
# ---------------------------------------------------------------------------

def test_librarian_unchanged_against_pinned_snapshot():
    """librarian.md must be byte-for-byte identical to the pinned pre-Slice-3
    fixture. Slice 3 establishes the roster but must NOT touch librarian — the
    comparison is against a captured snapshot, not the live file itself, so it
    cannot pass tautologically."""
    assert _LIBRARIAN_PIN.exists(), (
        f"pinned librarian fixture {_LIBRARIAN_PIN} must exist"
    )
    live = (AGENTS_DIR / "librarian.md").read_bytes()
    pinned = _LIBRARIAN_PIN.read_bytes()
    live_sha = hashlib.sha256(live).hexdigest()
    pinned_sha = hashlib.sha256(pinned).hexdigest()
    assert live_sha == pinned_sha, (
        "librarian.md changed in Slice 3 — it must remain byte-for-byte the "
        f"pre-Slice-3 baseline.\n  live   sha256={live_sha}\n  pinned sha256={pinned_sha}"
    )
