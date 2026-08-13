"""area_map.py core: area-map build (the kept area-menu path).

Covers ``build_area_map`` — the on-demand area menu served by ``lore areas``.
It formerly also served a SessionStart pointer; that hook has been retired.
``lore search`` is the query interface.

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

    def test_control_char_stripped_from_name(self, tmp_path):
        """A control character (here ESC, \\x1b) embedded in the `name`
        frontmatter field must not survive into the rendered `AreaEntry` —
        `render_area_menu` concatenates it into a plaintext line with no
        escaping, so an ANSI escape or an embedded control char reaches an
        AI agent's context verbatim."""
        vault = _make_vault(tmp_path)
        p = vault / "area" / "escaped.md"
        p.write_text(
            "---\ntype: area\nname: esc\x1b[31mape\nkeywords: [x]\n"
            "summary: fine\n---\n\n## Overview\n\nBody.\n"
        )
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert len(entries) == 1
        assert "\x1b" not in entries[0].name

    def test_control_char_stripped_from_keyword(self, tmp_path):
        vault = _make_vault(tmp_path)
        p = vault / "area" / "kw-escape.md"
        p.write_text(
            "---\ntype: area\nname: kw-escape\nkeywords: [safe, ba\x1b[2Jd]\n"
            "summary: fine\n---\n\n## Overview\n\nBody.\n"
        )
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert len(entries) == 1
        assert all("\x1b" not in k for k in entries[0].keywords)

    def test_control_char_stripped_from_one_liner(self, tmp_path):
        vault = _make_vault(tmp_path)
        p = vault / "area" / "summary-escape.md"
        p.write_text(
            "---\ntype: area\nname: summary-escape\nkeywords: [x]\n"
            "summary: legit text\x1b[0m more text\n---\n\n## Overview\n\nBody.\n"
        )
        area_map = load_area_map()
        entries = area_map.build_area_map(vault)
        assert len(entries) == 1
        assert "\x1b" not in entries[0].one_liner

    def test_empty_areas_dir_returns_empty_list(self, tmp_path):
        vault = _make_vault(tmp_path)
        area_map = load_area_map()
        assert area_map.build_area_map(vault) == []

    def test_missing_areas_dir_returns_empty_list(self, tmp_path):
        vault = tmp_path / "empty-vault"
        vault.mkdir()
        area_map = load_area_map()
        assert area_map.build_area_map(vault) == []


# ---------------------------------------------------------------------------
# build_area_map_multi
# ---------------------------------------------------------------------------


