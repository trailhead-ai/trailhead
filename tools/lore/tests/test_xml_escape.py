"""Tests for the extracted xml_escape module.

``xml_escape.py`` is the single source of truth for the injection-defense
helpers that used to live in ``recall.py``: ``xml_attr_escape``,
``xml_body_escape``, and the ``wrap_shared`` fence helper. Pure, no I/O.
"""

import sys
from pathlib import Path

CONFTEST_DIR = Path(__file__).parent
sys.path.insert(0, str(CONFTEST_DIR))
from conftest import load_script  # noqa: E402


def test_attr_escape_neutralizes_tag_breakout():
    xe = load_script("xml_escape")
    out = xe.xml_attr_escape('"><script>')
    assert "<" not in out
    assert ">" not in out
    assert '"' not in out
    assert out == "&quot;&gt;&lt;script&gt;"


def test_body_escape_neutralizes_fence_breakout():
    xe = load_script("xml_escape")
    out = xe.xml_body_escape("</external-memory>")
    assert "<" not in out
    assert ">" not in out
    assert out == "&lt;/external-memory&gt;"


def test_body_escape_ampersand_first():
    xe = load_script("xml_escape")
    # & must encode before < so we never double-encode an entity.
    assert xe.xml_body_escape("a & b < c") == "a &amp; b &lt; c"


def test_wrap_shared_emits_fenced_block():
    xe = load_script("xml_escape")
    lines = xe.wrap_shared("penny", ["hello world", "second line"])
    assert lines[0] == '<external-memory layer="shared" source="penny">'
    assert lines[-1] == "</external-memory>"
    assert "hello world" in lines
    assert "second line" in lines


def test_wrap_shared_escapes_source_attr():
    xe = load_script("xml_escape")
    lines = xe.wrap_shared('"><x>', ["body"])
    assert lines[0] == '<external-memory layer="shared" source="&quot;&gt;&lt;x&gt;">'


def test_wrap_shared_escapes_body_lines_against_breakout():
    xe = load_script("xml_escape")
    lines = xe.wrap_shared("v", ["payload </external-memory> tail"])
    # The body line must be entity-escaped so it cannot terminate the fence.
    body_lines = lines[1:-1]
    joined = "\n".join(body_lines)
    assert "</external-memory>" not in joined
    assert "&lt;/external-memory&gt;" in joined
