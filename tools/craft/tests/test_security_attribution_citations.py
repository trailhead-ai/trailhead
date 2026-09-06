"""Attribution citations keep their inlined regex and repoint to `_shared/security.md`.

`brainstorm/SKILL.md`, `distill/SKILL.md`, `plan/SKILL.md`, and `slice/SKILL.md`
apply the untrusted-value rule unprompted at their own substitution sites. Most
of these sites keep their own inlined copy of the safe-value regex
`^[A-Za-z0-9._/-]+$` rather than dispatching elsewhere to read it; some cite the
rule by name without re-inlining the regex a sibling site in the same file
already stated. Either way, only the attribution — crediting where the rule is
canonically stated — repoints, from `execute.md` to `security.md`.

This suite derives the site set empirically: it scans every migrated skill for
a unit that names the shared document (`execute.md` before this migration,
`security.md` after) without naming the credential-pattern scrub procedure —
the mark of an attribution site, as opposed to a point-of-use dispatch. A unit
naming neither document is an unrelated local shape check (slice's own
`--covers` value validation reuses the phrase "safe-value shape" for a
different regex entirely) and is correctly excluded. Never a hardcoded file or
line list. A non-vacuity guard covers the derived set, so a scan that matches
nothing does not report clean.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SKILLS = REPO_ROOT / "plugins" / "craft" / "skills"

_LEGITIMATE_EXECUTE_MD_READERS = {"execute", "drive"}
_SAFE_VALUE_SHAPE = "^[A-Za-z0-9._/-]+$"


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


def _attribution_sites() -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for path in sorted(SKILLS.glob("*/SKILL.md")):
        if path.parent.name in _LEGITIMATE_EXECUTE_MD_READERS:
            continue
        text = path.read_text(encoding="utf-8")
        for unit in _units(text):
            # Citing the shared security document at all, without dispatching
            # to its credential-pattern scrub, is what makes a unit an
            # attribution rather than a point-of-use dispatch. A unit that
            # names neither document is an unrelated local shape check (e.g.
            # slice's own `--covers` value validation), not an attribution.
            cites_shared_doc = "execute.md" in unit or "security.md" in unit
            if cites_shared_doc and "credential-pattern scrub" not in unit.lower():
                hits.append((path.parent.name, unit))
    return hits


def test_attribution_site_set_is_non_empty():
    """Non-vacuity guard: a scan matching nothing must not report clean."""
    sites = _attribution_sites()
    assert sites, (
        "expected at least one attribution site — a unit naming the untrusted-value "
        "rule outside a credential-pattern-scrub dispatch"
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
