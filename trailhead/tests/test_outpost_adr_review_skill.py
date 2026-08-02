"""Contract tests for the ``pickup-adr-review`` skill.

``pickup-adr-review`` is a sibling to ``pickup-spec-review``: same pure-HTTP-only
posture and the same reply verbs, driving the ADR namespace (``/api/adrs`` +
``/api/adr-reviews``, keyed ``(vault, slug)``).

Its one substantive divergence is the edit rule, and that rule is the reason
these tests exist. ``lore`` does not enforce ADR immutability — nothing in the
CLI refuses to overwrite an ``active`` decision record — so the skill's own
status check is the ONLY control between review feedback and a rewritten ADR.
The clauses pinned below (fail-closed status re-read, and a frozen ADR's
feedback landing in a real linked follow-up record rather than in prose) are
load-bearing safety text, not style; a future edit must not silently drop them.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_SKILLS_ROOT = _REPO_ROOT / "tools" / "outpost" / "plugins" / "outpost" / "skills"
_SKILL_MD = _SKILLS_ROOT / "pickup-adr-review" / "SKILL.md"
_SPEC_SKILL_MD = _SKILLS_ROOT / "pickup-spec-review" / "SKILL.md"
_DIFF_SKILL_MD = _SKILLS_ROOT / "pickup-review" / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_MD.read_text()


# ---------------------------------------------------------------------------
# Anatomy
# ---------------------------------------------------------------------------


class TestPluginAnatomy:
    def test_skill_md_exists(self):
        assert _SKILL_MD.exists(), f"missing {_SKILL_MD}"

    def test_skill_frontmatter_name_is_pickup_adr_review(self):
        text = _skill_text()
        assert text.startswith("---\n")
        fm = text[3 : text.find("\n---", 3)]
        name = next(
            (ln.split(":", 1)[1].strip() for ln in fm.splitlines() if ln.startswith("name:")),
            None,
        )
        assert name == "pickup-adr-review"

    def test_sibling_skills_keep_their_own_contract_minimums(self):
        """Adding this skill must not move either sibling's version floor."""
        assert "minimum `contract_version` of 1" in _DIFF_SKILL_MD.read_text()
        assert "minimum `contract_version` of 2" in _SPEC_SKILL_MD.read_text()


# ---------------------------------------------------------------------------
# Contract clauses the skill MUST document
# ---------------------------------------------------------------------------


