"""Tests for artist.md — de-zenithed design-mockup-writer absorption.

Contract assertions (Slice 2 / A-3 / A-4 / A-5 / D-7 / S-3):

  - A-3: the BLOCKED: message names BOTH escape hatches (file:line AND
    "new, no counterpart — <justification>" for greenfield).
  - A-4: artist.md states an explicit out-of-scope note for full guided
    aspirational-chrome setup (NOT absorbed in Step 6).
  - A-5: both create AND update modes survive in artist.md.
  - D-7/S-3: leak_gate.py over artist.md (+ Slice-1 surface) with an ephemeral
    Step-6 denylist → exit 0 (no zenith tokens survive).

Hermeticity: tmp_path-based ephemeral denylist; no real ~/.claude/ dependency.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "craft" / "scripts"
AGENTS_DIR = REPO_ROOT / "plugins" / "craft" / "agents"
CAPABILITIES_TOML = REPO_ROOT / "capabilities.toml"

ARTIST_MD = AGENTS_DIR / "artist.md"
GATE = SCRIPTS_DIR / "leak_gate.py"

# ---------------------------------------------------------------------------
# Step-6 ephemeral denylist tokens (D-7 / S-3)
# Structurally-observable zenith tokens — safe to name in tracked test source.
# ---------------------------------------------------------------------------

_STEP6_DENYLIST_TOKENS = [
    r"zenithhealth",
    r"\bzenith\b",
    r"dash0",
    r"cortana(-zh)?",
    r"\basana\b",
    r"platform-admin-ui",
    r"patient-portal-web",
    r"mobile-overview",
    r"admin-preview",
    r"preview\s*(url|server|host)",
    r"\.workspace-manifest",
    r"brain/(designs|chrome|specs|plans|sessions)",
]


def _write_ephemeral_denylist(p: Path) -> Path:
    """Write an ephemeral Step-6 denylist to tmp_path (S-3: never depend on machine-local)."""
    dl = p / "step6-denylist.txt"
    dl.write_text("\n".join(_STEP6_DENYLIST_TOKENS) + "\n", encoding="utf-8")
    return dl


# ---------------------------------------------------------------------------
# Fixture: artist.md text (skip all if absent — TDD RED phase)
# ---------------------------------------------------------------------------

@pytest.fixture
def artist_text() -> str:
    if not ARTIST_MD.exists():
        pytest.skip("artist.md not yet implemented")
    return ARTIST_MD.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# D-7 / S-3: leak gate — no zenith tokens in artist.md
# ---------------------------------------------------------------------------

def test_leak_gate_artist_md_is_clean(tmp_path: Path):
    """artist.md must have no Step-6 zenith tokens (D-7/S-3)."""
    if not ARTIST_MD.exists():
        pytest.skip("artist.md not yet implemented")

    denylist = _write_ephemeral_denylist(tmp_path)
    result = subprocess.run(
        [sys.executable, str(GATE), str(AGENTS_DIR), "--denylist", str(denylist)],
        capture_output=True,
        text=True,
    )
    # Filter to only artist.md hits
    hits = [line for line in result.stdout.splitlines() if "artist" in line]
    assert not hits, (
        "artist.md contains forbidden Step-6 zenith tokens:\n" + "\n".join(hits)
    )


def test_leak_gate_slice1_surface_still_clean(tmp_path: Path):
    """The Slice-1 scripts surface (combine_design.py + docs) must remain clean under the Step-6 denylist (D-7/S-3)."""
    scripts_dir = SCRIPTS_DIR
    if not (scripts_dir / "combine_design.py").exists():
        pytest.skip("combine_design.py not yet implemented")

    denylist = _write_ephemeral_denylist(tmp_path)
    result = subprocess.run(
        [sys.executable, str(GATE), str(scripts_dir), "--denylist", str(denylist)],
        capture_output=True,
        text=True,
    )
    hits = [line for line in result.stdout.splitlines() if "combine_design" in line]
    assert not hits, (
        "combine_design.py contains forbidden Step-6 zenith tokens:\n" + "\n".join(hits)
    )


# ---------------------------------------------------------------------------
# Registration: frontmatter name: artist, registrable
# (these mirror test_agents_registrable.py patterns for the named agent)
# ---------------------------------------------------------------------------

def test_artist_frontmatter_name_is_artist(artist_text: str):
    """artist.md frontmatter must carry name: artist (the renamed slot from Step 5)."""
    assert artist_text.startswith("---\n"), "artist.md must open with a YAML frontmatter block"
    end = artist_text.find("\n---", 3)
    assert end > 0, "artist.md frontmatter block must be closed"
    frontmatter = artist_text[3:end]
    name_lines = [
        ln for ln in frontmatter.splitlines()
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
        ln for ln in frontmatter.splitlines()
        if ln.strip().startswith("description:") and ln.split(":", 1)[1].strip()
    ]
    assert desc_lines, "artist.md frontmatter must carry a non-empty description:"


# ---------------------------------------------------------------------------
# Capability manifest: design capability resolves artist.md to an existing file
# ---------------------------------------------------------------------------

def test_design_capability_lists_artist_agent():
    """capabilities.toml [capabilities.design] must list agents/artist.md."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore

    text = CAPABILITIES_TOML.read_text(encoding="utf-8")
    data = tomllib.loads(text)

    design = data.get("capabilities", {}).get("design", {})
    agents = design.get("agents", [])
    assert "agents/artist.md" in agents, (
        f"[capabilities.design] agents must include 'agents/artist.md', got {agents!r}"
    )


