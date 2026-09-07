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

import re
from pathlib import Path

CRAFT_PLUGIN = Path(__file__).parent.parent / "plugins" / "craft"
# The child unit is spelled `task`; `slice` is its retired spelling. Both are the same
# anti-pattern when inlined as a sub-heading in the parent plan body, so the guard
# catches either. Both tests below read this one pattern — a second copy would let a
# narrowed guard keep passing.
_NO_INLINE_CHILD_UNIT_RE = re.compile(r"(?im)^\s*#{2,}\s+(?:task|slice)\b")


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
    "Maturity",
    "Acceptance Criteria",
    "Required Interfaces",
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

    assert not _NO_INLINE_CHILD_UNIT_RE.search(text), (
        "craft plan.md (parent-task body) must not carry `### Task` (or `### Slice`) sub-sections "
        "— each slice is its own child `task` record wired via `--parent`/`--depends-on`."
    )


def test_plan_template_guard_catches_inline_task_heading():
    """The child-unit is spelled `task` under current vocabulary — inlining it as a `### Task`
    sub-heading in the parent plan body is the same anti-pattern the guard exists to catch."""
    text = "### Task 3: does the thing\n"
    assert _NO_INLINE_CHILD_UNIT_RE.search(text), (
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


# ---------------------------------------------------------------------------
# The spec template's `## Maturity` section: placement, grammar comment, and a
# grammar reminder that survives even once that comment is stripped.
# ---------------------------------------------------------------------------


def _spec_template_text() -> str:
    return (TEMPLATES_DIR / "spec.md").read_text(encoding="utf-8")


def test_spec_template_maturity_is_a_sibling_directly_after_objectives_before_acceptance_criteria():
    """The assumption-prover proved all four probed placements inert; the template fixes
    the one that matches when the level is known and keeps the section permanently away
    from `## Slices` (appended later, near `## Acceptance Criteria`, never near the top)."""
    text = _spec_template_text()
    objectives_at = text.index("## Objectives")
    maturity_at = text.index("## Maturity")
    ac_at = text.index("## Acceptance Criteria")
    assert objectives_at < maturity_at < ac_at, (
        "`## Maturity` must sit directly after `## Objectives` and before "
        f"`## Acceptance Criteria`: objectives={objectives_at} maturity={maturity_at} "
        f"ac={ac_at}"
    )


def _maturity_section_text() -> str:
    text = _spec_template_text()
    start = text.index("## Maturity")
    end = text.index("## Acceptance Criteria")
    return text[start:end]


def test_spec_template_maturity_comment_states_closed_vocabulary_and_grammar():
    section = _maturity_section_text()
    assert re.search(r"\bprototype\b", section)
    assert re.search(r"\bearly\b", section)
    assert re.search(r"\bproduction\b", section)
    assert re.search(r"-\s*<[^>]*member[^>]*>\s*:\s*<[^>]*level[^>]*>", section), (
        "the `## Maturity` section comment must state the per-line grammar "
        f"(`- <member-name>: <level>`): {section!r}"
    )


def test_spec_template_maturity_comment_states_the_slices_sibling_constraint():
    """The narrow guard the prover's hard-constraint finding requires: the template's own
    comment must state that `## Maturity` must never be nested inside `## Slices`."""
    section = _maturity_section_text()
    assert re.search(r"Slices", section), (
        f"the `## Maturity` comment must mention `## Slices` by name: {section!r}"
    )
    assert re.search(r"never|must not|not nested|sibling", section, re.IGNORECASE), (
        f"the `## Maturity` comment must state the sibling/never-nested constraint: {section!r}"
    )


def test_spec_template_maturity_grammar_reminder_survives_stripping_the_pre_fill_comment():
    """A template comment is stripped once an author fills in a section — established
    convention this template already relies on elsewhere. The vocabulary reminder must
    still be legible in the rendered section after that strip, not only in the
    pre-fill comment, so an operator hand-correcting a stamp months later doesn't have
    to go find the reader's source to learn what they may type."""
    section = _maturity_section_text()
    comments = re.findall(r"<!--.*?-->", section, re.DOTALL)
    assert len(comments) >= 2, (
        "the `## Maturity` section must carry a full instructional comment plus a "
        f"separate, shorter reminder comment that survives the former's removal: {section!r}"
    )
    # Simulate an author stripping only the first (longest) pre-fill comment, as the
    # rest of this template's sections are conventionally treated once filled in.
    pre_fill_comment = max(comments, key=len)
    stripped = section.replace(pre_fill_comment, "")
    assert re.search(r"\bprototype\b", stripped)
    assert re.search(r"\bearly\b", stripped)
    assert re.search(r"\bproduction\b", stripped), (
        "the closed vocabulary must remain legible in the section even after the "
        f"pre-fill comment is stripped: {stripped!r}"
    )
