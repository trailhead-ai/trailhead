"""recall.py core: area-map build (the kept area-menu path).

Covers ``build_area_map`` — the on-demand area menu served by ``lore areas`` and
the SessionStart pointer. The ``recall_areas`` / ``render_recall_banner`` command
path was retired in Slice 5 (S3); ``lore search`` is now the query interface, so
the recall-command tests that used to live here were removed.

  build_area_map:
    - reads name + keywords + one-liner from areas/*.md
    - keyword-less area still appears (agent can match on one-liner)
    - malformed / binary / non-UTF-8 area file silently skipped (no raise)
    - deterministic alpha order by name
    - one-liner <= 120 chars (D-8c hard cap enforced)
    - keywords capped (D-8c)
    - summary: field used when present (D-2)
    - first ## Overview sentence used as fallback (D-2)
    - HTML-comment Overview lines skipped (D-2, pr-dashboard gotcha)
    - area with no summary and no usable Overview: appears with empty one-liner
"""

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def load_recall():
    """Load recall module freshly each call to avoid state pollution."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    for cached in ("recall", "vault", "frontmatter", "status_validator",
                   "regenerate_indices", "sessions"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(
        "recall", SCRIPTS_DIR / "recall.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules before exec so @dataclass can resolve cls.__module__
    sys.modules["recall"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixture vault helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "areas").mkdir(parents=True)
    return vault


def _write_area(
    vault: Path,
    name: str,
    keywords: list,
    summary: str | None = None,
    overview: str | None = None,
) -> Path:
    p = vault / "areas" / f"{name}.md"
    kw_str = "[" + ", ".join(keywords) + "]"
    summary_line = f"summary: {summary}\n" if summary else ""
    overview_block = f"\n## Overview\n\n{overview}\n" if overview else ""
    p.write_text(
        f"---\ntype: area\nname: {name}\nkeywords: {kw_str}\n{summary_line}---\n"
        f"{overview_block}"
    )
    return p


# ---------------------------------------------------------------------------
# build_area_map
# ---------------------------------------------------------------------------

class TestBuildAreaMap:
    def test_reads_name_keywords_one_liner(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth-flow", ["oauth", "login"], summary="Handles auth.")
        recall = load_recall()
        entries = recall.build_area_map(vault)
        assert len(entries) == 1
        e = entries[0]
        assert e.name == "auth-flow"
        assert "oauth" in e.keywords
        assert "login" in e.keywords
        assert e.one_liner == "Handles auth."

    def test_keyword_less_area_still_in_menu(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "no-keywords", [], summary="An area without keywords.")
        recall = load_recall()
        entries = recall.build_area_map(vault)
        names = [e.name for e in entries]
        assert "no-keywords" in names

    def test_alpha_order_deterministic(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "zebra", ["z"], summary="Z area.")
        _write_area(vault, "alpha", ["a"], summary="A area.")
        _write_area(vault, "middle", ["m"], summary="M area.")
        recall = load_recall()
        entries = recall.build_area_map(vault)
        names = [e.name for e in entries]
        assert names == sorted(names)

    def test_malformed_file_silently_skipped(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "good", ["ok"], summary="Good area.")
        bad = vault / "areas" / "bad-file.md"
        bad.write_bytes(b"\xff\xfe not utf8 \x00\x01")
        recall = load_recall()
        entries = recall.build_area_map(vault)
        names = [e.name for e in entries]
        assert "good" in names
        assert "bad-file" not in names

    def test_malformed_file_does_not_raise(self, tmp_path):
        vault = _make_vault(tmp_path)
        bad = vault / "areas" / "bad.md"
        bad.write_bytes(b"\xff\xfe garbage \x00\x01\x02")
        recall = load_recall()
        entries = recall.build_area_map(vault)
        assert isinstance(entries, list)

    def test_one_liner_capped_at_120_chars(self, tmp_path):
        vault = _make_vault(tmp_path)
        long_summary = "x" * 200
        _write_area(vault, "verbosity", ["v"], summary=long_summary)
        recall = load_recall()
        entries = recall.build_area_map(vault)
        assert len(entries[0].one_liner) <= 120

    def test_keywords_capped(self, tmp_path):
        vault = _make_vault(tmp_path)
        many_kw = [f"kw{i}" for i in range(20)]
        _write_area(vault, "verbose-kw", many_kw, summary="Many keywords.")
        recall = load_recall()
        entries = recall.build_area_map(vault)
        assert len(entries[0].keywords) <= 8

    def test_summary_field_preferred_over_overview(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "prefer-summary", ["x"],
                    summary="Summary wins.", overview="Overview text.")
        recall = load_recall()
        entries = recall.build_area_map(vault)
        assert entries[0].one_liner == "Summary wins."

    def test_overview_sentence_used_as_fallback(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "no-summary", ["x"], overview="First sentence of overview.")
        recall = load_recall()
        entries = recall.build_area_map(vault)
        assert "First sentence of overview" in entries[0].one_liner

    def test_html_comment_overview_skipped(self, tmp_path):
        vault = _make_vault(tmp_path)
        p = vault / "areas" / "html-comment.md"
        p.write_text(
            "---\ntype: area\nname: html-comment\nkeywords: [x]\n---\n\n"
            "## Overview\n<!-- Just a placeholder -->\n\nBody text.\n"
        )
        recall = load_recall()
        entries = recall.build_area_map(vault)
        assert len(entries) == 1
        assert "<!--" not in entries[0].one_liner

    def test_area_no_summary_no_usable_overview_appears_empty_one_liner(self, tmp_path):
        vault = _make_vault(tmp_path)
        p = vault / "areas" / "empty-area.md"
        p.write_text(
            "---\ntype: area\nname: empty-area\nkeywords: [x]\n---\n\n"
            "## Overview\n<!-- Placeholder -->\n"
        )
        recall = load_recall()
        entries = recall.build_area_map(vault)
        assert len(entries) == 1
        assert entries[0].name == "empty-area"

    def test_empty_areas_dir_returns_empty_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        recall = load_recall()
        assert recall.build_area_map(vault) == []

    def test_missing_areas_dir_returns_empty_list(self, tmp_path):
        vault = tmp_path / "empty-vault"
        vault.mkdir()
        recall = load_recall()
        assert recall.build_area_map(vault) == []
