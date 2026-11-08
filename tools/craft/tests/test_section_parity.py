"""Lore's pipeline-section finder and craft's heading searches must agree.

Craft reads the slice ledger (candidate_set.py, covers_gate.py) and the
follow-ups ledger (followups.py); lore decides which part of a spec body is
exempt from the freeze guard and which edits need a revision handshake. If they
disagree about where `## Slices` or `## Follow-ups` starts, the guard exempts
text the pipeline never owns or demands a handshake for text it does not guard.
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
import markdown_section  # noqa: E402
import waiting  # noqa: E402
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


HEADINGS = ("## Slices", "## Follow-ups", "## Waiting")


def _body_for(heading: str, body: str) -> str:
    if heading == "## Slices":
        return body
    if heading == "## Waiting":
        return (
            body.replace("Sliſes", "Waitıng")
            .replace("SLICES", "WAITING")
            .replace("slices", "waiting")
            .replace("Slices", "Waiting")
        )
    return (
        body.replace("Sliſes", "Follow-upſ")
        .replace("SLICES", "FOLLOW-UPS")
        .replace("slices", "follow-ups")
        .replace("Slices", "Follow-ups")
    )


def _craft_start(heading: str, body: str) -> int | None:
    lines = covers_gate._COMMONMARK_LINE_RE.split(body)
    masked = covers_gate._mask_fenced_lines(lines)
    if heading == "## Slices":
        after = covers_gate._find_unique_heading(
            lines,
            masked,
            candidate_set._SLICES_HEADING_RE,
            candidate_set._SLICES_HEADING,
            candidate_set._DUPLICATE_SLICES_HEADING_REASON_CODE,
        )
        return None if after is None else after - 1
    located = markdown_section.section_range(lines, masked, heading, "duplicate")
    return None if located is None else located[0]


def _lore_starts(heading: str, body: str) -> list[int]:
    lines = sections._split_lines(body)
    registered = [s for s in sections.PIPELINE_SECTIONS["spec"] if s.heading == heading]
    assert registered, f"lore registers no pipeline section {heading!r} for specs"
    section = registered[0]
    return sections._heading_lines(lines, sections._mask(lines), section.heading_re)


@pytest.mark.parametrize("heading", HEADINGS)
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_lore_and_craft_agree_where_the_section_starts(heading, name):
    body = _body_for(heading, CORPUS[name])
    try:
        expected = _craft_start(heading, body)
    except covers_gate.DuplicateHeadingError:
        assert len(_lore_starts(heading, body)) > 1
        return
    starts = _lore_starts(heading, body)
    assert starts == ([] if expected is None else [expected])


def _craft_outcome(heading: str, body: str) -> str:
    try:
        return "absent" if _craft_start(heading, body) is None else "start"
    except covers_gate.DuplicateHeadingError:
        return "duplicate"


@pytest.mark.parametrize("heading", HEADINGS)
@pytest.mark.parametrize(
    "name,outcome",
    [("plain", "start"), ("none", "absent"), ("duplicate", "duplicate")],
)
def test_craft_finder_reaches_each_outcome_the_parity_corpus_relies_on(heading, name, outcome):
    assert _craft_outcome(heading, _body_for(heading, CORPUS[name])) == outcome


END_CORPUS = {
    "h3": "## {h}\n- a\n### Note\nafter\n",
    "h1": "## {h}\n- a\n# Top\nafter\n",
    "h6": "## {h}\n- a\n###### Deep\nafter\n",
    "h2": "## {h}\n- a\n## Other\nafter\n",
    "indented-3": "## {h}\n- a\n   ### Note\nafter\n",
    "indented-4": "## {h}\n- a\n    ### Note\nafter\n",
    "bare-hashes": "## {h}\n- a\n###\nafter\n",
    "seven-hashes": "## {h}\n- a\n####### x\nafter\n",
    "no-space": "## {h}\n- a\n#tag\nafter\n",
    "fenced": "## {h}\n```\n### Note\n```\nafter\n",
    "commented": "## {h}\n<!--\n### Note\n-->\nafter\n",
    "unterminated-fence": "## {h}\n```\n### Note\n",
    "crlf": "## {h}\r\n- a\r\n### Note\r\nafter\r\n",
    "to-eof": "## {h}\n- a\n",
}


@pytest.mark.parametrize("heading", ["## Follow-ups", "## Waiting"])
@pytest.mark.parametrize("name", sorted(END_CORPUS))
def test_lore_and_craft_agree_where_the_section_ends(heading, name):
    body = END_CORPUS[name].replace("{h}", heading[3:])
    lines = covers_gate._COMMONMARK_LINE_RE.split(body)
    craft_start, craft_end = markdown_section.section_range(
        lines, covers_gate._mask_fenced_lines(lines), heading, "duplicate"
    )
    lore_lines = sections._split_lines(body)
    (lore_start,) = _lore_starts(heading, body)
    assert craft_start == lore_start
    # craft's split yields a trailing empty line after a final newline; lore's does not
    lore_end = sections._section_end(lore_lines, sections._mask(lore_lines), lore_start)
    assert min(craft_end, len(lore_lines)) == lore_end


def test_a_heading_inside_the_waiting_section_leaves_what_follows_authored_in_both():
    entry = "- **AC7.** set aside\n  - waits on: x\n  - 2026-11-03 waiting: p\n"
    body = f"# S\n\n## Waiting\n\n{entry}\n### Note\n\nauthored text\n"
    kept = sections.authored_content("spec", body)
    assert "authored text" in kept and "### Note" in kept and "AC7" not in kept
    assert [e.criterion_id for e in waiting.parse_waiting(body)] == ["AC7"]
    after = body + "\n- **AC8.** set aside\n  - waits on: y\n  - 2026-11-03 waiting: p\n"
    assert [e.criterion_id for e in waiting.parse_waiting(after)] == ["AC7"]
