"""Tests for the frontmatter parser."""

from conftest import load_script


# ---- parse_frontmatter (ported from _vault_context.py) ---------------------


def test_parse_frontmatter_scalars(tmp_path):
    fm = load_script("lore.search.frontmatter")
    p = tmp_path / "note.md"
    p.write_text("---\ntype: deferred\nstatus: open\n---\n\nbody\n")
    out = fm.parse_frontmatter(p)
    assert out["type"] == "deferred"
    assert out["status"] == "open"


def test_parse_frontmatter_inline_list(tmp_path):
    fm = load_script("lore.search.frontmatter")
    p = tmp_path / "note.md"
    p.write_text('---\nsubsystems: ["foo", "bar"]\n---\n\nbody\n')
    out = fm.parse_frontmatter(p)
    assert out["subsystems"] == ["foo", "bar"]


def test_parse_frontmatter_strips_quotes(tmp_path):
    fm = load_script("lore.search.frontmatter")
    p = tmp_path / "note.md"
    p.write_text('---\ntitle: "Hello World"\n---\n\n')
    out = fm.parse_frontmatter(p)
    assert out["title"] == "Hello World"


def test_parse_frontmatter_no_frontmatter(tmp_path):
    fm = load_script("lore.search.frontmatter")
    p = tmp_path / "note.md"
    p.write_text("just a body, no frontmatter\n")
    assert fm.parse_frontmatter(p) == {}


def test_parse_frontmatter_missing_file(tmp_path):
    fm = load_script("lore.search.frontmatter")
    assert fm.parse_frontmatter(tmp_path / "nope.md") == {}