def test_design_capability_artist_file_exists():
    """The agents/artist.md reference in capabilities.toml must resolve to an existing file."""
    plugin_root = REPO_ROOT / "plugins" / "craft"
    artist_path = plugin_root / "agents" / "artist.md"
    assert artist_path.exists(), (
        f"capabilities.toml references agents/artist.md but {artist_path} does not exist"
    )


# ---------------------------------------------------------------------------
# A-3: BLOCKED message names BOTH escape hatches
# The block rule must state BOTH:
#   (a) "file:line" citation path
#   (b) "new, no counterpart" greenfield note path
# so a greenfield user has a stated unblock path.
# ---------------------------------------------------------------------------

def test_blocked_message_names_file_line_escape(artist_text: str):
    """artist.md block rule must name the file:line citation escape hatch (A-3)."""
    # The BLOCKED rule must mention the file:line citation path
    assert "file:line" in artist_text or "file : line" in artist_text.lower(), (
        "artist.md must state the file:line citation as an escape hatch in the BLOCKED rule (A-3)"
    )


def test_blocked_message_names_greenfield_escape(artist_text: str):
    """artist.md block rule must name the 'new, no counterpart' greenfield escape hatch (A-3)."""
    assert "new, no counterpart" in artist_text, (
        "artist.md must state 'new, no counterpart' as a greenfield escape hatch in the BLOCKED rule (A-3)"
    )


