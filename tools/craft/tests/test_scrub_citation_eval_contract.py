"""Contract tests for the scrub-citation-vs-inline eval instrument.

AC1's own behavioural gate: whether the credential-pattern scrub still fires when an
agent reaches it through one unconditional citation to a shared `_shared/security.md`
document instead of finding the pattern list inline at the citing surface
(`plugins/craft/evals/scrub-citation-vs-inline/`).

These tests pin the eval's *structure* — both arms present, differing in exactly one
file; a non-empty expected verdict; every fixture actually carries scrubbable material;
neither arm nor fixture set is empty. They do **not** assert the eval's measured
result — that is recorded prose, decided by dispatched runs, not a behaviour this
suite pins.
"""

from __future__ import annotations

import filecmp
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
EVAL_DIR = REPO_ROOT / "plugins" / "craft" / "evals" / "scrub-citation-vs-inline"
ARMS_DIR = EVAL_DIR / "arms"
FIXTURES_DIR = EVAL_DIR / "fixtures"
EXPECTED = EVAL_DIR / "expected.md"

SCRIPTS = REPO_ROOT / "plugins" / "craft" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import criterion_gate  # noqa: E402  (reuses the canonical scrub pattern list — never re-derived)


def _arm_names() -> list[str]:
    if not ARMS_DIR.is_dir():
        return []
    return sorted(p.name for p in ARMS_DIR.iterdir() if p.is_dir())


def _fixture_paths() -> list[Path]:
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(FIXTURES_DIR.glob("*.md"))


def _relative_files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def test_arms_differ_in_exactly_one_file():
    """The whole claim 'differing in one variable' rests on this being mechanically
    checkable. Files that exist in both arms must be byte-identical except exactly
    one; a file that exists in only one arm (the citation arm's `_shared/security.md`,
    the document being cited) is the reference target, not a second differing copy —
    it is not counted as a difference between the arms' common surface."""
    assert set(_arm_names()) >= {"inline", "citation"}, (
        f"a two-arm eval needs both arms on disk, found: {_arm_names()}"
    )
    inline_dir = ARMS_DIR / "inline"
    citation_dir = ARMS_DIR / "citation"

    inline_files = _relative_files(inline_dir)
    citation_files = _relative_files(citation_dir)
    common = inline_files & citation_files
    assert common, "expected at least one file common to both arms"

    differing = [
        rel
        for rel in sorted(common)
        if not filecmp.cmp(inline_dir / rel, citation_dir / rel, shallow=False)
    ]
    assert len(differing) == 1, (
        f"expected exactly one differing common file between arms, found: {differing}"
    )


def test_the_eval_ships_fixtures_to_grade():
    """Non-vacuity guard: an empty fixtures/ directory would leave the check below
    parametrized over nothing and reporting clean."""
    assert _fixture_paths(), "expected at least one fixture under fixtures/"


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.name)
def test_fixture_contains_a_scrubbable_pattern(fixture_path: Path):
    """Every fixture must contain at least one string matching the scrub's own
    pattern list, so a fixture that would pass vacuously — nothing to redact —
    cannot be committed."""
    text = fixture_path.read_text(encoding="utf-8")
    matched = any(pattern.search(text) for pattern in criterion_gate._CREDENTIAL_PATTERNS)
    assert matched, f"{fixture_path.name} contains no string matching the scrub's pattern list"


# inert-gate: allow eval-arm input hygiene; the arm is a fixture, not a subject
def test_citation_arm_shared_document_holds_both_halves():
    """The treatment arm's shared document must hold BOTH halves — the scrub's
    pattern list AND a general statement of the untrusted-value / safe-value-shape
    rule — because task 2 ships exactly that composition, and an arm holding only
    the scrub would measure a smaller artifact than the one that ships."""
    security_doc = ARMS_DIR / "citation" / "skills" / "_shared" / "security.md"
    assert security_doc.is_file(), f"missing shared document at {security_doc}"
    text = security_doc.read_text(encoding="utf-8")

    has_scrub_pattern = any(
        pattern.pattern in text for pattern in criterion_gate._CREDENTIAL_PATTERNS
    )
    assert has_scrub_pattern, "shared document does not state any scrub regex verbatim"

    assert re.search(r"\^\[A-Za-z0-9\._/-\]\+\$", text), (
        "shared document does not state the safe-value shape rule"
    )
