"""Audit: no lore-tree prose or source reference to the retired `backlog`/
`plan` kinds or the retired `tracking` status outside historical changelogs.

Both kinds were unified into the single `task` kind (``model.py`` ``KINDS``);
`tracking` became the `task` status `blocked`. This module is a scripted grep
over the lore agent/skill/doc prose surface plus the lore package source,
asserting zero residual references.

Scope is deliberately PROSE + SOURCE, not tests/: several pre-existing test
files use `kind="plan"`/`kind="backlog"` as arbitrary fixture literals in code
paths that never validate against `model.KINDS` (KQL parsing, vault-routing
config, index projection/benchmark fixtures) — cleaning those up is left for a
later repo-wide sweep. Scanning tests/ would also require allowlisting
unrelated content (dev-process "plan" terminology in
test_no_dev_process_refs.py, session `--phase Plan` examples) that would
dilute this audit's signal for no regression-safety gain.
"""
from __future__ import annotations

import re
from pathlib import Path

LORE_ROOT = Path(__file__).parent.parent  # tools/lore
PLUGIN_ROOT = LORE_ROOT / "plugins" / "lore"

# Prose files this slice's Delivers named explicitly.
PROSE_FILES = [
    PLUGIN_ROOT / "agents" / "librarian.md",
    PLUGIN_ROOT / "agents" / "researcher.md",
    PLUGIN_ROOT / "agents" / "investigator.md",
    PLUGIN_ROOT / "skills" / "research" / "SKILL.md",
    PLUGIN_ROOT / "skills" / "record" / "SKILL.md",
    PLUGIN_ROOT / "skills" / "flush" / "SKILL.md",
    LORE_ROOT / "README.md",
    LORE_ROOT / "ROADMAP.md",
    LORE_ROOT / "docs" / "DEGRADATION.md",
]


def _source_files():
    """Every lore package source file (excludes __pycache__)."""
    for p in (PLUGIN_ROOT / "lore").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p
    yield PLUGIN_ROOT / "cli" / "lore"
    yield PLUGIN_ROOT / "_bootstrap.py"


# (relative-to-PLUGIN_ROOT path, reason) — each entry must still contain a hit
# (see the companion honesty test below) so a stale entry can't rot here
# silently once its false positive is cleaned up.
ALLOWLIST: dict[str, str] = {
    "lore/record/model.py": (
        "Historical unification note ('task unifies the former "
        "backlog/plan kinds ... worth tracking to completion') documenting "
        "already-shipped model behavior, not a residual reference."
    ),
    "lore/record/store.py": (
        "'order tracking' — unrelated English usage in the patch-hunk "
        "algorithm's offset bookkeeping, not the retired backlog status."
    ),
}

# Word-bounded and case-sensitive: catches every real kind/status reference
# this codebase writes lowercase (`backlog`, kind:plan, related.plan, ...)
# while never matching "planning"/"complaint" (no boundary mid-word) or a
# capitalized "Plan" used as a session --phase example (different concept).
_WORD_RE = re.compile(r"\b(backlog|tracking|plan)\b")


def _hits(text: str) -> list[str]:
    return sorted(set(_WORD_RE.findall(text)))


class TestNoLegacyKindOrStatusReferences:
    def test_prose_files_clean(self):
        violations = []
        for path in PROSE_FILES:
            hits = _hits(path.read_text())
            if hits:
                violations.append(f"{path.relative_to(LORE_ROOT)}: {hits}")
        assert not violations, (
            "Retired-kind/status references found in lore prose (the "
            "backlog/plan kinds and tracking status were unified into "
            "task/blocked):\n  " + "\n  ".join(violations)
        )

    def test_source_files_clean(self):
        violations = []
        for path in _source_files():
            rel = str(path.relative_to(PLUGIN_ROOT))
            if rel in ALLOWLIST:
                continue
            hits = _hits(path.read_text())
            if hits:
                violations.append(f"{rel}: {hits}")
        assert not violations, (
            "Retired-kind/status references found in lore source:\n  "
            + "\n  ".join(violations)
        )

    def test_allowlist_entries_are_real_and_still_hit(self):
        """Guard against a stale allowlist entry outliving its false positive."""
        for rel in ALLOWLIST:
            path = PLUGIN_ROOT / rel
            assert path.exists(), f"allowlisted file missing: {rel}"
            assert _hits(path.read_text()), (
                f"allowlist entry {rel!r} no longer has a match — remove the "
                "stale entry"
            )
