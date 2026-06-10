"""Slice 0 — recall.py core: area-map build + match-pull + render banner.

TDD — written BEFORE recall.py exists. Every test must go RED on first run.

Covers (rederived from D23 area/D8b/D7/D1/D2/D9 invariants — NOT copy-edited
from test_subsystem_recall.py):

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

  recall_areas:
    - overlapping deferred/dead-end/lesson pulled by areas/surfaces overlap
    - non-overlapping note excluded
    - area profile body included
    - inactive lesson excluded; active included
    - recent cross-cutting item (within recency_days, no area overlap) included
    - stale cross-cutting item excluded
    - project-filtered deferred: different project excluded, agnostic included
    - dead-ends are universal (no project filter)
    - dedup: item overlapping two requested areas counted once
    - case-insensitive area_names (D-1)
    - non-existent area name -> zero-match, no KeyError
    - areas field is LIST-typed from parse_frontmatter (D-8b regression guard)
    - recency_days default is 90

  D-7 security:
    - recall_areas(vault, ["../escape"]) -> zero-match, no file read outside areas/
    - rendered banner contains structural label
    - RecallResult items carry source/layer field

  render_recall_banner:
    - leads "Recalled (areas: …) — N items"
    - differentiated zero-match: no-area-match message vs valid-area-empty message
    - never returns empty string
    - contains structural framing label
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

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
    for d in ("areas", "deferred", "dead-ends", "lessons", "decisions"):
        (vault / d).mkdir(parents=True)
    return vault


def _write_area(
    vault: Path,
    name: str,
    keywords: list[str],
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


def _write_deferred(
    vault: Path,
    name: str,
    areas: list[str] | None = None,
    surfaces: list[str] | None = None,
    project: str | None = None,
    status: str = "open",
    bucket: str | None = None,
) -> Path:
    folder = vault / "deferred"
    if bucket:
        (folder / bucket).mkdir(exist_ok=True)
        p = folder / bucket / f"{name}.md"
    else:
        p = folder / f"{name}.md"
    areas_str = ("[" + ", ".join(areas) + "]") if areas is not None else "[]"
    surfaces_str = ("[" + ", ".join(surfaces) + "]") if surfaces is not None else "[]"
    proj_line = f"project: {project}\n" if project else ""
    p.write_text(
        f"---\ntype: deferred\nstatus: {status}\n"
        f"areas: {areas_str}\nsurfaces: {surfaces_str}\n{proj_line}"
        f"next-check: 2026-07-01\n---\n\n# {name}\n\nSomething deferred.\n"
    )
    return p


def _write_dead_end(
    vault: Path,
    name: str,
    areas: list[str] | None = None,
    surfaces: list[str] | None = None,
    bucket: str | None = None,
) -> Path:
    folder = vault / "dead-ends"
    if bucket:
        (folder / bucket).mkdir(exist_ok=True)
        p = folder / bucket / f"{name}.md"
    else:
        p = folder / f"{name}.md"
    areas_str = ("[" + ", ".join(areas) + "]") if areas is not None else "[]"
    surfaces_str = ("[" + ", ".join(surfaces) + "]") if surfaces is not None else "[]"
    p.write_text(
        f"---\ntype: dead-end\nareas: {areas_str}\nsurfaces: {surfaces_str}\n"
        f"revive-condition: never\n---\n\n# {name}\n\nThis failed.\n"
    )
    return p


def _write_lesson(
    vault: Path,
    name: str,
    areas: list[str] | None = None,
    surfaces: list[str] | None = None,
    status: str = "active",
    bucket: str | None = None,
) -> Path:
    folder = vault / "lessons"
    if bucket:
        (folder / bucket).mkdir(exist_ok=True)
        p = folder / bucket / f"{name}.md"
    else:
        p = folder / f"{name}.md"
    areas_str = ("[" + ", ".join(areas) + "]") if areas is not None else "[]"
    surfaces_str = ("[" + ", ".join(surfaces) + "]") if surfaces is not None else "[]"
    p.write_text(
        f"---\ntype: lesson\nstatus: {status}\nareas: {areas_str}\n"
        f"surfaces: {surfaces_str}\nseverity: medium\n---\n\n# {name}\n\nLesson body.\n"
    )
    return p


def _write_decision(
    vault: Path,
    name: str,
    areas: list[str] | None = None,
    surfaces: list[str] | None = None,
    bucket: str | None = None,
) -> Path:
    folder = vault / "decisions"
    if bucket:
        (folder / bucket).mkdir(exist_ok=True)
        p = folder / bucket / f"{name}.md"
    else:
        p = folder / f"{name}.md"
    areas_str = ("[" + ", ".join(areas) + "]") if areas is not None else "[]"
    surfaces_str = ("[" + ", ".join(surfaces) + "]") if surfaces is not None else "[]"
    p.write_text(
        f"---\ntype: decision\nareas: {areas_str}\nsurfaces: {surfaces_str}\n"
        f"---\n\n# {name}\n\nDecision body.\n"
    )
    return p


def _recent_date(days_ago: int = 5) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _stale_date(days_ago: int = 180) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


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


# ---------------------------------------------------------------------------
# recall_areas — overlap pull
# ---------------------------------------------------------------------------

class TestRecallAreasOverlap:
    def test_deferred_with_matching_areas_pulled(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "oauth-work", areas=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        titles = [item.title for item in result.items]
        assert any("oauth-work" in t for t in titles)

    def test_deferred_with_surfaces_overlap_pulled(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "surfaces-match", surfaces=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        titles = [item.title for item in result.items]
        assert any("surfaces-match" in t for t in titles)

    def test_non_overlapping_deferred_excluded(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "unrelated", areas=["payments"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        titles = [item.title for item in result.items]
        assert not any("unrelated" in t for t in titles)

    def test_dead_end_with_matching_areas_pulled(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_dead_end(vault, "jwt-failed", areas=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        titles = [item.title for item in result.items]
        assert any("jwt-failed" in t for t in titles)

    def test_active_lesson_pulled(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_lesson(vault, "validate-tokens", areas=["auth"], status="active")
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        titles = [item.title for item in result.items]
        assert any("validate-tokens" in t for t in titles)

    def test_inactive_lesson_excluded(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_lesson(vault, "old-lesson", areas=["auth"], status="graduated")
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        titles = [item.title for item in result.items]
        assert not any("old-lesson" in t for t in titles)

    def test_decision_with_matching_areas_pulled(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_decision(vault, "use-jwt", areas=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        titles = [item.title for item in result.items]
        assert any("use-jwt" in t for t in titles)

    def test_areas_field_is_list_typed(self, tmp_path):
        """D-8b regression: frontmatter.parse_frontmatter returns list for areas,
        not the raw string '[auth]' that regenerate_indices.parse_frontmatter gives.

        If this test passes but no item is pulled, the scalar-string bug is back.
        """
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "list-check", areas=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        # The deferred must be pulled — if areas were treated as a string '[auth]',
        # set-intersection would fail and no item would be found.
        assert result.count >= 1, (
            "D-8b: areas field was not treated as a list — scalar-string bug"
        )
        for item in result.items:
            assert isinstance(item.source, str), "items must have source field"
            assert item.layer == "local", "items must have layer field set to 'local'"


# ---------------------------------------------------------------------------
# recall_areas — recent cross-cutting items
# ---------------------------------------------------------------------------

class TestRecallAreasCrossCutting:
    def test_recent_cross_cutting_item_included(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        p = vault / "lessons" / "recent-cross-cut.md"
        recent = _recent_date(5)
        p.write_text(
            f"---\ntype: lesson\nstatus: active\nareas: []\nsurfaces: []\n"
            f"date: {recent}\n---\n\n# recent-cross-cut\n\nRecent lesson.\n"
        )
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"], recency_days=90)
        titles = [item.title for item in result.items]
        assert any("recent-cross-cut" in t for t in titles)

    def test_stale_cross_cutting_item_excluded(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        p = vault / "lessons" / "stale-cross-cut.md"
        stale = _stale_date(200)
        p.write_text(
            f"---\ntype: lesson\nstatus: active\nareas: []\nsurfaces: []\n"
            f"date: {stale}\n---\n\n# stale-cross-cut\n\nOld lesson.\n"
        )
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"], recency_days=90)
        titles = [item.title for item in result.items]
        assert not any("stale-cross-cut" in t for t in titles)

    def test_14_day_boundary_10_days_ago_included(self, tmp_path):
        """D-1 amended: 14-day default. A 10-day-old cross-cutting item is included."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        p = vault / "lessons" / "ten-days-ago.md"
        ten_days = _recent_date(10)
        p.write_text(
            f"---\ntype: lesson\nstatus: active\nareas: []\nsurfaces: []\n"
            f"date: {ten_days}\n---\n\n# ten-days-ago\n\nTen day old lesson.\n"
        )
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])  # default 14 days
        titles = [item.title for item in result.items]
        assert any("ten-days-ago" in t for t in titles), (
            "A 10-day-old item must be included in the default 14-day window"
        )

    def test_14_day_boundary_20_days_ago_excluded(self, tmp_path):
        """D-1 amended: 14-day default. A 20-day-old cross-cutting item is excluded."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        p = vault / "lessons" / "twenty-days-ago.md"
        twenty_days = _recent_date(20)
        p.write_text(
            f"---\ntype: lesson\nstatus: active\nareas: []\nsurfaces: []\n"
            f"date: {twenty_days}\n---\n\n# twenty-days-ago\n\nTwenty day old lesson.\n"
        )
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])  # default 14 days
        titles = [item.title for item in result.items]
        assert not any("twenty-days-ago" in t for t in titles), (
            "A 20-day-old item must be excluded from the default 14-day window"
        )

    def test_cross_cutting_total_reflects_pre_cap_count(self, tmp_path):
        """RecallResult.cross_cutting_total is the pre-cap candidate count."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        # Write more cross-cutting items than the cap (10)
        for i in range(15):
            p = vault / "lessons" / f"cc-item-{i}.md"
            recent = _recent_date(5)
            p.write_text(
                f"---\ntype: lesson\nstatus: active\nareas: []\nsurfaces: []\n"
                f"date: {recent}\n---\n\n# cc-item-{i}\n\nCross-cutting {i}.\n"
            )
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        assert result.cross_cutting_total >= 15, (
            f"cross_cutting_total should reflect all 15 candidates, got {result.cross_cutting_total}"
        )
        # The actual added items are capped at 10
        cross_items = [it for it in result.items if it.type == "cross-cutting"]
        assert len(cross_items) <= 10


