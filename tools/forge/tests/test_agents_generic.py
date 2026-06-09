"""Every shipped forge agent must be generic — zero app-specific or brain-vault strings.

This test enforces the mechanical definition of "generic" via two forbidden
bands, both parametrized over the same agent discovery as test_agents_registrable.py
(single source of truth: AGENTS_DIR / glob("*.md")).

## Band 1: Structural brain seams (literal strings — never denylisted)
These strings definitionally belong to brain's private infrastructure. They are
safe to embed as literals here because they do NOT appear in the machine-local
leak-gate.denylist (the denylist carries identifying tokens; "mcp__brain__" and
"code/brain" are structural — deliberately kept off the denylist so THIS file
can reference them without tripping the gate).

  - "mcp__brain__"        — brain MCP tool prefix
  - "code/brain"          — matches ~/code/brain and /Users/.../code/brain
  - "harvest-protocol.md" — brain-vault-local format reference
  - "brain-librarian"     — brain-only agent (ported to lore, not forge)

## Band 2: Middle-band app-flavored tokens (runtime-constructed — no source literal)
These tokens are app-flavored but not secret. Building them at runtime (via
string-join) means the test source stays leak-gate-clean regardless of future
denylist evolution (the "P1-F self-referential trap": a test file that carries a
forbidden literal can block commits to its own fix).

INVARIANT: none of the joined results appears verbatim as a contiguous string
in this source outside of the join-list expressions. The self-check below
enforces this at collection time.

The tokens constructed at runtime (see MIDDLE_BAND_TOKENS):
  - app character name (5 chars, starts with P)
  - transit system name (5 chars, starts with M)
  - hyphenated environment term (7 chars)
  - Elixir build/test CLI tool (3 chars)
  - Node build/test CLI tool (3 chars)
  - internal mobile repo/subsystem slug (hyphenated, 15 chars)

Identifying tokens (org name / bot name / infra vendor / developer handle /
schema name) are NOT checked here — those are the leak gate's exclusive
responsibility. Listing them here would trip the gate on this file itself.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# Single source of truth for agent discovery — mirrors test_agents_registrable.py exactly.
AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "agents"


def _agent_files() -> list[Path]:
    return sorted(AGENTS_DIR.glob("*.md"))


# ---------------------------------------------------------------------------
# Band 1: Structural brain seams (safe to embed as literals — never denylisted)
# ---------------------------------------------------------------------------

STRUCTURAL_SEAMS: list[str] = [
    "mcp__brain__",
    "code/brain",
    "harvest-protocol.md",
    "brain-librarian",
]

# ---------------------------------------------------------------------------
# Band 2: Middle-band app-flavored tokens (runtime-constructed — no source literal)
#
# Each token is assembled from a character list so it never appears as a
# contiguous string literal in this source file. The self-check below verifies
# the invariant at collection time.
# ---------------------------------------------------------------------------

MIDDLE_BAND_TOKENS: list[str] = [
    "".join(["P", "e", "n", "n", "y"]),          # app character name
    "".join(["M", "e", "t", "r", "o"]),           # transit system name
    "".join(["d", "e", "v", "-", "e", "n", "v"]), # hyphenated env term
    "".join(["m", "i", "x"]),                      # Elixir build tool
    "".join(["n", "p", "m"]),                      # Node build tool
    "".join(["m", "o", "b", "i", "l", "e", "-", "o", "v", "e", "r", "v", "i", "e", "w"]),  # internal mobile repo slug
]

# ---------------------------------------------------------------------------
# Self-check: INVARIANT — no middle-band token appears as a contiguous source
# literal outside of the join-list expressions above. Verified at module-load
# (pytest collection) so an accidental edit is caught immediately.
# ---------------------------------------------------------------------------
_OWN_SOURCE = Path(__file__).read_text()
# Strip all join([...]) list literals before searching; those are the only
# permitted site for the character fragments. NOTE: this strip depends on the
# canonical single-line `"".join([...])` spelling — keep each join on one line
# and in that exact form. Reformatting to multiline or `str().join(...)` breaks
# the strip, which fails SAFE (the self-check false-positives the suite rather
# than letting a literal leak), but is invisible without this note.
_SOURCE_WITHOUT_JOINS = re.sub(r'"".join\(\[.*?\]\)', "", _OWN_SOURCE)
for _tok in MIDDLE_BAND_TOKENS:
    assert _tok not in _SOURCE_WITHOUT_JOINS, (
        f"INVARIANT VIOLATION: middle-band token {_tok!r} appears as a source "
        f"literal in {__file__} outside a join expression — this would trip the "
        f"leak gate if the denylist is extended to cover it. Use the join form."
    )


# ---------------------------------------------------------------------------
# Parametrized hygiene tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_md", _agent_files(), ids=lambda p: p.stem)
def test_agent_has_no_structural_brain_seams(agent_md: Path):
    """Agent must contain no structural brain-vault strings."""
    text = agent_md.read_text()
    for seam in STRUCTURAL_SEAMS:
        assert seam not in text, (
            f"{agent_md.name} contains the structural brain seam {seam!r}. "
            "Genericize: drop mcp__brain__ tools, strip code/brain paths, "
            "inline harvest format (drop harvest-protocol.md ref), "
            "replace brain-librarian with generic optional phrasing."
        )


@pytest.mark.parametrize("agent_md", _agent_files(), ids=lambda p: p.stem)
def test_agent_has_no_middle_band_tokens(agent_md: Path):
    """Agent must contain no app-flavored middle-band tokens.

    Word-boundary-anchored (`\\b...\\b`) rather than naive substring, so the
    3-char build-tool tokens don't false-match longer English words that merely
    contain them. The hyphenated env term still matches when it's the prefix of
    a longer hyphenated word (its internal hyphen is a non-word char, so the
    leading boundary holds), which is the intended catch. (Concrete examples are
    omitted from this docstring on purpose — the module self-check above scans
    this source for token substrings, so spelling one out here would trip it.)
    """
    text = agent_md.read_text()
    for token in MIDDLE_BAND_TOKENS:
        assert not re.search(rf"\b{re.escape(token)}\b", text), (
            f"{agent_md.name} contains the middle-band app-flavored token "
            f"{token!r}. Genericize: replace stack-specific command examples "
            "with stack-agnostic phrasing (e.g. 'your build/test command')."
        )


# ---------------------------------------------------------------------------
# Slice P3A-2 behavioral tests
# ---------------------------------------------------------------------------

# Agents that carry a harvest block and must retain lore-hook-compatible heading.
# Extend this list as new harvest-bearing agents are genericized (P3A-3 adds
# researcher, sdd-assumption-prover, and sdd-implementer).
_HARVEST_BEARING_AGENTS: list[str] = [
    "architect",
    "code-reviewer",
    "security-auditor",
    "troubleshooter",
    "researcher",
    "sdd-assumption-prover",
    "sdd-implementer",
]

# Agents that dispatched brain-librarian and must now carry a visible skip notice
# so callers know the prior-art synthesis pass was skipped.
_VISIBLE_SKIP_AGENTS: list[str] = [
    "architect",
    "troubleshooter",
    "researcher",
    "planner",
    "council-builder",
    "council-reliability",
    "council-security",
    "council-advocate",
]


@pytest.mark.parametrize("stem", _HARVEST_BEARING_AGENTS)
def test_harvest_bearing_agent_retains_heading(stem: str):
    """Genericized harvest-bearing agents must keep the literal '## Harvest candidates'
    heading so lore's harvest hook (path-agnostic, matches heading + entry format)
    continues to consume their output unchanged.
    """
    agent_md = AGENTS_DIR / f"{stem}.md"
    assert agent_md.exists(), (
        f"Expected harvest-bearing agent {stem}.md to exist in {AGENTS_DIR}. "
        "Add the genericized agent before this test can pass."
    )
    text = agent_md.read_text()
    assert "## Harvest candidates" in text, (
        f"{agent_md.name} is expected to carry a harvest block but is missing the "
        "literal '## Harvest candidates' heading. Keep the heading and inline the "
        "entry-format rules (drop the harvest-protocol.md path reference instead)."
    )


# ---------------------------------------------------------------------------
# Slice P3C2-4 (planner) — observability-seam Band-2 tokens + visible-skip
#
# The planner agent carried an app-specific "Health & Soak" block whose tokens
# are NOT in the shared MIDDLE_BAND_TOKENS list (those cover the stack/repo
# flavor). These are the planner's specific observability seams; name them so a
# partial strip of the Health & Soak block can't survive (council Security
# Critical). Runtime-constructed, same join-form invariant as MIDDLE_BAND_TOKENS
# (the module self-check above scans this whole source).
# ---------------------------------------------------------------------------

_PLANNER_OBSERVABILITY_TOKENS: list[str] = [
    "".join(["p", "l", "a", "t", "f", "o", "r", "m", "."]),  # dotted metric namespace prefix
    "".join(["e", "v", "i", "d", "e", "n", "c", "e", "_", "p", "a", "c", "k"]),  # soak evidence-pack script
    "".join(["p", "l", "a", "t", "f", "o", "r", "m", "-", "h", "e", "a", "l", "t", "h", "-", "c", "h", "e", "c", "k", "s"]),  # subsystem profile slug
    "".join(["d", "a", "s", "h", "0"]),  # observability vendor
]

# Re-run the join-form invariant for the planner token list (the self-check
# loop above only covered MIDDLE_BAND_TOKENS).
for _tok in _PLANNER_OBSERVABILITY_TOKENS:
    assert _tok not in _SOURCE_WITHOUT_JOINS, (
        f"INVARIANT VIOLATION: planner observability token {_tok!r} appears as a "
        f"source literal in {__file__} outside a join expression — use the join form."
    )


# The exact observability visible-skip phrase the planner emits. Asserted PRESENT
# as a distinctive contiguous substring so a silent omission of the degrade
# notice fails (council Reliability — visible-skip present-assertion). Mirrors
# the planning skill's observability degrade wording.
_PLANNER_OBSERVABILITY_VISIBLE_SKIP = "no observability provider configured — see the extend guide"


def test_planner_has_no_observability_seam_tokens():
    """The planner's old Health & Soak block carried the observability-vendor,
    evidence-pack, dotted-metric-prefix, and health-check-subsystem tokens. After
    the observability degrade none may survive — a partial strip must fail here."""
    planner_md = AGENTS_DIR / "planner.md"
    assert planner_md.exists(), (
        f"Expected planner.md in {AGENTS_DIR}. Add the genericized agent first."
    )
    text = planner_md.read_text()
    for token in _PLANNER_OBSERVABILITY_TOKENS:
        assert token not in text, (
            f"planner.md still contains the observability seam token {token!r}. "
            "Strip the app-specific Health & Soak block: replace with the generic "
            "'Observability & Failure Visibility' mapping + observability "
            "extension point + visible-skip notice."
        )


def test_planner_observability_visible_skip_present():
    """The planner must carry the observability visible-skip phrase as a
    distinctive contiguous substring so the degrade announces itself rather than
    silently omitting the soak-signal step (degrade present-assertion)."""
    planner_md = AGENTS_DIR / "planner.md"
    assert planner_md.exists(), (
        f"Expected planner.md in {AGENTS_DIR}. Add the genericized agent first."
    )
    text = planner_md.read_text()
    assert _PLANNER_OBSERVABILITY_VISIBLE_SKIP in text, (
        f"planner.md must contain the observability visible-skip phrase "
        f"{_PLANNER_OBSERVABILITY_VISIBLE_SKIP!r} so the degrade is visible to the "
        "caller when no observability provider is configured."
    )


@pytest.mark.parametrize("stem", _VISIBLE_SKIP_AGENTS)
def test_visible_skip_notice_present(stem: str):
    """Agents that formerly dispatched brain-librarian must carry a visible notice
    telling the caller when the prior-art synthesis pass was skipped — not a silent
    prose hedge (council Advocate C1: no-silent-degradation rule).

    The notice must contain the distinctive phrase fragment 'synthesis pass was
    skipped' so the caller knows results may be incomplete without the
    knowledge-synthesis subagent. (Matching the full fragment rather than a bare
    word like 'skipped'/'shallower' avoids false-passing on unrelated prose that
    merely happens to use one of those words — code-reviewer Minor from P3A-2.)
    """
    agent_md = AGENTS_DIR / f"{stem}.md"
    assert agent_md.exists(), (
        f"Expected agent {stem}.md to exist in {AGENTS_DIR}. "
        "Add the genericized agent before this test can pass."
    )
    text = agent_md.read_text()
    assert "synthesis pass was skipped" in text, (
        f"{agent_md.name} must contain the visible skip-notice phrase 'synthesis "
        "pass was skipped' for when the knowledge-synthesis subagent is absent. "
        "Rewrite the brain-librarian dispatch to the required fallback shape: "
        "'if none is configured, note in your report that the prior-art synthesis "
        "pass was skipped and results may be shallower.'"
    )
