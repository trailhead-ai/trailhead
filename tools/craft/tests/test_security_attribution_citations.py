"""Attribution citations keep their inlined regex and repoint to `_shared/security.md`.

`brainstorm/SKILL.md`, `distill/SKILL.md`, `plan/SKILL.md`, `slice/SKILL.md`, and
`drive/SKILL.md` apply the untrusted-value rule unprompted at their own
substitution sites. Most of these sites keep their own inlined copy of the
safe-value regex `^[A-Za-z0-9._/-]+$` rather than dispatching elsewhere to read
it; some cite the rule by name without re-inlining the regex a sibling site in
the same file already stated. Either way, only the attribution — crediting
where the rule is canonically stated — repoints, from `execute.md` to
`security.md`.

An attribution site is not merely a unit that happens to mention a shared
document's filename — `drive/SKILL.md` and `execute/SKILL.md` both name
`_shared/execute.md` dozens of times as the build-loop controller they read
and dispatch to end-to-end, and those mentions must keep passing. What marks a
unit as an attribution, rather than a procedural read or dispatch, is a
grammatical relation: the shared document is cited as the *source* of a named
rule — either as the subject of "codifies" (`` `_shared/<doc>.md` codifies ``)
or as the possessor of an "untrusted-*-rule" noun phrase
(`` `_shared/<doc>.md`'s untrusted-input rule ``). A procedural read or
dispatch instead makes the document the *target* of a verb ("read", "run
through", "routes through", "defers to") or names one of its numbered phases
— never the source of a rule. This is a structural/relational property, not a
phrase pin: it holds regardless of which file the citation sits in, so it
applies uniformly across every skill, `drive` and `execute` included, and
would catch a newly-added attribution anywhere in the corpus.

This suite derives the site set empirically by scanning every
`plugins/craft/skills/*/SKILL.md` for units matching that relation, never from
a hardcoded file or line list. A bare "the derived set is non-empty" guard is
not enough — narrowing the relation (dropping one of its two alternatives, say)
can silently shrink the site set from many skills down to one and still pass
that guard, since one surviving site still satisfies "non-empty". The coverage
guard below closes that hole: it independently derives, by a plain substring
scan that shares no regex with `_RULE_SOURCE_ROLE`, the set of skills that
inline the safe-value regex `^[A-Za-z0-9._/-]+$` at all, and requires every one
of those skills to have at least one rule-source attribution site. A relation
narrowed enough to drop a whole skill out of the derived set — exactly the
failure mode a bare non-vacuity check misses — now fails this guard by name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SKILLS = REPO_ROOT / "plugins" / "craft" / "skills"

# A shared-document token is an attribution *source* when it is the subject of
# "codifies" or the possessor of an "untrusted-*-rule" noun phrase — the two
# constructions this corpus uses to credit a document as where a rule is
# canonically stated. Naming the document as the *target* of a read/dispatch
# verb, or alongside a numbered phase anchor, never matches this pattern.
_RULE_SOURCE_ROLE = r"(?:'s\s+[\w-]*untrusted[\w-]*\s+rule|\s+codifies)"


def _rule_source_pattern(document: str) -> re.Pattern[str]:
    """A pattern matching `document` cited in the rule-source role, where
    `document` is the `_shared/<name>.md` stem or an alternation of stems."""
    return re.compile(rf"`(?:\.\./)?_shared/{document}\.md`" + _RULE_SOURCE_ROLE)


_ATTRIBUTION_PATTERN = _rule_source_pattern("(?:execute|security)")

# The literal safe-value regex this corpus's untrusted-vault-value rule requires
# at each substitution site. A skill that inlines this string is, by the
# corpus's own convention, applying the rule and must attribute it somewhere in
# the same file. Matched by plain substring search — no dependency on
# `_RULE_SOURCE_ROLE` or `_ATTRIBUTION_PATTERN` — so narrowing the rule-source
# relation cannot narrow this reference set along with it.
_SAFE_VALUE_REGEX_LITERAL = r"^[A-Za-z0-9._/-]+$"


def _units(text: str) -> list[str]:
    """Blank-line-delimited blocks, further split at each new bullet or
    numbered list item, so two adjacent list items under one intro paragraph
    are checked as separate units rather than one combined block."""
    blocks = re.split(r"\n\s*\n", text)
    units: list[str] = []
    for block in blocks:
        items = re.split(r"\n(?=[-*] |\d+\. )", block)
        units.extend(items)
    return units


def _skill_md_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def _attribution_sites() -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for path in _skill_md_files():
        text = path.read_text(encoding="utf-8")
        for unit in _units(text):
            if _ATTRIBUTION_PATTERN.search(unit):
                hits.append((path.parent.name, unit))
    return hits


def _regex_bearing_skills() -> set[str]:
    """Skills whose SKILL.md inlines the safe-value regex literally, derived by
    a plain substring scan independent of `_ATTRIBUTION_PATTERN`."""
    return {
        path.parent.name
        for path in _skill_md_files()
        if _SAFE_VALUE_REGEX_LITERAL in path.read_text(encoding="utf-8")
    }


def test_regex_bearing_skill_set_is_non_empty():
    """Non-vacuity guard on the reference set: if it were empty, the coverage
    assertion below would pass trivially and prove nothing."""
    assert _regex_bearing_skills(), (
        f"expected at least one skill to inline the safe-value regex "
        f"{_SAFE_VALUE_REGEX_LITERAL!r}"
    )


def test_every_regex_bearing_skill_has_an_attribution_site():
    """Coverage guard: every skill that inlines the safe-value regex must have
    at least one rule-source attribution site, so a relation narrowed enough to
    drop a skill's citations out of the derived set is caught by name — not
    masked by some other skill's site keeping the set merely non-empty."""
    attributed_skills = {name for name, _ in _attribution_sites()}
    missing = _regex_bearing_skills() - attributed_skills
    assert not missing, (
        "these skills inline the safe-value regex but the rule-source scan found no "
        f"attribution site for them: {sorted(missing)} — the rule-source relation may "
        "have been narrowed"
    )


@pytest.mark.parametrize(
    "skill_name,unit",
    _attribution_sites(),
    ids=[f"{name}[{i}]" for i, (name, _) in enumerate(_attribution_sites())],
)
def test_attribution_site_cites_security_md_not_execute_md(skill_name, unit):
    assert "security.md" in unit, (
        f"{skill_name}/SKILL.md's attribution site keeps the inlined regex but does not "
        "credit `_shared/security.md` as the rule's canonical statement"
    )
    assert "execute.md" not in unit, (
        f"{skill_name}/SKILL.md's attribution site still credits `execute.md` instead of "
        "repointing to `_shared/security.md`"
    )
