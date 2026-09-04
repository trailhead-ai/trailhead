"""The six blocked-path write repetitions in `execute.md` collapse to one.

`execute.md` used to restate the whole blocked-path write clause — the
`--diff` form, the unified-diff-appends requirement, the warning that bare
stdin is a full-body replace that destroys the record, and the Phase 5/6
pointers — verbatim at six escalation sites. This suite pins the collapse
structurally: the clause exists at most once in the document, and each site
still links to wherever it now lives.

It asserts no rewording of the clause itself — only that it is not repeated,
and that the six sites still point somewhere.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
EXECUTE_MD = (
    REPO_ROOT / "plugins" / "craft" / "skills" / "_shared" / "execute.md"
)

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


def test_blocked_path_clause_appears_at_most_once():
    """The six verbatim repetitions collapse to a single canonical statement.

    Deliberately one-sided: `count <= 1` never checks the clause is present
    at all (a `== 1` bound would, but only by pinning the captured string
    verbatim, converting this into a prose pin). That presence property is
    already held elsewhere — `toc_gate.py`'s contents-entry check — so this
    test's job is only to catch a reappearing duplicate, not to also stand
    in for the presence check.
    """
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
    assert anchors >= 6, (
        f"expected at least 6 links to the canonical section, found {anchors}"
    )
