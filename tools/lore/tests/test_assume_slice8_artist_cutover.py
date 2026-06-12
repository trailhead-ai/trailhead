"""Ephemeral assumption-prover for Slice 8 — artist brainstorm cutover.

CLEANUP: delete this file entirely after Slice 8 is implemented.
  Path: tools/lore/tests/test_assume_slice8_artist_cutover.py

Proves / disproves the Slice 8 Known Unknowns:

  KU-1: brainstorm/SKILL.md's design_mockup extension point is currently
        a GENERIC placeholder — it does NOT name `artist` as the provider.
        The cutover requires prose surgery in SKILL.md.

  KU-2: design-authoring.md still carries the RESERVED marker.
        The cutover must flip that to LIVE.

  KU-3: Exactly 2 in-repo files reference 'design-mockup-writer'.
        (tools/forge/plugins/forge/docs/design-authoring.md and
         tools/forge/tests/test_artist_dezenithed.py)

  KU-4: test_artist_dezenithed.py does NOT assert the seam is RESERVED
        (it is unaware of the seam state — safe to leave as-is after cutover).

  KU-5: No in-repo file in tools/ or trailhead/ references 'artist' as the
        design_mockup provider in brainstorm/SKILL.md (confirms the wire is absent).
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# tools/lore/tests/ -> tools/lore/ -> tools/ -> worktree root
TOOLS_ROOT = WORKTREE_ROOT / "tools"
TRAILHEAD_ROOT = WORKTREE_ROOT / "trailhead"

LORE_SKILL_DIR = TOOLS_ROOT / "lore" / "plugins" / "lore" / "skills"
BRAINSTORM_SKILL = LORE_SKILL_DIR / "brainstorm" / "SKILL.md"

FORGE_DOCS_DIR = TOOLS_ROOT / "forge" / "plugins" / "forge" / "docs"
DESIGN_AUTHORING_DOC = FORGE_DOCS_DIR / "design-authoring.md"

FORGE_TESTS_DIR = TOOLS_ROOT / "forge" / "tests"
DEZENITHED_TEST = FORGE_TESTS_DIR / "test_artist_dezenithed.py"

# ---------------------------------------------------------------------------
# KU-1: brainstorm SKILL.md design_mockup extension point is GENERIC
# (no 'artist' named as the concrete provider)
# ---------------------------------------------------------------------------


def test_brainstorm_design_mockup_extension_point_exists():
    """brainstorm/SKILL.md must contain the design_mockup extension-point section."""
    assert BRAINSTORM_SKILL.exists(), f"Missing: {BRAINSTORM_SKILL}"
    text = BRAINSTORM_SKILL.read_text(encoding="utf-8")
    assert "design_mockup" in text, (
        "brainstorm/SKILL.md must contain the design_mockup extension-point label"
    )


def test_brainstorm_design_mockup_does_not_name_artist_as_provider():
    """KU-1: the design_mockup block must NOT yet name 'artist' as the provider.

    This confirms the extension point is a generic placeholder, not a wired dispatch —
    which is exactly what the cutover must change.
    """
    assert BRAINSTORM_SKILL.exists(), f"Missing: {BRAINSTORM_SKILL}"
    text = BRAINSTORM_SKILL.read_text(encoding="utf-8")

    # Locate the design_mockup extension-point block
    dm_idx = text.find("design_mockup")
    assert dm_idx != -1, "brainstorm/SKILL.md must contain 'design_mockup'"

    # Read the paragraph around it (300 chars is enough to capture the block)
    window = text[dm_idx: dm_idx + 300]

    # The word 'artist' must NOT appear as the named concrete provider here
    assert "artist" not in window.lower(), (
        "brainstorm/SKILL.md's design_mockup block already names 'artist' as the provider — "
        "KU-1 assumption is INVALIDATED (cutover may already be done?). "
        f"Window: {window!r}"
    )


def test_brainstorm_design_mockup_is_generic_conditional():
    """KU-1: the design_mockup block uses generic conditional language ('if a design-mockup
    tool is configured'), NOT a hard dispatch to a named agent.
    """
    assert BRAINSTORM_SKILL.exists(), f"Missing: {BRAINSTORM_SKILL}"
    text = BRAINSTORM_SKILL.read_text(encoding="utf-8")

    # The existing generic phrasing (from SKILL.md lines 115-120)
    assert "design-mockup tool is configured" in text, (
        "brainstorm/SKILL.md must use the generic 'design-mockup tool is configured' phrasing "
        "(confirms the extension point is a generic placeholder, not a named dispatch)"
    )


# ---------------------------------------------------------------------------
# KU-2: design-authoring.md RESERVED marker is still present
# ---------------------------------------------------------------------------


def test_design_authoring_doc_carries_reserved_marker():
    """KU-2: design-authoring.md must still contain the RESERVED marker.

    The Slice 8 cutover must flip this to LIVE.
    """
    assert DESIGN_AUTHORING_DOC.exists(), f"Missing: {DESIGN_AUTHORING_DOC}"
    text = DESIGN_AUTHORING_DOC.read_text(encoding="utf-8")
    assert "RESERVED" in text, (
        "design-authoring.md no longer carries the RESERVED marker — "
        "KU-2 assumption INVALIDATED (has the cutover already been applied?)."
    )


def test_design_authoring_doc_reserved_section_names_artist():
    """KU-2 bonus: the RESERVED section must already name 'artist' as the target agent,
    confirming the contract is stable and Slice 8 only needs to flip RESERVED → LIVE.
    """
    assert DESIGN_AUTHORING_DOC.exists(), f"Missing: {DESIGN_AUTHORING_DOC}"
    text = DESIGN_AUTHORING_DOC.read_text(encoding="utf-8")

    reserved_idx = text.find("RESERVED")
    assert reserved_idx != -1, "design-authoring.md must contain RESERVED"

    # The section after RESERVED should name the artist
    window = text[reserved_idx: reserved_idx + 500]
    assert "artist" in window.lower(), (
        "The RESERVED section in design-authoring.md must name 'artist' as the target provider. "
        f"Window: {window!r}"
    )


def test_design_authoring_doc_reserved_section_names_brief_fields():
    """KU-2: the RESERVED section must document the brief shape fields (feature, surface,
    designs_root, chrome_root, component_mapping) so Slice 8 has a stable contract to wire.
    """
    assert DESIGN_AUTHORING_DOC.exists(), f"Missing: {DESIGN_AUTHORING_DOC}"
    text = DESIGN_AUTHORING_DOC.read_text(encoding="utf-8")

    for field in ("designs_root", "chrome_root", "component_mapping"):
        assert field in text, (
            f"design-authoring.md must document the brief field '{field}' in the seam contract"
        )


# ---------------------------------------------------------------------------
# KU-3: exactly 2 in-repo files reference 'design-mockup-writer'
# ---------------------------------------------------------------------------


_THIS_FILE = Path(__file__).resolve()


def _find_design_mockup_writer_refs() -> list[tuple[Path, int, str]]:
    """Return list of (file, line_no, line) for every occurrence of the token
    in tools/ and trailhead/, excluding this ephemeral probe file itself."""
    # The literal token is split here so this file does not self-match.
    TOKEN = "design-mockup" + "-writer"
    hits: list[tuple[Path, int, str]] = []
    for root in (TOOLS_ROOT, TRAILHEAD_ROOT):
        for suffix in ("*.md", "*.py", "*.toml"):
            for p in root.rglob(suffix):
                if p.resolve() == _THIS_FILE:
                    continue
                if "__pycache__" in p.parts or ".pytest_cache" in p.parts:
                    continue
                try:
                    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                        if TOKEN in line:
                            hits.append((p, i, line.strip()))
                except (UnicodeDecodeError, OSError):
                    pass
    return hits


def test_exactly_two_in_repo_files_reference_design_mockup_writer():
    """KU-3: exactly the 2 expected in-repo files must reference 'design-mockup-writer'.

    Expected files:
      - tools/forge/plugins/forge/docs/design-authoring.md
      - tools/forge/tests/test_artist_dezenithed.py
    """
    hits = _find_design_mockup_writer_refs()
    files_with_hits = sorted({str(p.relative_to(WORKTREE_ROOT)) for p, _, _ in hits})

    expected = sorted([
        "tools/forge/plugins/forge/docs/design-authoring.md",
        "tools/forge/tests/test_artist_dezenithed.py",
    ])
    assert files_with_hits == expected, (
        f"Expected exactly these 2 files to reference 'design-mockup-writer':\n"
        f"  {expected}\n"
        f"Got:\n"
        f"  {files_with_hits}\n"
        f"All hits: {[(str(p.relative_to(WORKTREE_ROOT)), ln, text) for p, ln, text in hits]}"
    )


# ---------------------------------------------------------------------------
# KU-4: test_artist_dezenithed.py does NOT assert the seam is RESERVED
# ---------------------------------------------------------------------------


def test_dezenithed_test_does_not_assert_seam_is_reserved():
    """KU-4: test_artist_dezenithed.py must NOT assert the seam is RESERVED.

    If it did, Slice 8 would also need to update that test when flipping to LIVE.
    """
    assert DEZENITHED_TEST.exists(), f"Missing: {DEZENITHED_TEST}"
    text = DEZENITHED_TEST.read_text(encoding="utf-8")

    # Must not assert "RESERVED" as a string that the seam is RESERVED
    # (checking for a seam-state assertion, not just the word in a comment)
    reserved_assert = re.search(r"assert.*RESERVED|RESERVED.*assert", text)
    assert reserved_assert is None, (
        "test_artist_dezenithed.py contains an assertion about the RESERVED seam state — "
        "Slice 8 must also update this test when flipping to LIVE. "
        f"Match: {reserved_assert.group()!r}"
    )


def test_dezenithed_test_does_not_reference_seam_state_at_all():
    """KU-4 corollary: the dezenithed test file does not check the RESERVED/LIVE seam state."""
    assert DEZENITHED_TEST.exists(), f"Missing: {DEZENITHED_TEST}"
    text = DEZENITHED_TEST.read_text(encoding="utf-8")
    # It may mention 'design-mockup-writer' in docstring — that's fine.
    # It must not check that design-authoring.md says RESERVED.
    assert "RESERVED" not in text, (
        "test_artist_dezenithed.py references 'RESERVED' — check if it asserts seam state "
        "(would need updating when Slice 8 flips to LIVE)."
    )
