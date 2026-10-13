"""Lore's pipeline-section finder and craft's `## Slices` heading search must agree.

Craft reads the slice ledger (candidate_set.py, covers_gate.py); lore decides
which part of a spec body is exempt from the freeze guard. If they disagree
about where `## Slices` starts, the guard exempts text the pipeline never owns.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "craft" / "scripts"
_LORE_PLUGIN = REPO_ROOT.parent / "lore" / "plugins" / "lore"
for _path in (str(SCRIPTS_DIR), str(_LORE_PLUGIN)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import candidate_set  # noqa: E402
import covers_gate  # noqa: E402
from lore.record import sections  # noqa: E402

CORPUS = {
    "plain": "# S\n\n## Slices\n\n- a\n",
    "none": "# S\n\nprose\n",
    "empty": "",
    "only-heading": "## Slices",
    "lowercase": "x\n## slices\n- a\n",
    "uppercase": "x\n## SLICES\n- a\n",
    "indented-1": "x\n ## Slices\n- a\n",
    "indented-3": "x\n   ## Slices\n- a\n",
    "indented-4": "x\n    ## Slices\n- a\n",
    "trailing-space": "x\n## Slices \n- a\n",
    "trailing-tab": "x\n## Slices\t\n- a\n",
    "trailing-hashes": "x\n## Slices ##\n- a\n",
    "h3": "x\n### Slices\n- a\n",
    "h1": "x\n# Slices\n- a\n",
    "no-space": "x\n##Slices\n- a\n",
    "inline-mention": "see ## Slices below\n",
    "setext-h2": "Slices\n------\n- a\n",
    "setext-then-atx": "Title\n=====\n## Slices\n- a\n",
    "atx-after-setext-underline": "## Slices\n------\n- a\n",
    "backtick-fence": "```\n## Slices\n```\n",
    "tilde-fence": "~~~\n## Slices\n~~~\n",
    "tilde-in-backtick": "```\n~~~\n## Slices\n```\n",
    "backtick-in-tilde": "~~~\n```\n## Slices\n~~~\n",
    "short-close-fence": "````\n## Slices\n```\n## Slices\n````\n## Slices\n",
    "unterminated-fence": "```\nopen\n## Slices\n",
    "fence-info-string": "```markdown\n## Slices\n```\n## Slices\n",
    "indented-fence": "   ```\n## Slices\n   ```\n## Slices\n",
    "comment": "<!--\n## Slices\n-->\n## Slices\n",
    "one-line-comment": "<!-- c -->\n## Slices\n",
    "unterminated-comment": "<!-- open\n## Slices\n",
    "comment-closes-on-start-line": "<!-- a --> ## Slices\n## Slices\n",
    "fence-in-comment": "<!--\n```\n-->\n## Slices\n",
    "comment-in-fence": "```\n<!--\n```\n## Slices\n",
    "duplicate": "## Slices\n- a\n## Slices\n- b\n",
    "duplicate-one-fenced": "## Slices\n```\n## Slices\n```\n",
    "crlf": "x\r\n## Slices\r\n- a\r\n",
    "cr": "x\r## Slices\r- a\r",
    "mixed-eol": "x\r\n## Slices\r- a\n## Slices\n",
    "unicode-separator": "x ## Slices\n",
    "vertical-tab": "x\x0b## Slices\n",
    "nel": "x\x85## Slices\n",
    "form-feed": "x\x0c## Slices\n",
    "trailing-nbsp": "x\n## Slices \n",
    "long-s": "x\n## Sliſes\n",
}


def _craft_start(body: str) -> int | None:
    lines = covers_gate._COMMONMARK_LINE_RE.split(body)
    masked = covers_gate._mask_fenced_lines(lines)
    after = covers_gate._find_unique_heading(
        lines,
        masked,
        candidate_set._SLICES_HEADING_RE,
        candidate_set._SLICES_HEADING,
        candidate_set._DUPLICATE_SLICES_HEADING_REASON_CODE,
    )
    return None if after is None else after - 1


def _lore_starts(body: str) -> list[int]:
    lines = sections._split_lines(body)
    section = sections.PIPELINE_SECTIONS["spec"][0]
    return sections._heading_lines(lines, sections._mask(lines), section.heading_re)


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_lore_and_craft_agree_where_slices_starts(name):
    body = CORPUS[name]
    try:
        expected = _craft_start(body)
    except covers_gate.DuplicateHeadingError:
        assert len(_lore_starts(body)) > 1
        return
    starts = _lore_starts(body)
    assert starts == ([] if expected is None else [expected])


def test_corpus_exercises_start_duplicate_and_absent_outcomes():
    outcomes = set()
    for body in CORPUS.values():
        try:
            outcomes.add("absent" if _craft_start(body) is None else "start")
        except covers_gate.DuplicateHeadingError:
            outcomes.add("duplicate")
    assert outcomes == {"absent", "start", "duplicate"}
