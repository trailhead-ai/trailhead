"""Point-of-use citations to `_shared/security.md` are unconditional.

`gauntlet/SKILL.md`, `slice/SKILL.md`, and `drive/SKILL.md` dispatch to the
credential-pattern scrub at the point they actually need it applied — a
genuinely different citation shape from an attribution that merely credits
where a rule is stated. Each such dispatch must be a plain, unconditional
directive: read the document now and follow it in full. Never a branch,
never "consult if relevant", never conditional dispatch — that shape
measured at 6/26 against 9/9 for the unconditional one.

This suite asserts the *structural* property only — the citing unit is not
governed by a conditional-dispatch marker — never a particular phrasing. It
derives the site set empirically: it scans every skill's `SKILL.md`, `drive`
and `execute` included (a per-file exclusion would only hide a genuine site
sitting among their legitimate build-procedure reads, the way one previously
hid `drive/SKILL.md`'s unrepointed attribution — see
`test_security_citation_migration.py`), for a unit (a paragraph, or a single
list item inside one) that names `security.md` alongside the phrase
"credential-pattern scrub", the mark of a dispatch rather than an attribution
(which cites the rule without naming the scrub procedure itself). Never a
hardcoded file or line list. A non-vacuity guard covers the derived set, so a
scan that matches nothing does not report clean.

**A bare "matches anything, anywhere" non-vacuity check is not enough.**
`execute/SKILL.md` names `security.md` alongside "credential-pattern scrub"
in its own read-directive paragraph — a promoted-dependency mention that
reads all five `_shared` documents together, not a per-site dispatch fired
at the point the scrub is actually applied. That single unit alone satisfies
a bare non-vacuity guard, so `gauntlet/SKILL.md`'s, `slice/SKILL.md`'s, and
`drive/SKILL.md`'s real dispatch sites — the ones this module actually
exists to pin — could all revert to attribution or disappear entirely and
the suite would still report clean.

**A bare per-skill membership check is not enough either.** `drive/SKILL.md`
carries three independent dispatch sites (its step-1 shape check, its
slice-ritual append, and its PR-tail secret scan). A check that only asks
"does `drive` have at least one surviving dispatch" cannot distinguish all
three surviving from two of the three being silently deleted — exactly the
defect a prior review round introduced and every citation suite missed. The
coverage guard below closes both holes: it requires an imperative "read
`_shared/security.md` now" directive — the mark of a genuine per-site
dispatch, distinct from a promoted-dependency mention that merely lists the
document among several to read — and counts them per skill against a pinned
expected count, so a deleted site is caught even when its siblings in the
same file survive.
"""

from __future__ import annotations

import re

import pytest

# The unit splitter and the corpus enumeration are the same ones
# test_security_attribution_citations.py defines, so both citation shapes are
# read out of one segmentation of the prose rather than two hand-copied ones
# that can drift apart.
from test_security_attribution_citations import _skill_md_files, _units

# A directive is conditional if its unit is governed by one of these — a
# branch, a hedge, or an aside that makes reading the citation optional.
_CONDITIONAL_MARKERS = re.compile(
    r"\b(if|unless|consult|when relevant|as needed|may need|optionally)\b",
    re.IGNORECASE,
)

# The mark of a genuine per-site dispatch, as opposed to a promoted-dependency
# mention like execute/SKILL.md's "Read all five and follow `execute.md` end
# to end" paragraph, which names `security.md` alongside the scrub phrase
# without ever directing the reader to open `security.md` itself, now, on its
# own.
_DISPATCH_DIRECTIVE = re.compile(r"Read\s+`(?:\.\./)?_shared/security\.md`\s+now")

# The three skills this corpus's own module docstring names as genuine
# point-of-use dispatch sites, and how many independent dispatch sites each
# one carries — see `test_security_point_of_use_citations.py` module
# docstring and `test_security_citation_migration.py`'s account of
# `drive/SKILL.md`'s unrepointed attribution. `drive/SKILL.md` dispatches at
# three separate points of use (its step-1 shape check, its slice-ritual
# append, and its PR-tail secret scan); a per-skill "at least one survives"
# check cannot tell two of those three being silently deleted from all three
# surviving, so counts are pinned per skill, not membership in a set — a
# count is what actually proves nothing was deleted.
_KNOWN_DISPATCH_SITE_COUNTS = {"gauntlet": 1, "slice": 2, "drive": 3}


def _point_of_use_sites() -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for path in _skill_md_files():
        text = path.read_text(encoding="utf-8")
        for unit in _units(text):
            if "security.md" in unit and "credential-pattern scrub" in unit.lower():
                hits.append((path.parent.name, unit))
    return hits


def _genuine_dispatch_sites() -> list[tuple[str, str]]:
    """The subset of `_point_of_use_sites()` that is an actual per-site
    dispatch — an imperative "read `_shared/security.md` now" directive —
    rather than a promoted-dependency mention that merely lists the document
    among several to read."""
    return [
        (name, unit)
        for name, unit in _point_of_use_sites()
        if _DISPATCH_DIRECTIVE.search(unit)
    ]


def test_point_of_use_site_set_is_non_empty():
    """Non-vacuity guard: a scan matching nothing must not report clean."""
    sites = _point_of_use_sites()
    assert sites, (
        "expected at least one point-of-use citation to `security.md` — a unit "
        "naming it alongside 'credential-pattern scrub'"
    )


def test_genuine_dispatch_sites_cover_the_known_dispatch_skills():
    """Coverage guard: the bare non-vacuity check above can be satisfied by a
    single promoted-dependency mention anywhere in the corpus — including
    execute/SKILL.md's own "read all five" paragraph — so gauntlet's,
    slice's, and drive's real dispatch sites could all revert to attribution,
    or vanish outright, and the suite above would still report clean.

    A per-skill "at least one survives" membership check is not enough
    either: `drive/SKILL.md` carries three independent dispatch sites, and
    deleting any one or two of them (a *missing* dispatch, as opposed to a
    stale or misdirected one) leaves `drive` still present in a bare
    membership set — exactly the gap that let a prior round delete
    `drive/SKILL.md`'s step-1 dispatch while every citation suite stayed
    green. This counts each known skill's genuine dispatch sites and
    requires the full pinned count, so a deleted site fails here even when
    its siblings in the same file survive."""
    counts: dict[str, int] = {}
    for name, _ in _genuine_dispatch_sites():
        counts[name] = counts.get(name, 0) + 1
    shortfalls = {
        name: (expected, counts.get(name, 0))
        for name, expected in _KNOWN_DISPATCH_SITE_COUNTS.items()
        if counts.get(name, 0) < expected
    }
    assert not shortfalls, (
        "expected an imperative 'Read `_shared/security.md` now' dispatch directive "
        f"at every known site, but found fewer than expected (skill: (expected, found)): "
        f"{shortfalls} — a point-of-use dispatch reverted to attribution, or was deleted"
    )


@pytest.mark.parametrize(
    "skill_name,unit",
    _point_of_use_sites(),
    ids=[f"{name}[{i}]" for i, (name, _) in enumerate(_point_of_use_sites())],
)
def test_point_of_use_citation_is_unconditional(skill_name, unit):
    match = _CONDITIONAL_MARKERS.search(unit)
    assert not match, (
        f"{skill_name}/SKILL.md's point-of-use citation to `security.md` reads as "
        f"conditional dispatch ({match.group(0)!r}), not a plain unconditional directive"
    )
