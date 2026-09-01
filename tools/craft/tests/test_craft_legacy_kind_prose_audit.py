"""Audit: no craft prose references the retired `backlog`/`plan` record kinds or the
old slice-in-plan-body mechanics.

The record model unified the `backlog` and `plan` kinds into a single `task` kind, and
plans are now a parent `task` record whose slices are separate child `task` records (wired
via `--parent`/`--depends-on`) rather than `### Slice` sub-sections in a `plan`-record body.
This audit is the machine-checkable gate that the craft agent/skill/template/doc surface
stays in agreement with that model.

Scanned surface: the whole shipped craft plugin tree (agents, skills, templates, docs).
Forbidden patterns:
  - `--kind backlog` / `--kind plan` — the retired create-command kinds.
  - `kind:backlog` / `kind:plan` — the retired search-query kinds.
  - `### Task` or `### Slice` (or deeper) headings inside `templates/plan.md` — the child unit
    is its own record, never a sub-section of the parent body.

Write BEFORE the prose migration — this test must fail RED first, then green after.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CRAFT_PLUGIN = Path(__file__).parent.parent / "plugins" / "craft"

# Retired create/search kind tokens. Word-boundary-anchored so `--kind task` and general
# English ("the plan", "planning") never trip — only the literal retired-kind selectors do.
_FORBIDDEN_KIND_RE = re.compile(r"(?:--kind|kind:)\s*(?:backlog|plan)\b")
_NO_INLINE_CHILD_UNIT_RE = re.compile(r"(?im)^\s*#{2,}\s+(?:task|slice)\b")


def _prose_files() -> list[Path]:
    """Every shipped markdown file under the craft plugin (agents / skills / templates / docs)."""
    return sorted(
        p
        for p in CRAFT_PLUGIN.rglob("*.md")
        if "__pycache__" not in p.parts
    )


@pytest.mark.parametrize("md", _prose_files(), ids=lambda p: str(p.relative_to(CRAFT_PLUGIN)))
def test_no_retired_kind_reference(md: Path):
    """No craft prose may select the retired `backlog`/`plan` record kinds."""
    hits = _FORBIDDEN_KIND_RE.findall(md.read_text())
    assert not hits, (
        f"{md.relative_to(CRAFT_PLUGIN)} references a retired record kind "
        f"({sorted(set(hits))}). The `backlog` and `plan` kinds were unified into `task`; "
        "use `--kind task` / `kind:task`."
    )


def test_plan_template_has_no_slice_body_mechanics():
    """The parent-task template must not carry `### Task`/`### Slice` sub-sections — each is now a
    child `task` record, not a heading in the parent body."""
    text = (CRAFT_PLUGIN / "templates" / "plan.md").read_text()
    assert not _NO_INLINE_CHILD_UNIT_RE.search(text), (
        "templates/plan.md still carries inlined child-unit body mechanics; each is a separate child "
        "`task` records wired via `--parent`/`--depends-on`."
    )


def test_plan_template_guard_catches_inline_task_heading():
    """The child-unit is spelled `task` under current vocabulary — inlining it as a `### Task`
    sub-heading in the parent plan body is the same anti-pattern the guard exists to catch."""
    text = "### Task 3: does the thing\n"
    assert _NO_INLINE_CHILD_UNIT_RE.search(text), (
        "guard must catch a `### Task` heading, not just the retired `### Slice` spelling"
    )
