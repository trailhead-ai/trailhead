"""Doc-consistency checks for the reserved-labels mechanism rule and its query
syntax counterpart.

The write side (``record/model.py``'s ``_check_map_str_str`` guard) classifies a
refused ``labels`` key into a runnable alternative: an edge (``--related
KIND=NAME``), a free attribute (``--label KEY=VALUE``), or — when the natural key
itself collides with a record kind or a query field name — a refusal whose fix is
``--annotation KEY=VALUE`` or a namespaced key (``<ns>/<key>``). None of that is
useful to an agent unless it's documented where the agent actually looks: the
always-loaded ``record/SKILL.md``, the ``record create --help`` output, and the
read-side query syntax in ``search/SKILL.md`` that makes the namespaced-key escape
route actually retrievable.
"""
from __future__ import annotations

import re
from pathlib import Path

from lore.argparse_util import _leaf_parsers
from lore.cli.dispatch import build_parser

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "plugins" / "lore" / "skills"
RECORD_SKILL = SKILLS_DIR / "record" / "SKILL.md"
SEARCH_SKILL = SKILLS_DIR / "search" / "SKILL.md"


def _normalize(text: str) -> str:
    """Collapse all whitespace runs to a single space.

    argparse wraps ``epilog`` text at the terminal width, so a multi-word phrase
    asserted against raw ``format_help()`` output can be split across a line
    break. Normalizing whitespace before substring checks makes the assertion
    robust to that wrapping.
    """
    return re.sub(r"\s+", " ", text)


def test_record_skill_documents_the_full_mechanism_rule():
    """record/SKILL.md must name all three arms of the mechanism rule in one
    place — a partial doc (e.g. --related and --label but not the refusal
    carrier) must fail this test."""
    text = RECORD_SKILL.read_text()
    assert "--related KIND=NAME" in text, (
        "record/SKILL.md must document --related KIND=NAME for a value naming "
        "another record (the edge arm)"
    )
    assert "--label KEY=VALUE" in text, (
        "record/SKILL.md must document --label KEY=VALUE for a free attribute "
        "(the label arm)"
    )
    assert "--annotation KEY=VALUE" in text, (
        "record/SKILL.md must document --annotation KEY=VALUE as the refusal "
        "carrier for a reserved labels key"
    )
    assert "namespaced key" in text and "<ns>/<key>" in text, (
        "record/SKILL.md must document the namespaced-key escape "
        "(<ns>/<key>) for a reserved labels key"
    )


def test_record_create_help_documents_the_mechanism_rule():
    """lore record create --help must carry the mechanism rule too — rendered
    from the real parser, not read off the source string, so a change to the
    epilog text is caught here rather than only in the skill doc."""
    parser = build_parser()
    leaves = _leaf_parsers(parser)
    help_text = _normalize(leaves["record create"].format_help())

    assert "--related KIND=NAME" in help_text
    assert "--label KEY=VALUE" in help_text
    assert "--annotation KEY=VALUE" in help_text
    assert "namespaced key" in help_text


def test_search_skill_documents_the_label_query_syntax():
    """search/SKILL.md must document the label query facets and the
    dot-for-slash convention that makes a namespaced label key retrievable —
    without it the escape route documented in record/SKILL.md is a dead end."""
    text = SEARCH_SKILL.read_text()
    assert "label.<key>:<value>" in text, (
        "search/SKILL.md must document the label.<key>:<value> exact-match facet"
    )
    assert "has:label.<key>" in text, (
        "search/SKILL.md must document the has:label.<key> existence facet"
    )
    assert "dot-for-slash" in text, (
        "search/SKILL.md must name the dot-for-slash convention for namespaced "
        "label keys (e.g. claude-code/model -> label.claude-code.model:)"
    )
