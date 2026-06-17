"""P1-A tests: frontmatter block-style list parsing + wikilink unwrapping.

All fixtures use SYNTHETIC vocabulary (synth-alpha, synth-tool, synth-spec-slug,
etc.) per the public-repo fixture discipline axiom — never real brain content.

Test contract (all must fail before the fix, pass after):
- Block-style surfaces:  - '[[areas/synth-alpha]]' → ["synth-alpha"]
- Inline surfaces: ["[[areas/synth-alpha]]", "[[tools/synth-tool]]"] → ["synth-alpha", "synth-tool"]
- Prefix coverage: areas/, tools/, AND plans/ all strip to bare slug for overlap keys
- related-spec: '[[specs/synth-spec-slug]]' → "specs/synth-spec-slug"
  (full path, NOT slug-reduced, NOT a list)
- Bare-slug forms (inline [a, b] and block - a) unchanged (regression guard)
- End-to-end (rederived): recall_areas finds deferred notes with block-style
  wikilink surfaces: [[areas/...]] format (D23 area semantics)
"""
from __future__ import annotations

import sys
from pathlib import Path


from conftest import load_script


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fm(text: str) -> dict:
    """Parse frontmatter from a raw YAML string (between the ---s)."""
    fm_mod = load_script("frontmatter")
    full = f"---\n{text}\n---\n\nbody\n"
    return fm_mod._parse_fm_text(full)


# ---------------------------------------------------------------------------
# Block-style list parsing
# ---------------------------------------------------------------------------

class TestBlockStyleListParsing:
    def test_block_style_single_bare_slug(self):
        """Block-style list with a bare slug entry."""
        result = _fm("surfaces:\n  - synth-alpha")
        assert result["surfaces"] == ["synth-alpha"]

    def test_block_style_multiple_bare_slugs(self):
        """Block-style list with multiple bare slug entries."""
        result = _fm("surfaces:\n  - synth-alpha\n  - synth-beta")
        assert result["surfaces"] == ["synth-alpha", "synth-beta"]

    def test_block_style_quoted_bare_slug(self):
        """Block-style list item with single quotes around a bare slug."""
        result = _fm("surfaces:\n  - 'synth-alpha'")
        assert result["surfaces"] == ["synth-alpha"]

    def test_block_style_double_quoted_bare_slug(self):
        """Block-style list item with double quotes around a bare slug."""
        result = _fm('surfaces:\n  - "synth-alpha"')
        assert result["surfaces"] == ["synth-alpha"]


# ---------------------------------------------------------------------------
# Wikilink unwrapping for overlap keys (slug-reduced)
# ---------------------------------------------------------------------------

class TestWikilinkUnwrapSlugReduced:
    def test_block_wikilink_areas_prefix_stripped(self):
        """Block-style [[areas/synth-alpha]] → synth-alpha for surfaces key."""
        result = _fm("surfaces:\n  - '[[areas/synth-alpha]]'")
        assert result["surfaces"] == ["synth-alpha"]

    def test_block_wikilink_tools_prefix_stripped(self):
        """Block-style [[tools/synth-tool]] → synth-tool for surfaces key."""
        result = _fm("surfaces:\n  - '[[tools/synth-tool]]'")
        assert result["surfaces"] == ["synth-tool"]

    def test_block_wikilink_plans_prefix_stripped(self):
        """Block-style [[plans/synth-plan]] → synth-plan for surfaces key."""
        result = _fm("surfaces:\n  - '[[plans/synth-plan]]'")
        assert result["surfaces"] == ["synth-plan"]

    def test_block_wikilink_double_quoted(self):
        """Block-style double-quoted wikilink → slug-reduced."""
        result = _fm('surfaces:\n  - "[[areas/synth-alpha]]"')
        assert result["surfaces"] == ["synth-alpha"]

    def test_inline_wikilink_list_areas_stripped(self):
        """Inline list with wikilink entries → slug-reduced."""
        result = _fm('surfaces: ["[[areas/synth-alpha]]", "[[tools/synth-tool]]"]')
        assert result["surfaces"] == ["synth-alpha", "synth-tool"]

    def test_areas_key_slug_reduced(self):
        """areas: is an overlap key — slug-reduce applies."""
        result = _fm("areas:\n  - '[[areas/synth-alpha]]'")
        assert result["areas"] == ["synth-alpha"]

    def test_related_areas_key_slug_reduced(self):
        """related-areas: is an overlap key — slug-reduce applies."""
        result = _fm("related-areas:\n  - '[[areas/synth-alpha]]'")
        assert result["related-areas"] == ["synth-alpha"]

    def test_all_three_prefixes_for_each_overlap_key(self):
        """areas/, tools/, plans/ all strip for surfaces key."""
        for prefix, slug in [
            ("areas", "synth-alpha"),
            ("tools", "synth-tool"),
            ("plans", "synth-plan"),
        ]:
            result = _fm(f"surfaces:\n  - '[[{prefix}/{slug}]]'")
            assert result["surfaces"] == [slug], (
                f"Expected [{slug!r}] for prefix {prefix!r}, got {result['surfaces']}"
            )


