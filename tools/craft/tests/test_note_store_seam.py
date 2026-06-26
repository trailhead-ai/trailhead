"""Craft owns the plan/spec template bodies + persists via the note_store seam.

TDD contract:
  1. The two craft-owned template BODIES exist at `templates/{plan,spec}.md` and carry the
     canonical section headers. They are bodies — lore owns the record sidecar/frontmatter — so
     this checks the section skeleton, NOT a `status:` frontmatter block.
  2. `_shared/note-storage.md` (the note_store contract) documents all THREE lifecycle ops
     (create / status / link), each with a concrete lore-provider command.

Write BEFORE the implementation — these tests must fail RED first, then green after.
"""

from __future__ import annotations

from pathlib import Path

CRAFT_PLUGIN = Path(__file__).parent.parent / "plugins" / "craft"
TEMPLATES_DIR = CRAFT_PLUGIN / "templates"
NOTE_STORAGE_MD = CRAFT_PLUGIN / "skills" / "_shared" / "note-storage.md"

# Canonical section headers, mirrored from the lore plan/spec template bodies so nothing
# downstream breaks. These are the body skeleton — no frontmatter.
_PLAN_SECTIONS = [
    "Goal",
    "Architecture",
    "Given Axioms",
    "Known Unknowns",
    "Slices",
]

_SPEC_SECTIONS = [
    "Problem",
    "Objectives",
    "Acceptance Criteria",
    "Non-Goals",
    "Constraints",
    "UI Direction",
    "Open Questions",
    "Related",
]


# ---------------------------------------------------------------------------
# Craft-owned template bodies exist and carry the canonical section headers
# ---------------------------------------------------------------------------


def test_plan_template_body_exists_with_sections():
    body = TEMPLATES_DIR / "plan.md"
    assert body.exists(), f"Expected craft-owned plan body at {body}"
    text = body.read_text()
    missing = [s for s in _PLAN_SECTIONS if s not in text]
    assert not missing, f"craft plan.md body is missing canonical sections: {missing}"


def test_spec_template_body_exists_with_sections():
    body = TEMPLATES_DIR / "spec.md"
    assert body.exists(), f"Expected craft-owned spec body at {body}"
    text = body.read_text()
    missing = [s for s in _SPEC_SECTIONS if s not in text]
    assert not missing, f"craft spec.md body is missing canonical sections: {missing}"


def test_template_bodies_carry_no_status_frontmatter():
    """The craft templates are BODIES — lore owns the record sidecar/frontmatter, so the
    body must not declare a `status:` field (that would shadow the record's status vocab)."""
    for stem in ("plan", "spec"):
        text = (TEMPLATES_DIR / f"{stem}.md").read_text()
        assert "\nstatus:" not in text and not text.startswith("status:"), (
            f"craft {stem}.md is a body, not a record — it must not declare `status:` "
            "frontmatter (lore owns the sidecar/status)."
        )


# ---------------------------------------------------------------------------
# note_store contract documents all three lifecycle ops with concrete lore commands
# ---------------------------------------------------------------------------


def test_note_storage_contract_exists():
    assert NOTE_STORAGE_MD.exists(), (
        f"Expected the note_store contract at {NOTE_STORAGE_MD} (sibling to _shared/council.md)."
    )


def test_note_storage_documents_create_op():
    text = NOTE_STORAGE_MD.read_text()
    assert "create" in text and "lore record create" in text, (
        "note-storage.md must document the `create` op with the concrete lore-provider "
        "command `lore record create`."
    )
    assert "--kind" in text and "stdin" in text.lower(), (
        "the create op must pipe the rendered body on stdin to `lore record create --kind ...`."
    )


def test_note_storage_documents_status_op():
    text = NOTE_STORAGE_MD.read_text()
    assert "status(" in text or "`status`" in text, (
        "note-storage.md must document the `status` lifecycle op."
    )
    # Records carry status in a JSON sidecar, mutated via the dedicated `--status` flag
    # (the legacy `--set status=` / `lore set-status` surface was removed).
    assert "lore record update" in text and "--status " in text, (
        "the status op must name the concrete record-provider command "
        "`lore record update <id> --status <value>`."
    )
    assert "--set " not in text and "set-status" not in text, (
        "the seam must NOT reference the removed `--set` flag or `lore set-status` command."
    )


def test_note_storage_documents_link_op():
    text = NOTE_STORAGE_MD.read_text()
    # Linking a plan to its spec uses the `related` map under the `spec` kind via the
    # dedicated `--related <kind>=<name>` flag (the `related-spec` sidecar field is gone).
    assert "link" in text and "--related spec=" in text, (
        "note-storage.md must document the `link` op via `--related spec=<spec-name>`."
    )
    assert "lore record update" in text, (
        "the link op must name the concrete record-provider command `lore record update`."
    )


def test_note_storage_defers_non_lore_provider():
    """The seam names lore as the sole/default provider; per-repo config resolution and any
    non-lore provider are explicitly deferred (out of scope)."""
    text = NOTE_STORAGE_MD.read_text().lower()
    assert "defer" in text, (
        "note-storage.md must explicitly defer per-repo config resolution + any non-lore "
        "provider (out of scope for this slice)."
    )
