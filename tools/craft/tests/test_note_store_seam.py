"""Craft owns the parent-task/child-task/spec template bodies + persists via the note_store seam.

TDD contract:
  1. The craft-owned template BODIES exist at `templates/{plan,task,spec}.md` and carry the
     canonical section headers. They are bodies — lore owns the record sidecar/frontmatter — so
     this checks the section skeleton, NOT a `status:` frontmatter block.
     - `templates/plan.md` is the **parent-task** body (Goal / Delta design / Given Axioms /
       Known Unknowns / `## Flow-out` completion-ritual checklist). Its slices live in separate
       **child** `task` records, so the parent body carries no `### Slice` sub-sections.
     - `templates/task.md` is the **child-task** body (Delivers / Test contract / Files).
  2. `_shared/note-storage.md` (the note_store contract) documents all THREE lifecycle ops
     (create / status / link), each with a concrete lore-provider command, plus the unified
     `task` kind's parent/child creation pattern (`--kind task`, `--parent`, `--depends-on`).

Write BEFORE the implementation — these tests must fail RED first, then green after.
"""

from __future__ import annotations

from pathlib import Path

CRAFT_PLUGIN = Path(__file__).parent.parent / "plugins" / "craft"
TEMPLATES_DIR = CRAFT_PLUGIN / "templates"
NOTE_STORAGE_MD = CRAFT_PLUGIN / "skills" / "_shared" / "note-storage.md"

# Canonical section headers for the parent-task body. These are the body skeleton — no
# frontmatter. A plan is a parent `task` record whose slices are separate child `task`
# records, so the parent carries a `## Flow-out` completion gate rather than a `Slices` block.
_PLAN_SECTIONS = [
    "Goal",
    "Delta design",
    "Given Axioms",
    "Known Unknowns",
    "Flow-out",
]

# Canonical section headers for the child-task body.
_TASK_SECTIONS = [
    "Delivers",
    "Test contract",
    "Files",
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
    assert body.exists(), f"Expected craft-owned parent-task body at {body}"
    text = body.read_text()
    missing = [s for s in _PLAN_SECTIONS if s not in text]
    assert not missing, f"craft plan.md body is missing canonical sections: {missing}"


def test_plan_template_flow_out_is_a_heading():
    """The `## Flow-out` completion-ritual gate must be a markdown heading (matches lore's
    parent-completion reminder regex `^\\s*#{2,}\\s+flow-out\\b`), not inline bold text."""
    text = (TEMPLATES_DIR / "plan.md").read_text()
    import re

    assert re.search(r"(?im)^\s*#{2,}\s+flow-out\b", text), (
        "craft plan.md must carry a `## Flow-out` heading so a completed parent task with this "
        "section suppresses lore's flow-out reminder."
    )


def test_plan_template_carries_no_slice_subsections():
    """Slices are now separate child `task` records, not `### Slice` (or `### Task`) body
    sub-sections."""
    text = (TEMPLATES_DIR / "plan.md").read_text()
    import re

    assert not re.search(r"(?im)^\s*#{2,}\s+(?:task|slice)\b", text), (
        "craft plan.md (parent-task body) must not carry `### Task` (or `### Slice`) sub-sections "
        "— each slice is its own child `task` record wired via `--parent`/`--depends-on`."
    )


def test_plan_template_guard_catches_inline_task_heading():
    """The child-unit is spelled `task` under current vocabulary — inlining it as a `### Task`
    sub-heading in the parent plan body is the same anti-pattern the guard exists to catch."""
    import re

    text = "### Task 3: does the thing\n"
    assert re.search(r"(?im)^\s*#{2,}\s+(?:task|slice)\b", text), (
        "guard must catch a `### Task` heading, not just the retired `### Slice` spelling"
    )


def test_task_template_body_exists_with_sections():
    body = TEMPLATES_DIR / "task.md"
    assert body.exists(), f"Expected craft-owned child-task body at {body}"
    text = body.read_text()
    missing = [s for s in _TASK_SECTIONS if s not in text]
    assert not missing, f"craft task.md body is missing canonical sections: {missing}"


def test_spec_template_body_exists_with_sections():
    body = TEMPLATES_DIR / "spec.md"
    assert body.exists(), f"Expected craft-owned spec body at {body}"
    text = body.read_text()
    missing = [s for s in _SPEC_SECTIONS if s not in text]
    assert not missing, f"craft spec.md body is missing canonical sections: {missing}"


def test_template_bodies_carry_no_status_frontmatter():
    """The craft templates are BODIES — lore owns the record sidecar/frontmatter, so the
    body must not declare a `status:` field (that would shadow the record's status vocab)."""
    for stem in ("plan", "task", "spec"):
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


def test_note_storage_documents_task_kind_and_graph_flags():
    """The seam persists plans as the unified `task` kind — a parent task plus child tasks
    wired with the graph edge flags. It must name `--kind task`, `--parent`, and
    `--depends-on`, and must NOT reference the retired `plan`/`backlog` kinds."""
    text = NOTE_STORAGE_MD.read_text()
    assert "--kind task" in text, (
        "note-storage.md must document persisting plans/tasks as `lore record create --kind task`."
    )
    assert "--parent" in text and "--depends-on" in text, (
        "note-storage.md must document the child-task graph edges (`--parent` containment, "
        "`--depends-on` ordering)."
    )
    assert "--kind plan" not in text and "--kind backlog" not in text, (
        "note-storage.md must not reference the retired `plan`/`backlog` record kinds."
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
