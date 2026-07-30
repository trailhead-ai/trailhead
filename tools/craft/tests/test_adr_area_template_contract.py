"""ADR and area templates — the section contracts a distill/gauntlet build depends on.

Two new craft-owned templates:

  templates/adr.md   Exactly four body sections: Context, Decision, Consequences,
                      Alternatives rejected. This is exhaustive by spec — no fifth
                      section, no reordering. Provenance (source/derived specs, absorbed
                      decisions, a superseded predecessor) lives in `related:` metadata,
                      never prose; gauntlet review provenance lives in annotations, never
                      the body. Both are pinned so a future edit can't quietly grow the
                      body into a place metadata should live.

  templates/area.md  The Overview / Key files / Gotchas / Conventions quartet the
                      librarian agent already asserts is the area-profile shape
                      (`lore` plugin's `agents/librarian.md`). Cross-referenced here so
                      the two prose surfaces can't drift apart silently: if a future edit
                      renames a section in one file without the other, this test catches
                      it. `## Overview` must be the exact heading spelling the area map's
                      `_ONE_LINER_MAX` lead-line extraction reads
                      (`lore/search/area_map.py:_first_overview_sentence`).

Write BEFORE the templates exist — these tests must fail RED first, then green after.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "templates"
ADR_TEMPLATE = TEMPLATES_DIR / "adr.md"
AREA_TEMPLATE = TEMPLATES_DIR / "area.md"

# Lives in the sibling `lore` plugin — read only, never modified by this slice.
LIBRARIAN_MD = (
    Path(__file__).parent.parent.parent / "lore" / "plugins" / "lore" / "agents" / "librarian.md"
)

_H2_RE = re.compile(r"^## (.+)$", re.MULTILINE)

_ADR_SECTIONS = ["Context", "Decision", "Consequences", "Alternatives rejected"]
_AREA_SECTIONS = ["Overview", "Key files", "Gotchas", "Conventions"]


def _h2_headings(text: str) -> list[str]:
    return _H2_RE.findall(text)


# ---------------------------------------------------------------------------
# adr.md — the four-section contract
# ---------------------------------------------------------------------------


def test_adr_template_exists():
    assert ADR_TEMPLATE.exists(), f"Expected templates/adr.md at {ADR_TEMPLATE}"


def test_adr_template_has_exactly_the_four_sections_in_order():
    """The four-section contract is exhaustive by spec — no fifth section, no reorder."""
    text = ADR_TEMPLATE.read_text()
    assert _h2_headings(text) == _ADR_SECTIONS, (
        f"templates/adr.md must carry exactly these H2 sections in order: {_ADR_SECTIONS}, "
        f"got: {_h2_headings(text)}"
    )


def test_adr_template_states_a_one_screenful_budget():
    text = ADR_TEMPLATE.read_text()
    assert "screenful" in text, (
        "templates/adr.md must state the ~one-screenful budget — an ADR that outgrows "
        "a screen is usually two decisions."
    )


def test_adr_template_pins_related_metadata_provenance():
    text = ADR_TEMPLATE.read_text()
    assert "`related:`" in text and "never" in text, (
        "templates/adr.md must state that provenance (source/derived specs, absorbed "
        "decisions, superseded predecessor) lives in `related:` metadata, never prose."
    )


def test_adr_template_pins_gauntlet_annotation_provenance():
    text = ADR_TEMPLATE.read_text()
    assert "annotation" in text.lower() and "never the body" in text, (
        "templates/adr.md must state that gauntlet review provenance goes to "
        "annotations, never the body."
    )


# ---------------------------------------------------------------------------
# area.md — the quartet, cross-referenced against the librarian's spelling
# ---------------------------------------------------------------------------


def test_area_template_exists():
    assert AREA_TEMPLATE.exists(), f"Expected templates/area.md at {AREA_TEMPLATE}"


def test_area_template_has_exactly_the_quartet_in_order():
    text = AREA_TEMPLATE.read_text()
    assert _h2_headings(text) == _AREA_SECTIONS, (
        f"templates/area.md must carry exactly these H2 sections in order: {_AREA_SECTIONS}, "
        f"got: {_h2_headings(text)}"
    )


def test_area_template_quartet_matches_librarian_spelling():
    """Cross-referenced: the librarian agent asserts this exact quartet is the area
    profile shape. If either file's spelling drifts, this test catches it — not just
    a golden-master pin on one side.
    """
    assert LIBRARIAN_MD.exists(), f"Expected lore's librarian.md at {LIBRARIAN_MD}"
    librarian_text = LIBRARIAN_MD.read_text()
    missing = [s for s in _AREA_SECTIONS if s not in librarian_text]
    assert not missing, (
        f"librarian.md no longer names these area-profile sections: {missing} — "
        "templates/area.md's quartet has drifted from the librarian's spelling."
    )


def test_area_template_preserves_overview_heading_for_one_liner_extraction():
    """The area map's one-liner extraction (`_first_overview_sentence`) reads the
    literal heading `## Overview` — not a variant spelling."""
    text = AREA_TEMPLATE.read_text()
    assert "## Overview" in text, (
        "templates/area.md must keep the exact heading '## Overview' — the area map's "
        "_ONE_LINER_MAX lead-line extraction depends on this literal spelling."
    )


def test_area_template_guides_adr_wikilink_citation():
    text = AREA_TEMPLATE.read_text()
    assert "[[" in text and "ADR" in text, (
        "templates/area.md must guide citing ADRs inline as [[wikilinks]]."
    )