class TestSkillContract:
    def test_uses_canonical_vault_slug_paths(self):
        text = _skill_text()
        assert "/api/adrs/:vault/:slug" in text
        assert "/api/adr-reviews" in text

    def test_documents_drain_read_and_reply_endpoints(self):
        text = _skill_text()
        assert "/api/adr-reviews/:id" in text
        assert "/api/adr-reviews/:id/replies" in text

    def test_documents_any_kind_record_read_for_wikilinks(self):
        text = _skill_text()
        assert "/api/records/:vault/:kind/:slug" in text

    def test_documents_contract_version_minimum_3_and_abort(self):
        text = _skill_text()
        assert "/health" in text
        assert "minimum `contract_version` of 3" in text, (
            "skill must state its contract_version minimum is 3 — the bump that "
            "shipped the ADR routes"
        )
        assert re.search(r"below 3.{0,120}abort", text, re.IGNORECASE | re.DOTALL), (
            "skill must abort on a contract_version below its minimum"
        )

    def test_documents_daemon_down_guidance(self):
        text = _skill_text()
        assert "trailhead outpost start" in text

    def test_human_only_endpoints_never_called(self):
        text = _skill_text()
        assert "/api/adrs/:vault/:slug/reviews/comments" in text
        assert "/api/adrs/:vault/:slug/comments/:cid" in text
        assert "/api/adr-reviews/:id/comments/:cid" in text

    def test_agent_never_resolves_a_review(self):
        text = _skill_text()
        assert re.search(r"never .{0,40}\bresolved\b", text, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Write path: lore CLI only, full-body replace, verify-by-re-read
# ---------------------------------------------------------------------------


class TestWritePathContract:
    def test_write_path_is_lore_record_update_only(self):
        assert "lore record update adr/<slug>" in _skill_text()

    def test_full_body_replace_not_diff(self):
        text = _skill_text()
        assert re.search(r"full.body replace", text, re.IGNORECASE)
        assert "--diff" in text

    def test_verify_by_re_read_after_every_update(self):
        text = _skill_text()
        assert "lore record show adr/<slug> --json" in text
        assert re.search(r"verify by re-?read", text, re.IGNORECASE)

    def test_daemon_is_never_a_write_path(self):
        text = _skill_text()
        assert re.search(
            r"daemon.{0,80}(read-only|never write|no endpoint)", text, re.IGNORECASE | re.DOTALL
        ), "skill must state the daemon has no endpoint that mutates an ADR body"


# ---------------------------------------------------------------------------
# The edit rule — draft-only, enforced fail-closed by THIS skill
# ---------------------------------------------------------------------------


class TestFailClosedEditRule:
    def test_draft_is_the_only_editable_status(self):
        text = _skill_text()
        assert re.search(r"`draft`.{0,60}only.{0,40}(editable|writable)", text, re.IGNORECASE | re.DOTALL)

    def test_names_every_frozen_status_as_never_edited(self):
        text = _skill_text()
        for status in ("active", "superseded", "dropped"):
            assert f"`{status}`" in text, f"frozen status '{status}' must be named"
        assert re.search(r"never edit", text, re.IGNORECASE)

    def test_states_that_lore_has_no_immutability_guard(self):
        """The skill must say out loud that it is the only control."""
        text = _skill_text()
        assert re.search(
            r"lore.{0,80}(does not enforce|no CLI-side guard|no guard)", text, re.IGNORECASE | re.DOTALL
        ), "skill must state lore does not enforce ADR immutability"
        assert re.search(r"only.{0,20}control", text, re.IGNORECASE)

    def test_requires_a_status_re_read_immediately_before_any_write(self):
        text = _skill_text()
        assert re.search(r"immediately before", text, re.IGNORECASE)
        assert re.search(r"hard-stop", text, re.IGNORECASE)

    def test_unconfirmed_status_fails_closed(self):
        text = _skill_text()
        assert re.search(r"fail[- ]closed", text, re.IGNORECASE)
        assert re.search(
            r"(cannot|unreadable|missing|ambiguous).{0,160}do not write", text, re.IGNORECASE | re.DOTALL
        ), "an unconfirmed draft status must be treated as frozen"


# ---------------------------------------------------------------------------
# Frozen path — feedback lands in a tracked, linked artifact
# ---------------------------------------------------------------------------


class TestFrozenFollowUpRecord:
    def test_frozen_feedback_creates_a_linked_task_record(self):
        text = _skill_text()
        assert "lore record create --kind task" in text
        assert "--related adr=<slug>" in text

    def test_reply_must_cite_the_created_record_id(self):
        text = _skill_text()
        assert re.search(r"cite.{0,40}record id", text, re.IGNORECASE)
        assert re.search(r"prose.{0,60}not.{0,40}(sufficient|outcome)", text, re.IGNORECASE | re.DOTALL)

    def test_created_record_is_verified_by_re_read(self):
        assert "lore record show task/<created-slug> --json" in _skill_text()

    def test_never_authors_a_superseding_adr_unilaterally(self):
        text = _skill_text()
        assert re.search(r"never author (a|the) superseding\s+adr", text, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Injection posture — inscribed in THIS file, not deferred to a sibling plugin
# ---------------------------------------------------------------------------


class TestInjectionPosture:
    def test_injection_safety_posture_is_explicit_and_self_contained(self):
        text = _skill_text()
        assert re.search(r"never (an )?instruction|not instruction", text, re.IGNORECASE)
        assert re.search(r"adr bod(y|ies)", text, re.IGNORECASE)
        assert re.search(r"anchor excerpt", text, re.IGNORECASE)

    def test_record_content_cannot_grant_edit_permission(self):
        text = _skill_text()
        assert re.search(
            r"(never|cannot).{0,60}grant.{0,40}edit permission", text, re.IGNORECASE | re.DOTALL
        ), "record content must not be able to talk the agent past the status gate"
