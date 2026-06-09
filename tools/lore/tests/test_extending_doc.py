"""Content-accuracy guard for docs/EXTENDING.md — the public adopter cookbook.

The cookbook must document the REAL extension-point set, anchored to the actual
shipped artifacts (the `extension point — X` tags in the lore/forge skills and
agents + the forge DEGRADATION.md table), not the spec's aspirational list.

Two failure modes this guard catches:
  1. The guide names an extension point that does NOT exist in any shipped
     skill/agent (an invented point) — e.g. `log_source` or `pr_review_bot`,
     which appear only as descriptive prose in the spec, never as a tagged seam.
  2. The guide OMITS a real, tagged extension point — every `extension point — X`
     tag shipped in lore's skills or forge's skills/agents must be mentioned by
     name in the cookbook.

The set of real extension points is DISCOVERED from the source tree at test time
(not hard-coded), so adding or renaming a seam upstream forces the cookbook to
keep pace. The forge tree is the dev-agent half; lore ships its own seamed
skills (brainstorm/intake). Both are scanned.

`design_mockup`, the `lore-librarian` knowledge-synthesis seam, and other tokens
appearing in this file are STRUCTURAL extension-point identifiers, not private
app tokens — they carry no denylist match and are safe as literals here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
EXTENDING = REPO_ROOT / "docs" / "EXTENDING.md"
README = REPO_ROOT / "README.md"

LORE_SKILLS = REPO_ROOT / "plugins" / "lore" / "skills"


def _find_forge_plugin() -> Path | None:
    """Locate the sibling forge plugin tree without embedding any machine- or
    owner-specific literal (this file is itself scanned by the leak gate).

    Honors $FORGE_PLUGIN_ROOT, else probes the conventional sibling checkout
    under the user's code/ dir. Returns None if forge is not present, so the
    forge-sourced extension points are skipped rather than failing the suite on
    a machine without a forge clone.
    """
    import os

    env = os.environ.get("FORGE_PLUGIN_ROOT")
    if env and Path(env).exists():
        return Path(env)
    for base in (Path.home() / "code", REPO_ROOT.parent.parent.parent):
        candidate = base / "forge" / "plugins" / "forge"
        if candidate.exists():
            return candidate
    return None


FORGE_PLUGIN = _find_forge_plugin()

# Matches `extension point — feature_flags` and `extension point — `feature_flags``
_TAG_RE = re.compile(r"extension point\s*[—-]+\s*`?([a-z_]+)`?", re.IGNORECASE)

# Points named only as descriptive prose upstream (no `extension point — X` tag,
# no visible-skip, no re-add path). The cookbook MUST NOT present these as
# configurable extension points.
NON_EXTENSION_POINTS = ["log_source", "pr_review_bot"]


def _discover_extension_points() -> set[str]:
    points: set[str] = set()
    roots = [LORE_SKILLS]
    if FORGE_PLUGIN is not None:
        roots += [FORGE_PLUGIN / "skills", FORGE_PLUGIN / "agents"]
    for root in roots:
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            for m in _TAG_RE.finditer(md.read_text(encoding="utf-8", errors="replace")):
                points.add(m.group(1).lower())
    return points


REAL_POINTS = sorted(_discover_extension_points())


def test_extending_doc_exists():
    assert EXTENDING.exists(), f"Expected {EXTENDING} to exist"


def test_real_extension_points_discovered():
    """Sanity: the discovery actually found the known-real seams. If this list
    shrinks unexpectedly, the discovery regex broke (not the doc).

    `design_mockup` ships in lore's own skills and is always discoverable. The
    forge-sourced seams are only asserted when a forge checkout is present."""
    assert "design_mockup" in REAL_POINTS, (
        f"discovery did not find 'design_mockup' in lore's skills; "
        f"found: {REAL_POINTS}"
    )
    if FORGE_PLUGIN is not None:
        for expected in ("feature_flags", "observability", "issue_tracker",
                         "build_test_commands"):
            assert expected in REAL_POINTS, (
                f"discovery did not find {expected!r} in the forge tree; "
                f"found: {REAL_POINTS}"
            )


@pytest.mark.parametrize("point", REAL_POINTS)
def test_every_real_extension_point_is_documented(point: str):
    """Every tagged extension point shipped in the tree must be named in the
    cookbook — no real seam silently omitted."""
    assert EXTENDING.exists(), "docs/EXTENDING.md must exist"
    text = EXTENDING.read_text()
    assert point in text, (
        f"docs/EXTENDING.md does not mention the real extension point {point!r}. "
        "Every shipped `extension point — X` seam must appear in the cookbook."
    )


@pytest.mark.parametrize("phantom", NON_EXTENSION_POINTS)
def test_no_invented_extension_points(phantom: str):
    """The cookbook must NOT present spec-aspirational, non-shipped points as
    configurable extension points."""
    if not EXTENDING.exists():
        pytest.skip("doc not yet written")
    text = EXTENDING.read_text()
    assert phantom not in text, (
        f"docs/EXTENDING.md names {phantom!r} as an extension point, but it is "
        "not a shipped seam (no `extension point — X` tag, no re-add path). "
        "Do not invent extension points from the spec's aspirational table."
    )


def test_extending_links_degradation_as_source_of_truth():
    """The cookbook links DEGRADATION.md rather than re-describing it, so it
    cannot go stale against the catalog."""
    text = EXTENDING.read_text()
    assert "DEGRADATION.md" in text, (
        "docs/EXTENDING.md must link the DEGRADATION.md re-add paths as the "
        "source of truth rather than duplicating them."
    )


def test_extending_warns_about_relative_hook_paths():
    """Adopter footgun: a relative hook command path silently exits 0 (the hook
    never fires). The wiring section must warn about it and show the
    CLAUDE_PROJECT_DIR-anchored form."""
    text = EXTENDING.read_text()
    assert "CLAUDE_PROJECT_DIR" in text, (
        "Wiring section must show a $CLAUDE_PROJECT_DIR-anchored hook command path."
    )
    lowered = text.lower()
    assert "relative" in lowered and ("exit 0" in lowered or "exits 0" in lowered), (
        "Wiring section must warn that a relative hook path silently exits 0."
    )


def test_readme_links_extending_doc():
    text = README.read_text()
    assert "EXTENDING.md" in text, (
        "README.md must link to docs/EXTENDING.md (the adopter cookbook)."
    )
