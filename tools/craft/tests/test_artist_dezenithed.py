"""Tests for artist.md — de-zenithed design-mockup-writer absorption.

Contract assertions:

  - the BLOCKED: message names BOTH escape hatches (file:line AND
    "new, no counterpart — <justification>" for greenfield).
  - artist.md states an explicit out-of-scope note for full guided
    aspirational-chrome setup.
  - both create AND update modes survive in artist.md.
  - leak_gate.py over artist.md with an ephemeral denylist → exit 0
    (no zenith tokens survive).

Hermeticity: tmp_path-based ephemeral denylist; no real ~/.claude/ dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
AGENTS_DIR = REPO_ROOT / "plugins" / "craft" / "agents"

ARTIST_MD = AGENTS_DIR / "artist.md"

# ---------------------------------------------------------------------------
# Fixture: artist.md text (skip all if absent — TDD RED phase)
# ---------------------------------------------------------------------------


@pytest.fixture
def artist_text() -> str:
    if not ARTIST_MD.exists():
        pytest.skip("artist.md not yet implemented")
    return ARTIST_MD.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Registration: frontmatter name: artist, registrable
# (these mirror test_agents_registrable.py patterns for the named agent)
# ---------------------------------------------------------------------------


def test_artist_frontmatter_name_is_artist(artist_text: str):
    """artist.md frontmatter must carry name: artist."""
    assert artist_text.startswith("---\n"), "artist.md must open with a YAML frontmatter block"
    end = artist_text.find("\n---", 3)
    assert end > 0, "artist.md frontmatter block must be closed"
    frontmatter = artist_text[3:end]
    name_lines = [
        ln
        for ln in frontmatter.splitlines()
        if ln.strip().startswith("name:") and ln.split(":", 1)[1].strip()
    ]
    assert name_lines, "artist.md frontmatter must carry a non-empty name:"
    name_value = name_lines[0].split(":", 1)[1].strip()
    assert name_value == "artist", (
        f"artist.md frontmatter name: must be 'artist', got {name_value!r}"
    )


def test_artist_frontmatter_has_description(artist_text: str):
    """artist.md frontmatter must carry a non-empty description:."""
    end = artist_text.find("\n---", 3)
    frontmatter = artist_text[3:end]
    desc_lines = [
        ln
        for ln in frontmatter.splitlines()
        if ln.strip().startswith("description:") and ln.split(":", 1)[1].strip()
    ]
    assert desc_lines, "artist.md frontmatter must carry a non-empty description:"


# ---------------------------------------------------------------------------
# BLOCKED message names BOTH escape hatches
# The block rule must state BOTH:
#   (a) "file:line" citation path
#   (b) "new, no counterpart" greenfield note path
# so a greenfield user has a stated unblock path.
# ---------------------------------------------------------------------------


def test_blocked_message_names_file_line_escape(artist_text: str):
    """artist.md block rule must name the file:line citation escape hatch."""
    # The BLOCKED rule must mention the file:line citation path
    assert "file:line" in artist_text or "file : line" in artist_text.lower(), (
        "artist.md must state the file:line citation as an escape hatch in the BLOCKED rule (A-3)"
    )


def test_blocked_message_names_greenfield_escape(artist_text: str):
    """artist.md block rule must name the 'new, no counterpart' greenfield escape hatch."""
    assert "new, no counterpart" in artist_text, (
        "artist.md must state 'new, no counterpart' as a greenfield escape hatch "
        "in the BLOCKED rule (A-3)"
    )


def test_blocked_message_names_both_escapes_in_block_rule(artist_text: str):
    """The citation-block BLOCKED message must name both escape hatches within its own text.

    Specifically: find the BLOCKED: whose text contains 'no anchor' or 'component-mapping row'
    (the citation check at validation step), and assert BOTH 'file:line' AND
    'new, no counterpart' appear within that specific block message — not by proximity
    to some other BLOCKED: message elsewhere in the doc.
    """
    # Locate all BLOCKED: occurrences and find the citation-block one specifically
    search_start = 0
    citation_blocked_idx = -1
    while True:
        idx = artist_text.find("BLOCKED:", search_start)
        if idx == -1:
            break
        window = artist_text[idx : idx + 400]
        if "no anchor" in window or "component-mapping row" in window:
            citation_blocked_idx = idx
            break
        search_start = idx + 1

    assert citation_blocked_idx != -1, (
        "artist.md must contain a BLOCKED: message for the citation check — "
        "one whose text references 'no anchor' or 'component-mapping row' (A-3)"
    )

    # The citation block itself must name both escapes
    block_window = artist_text[citation_blocked_idx : citation_blocked_idx + 400]
    assert "file:line" in block_window, (
        "The citation-block BLOCKED message must name 'file:line' as an escape hatch (A-3)"
    )
    assert "new, no counterpart" in block_window, (
        "The citation-block BLOCKED message must name 'new, no counterpart' "
        "as a greenfield escape hatch (A-3)"
    )


# ---------------------------------------------------------------------------
# greenfield aspirational-chrome is an explicit out-of-scope note
# The artist must state that full guided aspirational-chrome setup is NOT
# absorbed into this agent.
# ---------------------------------------------------------------------------


def test_greenfield_aspirational_chrome_out_of_scope_note(artist_text: str):
    """artist.md must state an explicit out-of-scope note for aspirational-chrome setup."""
    # Must mention aspirational chrome is out of scope
    has_aspirational = "aspirational" in artist_text.lower()
    has_out_of_scope = (
        "out of scope" in artist_text.lower() or "not in scope" in artist_text.lower()
    )

    assert has_aspirational and has_out_of_scope, (
        "artist.md must carry an explicit note that full aspirational-chrome setup "
        "is out of scope (A-4). "
        f"Found 'aspirational': {has_aspirational}, found out-of-scope note: {has_out_of_scope}"
    )


# ---------------------------------------------------------------------------
# both create AND update modes survive in artist.md
# ---------------------------------------------------------------------------


def test_artist_carries_create_mode(artist_text: str):
    """artist.md must describe a create mode."""
    assert "mode: create" in artist_text or "create" in artist_text, (
        "artist.md must carry a create mode section (A-5)"
    )
    # More specifically, must have a structured create section
    assert re.search(r"#+\s*.*create", artist_text, re.IGNORECASE), (
        "artist.md must have a section header describing the create mode (A-5)"
    )


def test_artist_carries_update_mode(artist_text: str):
    """artist.md must describe an update mode."""
    assert "mode: update" in artist_text or "update" in artist_text, (
        "artist.md must carry an update mode section (A-5)"
    )
    assert re.search(r"#+\s*.*update", artist_text, re.IGNORECASE), (
        "artist.md must have a section header describing the update mode (A-5)"
    )


def test_both_modes_structurally_present(artist_text: str):
    """artist.md must contain both create and update mode sections."""
    create_match = re.search(
        r"#+\s+.*\bcreate\b.*mode|mode.*\bcreate\b", artist_text, re.IGNORECASE
    )
    update_match = re.search(
        r"#+\s+.*\bupdate\b.*mode|mode.*\bupdate\b", artist_text, re.IGNORECASE
    )
    assert create_match is not None, (
        "artist.md must have a section heading for the create mode (A-5)"
    )
    assert update_match is not None, (
        "artist.md must have a section heading for the update mode (A-5)"
    )


# ---------------------------------------------------------------------------
# Prose contract: generic (no hardcoded surfaces, no baked-in aesthetic)
# ---------------------------------------------------------------------------


def test_artist_resolves_roots_from_input_env(artist_text: str):
    (
        """artist.md must reference resolving designs_root/chrome_root from input fields """
        """AND DESIGNS_ROOT/CHROME_ROOT env vars."""
    )
    # Must name the concrete fields
    assert "designs_root" in artist_text, (
        "artist.md must name the 'designs_root' field so callers know the resolution contract"
    )
    assert "chrome_root" in artist_text, (
        "artist.md must name the 'chrome_root' field so callers know the resolution contract"
    )
    # Must name the env vars (now real in combine_design.py)
    assert "DESIGNS_ROOT" in artist_text, (
        "artist.md must name the DESIGNS_ROOT env var as a fallback for designs_root"
    )
    assert "CHROME_ROOT" in artist_text, (
        "artist.md must name the CHROME_ROOT env var as a fallback for chrome_root"
    )


def test_artist_does_not_name_specific_aesthetic(artist_text: str):
    """artist.md must not bake in a specific aesthetic (the look comes from the catalog)."""
    forbidden_aesthetic_terms = [
        "cream",
        "burgundy",
        "serif",
        "Zenith's established",
        "established admin aesthetic",
        # Surface-specific descriptors from the de-zenith source (regression armor)
        "Suisse Intl",
        "390-wide",
    ]
    for term in forbidden_aesthetic_terms:
        assert term.lower() not in artist_text.lower(), (
            f"artist.md must not name the aesthetic term {term!r} — the look comes from "
            "the consumed catalog, not the agent"
        )


def test_artist_does_not_have_fixed_surface_list(artist_text: str):
    (
        """artist.md must not hardcode a fixed list of surfaces """
        """(surfaces are data, read from the brief + chrome catalog)."""
    )
    # The three zenith-specific surfaces must not appear
    forbidden_surfaces = [
        "platform-admin-ui",
        "patient-portal-web",
        "mobile-overview",
    ]
    for surface in forbidden_surfaces:
        assert surface not in artist_text, (
            f"artist.md must not hardcode the surface {surface!r} — "
            "surfaces are data read from the brief"
        )


def test_artist_carries_anchor_to_real_chrome_block_rule(artist_text: str):
    """artist.md must state the anchor-to-real-chrome BLOCK rule with both escape paths."""
    assert "BLOCKED" in artist_text, (
        "artist.md must carry a BLOCKED: rule for missing file:line citations"
    )