# ---------------------------------------------------------------------------
# Non-overlap wikilink fields: full path retained, NOT slug-reduced, NOT a list
# ---------------------------------------------------------------------------

class TestNonOverlapWikilinkFields:
    def test_related_spec_keeps_full_path(self):
        """related-spec: '[[specs/synth-spec-slug]]' → 'specs/synth-spec-slug'
        (scalar, full path)."""
        result = _fm("related-spec: '[[specs/synth-spec-slug]]'")
        assert result["related-spec"] == "specs/synth-spec-slug"
        assert not isinstance(result["related-spec"], list)

    def test_related_plan_keeps_full_path(self):
        """related-plan: '[[plans/synth-plan-slug]]' → 'plans/synth-plan-slug'
        (scalar, full path)."""
        result = _fm("related-plan: '[[plans/synth-plan-slug]]'")
        assert result["related-plan"] == "plans/synth-plan-slug"
        assert not isinstance(result["related-plan"], list)

    def test_scalar_wikilink_unwraps_in_place(self):
        """Arbitrary scalar wikilink field → full target string, not a list."""
        result = _fm("related-spec: '[[specs/synth-spec-slug]]'")
        # Must be a string, not a list; must be the full path
        val = result["related-spec"]
        assert isinstance(val, str)
        assert val == "specs/synth-spec-slug"

    def test_scalar_wikilink_not_slug_reduced(self):
        """For non-overlap keys, subsystems/ prefix is NOT stripped."""
        # If related-spec happens to point at subsystems/, it still keeps full path
        result = _fm("related-spec: '[[subsystems/synth-alpha]]'")
        assert result["related-spec"] == "subsystems/synth-alpha"


# ---------------------------------------------------------------------------
# Regression guard: existing forms still parse unchanged
# ---------------------------------------------------------------------------

class TestRegressionGuard:
    def test_inline_bare_slug_list_unchanged(self):
        """Existing inline [a, b] bare-slug lists must still parse correctly."""
        result = _fm("surfaces: [synth-alpha, synth-beta]")
        assert result["surfaces"] == ["synth-alpha", "synth-beta"]

    def test_inline_quoted_slug_list_unchanged(self):
        """Existing inline ['a', 'b'] lists still parse."""
        result = _fm("surfaces: ['synth-alpha', 'synth-beta']")
        assert result["surfaces"] == ["synth-alpha", "synth-beta"]

    def test_scalar_no_wikilink_unchanged(self):
        """Ordinary scalar fields still parse as strings."""
        result = _fm("status: open")
        assert result["status"] == "open"

    def test_scalar_quoted_no_wikilink_unchanged(self):
        """Quoted scalar still strips outer quotes."""
        result = _fm('title: "My Note"')
        assert result["title"] == "My Note"

    def test_block_bare_slug_regression(self):
        """Block-style list with bare slugs (no wikilink) must parse as-is."""
        result = _fm("subsystems:\n  - synth-alpha\n  - synth-beta")
        assert result["subsystems"] == ["synth-alpha", "synth-beta"]


# ---------------------------------------------------------------------------
# End-to-end: recall._has_overlap with block-style wikilink surfaces
# (MUST FAIL before the fix, PASS after — the verified break)
# ---------------------------------------------------------------------------

