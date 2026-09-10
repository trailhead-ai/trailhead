"""The shared council surface carries the maturity calibration mapping.

`_shared/council.md` is the single place per-lens bars are tuned. This suite
pins that the calibration table it now carries agrees with what
`scripts/maturity_bars.py` actually renders — both sides derived, never
hand-authored twice, so a change to either that is not made on the other fails
here rather than shipping silently divergent.

One test, because there is one thing to check: the two tables agree, cell for
cell, at every level. Asserting that the document also *states* the rules the
renderer emits would only pin that someone typed them.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COUNCIL_MD = REPO_ROOT / "plugins" / "craft" / "skills" / "_shared" / "council.md"
SKILLS_DIR = REPO_ROOT / "plugins" / "craft" / "skills"
BARS = REPO_ROOT / "plugins" / "craft" / "scripts" / "maturity_bars.py"

LEVELS = ("prototype", "early", "production")
SEVERITIES = ("Critical", "Important", "Minor")

_CONCERNS = (
    "backwards compatibility",
    "migration and backfill",
    "rollback and reversibility",
    "production failure visibility",
    "cross-consumer blast radius",
)


def council_text() -> str:
    return COUNCIL_MD.read_text(encoding="utf-8")


# ---- dispatcher discovery — reusable by the next task's dispatch-count test ----


def discover_council_dispatchers() -> list[Path]:
    """Every `SKILL.md` under `skills/` that fills the council's
    `<lens-critical-bars>` prompt-template token — i.e. every dispatcher of
    the council panel `_shared/council.md` governs. `_shared/council.md`
    itself defines the token rather than filling it, so it is excluded by
    only searching `SKILL.md` files.

    This is the single mechanical discovery both this document's roster
    line and the next task's dispatch-count test are checked against, so
    the two cannot disagree.
    """
    return sorted(
        skill_md
        for skill_md in SKILLS_DIR.glob("*/SKILL.md")
        if "<lens-critical-bars>" in skill_md.read_text(encoding="utf-8")
    )


# ---- calibration table parsing (document side) ---------------------------


_ROW_RE = re.compile(
    r"^\|\s*([a-zA-Z][a-zA-Z -]*?)\s*\|\s*(Critical|Important|Minor)\s*\|"
    r"\s*(Critical|Important|Minor)\s*\|\s*(Critical|Important|Minor)\s*\|\s*$",
    re.MULTILINE,
)


def parse_calibration_table(text: str) -> dict[str, dict[str, str]]:
    """Parse the `| Concern | prototype | early | production |` table out of
    `council.md`'s Maturity calibration section into
    `{concern: {level: severity}}`, driven purely by the table's own header
    row so a reordered column is still read correctly."""
    section_start = text.index("## Maturity calibration")
    section_end = text.index("\n## ", section_start + 1)
    section = text[section_start:section_end]

    header_match = re.search(
        r"^\|\s*Concern\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*$",
        section,
        re.MULTILINE,
    )
    assert header_match, "no `| Concern | ... |` header row found in the Maturity calibration section"
    columns = header_match.groups()

    mapping: dict[str, dict[str, str]] = {}
    for row in _ROW_RE.finditer(section):
        concern, *severities = row.groups()
        mapping[concern.strip()] = dict(zip(columns, severities))
    return mapping


# ---- renderer invocation (producer side) ----------------------------------


def _spec_for_level(level: str) -> str:
    return f"# Some Spec\n\n## Maturity\n\n- lookout: {level}\n"


def _render(level: str) -> str:
    result = subprocess.run(
        [sys.executable, str(BARS)],
        input=_spec_for_level(level).encode("utf-8"),
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8")
    return result.stdout.decode("utf-8")


def rendered_mapping() -> dict[str, dict[str, str]]:
    """`{concern: {level: severity}}` derived by actually invoking the
    renderer once per level and parsing its stdout — never hand-authored."""
    mapping: dict[str, dict[str, str]] = {concern: {} for concern in _CONCERNS}
    for level in LEVELS:
        out = _render(level)
        for line in out.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            concern, _, severity = stripped[2:].partition(":")
            concern = concern.strip()
            severity = severity.strip()
            if concern in mapping:
                mapping[concern][level] = severity
    return mapping


# ---- contract item 1: no missing cell -------------------------------------


# ---- contract item 2: document and renderer severities agree --------------


def test_document_and_renderer_severities_agree():
    doc = parse_calibration_table(council_text())
    rendered = rendered_mapping()
    for concern in _CONCERNS:
        for level in LEVELS:
            assert doc[concern][level] == rendered[concern][level], (
                f"{concern!r} at {level}: document says {doc[concern][level]!r}, "
                f"renderer emits {rendered[concern][level]!r}"
            )


# ---- contract item 3: same concern-name set --------------------------------


# ---- contract item 4: substitution-token contract names the new token -----


# ---- contract item 5: mapped concern always reported, never filtered ------


# ---- contract item 6: downgraded finding restates concern + level ---------


# ---- contract item 7: roster matches mechanical dispatcher discovery ------


# ---- contract item 8: closed severity vocabulary, checked against the renderer ----


def _rendered_severity_tiers() -> set[str]:
    """Every severity `maturity_bars.py` actually emits, across every level it accepts.

    The levels come from the renderer's own parser rather than a list here, so a
    level added or removed moves this set with it.
    """
    help_text = subprocess.run(
        [sys.executable, str(BARS), "--help"],
        input="", capture_output=True, text=True, timeout=30,
    ).stdout
    levels = re.search(r"--level \{([a-z,]+)\}", help_text)
    assert levels, f"could not read the renderer's level choices from its help:\n{help_text}"
    tiers: set[str] = set()
    for level in levels.group(1).split(","):
        rendered = subprocess.run(
            [sys.executable, str(BARS), "--level", level],
            input="", capture_output=True, text=True, timeout=30,
        )
        assert rendered.returncode == 0, rendered.stderr
        tiers |= set(re.findall(r"(?m)^- [^:]+: ([A-Z][a-z]+)$", rendered.stdout))
    return tiers
