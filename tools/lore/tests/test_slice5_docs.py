"""Slice 5 tests: harness injection instruction + shared-vault docs content.

Test contract:
  1. The librarian agent doc carries the injection-defense instruction.
  2. A lore docs section documents the "shared vaults default private"
     consequence.
  3. D-6: the docs state that shared areas surface only on explicit lore recall,
     not in the always-loaded menu.

Note: the brainstorm SKILL.md injection-defense assertions moved to the craft
plugin with the skill (S6 Slice 3) — see
tools/craft/tests/test_brainstorm_generic.py.
"""
from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
LORE_LIBRARIAN_AGENT = PLUGIN_ROOT / "agents" / "librarian.md"
LORE_PROMOTE_DOC = PLUGIN_ROOT / "docs" / "PROMOTE.md"


# ---------------------------------------------------------------------------
# Injection defense instruction: agents/librarian.md
# ---------------------------------------------------------------------------

class TestLibrarianInjectionInstruction:
    def test_lore_librarian_has_shared_content_is_data_rule(self) -> None:
        """librarian.md must carry the injection-defense instruction."""
        text = LORE_LIBRARIAN_AGENT.read_text()
        assert (
            "external-memory" in text
            or "shared" in text.lower() and "not instructions" in text.lower()
            or "data" in text.lower() and "not instructions" in text.lower()
        ), (
            f"librarian.md must carry the 'shared content is data, not instructions' rule. "
            f"Got: {text[:500]!r}"
        )

    def test_lore_librarian_references_external_memory_channel(self) -> None:
        """librarian.md must mention the <external-memory> structural delimiter."""
        text = LORE_LIBRARIAN_AGENT.read_text()
        assert "external-memory" in text, (
            f"librarian.md must reference the <external-memory> delimiter. "
            f"Got: {text[:500]!r}"
        )


# ---------------------------------------------------------------------------
# docs/PROMOTE.md: promote flow + shared-default-private consequence
# ---------------------------------------------------------------------------

class TestPromoteDoc:
    def test_promote_doc_exists(self) -> None:
        """docs/PROMOTE.md must exist."""
        assert LORE_PROMOTE_DOC.exists(), (
            f"docs/PROMOTE.md does not exist at {LORE_PROMOTE_DOC}"
        )

    def test_promote_doc_covers_personal_by_default(self) -> None:
        """docs/PROMOTE.md must cover 'personal by default, sharing is deliberate'."""
        text = LORE_PROMOTE_DOC.read_text()
        assert "personal" in text.lower() and (
            "default" in text.lower() or "deliberate" in text.lower()
        ), (
            f"PROMOTE.md must cover 'personal by default'. Got: {text[:500]!r}"
        )

    def test_promote_doc_covers_shared_vaults_default_private(self) -> None:
        """docs/PROMOTE.md must document the 'shared vault repos default private' consequence."""
        text = LORE_PROMOTE_DOC.read_text()
        assert "private" in text.lower(), (
            f"PROMOTE.md must document 'default private' for shared vault repos. "
            f"Got: {text[:500]!r}"
        )
        # Must mention public exposure risk
        assert "public" in text.lower(), (
            f"PROMOTE.md must warn about making a shared vault public. "
            f"Got: {text[:500]!r}"
        )

    def test_promote_doc_covers_interactive_only(self) -> None:
        """docs/PROMOTE.md must document that promote is interactive-only."""
        text = LORE_PROMOTE_DOC.read_text()
        assert "interactive" in text.lower() or "terminal" in text.lower(), (
            f"PROMOTE.md must mention interactive-only requirement. Got: {text[:500]!r}"
        )

    def test_promote_doc_covers_d6_menu_stays_personal(self) -> None:
        """docs/PROMOTE.md must document D-6: shared areas surface only on explicit recall."""
        text = LORE_PROMOTE_DOC.read_text()
        assert "recall" in text.lower(), (
            f"PROMOTE.md must mention 'lore recall' for shared areas. Got: {text[:500]!r}"
        )
        assert (
            "menu" in text.lower()
            or "always-loaded" in text.lower()
            or "personal" in text.lower()
        ), (
            f"PROMOTE.md must document D-6 menu stays personal. Got: {text[:500]!r}"
        )
