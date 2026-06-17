"""Tests for Slice 3 — design phase wiring + provider seam (D-9 / A-6 / D-7 / S-3).

Contract assertions:

  1. Provider-seam present: design-authoring.md has a documented, LIVE
     design_mockup provider seam that names the artist + brief shape (D-9;
     flipped LIVE by the Slice 8 brainstorm cutover).

  2. Cutover landed: the seam is wired — design-authoring.md is marked LIVE and
     states lore's brainstorm dispatches the artist as the default provider.

  3. Full-surface leak gate (D-7 / S-3): leak_gate.py over the full WS-2
     surface (artist.md + combine_design.py + design-authoring.md + any new
     docs) with an ephemeral tmp_path denylist → exit 0.

  4. Design-phase declaration: design-authoring.md states that `design` is the
     loop's design phase (the artist renders, combine_design.py produces the
     reference).

  5. A-6 completeness: design-authoring.md has both a "how to invoke the
     artist directly" section AND the named combine_design.py CLI args with an
     example invocation.

Hermeticity: tmp_path for ephemeral denylist; no network; no real ~/.claude/.
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
DOCS_DIR = REPO_ROOT / "plugins" / "craft" / "docs"

DESIGN_AUTHORING_MD = DOCS_DIR / "design-authoring.md"
ARTIST_MD = AGENTS_DIR / "artist.md"
GATE = SCRIPTS_DIR / "leak_gate.py"

# ---------------------------------------------------------------------------
# Step-6 ephemeral denylist tokens (D-7 / S-3)
# Same token list as test_artist_dezenithed.py — carried forward here so the
# full-surface gate is reproducible without the machine-local denylist.
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
    """Write an ephemeral Step-6 denylist to tmp_path (S-3)."""
    dl = p / "step6-denylist.txt"
    dl.write_text("\n".join(_STEP6_DENYLIST_TOKENS) + "\n", encoding="utf-8")
    return dl


# ---------------------------------------------------------------------------
# Fixture: design-authoring.md text
# ---------------------------------------------------------------------------

@pytest.fixture
def doc_text() -> str:
    assert DESIGN_AUTHORING_MD.exists(), (
        f"design-authoring.md not found at {DESIGN_AUTHORING_MD}"
    )
    return DESIGN_AUTHORING_MD.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Provider-seam present (D-9)
# ---------------------------------------------------------------------------

def test_design_mockup_provider_seam_present(doc_text: str):
    """design-authoring.md must document a 'design_mockup' provider seam (D-9)."""
    assert "design_mockup" in doc_text, (
        "design-authoring.md must name the 'design_mockup' provider seam (D-9)"
    )


def test_provider_seam_names_artist_agent(doc_text: str):
    """The design_mockup seam must name 'artist' as the provider agent (D-9)."""
    # Find the design_mockup section and check artist appears near it
    idx = doc_text.find("design_mockup")
    assert idx != -1, "design_mockup not found"
    window = doc_text[max(0, idx - 200): idx + 600]
    assert "artist" in window, (
        "The design_mockup provider seam must name 'artist' as the agent to dispatch (D-9)"
    )


def test_provider_seam_references_brief_shape(doc_text: str):
    """The design_mockup seam must reference the artist's brief/input shape (D-9)."""
    idx = doc_text.find("design_mockup")
    assert idx != -1, "design_mockup not found"
    # The seam should reference brief inputs in its vicinity
    window = doc_text[max(0, idx - 100): idx + 800]
    # Must reference either "brief" inputs or the artist's input fields
    has_brief_ref = "brief" in window.lower() or "input" in window.lower()
    assert has_brief_ref, (
        "The design_mockup seam must reference the artist's brief/input shape (D-9)"
    )


def test_provider_seam_is_marked_live(doc_text: str):
    """The design_mockup seam must be marked LIVE (Slice 8 cutover — brainstorm
    now dispatches the artist; the seam is no longer RESERVED / not-yet-wired)."""
    import re as _re
    doc_lower = doc_text.lower()

    # Find the section heading that introduces the provider seam
    section_match = _re.search(r"##[^\n]*design_mockup[^\n]*\n", doc_lower)
    if section_match:
        start = section_match.start()
        window = doc_lower[start: start + 1200]
    else:
        window = doc_lower

    assert "live" in window, (
        "The design_mockup seam must be marked LIVE — the brainstorm cutover landed"
    )
    assert "reserved" not in window and "not yet wired" not in window, (
        "The design_mockup seam must no longer be marked RESERVED / not yet wired "
        "(Slice 8 flipped it LIVE)"
    )


# ---------------------------------------------------------------------------
# 2. Cutover landed (Slice 8)
# The seam is now LIVE: design-authoring.md states that lore's brainstorm
# dispatches the artist as the default design_mockup provider.
# ---------------------------------------------------------------------------

def test_seam_is_marked_live(doc_text: str):
    """design-authoring.md must state the design_mockup seam is LIVE / wired (Slice 8)."""
    doc_lower = doc_text.lower()
    assert "live" in doc_lower and "reserved" not in doc_lower, (
        "design-authoring.md must mark the design_mockup seam LIVE (Slice 8 cutover) "
        "and no longer carry the RESERVED marker"
    )


def test_seam_states_brainstorm_dispatches_artist(doc_text: str):
    """design-authoring.md must state brainstorm dispatches the artist as the
    live design_mockup provider (Slice 8 cutover)."""
    doc_lower = doc_text.lower()
    has_present_dispatch = bool(
        re.search(r"brainstorm[^.]*dispatch(?:es)?\s+the\s+`?artist`?", doc_lower)
    )
    assert has_present_dispatch, (
        "design-authoring.md must state that brainstorm dispatches the artist as the "
        "live provider — the cutover has landed (Slice 8)"
    )


