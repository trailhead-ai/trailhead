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

# Captured verbatim from `execute.md` — the load-bearing tail every one of the
# six sites carried identically: the unified-diff requirement, the
# full-body-replace warning, and the Phase 5/6 pointers. A later task reworded
# the trailing clause to drop its `status-ownership.md` path (the document is
# now read unconditionally by every skill that reads this procedure), so the
# captured text tracks that rewording — this is a one-time move-and-reword
# check, not a permanent prose pin, deliberately not reused anywhere the
# collapse itself doesn't need it.
BLOCKED_PATH_CLAUSE = (
    "piping a unified diff that **appends** the blocked note (bare stdin is "
    "a full-body replace and would destroy the record) — plus the "
    "[Phase 5](#phase-5-flow-out) scrub and, if commits exist, the "
    "[Phase 6](#phase-6-close-and-completion-report) blocked-path push and "
    "its standalone `craft/branch` write; body-content contract and full "
    "rules govern this write, exactly as they govern every other status "
    "write."
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
    """The collapse removed six `status-ownership.md` sites and the canonical
    section added exactly one back, leaving nine flagged lines. A later task
    promoted the three genuinely-needed documents into the reading skills'
    reference sets and reworded every one of those nine lines except one — the
    gate must see the net of that: a single surviving mention, the bare
    `refine.md` comparison the promotion task does not own."""
    result = gate(EXECUTE_MD)
    assert result.returncode == 1, (
        f"expected one finding to remain (the bare refine.md mention):\n"
        f"{result.stderr}"
    )
    lines = [ln for ln in result.stderr.splitlines() if ": reason:" in ln]
    flagged_line_numbers = {ln.split("line ")[1].split(" ")[0] for ln in lines}
    assert flagged_line_numbers == {"7"}, (
        f"expected only line 7 flagged in execute.md, found "
        f"{flagged_line_numbers}:\n{result.stderr}"
    )
    assert len(lines) == 1, (
        f"expected exactly 1 mention in execute.md, found {len(lines)}:\n"
        f"{result.stderr}"
    )
    by_target = Counter(ln.rsplit(" ", 1)[-1] for ln in lines)
    status_ownership_count = by_target["'status-ownership.md'"]
    assert status_ownership_count == 0, (
        f"expected no remaining status-ownership.md mentions, found "
        f"{status_ownership_count}:\n{result.stderr}"
    )
