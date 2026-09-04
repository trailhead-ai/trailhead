"""The six blocked-path write repetitions in `execute.md` collapse to one.

`execute.md` used to restate the whole blocked-path write clause — the
`--diff` form, the unified-diff-appends requirement, the warning that bare
stdin is a full-body replace that destroys the record, and the Phase 5/6
pointers — verbatim at six escalation sites. This suite pins the collapse
structurally: the clause exists at most once in the document, each site still
links to wherever it now lives, and `reference_depth_gate.py` sees exactly the
sites this task did not touch.

It asserts no rewording of the clause itself — only that it is not repeated,
and that the six sites still point somewhere.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
EXECUTE_MD = (
    REPO_ROOT / "plugins" / "craft" / "skills" / "_shared" / "execute.md"
)
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "reference_depth_gate.py"

# Captured verbatim from `execute.md` before the collapse — the load-bearing
# tail every one of the six sites carried identically: the unified-diff
# requirement, the full-body-replace warning, and the Phase 5/6 pointers.
# This is a one-time move check, not a permanent prose pin — deliberately not
# reused anywhere the collapse itself doesn't need it.
BLOCKED_PATH_CLAUSE = (
    "piping a unified diff that **appends** the blocked note (bare stdin is "
    "a full-body replace and would destroy the record) — plus the "
    "[Phase 5](#phase-5-flow-out) scrub and, if commits exist, the "
    "[Phase 6](#phase-6-close-and-completion-report) blocked-path push and "
    "its standalone `craft/branch` write; body-content contract and full "
    "rules in `../_shared/status-ownership.md`."
)


def _text() -> str:
    return EXECUTE_MD.read_text(encoding="utf-8")


def gate(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


def test_blocked_path_clause_appears_at_most_once():
    """The six verbatim repetitions collapse to a single canonical statement."""
    text = _text()
    count = text.count(BLOCKED_PATH_CLAUSE)
    assert count <= 1, (
        f"expected the blocked-path clause at most once in execute.md, found "
        f"it {count} times — the six sites have not collapsed"
    )


def test_six_trigger_sites_link_to_the_canonical_section():
    """Every site that used to carry the clause still points at it."""
    text = _text()
    anchors = text.count("(#blocked-path-write)")
    assert anchors >= 5, (
        f"expected at least 5 links to the canonical section, found {anchors}"
    )


def test_reference_depth_gate_sees_only_the_untouched_sites():
    """The collapse removes six `status-ownership.md` sites and the canonical
    section adds exactly one back — the gate must see the net of that, not a
    new reference introduced by the anchors or the section itself."""
    result = gate(EXECUTE_MD)
    assert result.returncode == 1, (
        f"expected findings to remain (this task doesn't clear all of them):\n"
        f"{result.stderr}"
    )
    lines = [ln for ln in result.stderr.splitlines() if ": reason:" in ln]
    flagged_line_numbers = {ln.split("line ")[1].split(" ")[0] for ln in lines}
    assert len(flagged_line_numbers) == 9, (
        f"expected exactly 9 flagged lines in execute.md, found "
        f"{len(flagged_line_numbers)}:\n{result.stderr}"
    )
    assert len(lines) == 12, (
        f"expected exactly 12 mentions in execute.md, found {len(lines)}:\n"
        f"{result.stderr}"
    )
    by_target = Counter(ln.rsplit(" ", 1)[-1] for ln in lines)
    status_ownership_count = by_target["'status-ownership.md'"]
    assert status_ownership_count == 5, (
        f"expected exactly 5 remaining status-ownership.md mentions (the "
        f"four untouched sites plus the one new canonical-section mention), "
        f"found {status_ownership_count}:\n{result.stderr}"
    )
