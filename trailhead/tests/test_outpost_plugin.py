"""Contract tests for the wired ``tools/outpost`` plugin and its pickup-review skill.

``tools/outpost`` is the fifth trailhead plugin: skill-only (no python package, no
agents), modelled on ``tools/portage``'s anatomy. Its single skill,
``pickup-review``, is a **pure-HTTP contract** against the local outpost daemon —
it reads human-authored reviews in the cockpit, acts on each comment, and replies on
the token-less agent-permitted endpoints. It never touches the daemon's DB or
files directly, and it never possesses the human UI session token.

These tests pin the structural anatomy and the load-bearing clauses of that
contract so a future edit can't silently drop them.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_TOOL_ROOT = _REPO_ROOT / "tools" / "outpost"
_CAPABILITIES = _TOOL_ROOT / "capabilities.toml"
_PLUGIN_JSON = _TOOL_ROOT / "plugins" / "outpost" / ".claude-plugin" / "plugin.json"
_SKILL_MD = _TOOL_ROOT / "plugins" / "outpost" / "skills" / "pickup-review" / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_MD.read_text()


# ---------------------------------------------------------------------------
# Anatomy: skill-only plugin modelled on portage (no python package, no agents)
# ---------------------------------------------------------------------------


class TestPluginAnatomy:
    def test_capabilities_toml_exists(self):
        assert _CAPABILITIES.exists(), f"missing {_CAPABILITIES}"

    def test_capabilities_is_skill_only(self):
        """No always-on `base`, no hooks — the skill is discovered on disk."""
        from trailhead.capabilities import load_manifest

        m = load_manifest(_CAPABILITIES)
        assert m.base == [], "skill-only plugin must declare no `base` set"
        assert m.hooks_json is None, "skill-only plugin declares no hooks_json"

    def test_plugin_json_exists_and_names_outpost(self):
        import json

        assert _PLUGIN_JSON.exists(), f"missing {_PLUGIN_JSON}"
        data = json.loads(_PLUGIN_JSON.read_text())
        assert data.get("name") == "outpost"
        assert data.get("description", "").strip() != ""

    def test_no_python_package_or_agents(self):
        """Skill-only: no <name>/ package, no agents/ dir under the plugin root."""
        plugin_root = _TOOL_ROOT / "plugins" / "outpost"
        assert not (plugin_root / "outpost").exists(), "skill-only: no python package"
        assert not (plugin_root / "agents").exists(), "skill-only: no subagents"

    def test_no_per_tool_marketplace_json(self):
        """The single-marketplace convention: no per-tool marketplace.json remains."""
        assert not (_TOOL_ROOT / ".claude-plugin" / "marketplace.json").exists(), (
            "trailhead uses a single root marketplace; tools/outpost must not carry "
            "its own .claude-plugin/marketplace.json"
        )

    def test_skill_frontmatter_name_is_pickup_review(self):
        text = _skill_text()
        assert text.startswith("---\n")
        fm = text[3 : text.find("\n---", 3)]
        name = next(
            (ln.split(":", 1)[1].strip() for ln in fm.splitlines() if ln.startswith("name:")),
            None,
        )
        assert name == "pickup-review"


# ---------------------------------------------------------------------------
# Contract clauses the skill MUST document
# ---------------------------------------------------------------------------


class TestSkillContract:
    def test_uses_canonical_group_slug_member_paths(self):
        text = _skill_text()
        assert "/workspaces/:group/:slug/:member/reviews" in text, (
            "skill must document the canonical (group,slug,member) review path"
        )

    def test_documents_review_read_and_reply_endpoints(self):
        text = _skill_text()
        assert "/reviews/:id" in text
        assert "/reviews/:id/replies" in text

    def test_documents_server_side_anchor_endpoint(self):
        """Anchor is a server call now (GET /anchor), not a client-side script."""
        text = _skill_text()
        assert "/anchor" in text
        assert "excerpt" in text and "path" in text

    def test_documents_contract_version_abort_on_mismatch(self):
        text = _skill_text()
        assert "contract_version" in text
        assert "/health" in text
        # Must instruct abort/refuse when the daemon's contract_version is too low.
        assert re.search(r"abort|refuse|stop|do not proceed", text, re.IGNORECASE), (
            "skill must abort on a contract_version below its minimum"
        )
        assert re.search(r"CONTRACT[_ ]?VERSION|minimum.*contract|contract.*minimum", text, re.IGNORECASE)

    def test_documents_workspace_drain(self):
        """Iterate every open review across all members of the workspace."""
        text = _skill_text().lower()
        assert "open_reviews" in text or "open reviews" in text
        assert "member" in text

    def test_documents_daemon_down_guidance(self):
        text = _skill_text()
        assert "trailhead outpost start" in text, (
            "skill must tell the operator to run `trailhead outpost start` when the "
            "daemon is unreachable"
        )

    def test_injection_safety_posture_is_explicit(self):
        """Dashboard/review text is DATA the skill relays, never instructions."""
        text = _skill_text()
        assert re.search(r"\bdata\b", text, re.IGNORECASE)
        assert re.search(r"never (an )?instruction|not instruction", text, re.IGNORECASE), (
            "skill must state review/dashboard content is data, never instructions"
        )


# ---------------------------------------------------------------------------
# Authz posture: the skill never possesses the UI session token
# ---------------------------------------------------------------------------


class TestTokenlessPosture:
    def test_states_it_never_possesses_the_session_token(self):
        text = _skill_text()
        assert re.search(
            r"never (possess|hold|store|read|transmit).{0,60}session token"
            r"|session token.{0,60}never",
            text,
            re.IGNORECASE | re.DOTALL,
        ), "skill must explicitly state it never possesses the UI session token"

    def test_no_cookie_transmission_syntax_on_agent_paths(self):
        """The skill only calls token-less endpoints — no cookie is ever sent.

        Guards against a future edit that teaches the agent to attach the human
        cookie. We forbid the transmission *syntax*, not the disclaimer prose that
        explains the skill never has the token.
        """
        text = _skill_text()
        forbidden = ["Cookie:", "outpost_session", "--cookie", "-b outpost", "Set-Cookie"]
        hits = [pat for pat in forbidden if pat in text]
        assert not hits, f"SKILL.md must not send/reference a session cookie: {hits}"


# ---------------------------------------------------------------------------
# No zenith-era naming survives anywhere under tools/outpost
# ---------------------------------------------------------------------------


def test_no_zenith_tools_reference_anywhere():
    """The rewritten plugin carries no `zenith-tools` (or `zenith`) naming."""
    offenders: list[str] = []
    for path in _TOOL_ROOT.rglob("*"):
        if path.is_file() and "zenith" in path.read_text(errors="ignore").lower():
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert not offenders, f"zenith-era naming survives in: {offenders}"