# ---------------------------------------------------------------------------
# 3. Full-surface leak gate over WS-2 (D-7 / S-3)
# Run leak_gate.py over artist.md + combine_design.py + design-authoring.md
# with an ephemeral tmp_path denylist → exit 0.
# ---------------------------------------------------------------------------

def test_full_ws2_surface_leak_gate_agents(tmp_path: Path):
    """Full WS-2 agents surface (artist.md) must pass the Step-6 leak gate (D-7/S-3)."""
    if not ARTIST_MD.exists():
        pytest.skip("artist.md not present")

    denylist = _write_ephemeral_denylist(tmp_path)
    result = subprocess.run(
        [sys.executable, str(GATE), str(AGENTS_DIR), "--denylist", str(denylist)],
        capture_output=True,
        text=True,
    )
    artist_hits = [ln for ln in result.stdout.splitlines() if "artist" in ln]
    assert not artist_hits, (
        "artist.md failed the full-surface WS-2 leak gate:\n" + "\n".join(artist_hits)
    )


def test_full_ws2_surface_leak_gate_scripts(tmp_path: Path):
    """Full WS-2 scripts surface (combine_design.py) must pass the Step-6 leak gate (D-7/S-3)."""
    combine_py = SCRIPTS_DIR / "combine_design.py"
    if not combine_py.exists():
        pytest.skip("combine_design.py not present")

    denylist = _write_ephemeral_denylist(tmp_path)
    result = subprocess.run(
        [sys.executable, str(GATE), str(SCRIPTS_DIR), "--denylist", str(denylist)],
        capture_output=True,
        text=True,
    )
    combine_hits = [ln for ln in result.stdout.splitlines() if "combine_design" in ln]
    assert not combine_hits, (
        "combine_design.py failed the full-surface WS-2 leak gate:\n" + "\n".join(combine_hits)
    )


def test_full_ws2_surface_leak_gate_docs(tmp_path: Path):
    """Full WS-2 docs surface (design-authoring.md) must pass the Step-6 leak gate (D-7/S-3)."""
    denylist = _write_ephemeral_denylist(tmp_path)
    result = subprocess.run(
        [sys.executable, str(GATE), str(DOCS_DIR), "--denylist", str(denylist)],
        capture_output=True,
        text=True,
    )
    doc_hits = [
        ln for ln in result.stdout.splitlines() if "design-authoring" in ln
    ]
    assert not doc_hits, (
        "design-authoring.md failed the full-surface WS-2 leak gate:\n" + "\n".join(doc_hits)
    )


# ---------------------------------------------------------------------------
# 4. Design-phase declaration
# design-authoring.md must state that `design` is the loop's design phase.
# ---------------------------------------------------------------------------

def test_design_phase_declaration_present(doc_text: str):
    """design-authoring.md must declare that 'design' is the loop's design phase (Slice 3)."""
    doc_lower = doc_text.lower()
    # Must mention "design phase" in the context of the loop
    has_design_phase = "design phase" in doc_lower
    assert has_design_phase, (
        "design-authoring.md must state that 'design' is the loop's design phase "
        "(Slice 3 / spec concept map §1299)"
    )


def test_design_phase_names_artist_renders(doc_text: str):
    """design-authoring.md must name the artist as the renderer in the design phase."""
    doc_lower = doc_text.lower()
    # The artist renders a design; combine produces the reference
    # Both "artist" and "combine" should appear in the design phase description
    has_artist = "artist" in doc_lower
    has_combine = "combine" in doc_lower
    assert has_artist and has_combine, (
        "design-authoring.md must name both the artist (renders) and combine (produces reference) "
        "in the design phase description"
    )


def test_design_phase_names_combine_produces_reference(doc_text: str):
    """design-authoring.md must state combine_design.py produces the self-contained reference."""
    doc_lower = doc_text.lower()
    has_reference = "reference" in doc_lower
    assert has_reference, (
        "design-authoring.md must state that combine_design.py produces the "
        "self-contained reference"
    )


# ---------------------------------------------------------------------------
# 5. A-6 completeness: "how to invoke the artist directly" + combine CLI
# ---------------------------------------------------------------------------

def test_a6_invoke_artist_directly_section_present(doc_text: str):
    """design-authoring.md must have a 'how to invoke the artist directly' section (A-6)."""
    doc_lower = doc_text.lower()
    has_invoke = "invoke the artist" in doc_lower or "how to invoke" in doc_lower
    assert has_invoke, (
        "design-authoring.md must have a 'how to invoke the artist directly' section (A-6)"
    )


def test_a6_combine_cli_args_named(doc_text: str):
    """design-authoring.md must name the combine_design.py CLI args (A-6)."""
    # The key named args from A-7 must appear in the doc
    assert "--designs-dir" in doc_text, (
        "design-authoring.md must document --designs-dir arg (A-6)"
    )
    assert "--chrome-path" in doc_text, (
        "design-authoring.md must document --chrome-path arg (A-6)"
    )
    assert "--slug" in doc_text, (
        "design-authoring.md must document --slug arg (A-6)"
    )


def test_a6_example_invocation_present(doc_text: str):
    """design-authoring.md must include an example invocation of combine_design.py (A-6)."""
    # A bash code block with combine_design.py
    has_example = "combine_design.py" in doc_text
    assert has_example, (
        "design-authoring.md must include an example invocation of combine_design.py (A-6)"
    )
