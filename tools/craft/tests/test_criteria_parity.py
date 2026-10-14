"""Lore's criterion walk and craft's `parse_criteria_with_text` must agree.

Craft's covers gate reads a spec's criteria to certify slices; lore's amend
guard reads them to decide which criteria an amend adds or removes. If the two
walks disagree about what a criterion is, the guard polices a different list
from the one slices are certified against.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "craft" / "scripts"
FIXTURES = Path(__file__).parent / "fixtures"
_LORE_PLUGIN = REPO_ROOT.parent / "lore" / "plugins" / "lore"
for _path in (str(SCRIPTS_DIR), str(_LORE_PLUGIN)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import covers_gate  # noqa: E402
from lore.record import criteria  # noqa: E402

H = "## Acceptance Criteria"

CORPUS = {
    "plain": f"# S\n\n{H}\n\n- **AC1.** one\n- **AC2.** two\n",
    "no-heading": "# S\n\n- **AC1.** one\n",
    "empty": "",
    "only-heading": H,
    "lowercase-heading": "## acceptance criteria\n- **AC1.** one\n",
    "uppercase-heading": "## ACCEPTANCE CRITERIA\n- **AC1.** one\n",
    "indented-heading": f" {H}\n- **AC1.** one\n",
    "trailing-space-heading": f"{H} \n- **AC1.** one\n",
    "h3-heading": "### Acceptance Criteria\n- **AC1.** one\n",
    "inline-mention": f"see {H} below\n- **AC1.** one\n",
    "grouped": f"{H}\n### A\n- **AC1.** one\n### B\n- **AC2.** two\n",
    "unidentified-bullet": f"{H}\n- prose\n- **AC1.** one\n",
    "wrapped": f"{H}\n- **AC1.** one\n  wraps\n- **AC2.** two\n",
    "sub-bullet": f"{H}\n- **AC1.** one\n  - **AC9.** nested\n    more\n- **AC2.** two\n",
    "loose-continuation": f"{H}\n- **AC1.** one\n\n  second para\n- **AC2.** two\n",
    "loose-then-dedent": f"{H}\n- **AC1.** one\n\ntrailing prose\n- **AC2.** two\n",
    "next-h2-ends": f"{H}\n- **AC1.** one\n## Other\n- **AC2.** two\n",
    "fence-fake": f"{H}\n- **AC1.** one\n```\n- **AC2.** fake\n```\n- **AC3.** three\n",
    "tilde-fence-fake": f"{H}\n~~~\n- **AC2.** fake\n~~~\n- **AC3.** three\n",
    "fenced-heading-only": f"```\n{H}\n```\n- **AC1.** one\n",
    "fenced-heading-then-real": f"```\n{H}\n```\n{H}\n- **AC1.** one\n",
    "comment-fake": f"{H}\n<!--\n- **AC2.** fake\n-->\n- **AC3.** three\n",
    "one-line-comment": f"{H}\n<!-- c -->\n- **AC1.** one\n",
    "unterminated-comment": f"{H}\n- **AC1.** one\n<!-- open\n- **AC2.** hidden\n",
    "unterminated-fence": f"{H}\n- **AC1.** one\n```\n- **AC2.** hidden\n",
    "comment-hides-heading": f"<!--\n{H}\n-->\n{H}\n- **AC1.** one\n",
    "duplicate": f"{H}\n- **AC1.** a\n{H}\n- **AC2.** b\n",
    "duplicate-one-fenced": f"{H}\n- **AC1.** a\n```\n{H}\n```\n",
    "crlf": f"x\r\n{H}\r\n- **AC1.** one\r\n  wraps\r\n- **AC2.** two\r\n",
    "cr": f"x\r{H}\r- **AC1.** one\r- **AC2.** two\r",
    "mixed-eol": f"x\r\n{H}\r- **AC1.** one\n- **AC2.** two\r\n",
    "unicode-separator": f"x {H}\n- **AC1.** one\n",
    "paragraph-separator": f"x {H}\n- **AC1.** one\n",
    "vertical-tab": f"x\x0b{H}\n- **AC1.** one\n",
    "nel-in-bullet": f"{H}\n- **AC1.** one\x85two\n",
    "non-canonical-ids": f"{H}\n- **AC0.** zero\n- **AC007.** padded\n- **AC12.** twelve\n",
    "no-space-bullet": f"{H}\n-  **AC1.** double space\n-\t**AC2.** tab\n",
    "marker-without-dot": f"{H}\n- **AC1** one\n- **ac2.** lower\n",
    "trailing-whitespace": f"{H}\n- **AC1.** one  \n  wrap  \n- **AC2.** two \n",
    "tab-continuation": f"{H}\n- **AC1.** one\n\tcontinued\n",
    "heading-last-line": f"- **AC1.** before\n{H}",
}
for _fixture in sorted([*FIXTURES.glob("crit_*.md"), *FIXTURES.glob("spec_*.md")]):
    CORPUS[f"fixture:{_fixture.name}"] = _fixture.read_text(encoding="utf-8")


def _craft(body: str):
    return [
        (i, t) for i, t in covers_gate.parse_criteria_with_text(body) if i is not None
    ]


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_lore_and_craft_agree_on_declared_criteria(name):
    body = CORPUS[name]
    try:
        expected = _craft(body)
    except ValueError:
        with pytest.raises(criteria.CriteriaError):
            criteria.declared_criteria(body)
        return
    assert criteria.declared_criteria(body) == expected


def test_corpus_reaches_identified_entries_and_both_error_kinds():
    outcomes = {"entries": 0, "missing": 0, "duplicate": 0}
    for body in CORPUS.values():
        try:
            if _craft(body):
                outcomes["entries"] += 1
        except covers_gate.DuplicateHeadingError:
            outcomes["duplicate"] += 1
        except ValueError:
            outcomes["missing"] += 1
    assert all(outcomes.values()), outcomes