def _load_recall():
    """Load recall module with sys.modules registration so @dataclass resolves."""
    import importlib.util as _ilu
    scripts_dir = Path(__file__).parent.parent / "plugins" / "lore" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    for cached in ("recall", "vault", "frontmatter", "status_validator",
                   "regenerate_indices", "sessions"):
        sys.modules.pop(cached, None)
    spec = _ilu.spec_from_file_location("recall", scripts_dir / "recall.py")
    mod = _ilu.module_from_spec(spec)
    sys.modules["recall"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for d in ("areas", "deferred", "dead-ends", "lessons", "sessions"):
        (vault / d).mkdir(parents=True)
    return vault


def _write_area(vault: Path, name: str) -> Path:
    """Write a minimal area profile."""
    p = vault / "areas" / f"{name}.md"
    p.write_text(
        f"---\ntype: area\nname: {name}\nkeywords: [{name}]\n---\n\n"
        f"## Overview\nThe {name} area.\n"
    )
    return p


def _write_deferred_block_wikilink(vault: Path, name: str, area_slug: str) -> Path:
    """Write a deferred note using block-style wikilink surfaces.

    Uses [[areas/<slug>]] format — the current slug-reduced overlap key.
    """
    p = vault / "deferred" / f"{name}.md"
    p.write_text(
        f"---\n"
        f"type: deferred\n"
        f"status: open\n"
        f"surfaces:\n"
        f"  - '[[areas/{area_slug}]]'\n"
        f"next-check: 2026-07-01\n"
        f"---\n\n"
        f"# {name}\n\nSomething deferred.\n"
    )
    return p


class TestEndToEndRecallOverlap:
    def test_block_wikilink_surfaces_matches_overlap(self, tmp_path):
        """End-to-end: recall_areas finds a deferred note whose surfaces: uses
        block-style [[areas/synth-alpha]] wikilink.

        Before the fix: surfaces parses to '' (empty string) → no match.
        After the fix: surfaces parses to ['synth-alpha'] → match.
        """
        vault = _make_vault(tmp_path)
        _write_area(vault, "synth-alpha")
        _write_deferred_block_wikilink(vault, "synth-deferred-item", "synth-alpha")

        recall_mod = _load_recall()
        result = recall_mod.recall_areas(vault, ["synth-alpha"])
        item_names = [item.title for item in result.items]
        assert any("synth-deferred-item" in n for n in item_names), (
            f"recall_areas did not find 'synth-deferred-item'. Items: {item_names}. "
            f"Block-style wikilink surfaces: not being parsed correctly."
        )

    def test_relevant_deferred_finds_block_wikilink_note(self, tmp_path):
        """recall_areas returns the block-wikilink deferred note when the area
        slug matches — rederived from the old _relevant_deferred shape test."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "synth-alpha")
        _write_deferred_block_wikilink(vault, "synth-deferred-item", "synth-alpha")

        recall_mod = _load_recall()
        result = recall_mod.recall_areas(vault, ["synth-alpha"])
        assert result.count >= 1, (
            f"Expected at least 1 item, got {result.count}. "
            f"Block-style wikilink surfaces: not visible to recall_areas."
        )
        item_names = [item.title for item in result.items]
        assert any("synth-deferred-item" in n for n in item_names)

    def test_inline_wikilink_surfaces_also_matches(self, tmp_path):
        """Inline wikilink list [[areas/...]] format also works end-to-end."""
        vault = _make_vault(tmp_path)
        _write_area(vault, "synth-alpha")
        _write_area(vault, "synth-tool")
        p = vault / "deferred" / "synth-inline-item.md"
        p.write_text(
            '---\n'
            'type: deferred\n'
            'status: open\n'
            'surfaces: ["[[areas/synth-alpha]]", "[[areas/synth-tool]]"]\n'
            'next-check: 2026-07-01\n'
            '---\n\n'
            '# synth-inline-item\n\nSomething deferred.\n'
        )
        recall_mod = _load_recall()
        # Requesting either area should pull the inline-wikilink deferred note
        for area in ["synth-alpha", "synth-tool"]:
            result = recall_mod.recall_areas(vault, [area])
            item_names = [item.title for item in result.items]
            assert any("synth-inline-item" in n for n in item_names), (
                f"Expected synth-inline-item when requesting area '{area}', "
                f"got: {item_names}"
            )
