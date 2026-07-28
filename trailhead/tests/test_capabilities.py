"""Tests for trailhead/capabilities.py — plugin-inventory loader.

The capability-GROUP model is gone; install selects subagents/skills by NAME.
A manifest declares only the always-on set (``base`` + optional ``hooks_json``);
the selectable inventory is discovered by convention:

  - subagents  = agents/*.md            (name = stem)
  - skills      = skills/<dir>/SKILL.md  (name = dir), MINUS base entries

Pinned rules:
  - skills/<x> dirs without a SKILL.md (e.g. skills/_shared) are NOT selectable.
  - a base-listed dir is always-on and never appears in the selectable set.
  - confinement (D-F) on base + hooks_json is checked BEFORE any stat call.
  - validate=true asserts base dirs exist (dir) and hooks_json exists (file).
"""

from pathlib import Path

import pytest

from trailhead.capabilities import (
    ConfineError,
    Manifest,
    ManifestError,
    load_manifest,
)

_REPO_ROOT = Path(__file__).parent.parent.parent
_LORE_MANIFEST = _REPO_ROOT / "tools" / "lore" / "capabilities.toml"
_CRAFT_MANIFEST = _REPO_ROOT / "tools" / "craft" / "capabilities.toml"
_CAMP_MANIFEST = _REPO_ROOT / "tools" / "camp" / "capabilities.toml"
_PORTAGE_MANIFEST = _REPO_ROOT / "tools" / "portage" / "capabilities.toml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "capabilities.toml"
    p.write_text(content)
    return p


def _make_plugin_dir(tmp_path: Path, tool_name: str) -> Path:
    plugin_root = tmp_path / "plugins" / tool_name
    plugin_root.mkdir(parents=True)
    return plugin_root


