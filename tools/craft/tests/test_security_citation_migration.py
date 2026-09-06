"""No craft skill still credits `execute.md` as the source of a security rule.

`execute/SKILL.md` and `drive/SKILL.md` legitimately read `_shared/execute.md`
end to end — they run its build-loop controller, and each names the filename
dozens of times as the thing to read, dispatch to, or follow through a
specific numbered phase. None of that is a security-rule citation, and a
per-file exclusion that skips these two skills' `SKILL.md` entirely cannot
tell the difference between those legitimate reads and an actual leftover
citation sitting among them — which is exactly how `drive/SKILL.md`'s own
attribution at its step-1 shape check went unrepointed by the migration this
suite is meant to guard.

So this suite scans every skill, `drive` and `execute` included, for the same
structural relation `test_security_attribution_citations.py` defines: a
shared-document token cited as the *source* of a rule — the subject of
"codifies", or the possessor of an "untrusted-*-rule" noun phrase — as opposed
to the *target* of a read/dispatch verb or a numbered-phase reference. Every
citing surface's rule-source citation must repoint to `_shared/security.md`,
the document those rules now live in on their own; none may still name
`execute.md` in that role, regardless of which skill it appears in.

This suite derives the site set empirically, never from a hardcoded count or
list of citing filenames — a scan that matched nothing would report clean
while proving nothing, so a non-vacuity guard covers both the whole-corpus
scan and the pattern's own discriminating power.
"""

from __future__ import annotations

import pytest

# The rule-source relation, the unit splitter, and the corpus enumeration are
# the same ones test_security_attribution_citations.py defines, so the two
# suites read one grammar of "cited as the source of a rule" rather than two
# hand-copied ones that can drift apart. Deliberately NOT imported: that
# suite's `_ATTRIBUTION_PATTERN` — this suite narrows the same relation to one
# document at a time, which is the distinction it exists to draw.
from test_security_attribution_citations import (
    SKILLS,
    _rule_source_pattern,
    _skill_md_files,
    _units,
)

# Any unit citing `execute.md` in the rule-source role is a leftover citation
# this migration must have repointed.
_STALE_EXECUTE_MD_ATTRIBUTION = _rule_source_pattern("execute")

# Proves the relation actually discriminates: it must still find genuine
# rule-source citations of `security.md`, so a pattern that matches nothing
# (e.g. a typo'd regex) is not mistaken for a clean scan.
_LIVE_SECURITY_MD_ATTRIBUTION = _rule_source_pattern("security")


def test_skill_md_corpus_is_non_empty():
    """Non-vacuity guard on the whole-corpus scan below."""
    assert _skill_md_files(), f"no SKILL.md found under {SKILLS}"


def test_pattern_still_finds_live_security_md_attributions():
    """Non-vacuity guard on the pattern itself: it must still match the
    genuine post-migration citations, or a clean scan below would be
    indistinguishable from a broken, always-empty pattern."""
    hits = []
    for path in _skill_md_files():
        text = path.read_text(encoding="utf-8")
        for unit in _units(text):
            if _LIVE_SECURITY_MD_ATTRIBUTION.search(unit):
                hits.append((path.parent.name, unit))
    assert hits, "expected at least one live rule-source citation of `_shared/security.md`"


@pytest.mark.parametrize("path", _skill_md_files(), ids=lambda p: p.parent.name)
def test_skill_does_not_credit_execute_md_as_rule_source(path):
    text = path.read_text(encoding="utf-8")
    for unit in _units(text):
        match = _STALE_EXECUTE_MD_ATTRIBUTION.search(unit)
        assert not match, (
            f"{path.parent.name}/SKILL.md still credits `execute.md` as the source of a "
            f"security rule ({match.group(0)!r}) — its citation must repoint to "
            "`_shared/security.md`"
        )