# ---------------------------------------------------------------------------
# recall_areas — project filter
# ---------------------------------------------------------------------------

class TestRecallAreasProjectFilter:
    def test_deferred_for_different_project_excluded(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "other-proj-work", areas=["auth"], project="other-app")
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"], project="my-app")
        titles = [item.title for item in result.items]
        assert not any("other-proj-work" in t for t in titles)

    def test_project_agnostic_deferred_included(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "agnostic-work", areas=["auth"], project=None)
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"], project="my-app")
        titles = [item.title for item in result.items]
        assert any("agnostic-work" in t for t in titles)

    def test_dead_ends_universal_not_project_filtered(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_dead_end(vault, "universal-dead-end", areas=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"], project="completely-different")
        titles = [item.title for item in result.items]
        assert any("universal-dead-end" in t for t in titles)


# ---------------------------------------------------------------------------
# recall_areas — dedup, case-normalize, edge cases
# ---------------------------------------------------------------------------

class TestRecallAreasEdgeCases:
    def test_item_overlapping_two_areas_counted_once(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_area(vault, "payments", ["billing"], summary="Payments area.")
        _write_deferred(vault, "cross-cut-deferred", areas=["auth", "payments"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth", "payments"])
        titles = [item.title for item in result.items]
        count = sum(1 for t in titles if "cross-cut-deferred" in t)
        assert count == 1, f"Expected once, got {count}: {titles}"

    def test_case_insensitive_area_names(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "case-test-work", areas=["auth"])
        recall = load_recall()
        result_lower = recall.recall_areas(vault, ["auth"])
        result_upper = recall.recall_areas(vault, ["AUTH"])
        assert result_lower.count == result_upper.count

    def test_non_existent_area_name_zero_match_no_error(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        recall = load_recall()
        result = recall.recall_areas(vault, ["nonexistent-area"])
        assert isinstance(result.count, int)
        assert result.count >= 0

    def test_recency_days_default_is_14(self, tmp_path):
        """D-1 amended: 14-day cross-cutting window (90d returned ~649 items on
        the live vault — noise that diluted area-tagged signal)."""
        vault = _make_vault(tmp_path)
        recall = load_recall()
        import inspect
        sig = inspect.signature(recall.recall_areas)
        assert sig.parameters["recency_days"].default == 14

    def test_result_items_carry_source_layer_field(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "work-item", areas=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        for item in result.items:
            assert hasattr(item, "source"), "RecallItem must have source field"
            assert hasattr(item, "layer"), "RecallItem must have layer field"
            assert item.layer == "local"


# ---------------------------------------------------------------------------
# D-7 security
# ---------------------------------------------------------------------------

class TestD7Security:
    def test_path_traversal_area_name_zero_match(self, tmp_path):
        """--areas ../escape must not read outside areas/ dir."""
        vault = _make_vault(tmp_path)
        secret = tmp_path / "secret.md"
        secret.write_text("---\ntype: area\nname: escaped\nkeywords: [x]\n---\n")
        _write_area(vault, "safe", ["safe"], summary="Safe area.")
        recall = load_recall()
        result = recall.recall_areas(vault, ["../escape"])
        assert result.count == 0

    def test_path_traversal_does_not_read_outside_areas(self, tmp_path):
        """The traversal attempt must not cause any file read outside vault/areas/."""
        vault = _make_vault(tmp_path)
        recall = load_recall()
        result = recall.recall_areas(vault, ["../etc/passwd", "../../secret"])
        assert result.count == 0

    def test_render_banner_contains_structural_label(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "work", areas=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        banner = recall.render_recall_banner(result)
        assert "lore memory" in banner.lower() or "--- " in banner


# ---------------------------------------------------------------------------
# render_recall_banner
# ---------------------------------------------------------------------------

class TestRenderRecallBanner:
    def test_leads_with_recalled_header(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "work", areas=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        banner = recall.render_recall_banner(result)
        # The banner contains the Recalled header (may follow a framing label)
        assert "Recalled (areas:" in banner

    def test_shows_item_count(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "work", areas=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        banner = recall.render_recall_banner(result)
        assert f"— {result.count} item" in banner

    def test_zero_match_no_area_found_message(self, tmp_path):
        """No area matched the name -> differentiated message mentioning check."""
        vault = _make_vault(tmp_path)
        recall = load_recall()
        result = recall.recall_areas(vault, ["nonexistent-xyz"])
        banner = recall.render_recall_banner(result)
        assert banner
        assert "nonexistent-xyz" in banner or "check" in banner.lower()

    def test_zero_match_valid_area_empty_message(self, tmp_path):
        """Valid area exists but zero items -> 'no tagged notes' message."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "empty-area", ["x"], summary="Empty area.")
        recall = load_recall()
        result = recall.recall_areas(vault, ["empty-area"])
        banner = recall.render_recall_banner(result)
        assert banner
        assert banner.strip() != ""

    def test_banner_never_empty_string(self, tmp_path):
        vault = _make_vault(tmp_path)
        recall = load_recall()
        result = recall.recall_areas(vault, [])
        banner = recall.render_recall_banner(result)
        assert banner.strip() != ""

    def test_banner_contains_structural_framing(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth area.")
        _write_deferred(vault, "work", areas=["auth"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth"])
        banner = recall.render_recall_banner(result)
        assert "---" in banner

    def test_multiple_areas_listed_in_header(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_area(vault, "auth", ["oauth"], summary="Auth.")
        _write_area(vault, "payments", ["billing"], summary="Payments.")
        _write_deferred(vault, "w1", areas=["auth"])
        _write_deferred(vault, "w2", areas=["payments"])
        recall = load_recall()
        result = recall.recall_areas(vault, ["auth", "payments"])
        banner = recall.render_recall_banner(result)
        assert "auth" in banner
        assert "payments" in banner
