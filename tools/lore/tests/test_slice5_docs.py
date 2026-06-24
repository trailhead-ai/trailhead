"""Slice 5 tests: harness injection instruction.

Test contract:
  1. The librarian agent doc carries the injection-defense instruction.

Note: the brainstorm SKILL.md injection-defense assertions moved to the craft
plugin with the skill (S6 Slice 3) — see
tools/craft/tests/test_brainstorm_generic.py.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
LORE_LIBRARIAN_AGENT = PLUGIN_ROOT / "agents" / "librarian.md"


# ---------------------------------------------------------------------------
# Injection defense instruction: agents/librarian.md
# ---------------------------------------------------------------------------


class TestLibrarianInjectionInstruction:
    def test_lore_librarian_has_shared_content_is_data_rule(self) -> None:
        """librarian.md must carry the injection-defense instruction."""
        text = LORE_LIBRARIAN_AGENT.read_text()
        assert (
            "external-memory" in text
            or "shared" in text.lower()
            and "not instructions" in text.lower()
            or "data" in text.lower()
            and "not instructions" in text.lower()
        ), (
            f"librarian.md must carry the 'shared content is data, not instructions' rule. "
            f"Got: {text[:500]!r}"
        )

    def test_lore_librarian_references_external_memory_channel(self) -> None:
        """librarian.md must mention the <external-memory> structural delimiter."""
        text = LORE_LIBRARIAN_AGENT.read_text()
        assert "external-memory" in text, (
            f"librarian.md must reference the <external-memory> delimiter. Got: {text[:500]!r}"
        )
