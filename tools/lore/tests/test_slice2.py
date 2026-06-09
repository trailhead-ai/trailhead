"""Slice 2 tests: doc-consistency check for the `finished` skill update.

Verifies that:
1. The `lore:finished` SKILL.md references harvest-expansion behavior (the
   word "harvest" is present), confirming the skill documents the Slice 1 CLI.
2. The skill does NOT claim the skill itself performs synthesis/expansion
   (the heavy lifting is the CLI — the skill stays lean, just calling
   `lore finish`).
3. The skill describes `lore:finished` as the canonical end-of-session finish.
4. The README's `lore finish` CLI description reflects the expanded behavior.
5. All existing registrable/doc tests still pass (covered by running the full
   suite, but we pin the finished-skill entries here explicitly).
"""
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "plugins" / "lore" / "skills"
FINISHED_SKILL = SKILLS_DIR / "finished" / "SKILL.md"
README = REPO_ROOT / "README.md"


def _skill_text() -> str:
    return FINISHED_SKILL.read_text()


def _readme_text() -> str:
    return README.read_text()


# ---------------------------------------------------------------------------
# 1. SKILL.md documents harvest-expansion behavior
# ---------------------------------------------------------------------------

def test_skill_references_harvest():
    """The finished skill must mention 'harvest' — it documents the CLI expansion."""
    text = _skill_text()
    assert "harvest" in text.lower(), (
        "finished/SKILL.md must reference harvest behavior that lore finish now performs; "
        "got no 'harvest' in the file"
    )


def test_skill_references_harvest_pending():
    """The skill must specifically call out harvest-pending.md as the source."""
    text = _skill_text()
    assert "harvest-pending" in text, (
        "finished/SKILL.md must mention 'harvest-pending' — that is the file the CLI reads"
    )


def test_skill_references_gotcha_surfaced():
    """The skill must note that gotcha entries are surfaced, not auto-expanded."""
    text = _skill_text()
    assert "gotcha" in text.lower(), (
        "finished/SKILL.md must document that 'gotcha' entries are surfaced "
        "(not expanded) for manual /lore:subsystem patching"
    )


def test_skill_references_lore_subsystem_for_gotchas():
    """The skill must direct the user to /lore:subsystem for gotcha entries."""
    text = _skill_text()
    assert "lore:subsystem" in text or "subsystem" in text.lower(), (
        "finished/SKILL.md must mention /lore:subsystem as the path for gotcha entries"
    )


def test_skill_documents_malformed_lines_retained():
    """The skill must state that malformed/unmarked lines are left in pending."""
    text = _skill_text()
    assert "malformed" in text.lower() or "unmarked" in text.lower(), (
        "finished/SKILL.md must document that malformed/unmarked lines are "
        "retained in harvest-pending.md (not silently consumed)"
    )


# ---------------------------------------------------------------------------
# 2. Skill stays LEAN — expansion is CLI, not a skill synthesis step
# ---------------------------------------------------------------------------

def test_skill_does_not_claim_skill_itself_expands():
    """The skill must NOT instruct the AI to synthesize/expand notes itself.

    Expansion is deterministic CLI work. The skill's job is to draft the
    session note sections + call `lore finish`. It must not add a manual
    "now go expand each harvest entry yourself" step.
    """
    text = _skill_text()
    # The skill DESCRIBES the expansion (it happens via lore finish), but must
    # not instruct the model to run expansion commands manually before calling
    # lore finish. A "Step N — expand harvest" instruction block would be wrong.
    # We check: no instruction pattern like "Step N — Expand harvest" or
    # "expand the harvest entries" as an imperative step of the skill itself.
    import re
    bad_patterns = [
        r"step\s+\d+\s*[-—]\s*expand\s+harvest",
        r"now\s+expand\s+(each\s+)?harvest",
        r"manually\s+expand\s+harvest",
    ]
    for pattern in bad_patterns:
        assert not re.search(pattern, text, re.IGNORECASE), (
            f"finished/SKILL.md must not instruct the model to manually expand "
            f"harvest entries (pattern found: {pattern!r}). The CLI does it."
        )


# ---------------------------------------------------------------------------
# 3. lore:finished framed as the canonical end-of-session finish
# ---------------------------------------------------------------------------

def test_skill_framed_as_canonical():
    """The skill must describe lore:finished as the canonical end-of-session finish."""
    text = _skill_text()
    assert "canonical" in text.lower(), (
        "finished/SKILL.md should frame lore:finished as the canonical "
        "end-of-session finish"
    )


# ---------------------------------------------------------------------------
# 4. README's CLI table updated for expanded lore finish behavior
# ---------------------------------------------------------------------------

def test_readme_lore_finish_mentions_harvest_or_expanded():
    """The README's lore finish CLI line must reflect the expanded behavior."""
    text = _readme_text()
    # Find the lore finish line in the CLI table
    for line in text.splitlines():
        if "lore finish" in line and ("Finalize" in line or "finalize" in line):
            # The line exists; it should now reflect the harvest expansion
            # (either by mentioning harvest/expand or by being updated).
            # The old text was just "Finalize the active session note and commit"
            # — we check it's been updated.
            assert "harvest" in line.lower() or "expand" in line.lower() or "notes" in line.lower(), (
                f"README's 'lore finish' CLI line should reflect the expanded "
                f"behavior (harvest / expand / notes); got: {line!r}"
            )
            return
    # If we didn't find a lore finish line at all, that's also a failure.
    assert False, "README must have a 'lore finish' entry in the CLI table"


# ---------------------------------------------------------------------------
# 5. Skill frontmatter is still registrable (non-regression)
# ---------------------------------------------------------------------------

def test_finished_skill_frontmatter_still_registrable():
    """Editing the skill must not break the frontmatter that Claude Code needs."""
    text = FINISHED_SKILL.read_text()
    assert text.startswith("---\n"), "finished/SKILL.md must still open with a YAML frontmatter block"
    end = text.find("\n---", 3)
    assert end > 0, "finished/SKILL.md frontmatter block must still be closed"
    frontmatter = text[3:end]
    desc_lines = [
        ln for ln in frontmatter.splitlines()
        if ln.strip().startswith("description:") and ln.split(":", 1)[1].strip()
    ]
    assert desc_lines, "finished/SKILL.md must still carry a non-empty description:"
