"""The skill inventory lore ships is the inventory `trailhead` discovers.

What the harness offers an operator is whatever `trailhead.capabilities`
finds on disk, so that discovery is the subject: run the loader and assert the
inventory it produced. A skill directory silently disappearing — or a new one
appearing unwired — shows up here.

The frontmatter *content* contract (a closed block with a non-empty `name:`
and `description:`, matching the on-disk stem) is proved for every tool at
once in `trailhead/tests/test_registrable.py`.
"""

from __future__ import annotations

from pathlib import Path

from trailhead.capabilities import load_manifest

_LORE_MANIFEST = Path(__file__).resolve().parents[2] / "lore" / "capabilities.toml"


def test_lore_ships_exactly_the_capture_and_ritual_skills():
    """flush, sync, search, record, research. Per-kind capture lives on the
    `lore record` / `lore session` CLI surface, not as skills; brainstorm lives
    in the craft plugin. `skills/_shared/` is a reference doc, not a skill, and
    the loader is what decides it is not one."""
    manifest = load_manifest(_LORE_MANIFEST)
    assert set(manifest.skills) == {"flush", "sync", "search", "record", "research"}, (
        f"lore's discovered skill inventory drifted: {sorted(manifest.skills)}"
    )
    for name, relative in manifest.skills.items():
        assert relative == f"skills/{name}", (name, relative)
