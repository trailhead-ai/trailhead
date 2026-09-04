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

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
EXECUTE_MD = (
    REPO_ROOT / "plugins" / "craft" / "skills" / "_shared" / "execute.md"
)

_HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.MULTILINE)


def _slugify(title: str) -> str:
    """GitHub-flavored heading slug: lowercase, strip everything but
    letters/digits/hyphen/underscore/space, spaces become hyphens."""
    slug = re.sub(r"[^\w\- ]", "", title.lower())
    return slug.strip().replace(" ", "-")

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
    verbatim, converting this into a prose pin). `toc_gate.py` does NOT hold
    that presence property — it only compares the derived section list
    against the document's own declared contents block, so deleting the
    `## Blocked-Path Write` heading together with its `- Blocked-Path Write`
    contents entry leaves both sides agreeing and the gate green. Presence is
    pinned separately below, by
    `test_canonical_section_heading_exists_and_matches_the_anchors` — this
    test's job is only to catch a reappearing duplicate.
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


def test_canonical_section_heading_exists_and_matches_the_anchors():
    """The `(#blocked-path-write)` anchors above must point somewhere real.

    Structural, not a prose pin: this asserts a heading exists whose slug is
    `blocked-path-write`, never the section's body wording. Without this, the
    heading and its contents-block entry could both be deleted together
    (`toc_gate.py` stays green on that, per the docstring above) and every
    trigger site would dangle silently.
    """
    text = _text()
    slugs = {_slugify(title) for title in _HEADING_RE.findall(text)}
    assert "blocked-path-write" in slugs, (
        "expected a heading in execute.md whose slug is 'blocked-path-write' "
        f"— found heading slugs {sorted(slugs)}"
    )
