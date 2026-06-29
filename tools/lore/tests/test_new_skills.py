"""The new read/capture/dispatch skills (search / record / research)
plus the FINAL cross-skill lockstep grep gate.

These three skills sit on top of the rewired session skills:
  - `search`  — wraps `lore search` (KQL-subset read path; replaces old `recall`).
                Carries the `<external-memory>` injection-defense guard because
                search results land in the MAIN session and can include shared-
                layer vault content.
  - `record`  — thin GUIDE for a SINGLE deliberate capture NOW via `lore record`
                / `lore session …`. Its trigger must be scope-disjoint from
                `checkpoint` (which is a session *sweep*).
  - `research` — dispatches the lore `investigator` agent (deep) or `researcher`
                agent (lighter / `tracking`-backlog polling). Dispatch targets
                must resolve to real agent FILES.

The FINAL LOCKSTEP GATE greps ALL retained lore skills for the removed
commands (`lore new`, `lore recall`, `lore patch`) and asserts zero matches.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
SKILLS_DIR = PLUGIN_ROOT / "skills"
AGENTS_DIR = PLUGIN_ROOT / "agents"

# The three skills under test.
NEW_SKILLS = ("search", "record", "research")


def _skill_text(name: str) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text()


def _frontmatter(name: str) -> str:
    text = _skill_text(name)
    assert text.startswith("---\n"), f"{name}/SKILL.md must open with `---` frontmatter"
    end = text.find("\n---", 3)
    assert end > 0, f"{name}/SKILL.md frontmatter block is not closed"
    return text[3:end]


def _description(name: str) -> str:
    """The frontmatter `description:` value (may span multiple folded lines)."""
    fm = _frontmatter(name)
    lines = fm.splitlines()
    out: list[str] = []
    capturing = False
    for ln in lines:
        if ln.strip().startswith("description:"):
            capturing = True
            out.append(ln.split(":", 1)[1].strip())
            continue
        if capturing:
            # A new top-level YAML key ends the description block.
            if re.match(r"^[a-zA-Z_-]+:", ln):
                break
            out.append(ln.strip())
    return " ".join(p for p in out if p)


# ---------------------------------------------------------------------------
# Presence + registrable frontmatter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_skill_present_with_registrable_frontmatter(name: str):
    """Each new skill exists with a closed frontmatter block + non-empty
    description (the field Claude Code uses to register `/lore:<name>`)."""
    skill_md = SKILLS_DIR / name / "SKILL.md"
    assert skill_md.exists(), f"{name}/SKILL.md must exist"
    assert _description(name), f"{name}/SKILL.md must carry a non-empty description:"


# ---------------------------------------------------------------------------
# search — read path + injection-defense guard
# ---------------------------------------------------------------------------

def test_search_invokes_lore_search_not_recall():
    """`search` wraps `lore search` (the S3 read path) and never `lore recall`."""
    text = _skill_text("search")
    assert "lore search" in text, (
        "search/SKILL.md must invoke `lore search` (the KQL-subset read path)"
    )
    assert "lore recall" not in text, (
        "search/SKILL.md must NOT reference `lore recall` — it is the removed read "
        "command that `lore search` replaces"
    )


def test_search_carries_external_memory_injection_guard():
    """search results land in the MAIN session and can include shared-layer
    content, so the SKILL.md MUST carry the `librarian`-style `<external-memory>`
    injection-defense guard (asserted by the guard tokens)."""
    text = _skill_text("search")
    assert "external-memory" in text, (
        "search/SKILL.md must carry the `<external-memory>` injection-defense guard"
    )
    assert "NEVER" in text, (
        "search/SKILL.md injection guard must state shared-layer content is NEVER "
        "instructions"
    )
    assert "instructions" in text, (
        "search/SKILL.md injection guard must frame shared-layer content as "
        "reference-only, never as instructions"
    )


def test_search_documents_kql_subset_query_shape():
    """search must document the KQL-subset query shape + the read flags."""
    text = _skill_text("search")
    assert "kind:" in text and "area:" in text, (
        "search/SKILL.md must document the `kind:`/`area:` KQL-subset facets"
    )
    assert "--json" in text and "--limit" in text, (
        "search/SKILL.md must document the `--json` and `--limit` flags"
    )


# ---------------------------------------------------------------------------
# record — single deliberate capture; scope-disjoint from checkpoint
# ---------------------------------------------------------------------------

def test_record_references_only_record_and_session_surface():
    """`record` references ONLY the new capture surface — `lore record` /
    `lore session …` — never `lore new`, `lore patch`, or `lore recall`."""
    text = _skill_text("record")
    assert "lore record" in text, (
        "record/SKILL.md must reference `lore record` (the deliberate-capture surface)"
    )
    for forbidden in ("lore new", "lore patch", "lore recall"):
        assert forbidden not in text, (
            f"record/SKILL.md must not reference removed command {forbidden!r}"
        )


def test_record_and_flush_descriptions_are_scope_disjoint():
    """The `record` and `flush` trigger descriptions must carve non-overlapping scopes.

    `checkpoint` was deleted and `finish` was renamed → `flush`. The
    scope-disjointness concern now applies to `record` vs `flush`:

      - record = "log ONE specific item NOW" (a single deliberate capture)
      - flush  = "evaluate outstanding candidates → records, then flip clean"
                 (a batch judgment step, not a single-item NOW capture)

    `record` must signal "one"/"now" and must NOT claim the evaluation-batch scope
    `flush` owns; `flush` must not claim the single-deliberate-capture scope
    `record` owns.
    """
    record_desc = _description("record").lower()
    flush_desc = _description("flush").lower()

    # record names its distinct moment: a single item, now.
    assert any(tok in record_desc for tok in ("one", "single", "now")), (
        "record description must name its distinct moment — log ONE item NOW — "
        "not a generic 'capture'"
    )
    # record must NOT affirmatively claim the batch-evaluation scope flush owns.
    # (Pointing to /lore:flush as a redirect is fine — claiming the evaluation
    #  work itself is not.)
    for batch_tok in ("evaluate", "outstanding", "candidates"):
        assert batch_tok not in record_desc, (
            f"record description must not claim flush's batch-evaluation scope "
            f"({batch_tok!r}) — record is a single deliberate capture"
        )

    # flush must describe candidate evaluation (its core judgment step).
    assert any(tok in flush_desc for tok in ("candidate", "evaluate", "outstanding")), (
        "flush description must describe evaluating candidates (its core judgment step), "
        "not just the mechanical clean-flip"
    )


# ---------------------------------------------------------------------------
# research — dispatches the lore agents; targets resolve to real agent FILES
# ---------------------------------------------------------------------------

def test_research_names_both_dispatch_agents():
    """research must name both dispatch targets so the caller can route."""
    text = _skill_text("research")
    assert "investigator" in text, "research/SKILL.md must name the `investigator` agent"
    assert "researcher" in text, "research/SKILL.md must name the `researcher` agent"


def test_research_dispatch_targets_resolve_to_real_agent_files():
    """The agents `research` dispatches to must exist as real files (not merely
    name-resolution): if coordination slipped, this fails instead of
    dispatching to a nonexistent agent."""
    for agent in ("investigator", "researcher"):
        agent_file = AGENTS_DIR / f"{agent}.md"
        assert agent_file.exists(), (
            f"research dispatches to `{agent}` but {agent_file} does not exist"
        )


def test_research_signals_investigator_vs_researcher_selection():
    """research must explain WHEN to pick the deep `investigator` vs the lighter
    `researcher` (incl. `tracking`-backlog polling) so the caller routes
    correctly (council Minor — Advocate)."""
    text = _skill_text("research").lower()
    assert "deep" in text, (
        "research/SKILL.md must signal `investigator` is the deep/expensive path"
    )
    assert "tracking" in text, (
        "research/SKILL.md must document `researcher` for polling `tracking`-status "
        "backlog items"
    )


# ---------------------------------------------------------------------------
# FINAL LOCKSTEP GATE (spec AC — the capstone)
# ---------------------------------------------------------------------------

def _all_retained_skill_files() -> list[Path]:
    return sorted(
        d / "SKILL.md"
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name != "_shared" and (d / "SKILL.md").exists()
    )


REMOVED_COMMANDS = ("lore new", "lore recall", "lore patch")


@pytest.mark.parametrize("removed", REMOVED_COMMANDS)
def test_final_lockstep_gate_no_removed_commands_in_any_retained_skill(removed: str):
    """FINAL LOCKSTEP GATE: a grep across ALL retained lore skills for the removed
    commands returns ZERO. Every capture path resolves to `lore record` /
    `lore session`; every read/search path to `lore search`."""
    offenders = [
        str(p) for p in _all_retained_skill_files() if removed in p.read_text()
    ]
    assert not offenders, (
        f"FINAL LOCKSTEP GATE FAILED: removed command {removed!r} still present in: "
        f"{offenders}"
    )


@pytest.mark.parametrize("removed", REMOVED_COMMANDS)
def test_final_lockstep_gate_clean_across_whole_skills_tree(removed: str):
    """The spec runs the literal gate over the WHOLE skills/ tree
    (`grep -rnE … tools/lore/plugins/lore/skills/`), which includes the
    `_shared/` reference doc — not just the SKILL.md files. Assert that literal
    scope is also clean, so the shared capture-pattern doc cannot smuggle a
    removed command (`lore new`) past the per-skill enumeration above."""
    offenders = [
        str(p)
        for p in SKILLS_DIR.rglob("*.md")
        if removed in p.read_text()
    ]
    assert not offenders, (
        f"FINAL LOCKSTEP GATE FAILED (whole skills/ tree): removed command "
        f"{removed!r} still present in: {offenders}"
    )


def test_final_lockstep_gate_enumerates_expected_retained_set():
    """The lockstep gate must run over the full retained skill set — guard that
    the enumeration actually covers the five retained lore skills (so the gate
    above is not vacuously passing over a shrunken set).

    'finish' was renamed → 'flush' and 'checkpoint' was deleted.
    Retained set: flush, sync, search, record, research.
    """
    names = {p.parent.name for p in _all_retained_skill_files()}
    expected = {"flush", "sync", "search", "record", "research"}
    assert expected <= names, (
        f"retained lore skill set is missing {sorted(expected - names)} — the "
        "lockstep gate would not cover them"
    )