class TestBuildAreaMapMulti:
    def test_areas_from_both_vaults_present(self, tmp_path):
        v1 = tmp_path / "v1"
        v2 = tmp_path / "v2"
        (v1 / "area").mkdir(parents=True)
        (v2 / "area").mkdir(parents=True)
        _write_area(v1, "auth", ["oauth"], summary="Auth in v1.")
        _write_area(v2, "billing", ["stripe"], summary="Billing in v2.")

        area_map = load_area_map()
        entries = area_map.build_area_map_multi([v1, v2])
        names = [e.name for e in entries]

        assert "auth" in names
        assert "billing" in names

    def test_same_name_in_two_vaults_dedupes_to_first_in_order(self, tmp_path):
        v1 = tmp_path / "v1"
        v2 = tmp_path / "v2"
        (v1 / "area").mkdir(parents=True)
        (v2 / "area").mkdir(parents=True)
        _write_area(v1, "auth", ["oauth"], summary="First vault wins.")
        _write_area(v2, "auth", ["saml"], summary="Second vault loses.")

        area_map = load_area_map()
        entries = area_map.build_area_map_multi([v1, v2])
        matching = [e for e in entries if e.name == "auth"]

        assert len(matching) == 1
        assert matching[0].one_liner == "First vault wins."

    def test_merged_order_is_alpha_across_vault_boundaries(self, tmp_path):
        v1 = tmp_path / "v1"
        v2 = tmp_path / "v2"
        (v1 / "area").mkdir(parents=True)
        (v2 / "area").mkdir(parents=True)
        _write_area(v1, "zebra", ["z"], summary="Z area.")
        _write_area(v2, "alpha", ["a"], summary="A area.")

        area_map = load_area_map()
        entries = area_map.build_area_map_multi([v1, v2])
        names = [e.name for e in entries]

        assert names == sorted(names)

    def test_one_liner_cap_applied_to_merged_set(self, tmp_path):
        v1 = tmp_path / "v1"
        (v1 / "area").mkdir(parents=True)
        _write_area(v1, "verbosity", ["v"], summary="x" * 200)

        area_map = load_area_map()
        entries = area_map.build_area_map_multi([v1])

        assert len(entries[0].one_liner) <= 120

    def test_keywords_cap_applied_to_merged_set(self, tmp_path):
        v1 = tmp_path / "v1"
        (v1 / "area").mkdir(parents=True)
        many_kw = [f"kw{i}" for i in range(20)]
        _write_area(v1, "verbose-kw", many_kw, summary="Many keywords.")

        area_map = load_area_map()
        entries = area_map.build_area_map_multi([v1])

        assert len(entries[0].keywords) <= 8

    def test_one_vault_raising_does_not_lose_the_others(self, tmp_path):
        v1 = tmp_path / "v1"
        v2 = tmp_path / "v2"
        (v1 / "area").mkdir(parents=True)
        (v2 / "area").mkdir(parents=True)
        _write_area(v1, "auth", ["oauth"], summary="Auth area.")
        _write_area(v2, "billing", ["stripe"], summary="Billing area.")

        area_map = load_area_map()
        original = area_map.build_area_map

        def _raise_for_v1(vault, *a, **kw):
            if Path(vault) == v1:
                raise RuntimeError("boom")
            return original(vault, *a, **kw)

        from unittest import mock

        with mock.patch.object(area_map, "build_area_map", side_effect=_raise_for_v1):
            entries = area_map.build_area_map_multi([v1, v2])

        names = [e.name for e in entries]
        assert "billing" in names
        assert "auth" not in names

    def test_empty_vault_list_returns_empty_list(self):
        area_map = load_area_map()
        assert area_map.build_area_map_multi([]) == []

    def test_same_name_within_one_vault_both_kept_not_deduped(self, tmp_path):
        """Dedup is a cross-vault concern only. Two files in the SAME root
        colliding on frontmatter ``name`` (the file stem differs from the
        declared name) must both survive the merge — this call has only one
        root, so it must render byte-identically to calling
        ``build_area_map`` directly on that root (the pre-multi-vault,
        single-vault path), which applies no dedup at all."""
        v1 = tmp_path / "v1"
        (v1 / "area").mkdir(parents=True)
        (v1 / "area" / "auth-1.md").write_text(
            "---\ntype: area\nname: auth\nkeywords: [oauth]\nsummary: First file.\n---\n"
        )
        (v1 / "area" / "auth-2.md").write_text(
            "---\ntype: area\nname: auth\nkeywords: [saml]\nsummary: Second file.\n---\n"
        )

        area_map = load_area_map()
        direct = area_map.build_area_map(v1)
        merged = area_map.build_area_map_multi([v1])

        assert len(direct) == 2
        assert [e.one_liner for e in merged] == [e.one_liner for e in direct]

    def test_errors_out_param_populated_with_vault_and_exception(self, tmp_path):
        v1 = tmp_path / "v1"
        v2 = tmp_path / "v2"
        (v1 / "area").mkdir(parents=True)
        (v2 / "area").mkdir(parents=True)
        _write_area(v2, "billing", ["stripe"], summary="Billing area.")

        area_map = load_area_map()
        original = area_map.build_area_map
        boom = RuntimeError("boom")

        def _raise_for_v1(vault, *a, **kw):
            if Path(vault) == v1:
                raise boom
            return original(vault, *a, **kw)

        from unittest import mock

        errors: list = []
        with mock.patch.object(area_map, "build_area_map", side_effect=_raise_for_v1):
            entries = area_map.build_area_map_multi([v1, v2], errors=errors)

        assert [e.name for e in entries] == ["billing"]
        assert errors == [(v1, boom)]

    def test_errors_out_param_left_untouched_when_nothing_fails(self, tmp_path):
        v1 = tmp_path / "v1"
        (v1 / "area").mkdir(parents=True)
        _write_area(v1, "auth", ["oauth"], summary="Auth area.")

        area_map = load_area_map()
        errors: list = []
        entries = area_map.build_area_map_multi([v1], errors=errors)

        assert [e.name for e in entries] == ["auth"]
        assert errors == []
