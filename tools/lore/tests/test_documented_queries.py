"""The KQL the docs teach an agent to write is KQL the parser accepts.

`search/SKILL.md` documents the query subset — the facets, the quoting rule for
a value containing `/`, the dot-for-slash convention for a namespaced label key
— and the skills and agents spell out example queries an agent copies. A facet
the parser does not implement, or an example whose quoting the tokenizer
rejects, reads fine in the doc and errors on first use.

The documents are the INPUT: their selectors and example queries are lifted out
and run through the real KQL parser. Nothing here asserts that a sentence
appears in a document.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import load_script

_PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
_SEARCH_SKILL = _PLUGIN_ROOT / "skills" / "search" / "SKILL.md"

# A selector as the facet list writes it: a backticked `field:value`, where the
# value may be quoted. Read off the "Query shape" section only, so the docs'
# other backticked colon spans (`/lore:record`, `file:line`) stay out.
_QUERY_SHAPE_HEADING = "## Query shape (KQL subset)"
# The unquoted value class deliberately includes `/`, so a doc that drops the
# quotes from a slash-bearing value is still extracted — and then fails to parse
# — rather than silently falling out of the scan.
_SELECTOR = re.compile(r'`(-?[a-z][a-z.-]*:(?:"[^"`]*"|[A-Za-z0-9_./-]+))`')
# A full example query: `lore search '<query>'`, single-quoted as the docs write it.
_EXAMPLE_QUERY = re.compile(r"^\s*lore search '([^']*)'", re.MULTILINE)
_PLACEHOLDER = re.compile(r"<[^>]+>")


@pytest.fixture()
def kql():
    return load_script("lore.search.kql")


def _documented_selectors() -> list[str]:
    """Every selector the search skill's facet list gives as a concrete example."""
    text = _SEARCH_SKILL.read_text(encoding="utf-8")
    start = text.index(_QUERY_SHAPE_HEADING) + len(_QUERY_SHAPE_HEADING)
    section = text[start:]
    end = section.find("\n## ")
    if end != -1:
        section = section[:end]
    return sorted(
        {s for s in _SELECTOR.findall(section) if not _PLACEHOLDER.search(s)}
    )


def _documented_queries() -> list[str]:
    """Every complete `lore search '…'` query the shipped docs spell out."""
    found: list[str] = []
    for md in sorted(_PLUGIN_ROOT.rglob("*.md")):
        found.extend(
            q
            for q in _EXAMPLE_QUERY.findall(md.read_text(encoding="utf-8"))
            if q and not _PLACEHOLDER.search(q)
        )
    return sorted(set(found))


@pytest.mark.parametrize("selector", _documented_selectors())
def test_every_documented_facet_selector_parses(kql, selector: str) -> None:
    kql.parse(selector)


@pytest.mark.parametrize("query", _documented_queries())
def test_every_documented_example_query_parses(kql, query: str) -> None:
    kql.parse(query)


def test_the_documented_quoting_rule_is_the_parsers_actual_rule(kql) -> None:
    """The docs say a value containing `/` must be quoted. That claim is only
    worth documenting if the bare form really is rejected — assert both halves
    against the parser rather than against the sentence."""
    quoted = [s for s in _documented_selectors() if '"' in s and "/" in s]
    assert quoted, "the facet list no longer shows a quoted slash-bearing value"
    for selector in quoted:
        kql.parse(selector)
        with pytest.raises(kql.KqlParseError):
            kql.parse(selector.replace('"', ""))
