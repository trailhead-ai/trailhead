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

**The attribution-shaped relation above cannot see a stale *dispatch*.**
`_rule_source_pattern` only matches "codifies" and "'s ...rule" constructions.
A point-of-use dispatch reads differently — "run the text through the
credential-pattern scrub … (`_shared/execute.md`, [Phase
5](../_shared/execute.md#phase-5-flow-out))" — naming the scrub procedure by
name and pointing the reader at `execute.md`'s numbered phase for it, with no
"codifies" or possessive noun phrase anywhere in the unit. That shape passed
this suite fully green while `drive/SKILL.md` carried four such leftovers
after the migration, because Phase 5 no longer holds the pattern list and,
at three of those four sites, no other citation earlier in the document had
introduced `security.md` yet either. A second, independent scan below closes
this gap: any unit that names the credential-pattern scrub procedure *and*
still cites `execute.md`, without also citing `security.md` in the same unit,
is a stale dispatch — the scrub only lives in `security.md` now, so a
same-unit citation of `execute.md` alone for it is never correct, regardless
of grammatical shape.
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
    _regex_bearing_skills,
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

# The mark of a *dispatch* to the credential-pattern scrub, as opposed to an
# attribution — the same substring `test_security_point_of_use_citations.py`
# uses to derive its own dispatch-site set, reused here so both suites read
# one definition of "this unit names the scrub procedure."
_SCRUB_PROCEDURE_MARKER = "credential-pattern scrub"


def _scrub_dispatch_stale_to_execute_md_sites() -> list[tuple[str, str]]:
    """Units that name the credential-pattern scrub procedure and still cite
    `execute.md`, without also citing `security.md` in the same unit — a
    dispatch left pointing at the file the scrub no longer lives in. Derived
    independently of `_rule_source_pattern`, so narrowing that grammar cannot
    narrow this scan along with it."""
    hits: list[tuple[str, str]] = []
    for path in _skill_md_files():
        text = path.read_text(encoding="utf-8")
        for unit in _units(text):
            unit_lower = unit.lower()
            if (
                _SCRUB_PROCEDURE_MARKER in unit_lower
                and "execute.md" in unit
                and "security.md" not in unit
            ):
                hits.append((path.parent.name, unit))
    return hits


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


def test_live_security_md_attribution_covers_every_regex_bearing_skill():
    """Coverage guard: the non-vacuity check above (>=1 live citation anywhere)
    can stay green while a narrowed `_rule_source_pattern` alternative drops
    most skills' citations out of the scan — exactly the hole this suite
    inherits from `test_security_attribution_citations.py` by sharing its
    pattern. Every skill that inlines the safe-value regex must have a live
    `_shared/security.md` rule-source citation, derived independently of the
    pattern being checked."""
    attributed_skills: set[str] = set()
    for path in _skill_md_files():
        text = path.read_text(encoding="utf-8")
        for unit in _units(text):
            if _LIVE_SECURITY_MD_ATTRIBUTION.search(unit):
                attributed_skills.add(path.parent.name)
    missing = _regex_bearing_skills() - attributed_skills
    assert not missing, (
        "these skills inline the safe-value regex but no live rule-source citation of "
        f"`_shared/security.md` was found for them: {sorted(missing)} — the rule-source "
        "relation may have been narrowed"
    )


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


@pytest.mark.parametrize("path", _skill_md_files(), ids=lambda p: p.parent.name)
def test_scrub_dispatch_does_not_still_point_at_execute_md(path):
    """A unit that names the credential-pattern scrub procedure and cites
    `execute.md` for it, without also citing `security.md`, is a stale
    dispatch — a shape the attribution-only grammar above cannot see.

    Parametrized over the whole corpus rather than over the derived stale-site
    set: a set-parametrized version reports an empty parameter set as a skip on
    a clean tree, so narrowing the derivation would silently stop testing
    instead of failing."""
    text = path.read_text(encoding="utf-8")
    stale = [
        unit
        for unit in _units(text)
        if _SCRUB_PROCEDURE_MARKER in unit.lower()
        and "execute.md" in unit
        and "security.md" not in unit
    ]
    assert not stale, (
        f"{path.parent.name}/SKILL.md names the credential-pattern scrub and still "
        f"points at `execute.md` for it (no `security.md` citation in the same unit): "
        f"{stale!r} — repoint this dispatch to `_shared/security.md`"
    )
