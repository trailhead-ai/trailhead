"""Tests for frontmatter block-style list parsing + wikilink unwrapping.

All fixtures use SYNTHETIC vocabulary (synth-alpha, synth-tool, synth-spec-slug,
etc.) per the public-repo fixture discipline axiom — never real brain content.

Test contract (all must fail before the fix, pass after):
- Block-style surfaces:  - '[[area/synth-alpha]]' → ["synth-alpha"]
- Inline surfaces: ["[[area/synth-alpha]]", "[[tools/synth-tool]]"] → ["synth-alpha", "synth-tool"]
- Prefix coverage: area/, tools/, AND task/ all strip to bare slug for overlap keys
  (singular vault dirs — areas/ and tasks/ prefixes are retired)
- related-spec: '[[specs/synth-spec-slug]]' → "specs/synth-spec-slug"
  (full path, NOT slug-reduced, NOT a list)
- Bare-slug forms (inline [a, b] and block - a) unchanged (regression guard)

(The end-to-end `recall_areas` overlap cases were removed when the
recall command path was retired; membership is now an index-projection property
covered by test_index_projection.py.)
"""

from __future__ import annotations

from conftest import load_script
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fm(text: str) -> dict:
    """Parse frontmatter from a raw YAML string (between the ---s)."""
    fm_mod = load_script("lore.search.frontmatter")
    full = f"---\n{text}\n---\n\nbody\n"
    return fm_mod._parse_fm_text(full)


# ---------------------------------------------------------------------------
# Block-style list parsing
# ---------------------------------------------------------------------------


class TestBlockStyleListParsing:
    @pytest.mark.parametrize(
        "block",
        [
            pytest.param("surfaces:\n  - synth-alpha", id="single"),
            pytest.param("surfaces:\n  - 'synth-alpha'", id="quoted"),
            pytest.param('surfaces:\n  - "synth-alpha"', id="double_quoted"),
        ],
    )
    def test_block_style_bare_slug_is_kept(self, block):
        result = _fm(block)
        assert result["surfaces"] == ["synth-alpha"]

    def test_block_style_multiple_bare_slugs(self):
        """Block-style list with multiple bare slug entries."""
        result = _fm("surfaces:\n  - synth-alpha\n  - synth-beta")
        assert result["surfaces"] == ["synth-alpha", "synth-beta"]


# ---------------------------------------------------------------------------
# Wikilink unwrapping for overlap keys (slug-reduced)
# ---------------------------------------------------------------------------


class TestWikilinkUnwrapSlugReduced:
    @pytest.mark.parametrize(
        ("block", "key", "expected"),
        [
            pytest.param(
                "surfaces:\n  - '[[area/synth-alpha]]'",
                "surfaces",
                "synth-alpha",
                id="block_wikilink_areas_prefix_stripped",
            ),
            pytest.param(
                "surfaces:\n  - '[[tools/synth-tool]]'",
                "surfaces",
                "synth-tool",
                id="block_wikilink_tools_prefix_stripped",
            ),
            pytest.param(
                "surfaces:\n  - '[[task/synth-task]]'",
                "surfaces",
                "synth-task",
                id="block_wikilink_tasks_prefix_stripped",
            ),
            pytest.param(
                'surfaces:\n  - "[[area/synth-alpha]]"',
                "surfaces",
                "synth-alpha",
                id="block_wikilink_double_quoted",
            ),
            pytest.param("areas:\n  - '[[area/synth-alpha]]'", "areas", "synth-alpha", id="areas_key_slug_reduced"),
            pytest.param(
                "related-areas:\n  - '[[area/synth-alpha]]'",
                "related-areas",
                "synth-alpha",
                id="related_areas_key_slug_reduced",
            ),
        ],
    )
    def test_block_wikilink_is_unwrapped_and_slug_reduced(self, block, key, expected):
        result = _fm(block)
        assert result[key] == [expected]

    def test_inline_wikilink_list_areas_stripped(self):
        """Inline list with singular wikilink entries → slug-reduced."""
        result = _fm('surfaces: ["[[area/synth-alpha]]", "[[tools/synth-tool]]"]')
        assert result["surfaces"] == ["synth-alpha", "synth-tool"]

    def test_all_three_prefixes_for_each_overlap_key(self):
        """area/, tools/, task/ all strip for surfaces key (singular dirs)."""
        for prefix, slug in [
            ("area", "synth-alpha"),
            ("tools", "synth-tool"),
            ("task", "synth-task"),
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

    def test_related_task_keeps_full_path(self):
        """related-task: '[[tasks/synth-task-slug]]' → 'tasks/synth-task-slug'
        (scalar, full path)."""
        result = _fm("related-task: '[[tasks/synth-task-slug]]'")
        assert result["related-task"] == "tasks/synth-task-slug"
        assert not isinstance(result["related-task"], list)

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
    @pytest.mark.parametrize(
        ("text", "key"),
        [
            pytest.param("surfaces: [synth-alpha, synth-beta]", "surfaces", id="inline_bare_slug_list_unchanged"),
            pytest.param(
                "surfaces: ['synth-alpha', 'synth-beta']",
                "surfaces",
                id="inline_quoted_slug_list_unchanged",
            ),
            pytest.param(
                "subsystems:\n  - synth-alpha\n  - synth-beta",
                "subsystems",
                id="block_bare_slug_regression",
            ),
        ],
    )
    def test_list_form_parses_to_bare_slugs(self, text, key):
        result = _fm(text)
        assert result[key] == ["synth-alpha", "synth-beta"]

    def test_scalar_no_wikilink_unchanged(self):
        """Ordinary scalar fields still parse as strings."""
        result = _fm("status: open")
        assert result["status"] == "open"

    def test_scalar_quoted_no_wikilink_unchanged(self):
        """Quoted scalar still strips outer quotes."""
        result = _fm('title: "My Note"')
        assert result["title"] == "My Note"