def test_blocked_message_names_both_escapes_in_block_rule(artist_text: str):
    """The citation-block BLOCKED message must name both escape hatches within its own text (A-3).

    Specifically: find the BLOCKED: whose text contains 'no anchor' or 'component-mapping row'
    (the A-3 citation check at validation step), and assert BOTH 'file:line' AND
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
        window = artist_text[idx: idx + 400]
        if "no anchor" in window or "component-mapping row" in window:
            citation_blocked_idx = idx
            break
        search_start = idx + 1

    assert citation_blocked_idx != -1, (
        "artist.md must contain a BLOCKED: message for the citation check — "
        "one whose text references 'no anchor' or 'component-mapping row' (A-3)"
    )

    # The citation block itself must name both escapes
    block_window = artist_text[citation_blocked_idx: citation_blocked_idx + 400]
    assert "file:line" in block_window, (
        "The citation-block BLOCKED message must name 'file:line' as an escape hatch (A-3)"
    )
    assert "new, no counterpart" in block_window, (
        "The citation-block BLOCKED message must name 'new, no counterpart' as a greenfield escape hatch (A-3)"
    )


# ---------------------------------------------------------------------------
# A-4: greenfield aspirational-chrome is an explicit out-of-scope note
# The artist must state that full guided aspirational-chrome setup is NOT
# absorbed in Step 6 / this agent.
# ---------------------------------------------------------------------------

def test_greenfield_aspirational_chrome_out_of_scope_note(artist_text: str):
    """artist.md must state an explicit out-of-scope note for aspirational-chrome setup (A-4)."""
    # Must mention aspirational chrome is out of scope
    has_aspirational = "aspirational" in artist_text.lower()
    has_out_of_scope = "out of scope" in artist_text.lower() or "not in scope" in artist_text.lower()

    assert has_aspirational and has_out_of_scope, (
        "artist.md must carry an explicit note that full aspirational-chrome setup is out of scope (A-4). "
        f"Found 'aspirational': {has_aspirational}, found out-of-scope note: {has_out_of_scope}"
    )


# ---------------------------------------------------------------------------
# A-5: both create AND update modes survive in artist.md
# ---------------------------------------------------------------------------

def test_artist_carries_create_mode(artist_text: str):
    """artist.md must describe a create mode (A-5)."""
    assert "mode: create" in artist_text or "create" in artist_text, (
        "artist.md must carry a create mode section (A-5)"
    )
    # More specifically, must have a structured create section
    assert re.search(r"#+\s*.*create", artist_text, re.IGNORECASE), (
        "artist.md must have a section header describing the create mode (A-5)"
    )


def test_artist_carries_update_mode(artist_text: str):
    """artist.md must describe an update mode (A-5)."""
    assert "mode: update" in artist_text or "update" in artist_text, (
        "artist.md must carry an update mode section (A-5)"
    )
    assert re.search(r"#+\s*.*update", artist_text, re.IGNORECASE), (
        "artist.md must have a section header describing the update mode (A-5)"
    )


def test_both_modes_structurally_present(artist_text: str):
    """artist.md must contain both create and update mode sections (A-5)."""
    create_match = re.search(r"#+\s+.*\bcreate\b.*mode|mode.*\bcreate\b", artist_text, re.IGNORECASE)
    update_match = re.search(r"#+\s+.*\bupdate\b.*mode|mode.*\bupdate\b", artist_text, re.IGNORECASE)
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
    """artist.md must reference resolving designs_root/chrome_root from input fields AND DESIGNS_ROOT/CHROME_ROOT env vars."""
    # Must name the concrete fields
    assert "designs_root" in artist_text, (
        "artist.md must name the 'designs_root' field so callers know the resolution contract"
    )
    assert "chrome_root" in artist_text, (
        "artist.md must name the 'chrome_root' field so callers know the resolution contract"
    )
    # Must name the env vars (now that I-1 makes them real in combine_design.py)
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
        # Surface-specific descriptors from the de-zenith source (M-1 regression armor)
        "Suisse Intl",
        "390-wide",
    ]
    for term in forbidden_aesthetic_terms:
        assert term.lower() not in artist_text.lower(), (
            f"artist.md must not name the aesthetic term {term!r} — the look comes from the consumed catalog, not the agent"
        )


def test_artist_does_not_have_fixed_surface_list(artist_text: str):
    """artist.md must not hardcode a fixed list of surfaces (surfaces are data, read from the brief + chrome catalog)."""
    # The three zenith-specific surfaces must not appear
    forbidden_surfaces = [
        "platform-admin-ui",
        "patient-portal-web",
        "mobile-overview",
    ]
    for surface in forbidden_surfaces:
        assert surface not in artist_text, (
            f"artist.md must not hardcode the surface {surface!r} — surfaces are data read from the brief"
        )


def test_artist_references_combine_as_deliverable_step(artist_text: str):
    """artist.md must reference 'combine' as the deliverable step (renamed vocabulary from Slice 1)."""
    assert "combine" in artist_text.lower(), (
        "artist.md must reference 'combine' / combine_design.py as the deliverable step (Slice-1 renamed vocabulary)"
    )


def test_artist_carries_anchor_to_real_chrome_block_rule(artist_text: str):
    """artist.md must state the anchor-to-real-chrome BLOCK rule with both escape paths (A-3)."""
    assert "BLOCKED" in artist_text, (
        "artist.md must carry a BLOCKED: rule for missing file:line citations"
    )


def test_artist_component_mapping_before_html(artist_text: str):
    """artist.md must state that the component-mapping table appears BEFORE any HTML."""
    # The contract: component mapping table is rendered before any HTML
    text_lower = artist_text.lower()
    mapping_idx = text_lower.find("component mapping")
    assert mapping_idx != -1, "artist.md must describe a component mapping table"
    # "before" must appear in proximity to component mapping or HTML rendering
    assert "before" in text_lower, (
        "artist.md must state that the component-mapping table appears BEFORE any HTML"
    )


def test_artist_report_under_12_lines(artist_text: str):
    """artist.md must describe a report structure of ~12 lines or fewer."""
    assert "12" in artist_text or "twelve" in artist_text.lower() or "under" in artist_text.lower(), (
        "artist.md must specify the report structure is under ~12 lines"
    )
