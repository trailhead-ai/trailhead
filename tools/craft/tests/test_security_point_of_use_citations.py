"""Point-of-use citations to `_shared/security.md` are unconditional.

`gauntlet/SKILL.md` and `slice/SKILL.md` dispatch to the credential-pattern
scrub at the point they actually need it applied — a genuinely different
citation shape from an attribution that merely credits where a rule is
stated. Each such dispatch must be a plain, unconditional directive: read the
document now and follow it in full. Never a branch, never "consult if
relevant", never conditional dispatch — that shape measured at 6/26 against
9/9 for the unconditional one.

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


def _point_of_use_sites() -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for path in _skill_md_files():
        text = path.read_text(encoding="utf-8")
        for unit in _units(text):
            if "security.md" in unit and "credential-pattern scrub" in unit.lower():
                hits.append((path.parent.name, unit))
    return hits


def test_point_of_use_site_set_is_non_empty():
    """Non-vacuity guard: a scan matching nothing must not report clean."""
    sites = _point_of_use_sites()
    assert sites, (
        "expected at least one point-of-use citation to `security.md` — a unit "
        "naming it alongside 'credential-pattern scrub'"
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
