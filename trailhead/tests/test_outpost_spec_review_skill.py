"""Contract tests for the ``pickup-spec-review`` skill (specs-v1 S8).

``pickup-spec-review`` is a sibling to ``pickup-review`` (the diff-review skill):
same pure-HTTP-only posture, but driving the spec-review namespace S4 shipped
(``/api/specs`` + ``/api/spec-reviews``, keyed ``(vault, slug)``) instead of the
diff loop's ``(group, slug, member)`` triple.

These tests pin the structural anatomy and the load-bearing clauses of that
contract — the same role ``test_outpost_plugin.py`` plays for ``pickup-review`` —
so a future edit can't silently drop them. ``test_outpost_plugin.py``'s
``test_no_zenith_tools_reference_anywhere`` already rglobs the whole
``tools/outpost`` tree, so this file doesn't duplicate that sweep.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_TOOL_ROOT = _REPO_ROOT / "tools" / "outpost"
_SKILL_MD = _TOOL_ROOT / "plugins" / "outpost" / "skills" / "pickup-spec-review" / "SKILL.md"
_SIBLING_SKILL_MD = _TOOL_ROOT / "plugins" / "outpost" / "skills" / "pickup-review" / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_MD.read_text()


def _write_path_section() -> str:
    """Return just the `### 4. Write path …` section body of the skill."""
    text = _skill_text()
    start = text.index("\n### 4. Write path")
    end = text.index("\n### 5.", start)
    return text[start:end]


# ---------------------------------------------------------------------------
# Anatomy
# ---------------------------------------------------------------------------


class TestPluginAnatomy:
    def test_skill_md_exists(self):
        assert _SKILL_MD.exists(), f"missing {_SKILL_MD}"

    def test_skill_frontmatter_name_is_pickup_spec_review(self):
        text = _skill_text()
        assert text.startswith("---\n")
        fm = text[3 : text.find("\n---", 3)]
        name = next(
            (ln.split(":", 1)[1].strip() for ln in fm.splitlines() if ln.startswith("name:")),
            None,
        )
        assert name == "pickup-spec-review"

    def test_sibling_pickup_review_skill_is_untouched(self):
        """This slice must not lower/raise pickup-review's own minimum."""
        text = _SIBLING_SKILL_MD.read_text()
        assert "minimum `contract_version` of 1" in text, (
            "pickup-review's own contract_version minimum must stay unchanged"
        )


# ---------------------------------------------------------------------------
# Contract clauses the skill MUST document
# ---------------------------------------------------------------------------


class TestSkillContract:
    def test_uses_canonical_vault_slug_paths(self):
        text = _skill_text()
        assert "/api/specs/:vault/:slug" in text
        assert "/api/spec-reviews" in text

    def test_documents_drain_read_and_reply_endpoints(self):
        text = _skill_text()
        assert "/api/spec-reviews/:id" in text
        assert "/api/spec-reviews/:id/replies" in text

    def test_documents_any_kind_record_read_for_wikilinks(self):
        text = _skill_text()
        assert "/api/records/:vault/:kind/:slug" in text

    def test_documents_contract_version_minimum_2_and_abort(self):
        text = _skill_text()
        assert "contract_version" in text
        assert "/health" in text
        assert re.search(r"minimum.{0,40}\b2\b|\b2\b.{0,40}minimum", text, re.IGNORECASE), (
            "skill must state its contract_version minimum is 2, not 1"
        )
        assert re.search(r"abort|refuse|stop|do not proceed", text, re.IGNORECASE), (
            "skill must abort on a contract_version below its minimum"
        )

    def test_documents_workspace_wide_drain_and_optional_scoping(self):
        text = _skill_text().lower()
        assert "spec-reviews" in text
        assert "scope" in text or "scoped" in text

    def test_documents_daemon_down_guidance(self):
        text = _skill_text()
        assert "trailhead outpost start" in text

    def test_human_only_endpoints_never_called(self):
        """The skill must name the human-only endpoints and say it never calls them."""
        text = _skill_text()
        assert "/api/specs/:vault/:slug/reviews/comments" in text
        assert "/api/specs/:vault/:slug/comments/:cid" in text
        assert "/api/spec-reviews/:id/comments/:cid" in text


# ---------------------------------------------------------------------------
# Write path: lore CLI only, full-body replace preferred, verify-by-re-read
# ---------------------------------------------------------------------------


class TestWritePathContract:
    def test_write_path_is_lore_record_update_only(self):
        text = _skill_text()
        assert "lore record update" in text

    def test_full_body_replace_preferred_over_diff(self):
        text = _skill_text()
        assert re.search(r"full.body replace", text, re.IGNORECASE)
        assert "--diff" in text

    def test_verify_by_re_read_after_every_update(self):
        text = _skill_text()
        assert re.search(r"re-?read", text, re.IGNORECASE)
        assert re.search(r"verify", text, re.IGNORECASE)

    def test_body_reaches_the_cli_via_a_tmpfile_not_a_heredoc(self):
        """A quoted heredoc suppresses variable expansion but not delimiter
        matching, so a spec body containing a bare `EOF` line would truncate the
        write. The write-path section must redirect a temp file into the CLI and
        must not itself demonstrate a heredoc."""
        section = _write_path_section()
        assert '< "$tmpfile"' in section
        assert "<<'EOF'" not in section

    def test_daemon_is_never_a_write_path(self):
        text = _skill_text()
        assert re.search(r"daemon.{0,80}(read-only|never write|no endpoint)", text, re.IGNORECASE | re.DOTALL), (
            "skill must state the daemon has no endpoint that mutates a spec body"
        )


# ---------------------------------------------------------------------------
# Lifecycle rule: draft vs. frozen
# ---------------------------------------------------------------------------


class TestLifecycleRule:
    def test_documents_draft_edit_in_place(self):
        text = _skill_text()
        assert re.search(r"\bdraft\b.{0,80}edit", text, re.IGNORECASE | re.DOTALL)

    def test_documents_frozen_statuses_never_edited(self):
        text = _skill_text()
        for status in ("ready", "planned", "complete", "superseded", "dropped"):
            assert status in text, f"frozen status '{status}' must be named"
        assert re.search(r"never edit", text, re.IGNORECASE)

    def test_documents_explicit_routing_on_frozen_spec(self):
        text = _skill_text()
        assert re.search(r"successor spec", text, re.IGNORECASE)
        assert re.search(r"follow-up task", text, re.IGNORECASE)
        assert re.search(r"explicit", text, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Injection posture — inscribed in THIS file, not deferred to a sibling plugin
# ---------------------------------------------------------------------------


class TestInjectionPosture:
    def test_injection_safety_posture_is_explicit_and_self_contained(self):
        text = _skill_text()
        assert re.search(r"\bdata\b", text, re.IGNORECASE)
        assert re.search(r"never (an )?instruction|not instruction", text, re.IGNORECASE)
        # Self-contained: must name the data sources this skill itself reads.
        assert re.search(r"spec bod(y|ies)", text, re.IGNORECASE)
        assert re.search(r"comment", text, re.IGNORECASE)
        assert re.search(r"anchor excerpt", text, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Gauntlet write precedence
# ---------------------------------------------------------------------------


class TestGauntletPrecedence:
    def test_documents_re_read_immediately_before_edit(self):
        text = _skill_text()
        assert re.search(r"gauntlet", text, re.IGNORECASE)
        assert re.search(r"immediately before", text, re.IGNORECASE)
