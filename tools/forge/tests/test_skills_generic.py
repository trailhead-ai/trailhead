"""Every shipped forge skill must be generic — zero brain-vault structural
strings and zero app-flavored seam tokens.

This test enforces the mechanical definition of "generic" via two forbidden
bands, parametrized over skills discovered in plugins/forge/skills/*/SKILL.md.

## Band 1: Structural brain seams (literal strings — never denylisted)
These strings definitionally belong to brain's private infrastructure. They are
safe to embed as literals here because they do NOT appear in the machine-local
leak-gate.denylist (the denylist carries identifying tokens; "mcp__brain__" and
"code/brain" are structural — deliberately kept off the denylist so THIS file
can reference them without tripping the gate).

  - "mcp__brain__"  — brain MCP tool prefix
  - "code/brain"    — matches ~/code/brain and /Users/.../code/brain

## Band 2: App-flavored seam tokens (runtime-constructed — no source literal)
These tokens name the app-specific seams that the genericized skills strip
(observability vendor, flag-provider skill, schema name, build/test CLIs, issue
tracker, cost-history report). Building each at runtime (via string-join) keeps
this test source leak-gate-clean regardless of future denylist evolution — the
"P1-F self-referential trap": a test file carrying a forbidden literal can block
commits to its own fix. The module self-check below enforces that none of the
joined results appears verbatim as a contiguous source literal here.

Identifying tokens (developer handle / org name / machine path) are NOT checked
here — those are the leak gate's exclusive responsibility. Adding them here as
literals would trip the gate on this file itself.

`skills/_shared/` is a reference doc, not a skill, and is exempt.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "skills"

# ---------------------------------------------------------------------------
# Band 1: Structural brain seams (safe to embed as literals — never denylisted)
# ---------------------------------------------------------------------------

STRUCTURAL_SEAMS: list[str] = [
    "mcp__brain__",
    "code/brain",
]

# ---------------------------------------------------------------------------
# Band 2: App-flavored seam tokens (runtime-constructed — no source literal)
#
# Each token is assembled from a character list so it never appears as a
# contiguous string literal in this source file. The self-check below verifies
# the invariant at collection time.
# ---------------------------------------------------------------------------

APP_SEAM_TOKENS: list[str] = [
    "".join(["p", "o", "s", "t", "h", "o", "g"]),                                              # analytics/flag vendor
    "".join(["i", "n", "s", "t", "r", "u", "m", "e", "n", "t", "-", "f", "e", "a", "t", "u", "r", "e", "-", "f", "l", "a", "g", "s"]),  # flag-provider skill slug
    "".join(["d", "a", "s", "h", "0"]),                                                        # observability vendor
    "".join(["e", "v", "i", "d", "e", "n", "c", "e", "_", "p", "a", "c", "k"]),                # soak evidence allowlist script
    "".join(["p", "l", "a", "n", "-", "c", "o", "s", "t", "-", "h", "i", "s", "t", "o", "r", "y"]),  # cost-history report (Cluster B)
    "".join(["p", "r", "o", "j", "e", "c", "t", "i", "o", "n", "s"]),                          # private DB schema name
    "".join(["p", "l", "a", "t", "f", "o", "r", "m", "."]),                                    # dotted metric namespace prefix
    "".join(["a", "s", "a", "n", "a"]),                                                        # issue tracker vendor
    "".join(["m", "i", "x"]),                                                                   # build/test CLI
    "".join(["n", "p", "m"]),                                                                   # build/test CLI
    "".join(["z", "e", "n", "i", "t", "h", "/", ".", "c", "l", "a", "u", "d", "e"]),           # host-config cross-ref path (denylisted org name → runtime-built)
]

# ---------------------------------------------------------------------------
# Self-check: INVARIANT — no app-seam token appears as a contiguous source
# literal outside of the join-list expressions above. Verified at module-load
# (pytest collection) so an accidental edit is caught immediately. Mirrors the
# self-check in test_agents_generic.py — keep each join on one canonical
# single-line `"".join([...])` so the strip regex finds it.
# ---------------------------------------------------------------------------
_OWN_SOURCE = Path(__file__).read_text()
_SOURCE_WITHOUT_JOINS = re.sub(r'"".join\(\[.*?\]\)', "", _OWN_SOURCE)
for _tok in APP_SEAM_TOKENS:
    assert _tok not in _SOURCE_WITHOUT_JOINS, (
        f"INVARIANT VIOLATION: app-seam token {_tok!r} appears as a source "
        f"literal in {__file__} outside a join expression — this would trip the "
        f"leak gate if the denylist is extended to cover it. Use the join form."
    )


def _skill_files() -> list[Path]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        d / "SKILL.md"
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name != "_shared" and (d / "SKILL.md").exists()
    )


@pytest.mark.parametrize("skill_md", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_has_no_structural_brain_seams(skill_md: Path):
    """Skill must contain no structural brain-vault strings."""
    text = skill_md.read_text()
    for seam in STRUCTURAL_SEAMS:
        assert seam not in text, (
            f"{skill_md.parent.name}/SKILL.md contains the structural brain seam "
            f"{seam!r}. Genericize: drop mcp__brain__ tools, strip code/brain paths."
        )


@pytest.mark.parametrize("skill_md", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_has_no_app_seam_tokens(skill_md: Path):
    """Skill must contain no app-flavored seam tokens.

    Case-insensitive substring match (not word-boundary): some tokens — the
    dotted metric prefix and the issue-tracker vendor — are meant to catch any
    occurrence, including when they appear as the prefix of a longer dotted
    metric name or a hyphenated/underscored compound. The genericized skills
    replace these with provider-agnostic phrasing + a visible-skip notice.

    Footgun note: substring matching makes the short build-tool and
    issue-tracker tokens intentionally broad — a future skill whose innocent
    prose happens to contain one as a substring will trip this. That asymmetry
    is deliberate: a false positive fails loud and cheap (rephrase one word), a
    false negative leaks a private token. If an innocent word ever trips it,
    special-case THAT word — do not weaken the token to word-boundary.
    (Avoid spelling the tokens themselves in this docstring — the module
    self-check below scans this file's own source.)
    """
    text = skill_md.read_text().lower()
    for token in APP_SEAM_TOKENS:
        assert token.lower() not in text, (
            f"{skill_md.parent.name}/SKILL.md contains the app-flavored seam token "
            f"{token!r}. Genericize: replace vendor/stack-specific names with "
            "provider-agnostic phrasing and a visible-skip notice."
        )


# ---------------------------------------------------------------------------
# Visible-skip assertions: a stripped seam must announce itself, never silently
# vanish. A tokens-absent-only test would pass a skill that simply deleted the
# step — the exact silent-degradation the spec forbids. Each strip → a paired
# present-assertion on a distinctive CONTIGUOUS notice substring.
# ---------------------------------------------------------------------------

# skill stem -> list of contiguous visible-skip phrases that MUST be present
_VISIBLE_SKIP_PHRASES: dict[str, list[str]] = {
    "planning": [
        "no feature-flag provider configured",
        "no observability provider configured",
        "no issue tracker configured",
    ],
    "subagent-driven-development": [
        "no feature-flag provider configured — flag setup skipped",
        "no issue tracker configured — status transitions skipped",
    ],
}


@pytest.mark.parametrize("stem", sorted(_VISIBLE_SKIP_PHRASES))
def test_skill_visible_skip_phrases_present(stem: str):
    """A genericized skill that strips a seam must print a visible-skip notice.

    Asserts each distinctive contiguous phrase appears verbatim — a silently
    dropped step (no notice) fails here even though it would pass a
    tokens-absent-only scan.
    """
    skill_md = SKILLS_DIR / stem / "SKILL.md"
    assert skill_md.exists(), (
        f"Expected skill {stem}/SKILL.md to exist in {SKILLS_DIR}. "
        "Add the genericized skill before this test can pass."
    )
    text = skill_md.read_text()
    for phrase in _VISIBLE_SKIP_PHRASES[stem]:
        assert phrase in text, (
            f"{stem}/SKILL.md is missing the visible-skip phrase {phrase!r}. "
            "A stripped seam must announce itself, not silently disappear."
        )


# ---------------------------------------------------------------------------
# Generalize-replacement assertions: a genericize (not a degrade) must land
# the provider-agnostic replacement, not just drop the original. A
# tokens-absent-only test would pass a skill that simply deleted the example
# without replacing it — exactly the silent omission the spec forbids.
# Each entry: skill stem -> list of (absent_marker, present_phrase) pairs.
#   absent_marker  — the private literal that must NOT appear (Band-1 catches
#                    "code/brain" already; this is an extra named check for
#                    clarity in error messages).
#   present_phrase — a distinctive contiguous substring that MUST appear,
#                    confirming the generic replacement actually landed.
# ---------------------------------------------------------------------------

_GENERALIZE_REPLACEMENTS: dict[str, list[tuple[str, str]]] = {
    "followup": [
        (
            "brain/plans/",
            "lore new plan",
        ),
        (
            "aggregated by the cost reporter",
            "lore new plan",
        ),
    ],
    "requesting-code-review": [
        (
            "brain/plans/",
            "the plan/requirements the caller provides",
        ),
    ],
}


@pytest.mark.parametrize("stem", sorted(_GENERALIZE_REPLACEMENTS))
def test_skill_generalize_replacement_landed(stem: str):
    """A genericized seam (generalize flavor) must have its replacement present.

    Checks that the private path/token is gone AND the provider-agnostic
    replacement phrase is present. Both halves must hold — absence of the
    private token does not prove the replacement arrived.
    """
    skill_md = SKILLS_DIR / stem / "SKILL.md"
    assert skill_md.exists(), (
        f"Expected skill {stem}/SKILL.md to exist in {SKILLS_DIR}. "
        "Add the genericized skill before this test can pass."
    )
    text = skill_md.read_text()
    for absent_marker, present_phrase in _GENERALIZE_REPLACEMENTS[stem]:
        assert absent_marker not in text, (
            f"{stem}/SKILL.md still contains the private path/token "
            f"{absent_marker!r}. Strip it and replace with the generic phrasing."
        )
        assert present_phrase in text, (
            f"{stem}/SKILL.md is missing the generic replacement phrase "
            f"{present_phrase!r}. A silent omission of the private token is "
            "not enough — the replacement must explicitly land."
        )


# ---------------------------------------------------------------------------
# Inlined-value assertions: when a generic table/value is RELOCATED inline from
# an external doc (rather than degraded or path-genericized), a tokens-absent
# scan can't tell a faithful copy from a copy-paste corruption. The
# subagent-driven-development review-threshold table was relocated inline from
# the host project's CLAUDE.md — guard its boundary values so a future edit
# that scrambles them fails loud (council Reliability Minor).
#
# skill stem -> list of substrings that MUST be present verbatim
# ---------------------------------------------------------------------------

_INLINED_VALUES: dict[str, list[str]] = {
    "subagent-driven-development": [
        "30",   # Small/Medium line boundary (≤30 lines)
        "200",  # Medium/Large line boundary (30-200 lines)
        "5+",   # Large file-count threshold (5+ files)
    ],
}


@pytest.mark.parametrize("stem", sorted(_INLINED_VALUES))
def test_skill_inlined_values_present(stem: str):
    """A relocated-inline table must retain its boundary values verbatim.

    Guards against a copy-paste corruption of the review-threshold boundaries
    (Small ≤30 / Medium 30-200 / Large 200+ or 5+ files) that no
    tokens-absent or visible-skip check would catch.
    """
    skill_md = SKILLS_DIR / stem / "SKILL.md"
    assert skill_md.exists(), (
        f"Expected skill {stem}/SKILL.md to exist in {SKILLS_DIR}. "
        "Add the genericized skill before this test can pass."
    )
    text = skill_md.read_text()
    for value in _INLINED_VALUES[stem]:
        assert value in text, (
            f"{stem}/SKILL.md is missing the inlined boundary value {value!r}. "
            "The relocated review-threshold table must keep its values intact."
        )
