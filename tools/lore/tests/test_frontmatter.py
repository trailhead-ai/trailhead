"""Slice 2 tests: ported frontmatter parser + set_status / patch_section primitives."""

from conftest import load_script


# ---- parse_frontmatter (ported from _vault_context.py) ---------------------

def test_parse_frontmatter_scalars(tmp_path):
    fm = load_script("frontmatter")
    p = tmp_path / "note.md"
    p.write_text("---\ntype: deferred\nstatus: open\n---\n\nbody\n")
    out = fm.parse_frontmatter(p)
    assert out["type"] == "deferred"
    assert out["status"] == "open"


def test_parse_frontmatter_inline_list(tmp_path):
    fm = load_script("frontmatter")
    p = tmp_path / "note.md"
    p.write_text('---\nsubsystems: ["foo", "bar"]\n---\n\nbody\n')
    out = fm.parse_frontmatter(p)
    assert out["subsystems"] == ["foo", "bar"]


def test_parse_frontmatter_strips_quotes(tmp_path):
    fm = load_script("frontmatter")
    p = tmp_path / "note.md"
    p.write_text('---\ntitle: "Hello World"\n---\n\n')
    out = fm.parse_frontmatter(p)
    assert out["title"] == "Hello World"


def test_parse_frontmatter_no_frontmatter(tmp_path):
    fm = load_script("frontmatter")
    p = tmp_path / "note.md"
    p.write_text("just a body, no frontmatter\n")
    assert fm.parse_frontmatter(p) == {}


def test_parse_frontmatter_missing_file(tmp_path):
    fm = load_script("frontmatter")
    assert fm.parse_frontmatter(tmp_path / "nope.md") == {}


# ---- set_status -------------------------------------------------------------

def test_set_status_replaces_existing(tmp_path):
    fm = load_script("frontmatter")
    p = tmp_path / "note.md"
    p.write_text("---\ntype: deferred\nstatus: open\n---\n\n# Body\ncontent\n")
    fm.set_status(p, "resolved")
    out = fm.parse_frontmatter(p)
    assert out["status"] == "resolved"
    # body preserved
    assert "# Body\ncontent" in p.read_text()


def test_set_status_adds_when_missing(tmp_path):
    fm = load_script("frontmatter")
    p = tmp_path / "note.md"
    p.write_text("---\ntype: deferred\n---\n\nbody\n")
    fm.set_status(p, "open")
    assert fm.parse_frontmatter(p)["status"] == "open"


def test_set_status_only_touches_status_line(tmp_path):
    fm = load_script("frontmatter")
    p = tmp_path / "note.md"
    original = "---\ntype: deferred\nstatus: open\nproject: x\n---\n\nbody\n"
    p.write_text(original)
    fm.set_status(p, "resolved")
    text = p.read_text()
    assert "type: deferred" in text
    assert "project: x" in text
    assert "status: resolved" in text


# ---- patch_section ----------------------------------------------------------

def _doc():
    return (
        "---\ntype: session\n---\n\n"
        "# Title\n\n"
        "## Alpha\n"
        "alpha line\n\n"
        "## Beta\n"
        "beta line\n"
    )


def test_patch_section_appends_under_heading(tmp_path):
    fm = load_script("frontmatter")
    p = tmp_path / "note.md"
    p.write_text(_doc())
    fm.patch_section(p, "Alpha", "- new alpha entry")
    text = p.read_text()
    alpha_idx = text.index("## Alpha")
    beta_idx = text.index("## Beta")
    section = text[alpha_idx:beta_idx]
    assert "- new alpha entry" in section


def test_patch_section_leaves_sibling_byte_identical(tmp_path):
    fm = load_script("frontmatter")
    p = tmp_path / "note.md"
    p.write_text(_doc())
    fm.patch_section(p, "Alpha", "- new alpha entry")
    text = p.read_text()
    # The Beta section (heading through end) must be byte-identical to before.
    beta_block = text[text.index("## Beta"):]
    assert beta_block == "## Beta\nbeta line\n"


def test_patch_section_creates_section_when_missing(tmp_path):
    fm = load_script("frontmatter")
    p = tmp_path / "note.md"
    p.write_text(_doc())
    fm.patch_section(p, "Gamma", "gamma content")
    text = p.read_text()
    assert "## Gamma" in text
    assert "gamma content" in text
    # appended at end, after Beta
    assert text.index("## Gamma") > text.index("## Beta")