def _make_skill(plugin_root: Path, name: str, *, with_skill_md: bool = True) -> Path:
    d = plugin_root / "skills" / name
    d.mkdir(parents=True)
    if with_skill_md:
        (d / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\n")
    return d


def _make_agent(plugin_root: Path, name: str) -> Path:
    d = plugin_root / "agents"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text("# agent")
    return p


# ---------------------------------------------------------------------------
# Real lore sample
# ---------------------------------------------------------------------------


class TestLoreInventory:
    def test_returns_manifest(self):
        assert isinstance(load_manifest(_LORE_MANIFEST), Manifest)

    def test_tool_name(self):
        assert load_manifest(_LORE_MANIFEST).tool_name == "lore"

    def test_base_is_empty(self):
        # lore ships no always-on base dirs; the CLI-only vault-write rule reaches
        # agents via CLAUDE.md, so no shared reference doc needs shipping.
        assert load_manifest(_LORE_MANIFEST).base == []

    def test_hooks_json(self):
        assert load_manifest(_LORE_MANIFEST).hooks_json is None

    def test_lore_agents_discovered(self):
        # The lore agent roster: librarian (unchanged) +
        # investigator (deep investigation, opus/xhigh) + researcher (lighter
        # lookups + tracking-backlog polling, haiku/low).
        m = load_manifest(_LORE_MANIFEST)
        assert m.subagents == {
            "librarian": "agents/librarian.md",
            "investigator": "agents/investigator.md",
            "researcher": "agents/researcher.md",
        }

    def test_session_skills_selectable(self):
        # The lore skills are exactly flush, sync, search, record, research.
        # Per-kind capture is via the lore record/session CLI (not skills);
        # brainstorm lives in the craft plugin; the finalization skill is flush.
        m = load_manifest(_LORE_MANIFEST)
        assert set(m.skills) == {"flush", "sync", "search", "record", "research"}
        for name in m.skills:
            assert m.skills[name] == f"skills/{name}"

    def test_sync_is_now_selectable(self):
        # sync was always-on (base) under the capability model; it has a SKILL.md
        # so it is now a selectable skill.
        assert "sync" in load_manifest(_LORE_MANIFEST).skills


# ---------------------------------------------------------------------------
# Real craft sample
# ---------------------------------------------------------------------------


class TestCraftInventory:
    def test_tool_name(self):
        assert load_manifest(_CRAFT_MANIFEST).tool_name == "craft"

    def test_no_hooks_json(self):
        assert load_manifest(_CRAFT_MANIFEST).hooks_json is None

    def test_base_is_shared_and_templates(self):
        assert load_manifest(_CRAFT_MANIFEST).base == ["skills/_shared", "templates"]

    def test_council_subagents_discovered(self):
        m = load_manifest(_CRAFT_MANIFEST)
        for name in ("advocate", "builder", "breaker", "attacker"):
            assert name in m.subagents

    def test_gauntlet_subagents_discovered(self):
        # The spec-gauntlet passes that aren't council lenses. Each must be
        # discovered as selectable, or the gauntlet's dispatch dead-ends.
        m = load_manifest(_CRAFT_MANIFEST)
        for name in ("premise-attacker", "consistency-auditor", "divergence-prober"):
            assert name in m.subagents

    def test_helper_subagents_discovered(self):
        m = load_manifest(_CRAFT_MANIFEST)
        for name in (
            "doc-finder",
            "log-sifter",
            "researcher",
            "troubleshooter",
            "test-runner",
            "security-auditor",
        ):
            assert name in m.subagents

    def test_lifecycle_skills_selectable(self):
        # The craft skills are exactly these — each has a SKILL.md and is
        # discovered as selectable. brainstorm is a craft skill (discovery →
        # draft spec, runs before planning). gauntlet is the adversarial spec
        # review that owns the spec's draft → ready edge, sitting between
        # brainstorm and plan. receiving-code-review is the
        # untrusted-content-framing reference skill for evaluating incoming
        # review/CI-annotation feedback. refine is the self-serve promotion
        # ritual that turns a captured standalone `open` task into a `ready`
        # executor-runnable leaf.
        m = load_manifest(_CRAFT_MANIFEST)
        assert set(m.skills) == {
            "polish",
            "plan",
            "execute",
            "review",
            "consult",
            "brainstorm",
            "gauntlet",
            "receiving-code-review",
            "refine",
        }

    def test_shared_not_selectable(self):
        assert "_shared" not in load_manifest(_CRAFT_MANIFEST).skills


# ---------------------------------------------------------------------------
# camp / portage
# ---------------------------------------------------------------------------


class TestOtherInventories:
    def test_camp_has_no_selectable_skills(self):
        # camp ships only a CLI (bin) + hooks — no base, no subagents, no skills.
        # The worktree SKILL was removed: the workspace exists before the harness
        # opens, so worktree orchestration is operator-facing (README), not a skill.
        m = load_manifest(_CAMP_MANIFEST)
        assert m.base == []
        assert m.subagents == {}
        assert m.skills == {}

    def test_portage_inventory(self):
        # The four legacy skills (open/update/monitor/merge) collapsed into one
        # verb-dispatched pull_request skill.
        m = load_manifest(_PORTAGE_MANIFEST)
        assert set(m.subagents) == {"green-driver", "monitor", "summarizer", "updater"}
        assert set(m.skills) == {"pull_request"}


# ---------------------------------------------------------------------------
# Discovery rules (synthetic trees)
# ---------------------------------------------------------------------------


class TestDiscoveryRules:
    def test_skill_without_skill_md_is_not_selectable(self, tmp_path):
        root = _make_plugin_dir(tmp_path, "mytool")
        _make_skill(root, "real")
        _make_skill(root, "_shared", with_skill_md=False)
        manifest_path = _write_manifest(tmp_path, '[tool]\nname = "mytool"\n')
        m = load_manifest(manifest_path)
        assert "real" in m.skills
        assert "_shared" not in m.skills

    def test_base_listed_skill_excluded_from_selectable(self, tmp_path):
        root = _make_plugin_dir(tmp_path, "mytool")
        _make_skill(root, "shared_thing")
        _make_skill(root, "real")
        content = '[tool]\nname = "mytool"\nbase = ["skills/shared_thing"]\n'
        m = load_manifest(_write_manifest(tmp_path, content))
        assert "real" in m.skills
        assert "shared_thing" not in m.skills  # always-on, not separately selectable

    def test_subagents_discovered_from_agents_dir(self, tmp_path):
        root = _make_plugin_dir(tmp_path, "mytool")
        _make_agent(root, "alpha")
        _make_agent(root, "beta")
        m = load_manifest(_write_manifest(tmp_path, '[tool]\nname = "mytool"\n'))
        assert set(m.subagents) == {"alpha", "beta"}

    def test_empty_plugin_has_empty_inventory(self, tmp_path):
        _make_plugin_dir(tmp_path, "mytool")
        m = load_manifest(_write_manifest(tmp_path, '[tool]\nname = "mytool"\n'))
        assert m.subagents == {}
        assert m.skills == {}


# ---------------------------------------------------------------------------
# Malformed / missing fields
# ---------------------------------------------------------------------------


class TestMalformedManifests:
    def test_malformed_toml_raises(self, tmp_path):
        manifest_path = _write_manifest(tmp_path, "this is [not valid toml ][[\n")
        with pytest.raises(ManifestError, match=str(manifest_path)):
            load_manifest(manifest_path)

    def test_missing_tool_section_raises(self, tmp_path):
        manifest_path = _write_manifest(tmp_path, "other = 1\n")
        with pytest.raises(ManifestError, match=r"\[tool\]"):
            load_manifest(manifest_path)

    def test_missing_tool_name_raises(self, tmp_path):
        manifest_path = _write_manifest(tmp_path, "[tool]\nbase = []\n")
        with pytest.raises(ManifestError, match="name"):
            load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# Confinement (D-F) — base + hooks_json
# ---------------------------------------------------------------------------


class TestConfinement:
    def test_dotdot_traversal_in_base_raises(self, tmp_path):
        _make_plugin_dir(tmp_path, "badtool")
        content = '[tool]\nname = "badtool"\nbase = ["../escape"]\nvalidate = false\n'
        with pytest.raises(ConfineError):
            load_manifest(_write_manifest(tmp_path, content))

    def test_absolute_path_in_base_raises(self, tmp_path):
        _make_plugin_dir(tmp_path, "badtool")
        content = '[tool]\nname = "badtool"\nbase = ["/etc/passwd"]\nvalidate = false\n'
        with pytest.raises(ConfineError):
            load_manifest(_write_manifest(tmp_path, content))

    def test_hooks_json_traversal_raises(self, tmp_path):
        _make_plugin_dir(tmp_path, "badtool")
        content = '[tool]\nname = "badtool"\nhooks_json = "../../../etc/passwd"\nvalidate = false\n'
        with pytest.raises(ConfineError):
            load_manifest(_write_manifest(tmp_path, content))

    def test_absolute_hooks_json_raises(self, tmp_path):
        _make_plugin_dir(tmp_path, "badtool")
        content = '[tool]\nname = "badtool"\nhooks_json = "/etc/passwd"\nvalidate = false\n'
        with pytest.raises(ConfineError):
            load_manifest(_write_manifest(tmp_path, content))

    def test_cli_bin_traversal_raises(self, tmp_path):
        _make_plugin_dir(tmp_path, "badtool")
        content = '[tool]\nname = "badtool"\ncli_bin = "../../../etc/passwd"\nvalidate = false\n'
        with pytest.raises(ConfineError) as exc_info:
            load_manifest(_write_manifest(tmp_path, content))
        assert exc_info.value.context == "cli_bin"

    def test_absolute_cli_bin_raises(self, tmp_path):
        _make_plugin_dir(tmp_path, "badtool")
        content = '[tool]\nname = "badtool"\ncli_bin = "/etc/passwd"\nvalidate = false\n'
        with pytest.raises(ConfineError) as exc_info:
            load_manifest(_write_manifest(tmp_path, content))
        assert exc_info.value.context == "cli_bin"


# ---------------------------------------------------------------------------
# Validate: existence + type
# ---------------------------------------------------------------------------


class TestValidate:
    def test_missing_base_dir_raises(self, tmp_path):
        _make_plugin_dir(tmp_path, "mytool")
        content = '[tool]\nname = "mytool"\nbase = ["skills/nonexistent"]\n'
        with pytest.raises(ManifestError, match="nonexistent"):
            load_manifest(_write_manifest(tmp_path, content))

    def test_base_that_is_a_file_raises(self, tmp_path):
        root = _make_plugin_dir(tmp_path, "mytool")
        (root / "skills").mkdir()
        (root / "skills" / "notadir").write_text("x")
        content = '[tool]\nname = "mytool"\nbase = ["skills/notadir"]\n'
        with pytest.raises(ManifestError, match="directory"):
            load_manifest(_write_manifest(tmp_path, content))

    def test_missing_hooks_json_raises(self, tmp_path):
        _make_plugin_dir(tmp_path, "mytool")
        content = '[tool]\nname = "mytool"\nhooks_json = "hooks/hooks.json"\n'
        with pytest.raises(ManifestError, match="hooks"):
            load_manifest(_write_manifest(tmp_path, content))

    def test_hooks_json_that_is_a_dir_raises(self, tmp_path):
        root = _make_plugin_dir(tmp_path, "mytool")
        (root / "hooks" / "hooks.json").mkdir(parents=True)
        content = '[tool]\nname = "mytool"\nhooks_json = "hooks/hooks.json"\n'
        with pytest.raises(ManifestError, match="file"):
            load_manifest(_write_manifest(tmp_path, content))

    def test_validate_false_suppresses_missing_base(self, tmp_path):
        _make_plugin_dir(tmp_path, "mytool")
        content = '[tool]\nname = "mytool"\nbase = ["skills/nope"]\nvalidate = false\n'
        # Must not raise even though skills/nope is missing.
        load_manifest(_write_manifest(tmp_path, content))

    def test_missing_cli_bin_raises(self, tmp_path):
        _make_plugin_dir(tmp_path, "mytool")
        content = '[tool]\nname = "mytool"\ncli_bin = "bin/mytool"\n'
        with pytest.raises(ManifestError, match="mytool"):
            load_manifest(_write_manifest(tmp_path, content))


# ---------------------------------------------------------------------------
# cli_bin
# ---------------------------------------------------------------------------


class TestCliBin:
    def test_cli_bin_parses(self, tmp_path):
        root = _make_plugin_dir(tmp_path, "mytool")
        (root / "bin").mkdir()
        (root / "bin" / "mytool").write_text("#!/usr/bin/env bash\n")
        content = '[tool]\nname = "mytool"\ncli_bin = "bin/mytool"\n'
        m = load_manifest(_write_manifest(tmp_path, content))
        assert m.cli_bin == "bin/mytool"

    def test_cli_bin_absent_is_none(self, tmp_path):
        _make_plugin_dir(tmp_path, "mytool")
        m = load_manifest(_write_manifest(tmp_path, '[tool]\nname = "mytool"\n'))
        assert m.cli_bin is None

    def test_camp_cli_bin(self):
        m = load_manifest(_CAMP_MANIFEST)
        assert m.cli_bin == "bin/camp"
        assert (m.plugin_root / m.cli_bin).is_file()

    def test_lore_cli_bin(self):
        m = load_manifest(_LORE_MANIFEST)
        assert m.cli_bin == "bin/lore"
        assert (m.plugin_root / m.cli_bin).is_file()

    def test_portage_cli_bin(self):
        m = load_manifest(_PORTAGE_MANIFEST)
        assert m.cli_bin == "bin/portage"
        assert (m.plugin_root / m.cli_bin).is_file()
