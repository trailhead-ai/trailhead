"""area_map.py core: area-map build (the kept area-menu path).

Covers ``build_area_map`` — the on-demand area menu served by ``lore areas`` and
the SessionStart pointer. ``lore search`` is the query interface.

  build_area_map:
    - reads name + keywords + one-liner from area/*.md
    - keyword-less area still appears (agent can match on one-liner)
    - malformed / binary / non-UTF-8 area file silently skipped (no raise)
    - deterministic alpha order by name
    - one-liner <= 120 chars (hard cap enforced)
    - keywords capped
    - summary: field used when present
    - first ## Overview sentence used as fallback
    - HTML-comment Overview lines skipped (pr-dashboard gotcha)
    - area with no summary and no usable Overview: appears with empty one-liner
"""

from pathlib import Path

from conftest import load_script


def load_area_map():
    """Load area_map module freshly each call to avoid state pollution."""
    return load_script("lore.search.area_map")


# ---------------------------------------------------------------------------
# Fixture vault helpers
# ---------------------------------------------------------------------------


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "area").mkdir(parents=True)
    return vault


def _write_area(
    vault: Path,
    name: str,
    keywords: list,
    summary: str | None = None,
    overview: str | None = None,
) -> Path:
    p = vault / "area" / f"{name}.md"
    kw_str = "[" + ", ".join(keywords) + "]"
    summary_line = f"summary: {summary}\n" if summary else ""
    overview_block = f"\n## Overview\n\n{overview}\n" if overview else ""
    p.write_text(
        f"---\ntype: area\nname: {name}\nkeywords: {kw_str}\n{summary_line}---\n{overview_block}"
    )
    return p


# ---------------------------------------------------------------------------
# build_area_map
# ---------------------------------------------------------------------------


class TestBuildAreaMap:
    def test_reads_name_keywords_one_liner(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth-flow", ["oauth", "login"], summary="Handles auth.")
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert len(entries) == 1
        e = entries[0]
        assert e.name == "auth-flow"
        assert "oauth" in e.keywords
        assert "login" in e.keywords
        assert e.one_liner == "Handles auth."

    def test_keyword_less_area_still_in_menu(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "no-keywords", [], summary="An area without keywords.")
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        names = [e.name for e in entries]
        assert "no-keywords" in names

    def test_alpha_order_deterministic(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "zebra", ["z"], summary="Z area.")
        _write_area(vault, "alpha", ["a"], summary="A area.")
        _write_area(vault, "middle", ["m"], summary="M area.")
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        names = [e.name for e in entries]
        assert names == sorted(names)

    def test_malformed_file_silently_skipped(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "good", ["ok"], summary="Good area.")
        bad = vault / "area" / "bad-file.md"
        bad.write_bytes(b"\xff\xfe not utf8 \x00\x01")
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        names = [e.name for e in entries]
        assert "good" in names
        assert "bad-file" not in names

    def test_malformed_file_does_not_raise(self, tmp_path):
        vault = _make_vault(tmp_path)
        bad = vault / "area" / "bad.md"
        bad.write_bytes(b"\xff\xfe garbage \x00\x01\x02")
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert isinstance(entries, list)

    def test_one_liner_capped_at_120_chars(self, tmp_path):
        vault = _make_vault(tmp_path)
        long_summary = "x" * 200
        _write_area(vault, "verbosity", ["v"], summary=long_summary)
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert len(entries[0].one_liner) <= 120

    def test_keywords_capped(self, tmp_path):
        vault = _make_vault(tmp_path)
        many_kw = [f"kw{i}" for i in range(20)]
        _write_area(vault, "verbose-kw", many_kw, summary="Many keywords.")
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert len(entries[0].keywords) <= 8

    def test_summary_field_preferred_over_overview(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(
            vault, "prefer-summary", ["x"], summary="Summary wins.", overview="Overview text."
        )
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert entries[0].one_liner == "Summary wins."

    def test_overview_sentence_used_as_fallback(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "no-summary", ["x"], overview="First sentence of overview.")
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert "First sentence of overview" in entries[0].one_liner

    def test_html_comment_overview_skipped(self, tmp_path):
        vault = _make_vault(tmp_path)
        p = vault / "area" / "html-comment.md"
        p.write_text(
            "---\ntype: area\nname: html-comment\nkeywords: [x]\n---\n\n"
            "## Overview\n<!-- Just a placeholder -->\n\nBody text.\n"
        )
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert len(entries) == 1
        assert "<!--" not in entries[0].one_liner

    def test_area_no_summary_no_usable_overview_appears_empty_one_liner(self, tmp_path):
        vault = _make_vault(tmp_path)
        p = vault / "area" / "empty-area.md"
        p.write_text(
            "---\ntype: area\nname: empty-area\nkeywords: [x]\n---\n\n"
            "## Overview\n<!-- Placeholder -->\n"
        )
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert len(entries) == 1
        assert entries[0].name == "empty-area"

    def test_empty_areas_dir_returns_empty_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        area_map = load_area_map()
        assert area_map.build_area_map(vault) == []

    def test_missing_areas_dir_returns_empty_list(self, tmp_path):
        vault = tmp_path / "empty-vault"
        vault.mkdir()
        area_map = load_area_map()
        assert area_map.build_area_map(vault) == []
