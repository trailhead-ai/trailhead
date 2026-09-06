"""The security-citation scans' corpus is narrower than the surface that can
carry these citations — this suite makes that a checked property, not an
unstated assumption.

`test_security_attribution_citations.py`, `test_security_citation_migration.py`,
and `test_security_point_of_use_citations.py` all derive their site set from
`_skill_md_files()` — `plugins/craft/skills/*/SKILL.md` only. Nothing in that
corpus has ever looked at `_shared/*.md`, `agents/*.md`, or
`skills/*/references/` for the same citation shapes. That gap is exactly why
`_shared/refine.md`'s reachability regression (see `test_skill_reference_sets.py`,
`test_refine_skill_names_security_md_in_its_unconditional_read_directive`) went
unnoticed by a fully green suite: none of those three scans could have seen it
even in principle, since a `_shared` document is not `skills/*/SKILL.md`.

`_shared/*.md` is not excluded on faith, though. `test_reference_depth_gate.py`
and `test_shared_docs_reference_depth_contract.py` already prove, independently
and continuously, that no `_shared` document can name a sibling `_shared`
document's filename (`security.md` included) at all — so no citation of the
point-of-use or attribution shape the three scans above look for could ever
appear inside one. `test_shared_docs_stay_provably_incapable_of_naming_a_sibling`
below re-runs that same gate here, against the live tree, so a reader does not
have to trust this docstring's claim on faith or chase it through a different
test file to see it demonstrated.

`agents/*.md` and `skills/*/references/` carry no such gate — nothing stops a
citation from landing there. This suite scans them directly for the same
markers the three corpus-scanning suites use, so a citation that ever moves
into either surface is caught here rather than staying invisible to every
scan, which is the outcome this suite exists to rule out.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# The same unit splitter the corpus-scanning suites share, so this suite reads
# out of the same segmentation of the prose rather than a second hand-rolled
# one that could drift.
from test_security_attribution_citations import _units

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "craft"
AGENTS = PLUGIN_ROOT / "agents"
SKILLS = PLUGIN_ROOT / "skills"
SHARED = SKILLS / "_shared"
REFERENCE_DEPTH_GATE = PLUGIN_ROOT / "scripts" / "reference_depth_gate.py"

# The same point-of-use dispatch marker `test_security_point_of_use_citations.py`
# uses, and the same "names security.md alongside the scrub" attribution shape
# `test_security_attribution_citations.py` and `test_security_point_of_use_citations.py`
# both look for — reused here rather than redefined, so a narrowing of either
# marker there would be visible here too.
_SCRUB_PROCEDURE_MARKER = "credential-pattern scrub"


def _excluded_surface_files() -> list[Path]:
    """`agents/*.md` and every `skills/*/references/**/*.md` — the surfaces a
    security citation could land on that no corpus-scanning suite reads.
    `_shared/*.md` is deliberately not included here: it is covered by
    `test_shared_docs_stay_provably_incapable_of_naming_a_sibling` below
    instead, on the strength of a different, gate-backed guarantee."""
    return sorted(AGENTS.glob("*.md")) + sorted(SKILLS.glob("*/references/**/*.md"))


def test_excluded_surface_probe_has_a_real_directory_to_scan():
    """Non-vacuity guard: `agents/` must actually exist and hold files, or the
    scan below would report clean while checking nothing."""
    assert list(AGENTS.glob("*.md")), f"no agent doc found under {AGENTS}"


def test_no_security_citation_hides_outside_the_scanned_corpus():
    """The excluded surfaces carry no unit naming `security.md` alongside the
    credential-pattern scrub — the same marker the corpus-scanning suites
    treat as a citation. A hit here means a citation exists somewhere none of
    those suites can see it, and the scan corpus must widen to cover it."""
    hits: list[str] = []
    for path in _excluded_surface_files():
        text = path.read_text(encoding="utf-8")
        for unit in _units(text):
            if "security.md" in unit and _SCRUB_PROCEDURE_MARKER in unit.lower():
                hits.append(str(path.relative_to(PLUGIN_ROOT)))
                break
    assert not hits, (
        "found a security-citation marker outside skills/*/SKILL.md, where none of "
        "test_security_attribution_citations.py, test_security_citation_migration.py, "
        f"or test_security_point_of_use_citations.py can see it: {hits} — widen those "
        "suites' scan corpus to cover this surface"
    )


def test_shared_docs_stay_provably_incapable_of_naming_a_sibling():
    """`_shared/*.md` is excluded from `_excluded_surface_files()` above on a
    different guarantee than "nothing scans it": `reference_depth_gate.py`
    already proves, over every `_shared/*.md` file, that no sibling `_shared`
    document's filename — `security.md` included — survives inside it. This
    pins that the proof this exclusion leans on actually runs clean right
    now, rather than trusting the module docstring's claim on faith."""
    shared_files = sorted(SHARED.glob("*.md"))
    assert shared_files, f"no _shared document found under {SHARED}"
    result = subprocess.run(
        [sys.executable, str(REFERENCE_DEPTH_GATE), *(str(p) for p in shared_files)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "a _shared document names a sibling _shared document by filename — the "
        f"guarantee this suite's _shared/*.md exclusion relies on no longer holds:\n"
        f"{result.stderr}"
    )
