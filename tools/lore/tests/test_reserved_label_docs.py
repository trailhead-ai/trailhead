"""Doc-consistency checks for the reserved-labels mechanism rule and its query
syntax counterpart.

The write side (``record/model.py``'s ``_check_map_str_str`` guard) classifies a
refused ``labels`` key into a runnable alternative: an edge (``--related
KIND=NAME``), a free attribute (``--label KEY=VALUE``), or — when the natural key
itself collides with a record kind or a query field name — a refusal whose fix is
``--annotation KEY=VALUE`` or a namespaced key (``<ns>/<key>``).

The rule reaches the agent through ``--help``, so ``--help`` is what these run:
the parser is built and its epilog rendered, and the rule is read off the text
argparse actually produced for ``record create`` and ``record update``.
"""
from __future__ import annotations

import re
from pathlib import Path

from lore.argparse_util import _leaf_parsers
from lore.cli.dispatch import build_parser

REPO_ROOT = Path(__file__).parent.parent


def _normalize(text: str) -> str:
    """Collapse all whitespace runs to a single space.

    argparse wraps ``epilog`` text at the terminal width, so a multi-word phrase
    asserted against raw ``format_help()`` output can be split across a line
    break. Normalizing whitespace before substring checks makes the assertion
    robust to that wrapping.
    """
    return re.sub(r"\s+", " ", text)


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


def test_record_update_help_documents_the_mechanism_rule():
    """lore record update --help must carry the same mechanism rule as create —
    update is the verb an agent most often reaches for to write labels, so it
    must not be the one place the rule is missing."""
    parser = build_parser()
    leaves = _leaf_parsers(parser)
    help_text = _normalize(leaves["record update"].format_help())

    assert "--related KIND=NAME" in help_text
    assert "--label KEY=VALUE" in help_text
    assert "--annotation KEY=VALUE" in help_text
    assert "namespaced key" in help_text


def test_record_create_and_update_epilogs_share_the_mechanism_rule_text():
    """The mechanism rule must be a single source of truth (a shared
    module-level constant), not two independently-maintained copies that can
    drift apart."""
    parser = build_parser()
    leaves = _leaf_parsers(parser)
    create_help = _normalize(leaves["record create"].format_help())
    update_help = _normalize(leaves["record update"].format_help())

    rule_sentence = "Choosing a labels flag:"
    assert rule_sentence in create_help
    assert rule_sentence in update_help
