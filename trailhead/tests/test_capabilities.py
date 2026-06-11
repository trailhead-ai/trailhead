"""
Tests for trailhead/capabilities.py — capability-manifest format + validating loader.

TDD: these tests are written BEFORE the implementation. All must pass after
trailhead/capabilities.py is implemented.

Pinned rules:
  - A capability MUST have both `skills` and `agents` keys present (even if []).
    A capability with either key missing entirely is flagged with ManifestError.
    (vs explicit [] which is valid — that's a future/not-yet-built capability)
  - `skills/<x>` entries must resolve to directories; `agents/<x>.md` must resolve to files.
  - hooks_json must resolve to a file (not a dir).
  - Confinement (D-F) is checked BEFORE any stat/existence call.
  - tomllib raises on duplicate [capabilities.foo] tables; the loader wraps this in ManifestError.
"""

import tomllib
from pathlib import Path

import pytest

from trailhead.capabilities import (
    ConfineError,
    Manifest,
    ManifestError,
    load_manifest,
)

# Paths to the real committed samples (relative to the repo root, written as absolute)
_REPO_ROOT = Path(__file__).parent.parent.parent
_LORE_MANIFEST = _REPO_ROOT / "tools" / "lore" / "capabilities.toml"
_FORGE_MANIFEST = _REPO_ROOT / "tools" / "forge" / "capabilities.toml"
_CAMP_MANIFEST = _REPO_ROOT / "tools" / "camp" / "capabilities.toml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "capabilities.toml"
    p.write_text(content)
    return p


def _make_plugin_dir(tmp_path: Path, tool_name: str) -> Path:
    """Create a minimal plugin root structure for a tool."""
    plugin_root = tmp_path / "plugins" / tool_name
    plugin_root.mkdir(parents=True)
    return plugin_root


def _make_skill_dir(plugin_root: Path, skill: str) -> Path:
    d = plugin_root / skill
    d.mkdir(parents=True)
    return d


def _make_agent_file(plugin_root: Path, agent: str) -> Path:
    p = plugin_root / agent
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# agent")
    return p


# ---------------------------------------------------------------------------
# T1: Real lore sample parses and returns the expected structure
# ---------------------------------------------------------------------------


class TestLoreManifestParsesCorrectly:
    def test_lore_manifest_returns_manifest_instance(self):
        m = load_manifest(_LORE_MANIFEST)
        assert isinstance(m, Manifest)

    def test_lore_tool_name(self):
        m = load_manifest(_LORE_MANIFEST)
        assert m.tool_name == "lore"

    def test_lore_base_dirs(self):
        m = load_manifest(_LORE_MANIFEST)
        assert m.base == ["skills/_shared", "skills/sync", "skills/ping"]

    def test_lore_hooks_json(self):
        m = load_manifest(_LORE_MANIFEST)
        assert m.hooks_json == "hooks/hooks.json"

    def test_lore_has_capture_capability(self):
        m = load_manifest(_LORE_MANIFEST)
        assert "capture" in m.capabilities

    def test_lore_capture_skills(self):
        m = load_manifest(_LORE_MANIFEST)
        cap = m.capabilities["capture"]
        assert "skills/decision" in cap["skills"]
        assert "skills/dead-end" in cap["skills"]
        assert "skills/defer" in cap["skills"]
        assert "skills/radar" in cap["skills"]
        assert "skills/check-radar" in cap["skills"]
        assert "skills/area" in cap["skills"]
        assert "skills/seed" in cap["skills"]
        assert "skills/brainstorm" in cap["skills"]

    def test_lore_capture_agents_empty(self):
        m = load_manifest(_LORE_MANIFEST)
        assert m.capabilities["capture"]["agents"] == []

    def test_lore_recall_capability(self):
        m = load_manifest(_LORE_MANIFEST)
        cap = m.capabilities["recall"]
        assert cap["skills"] == ["skills/tend", "skills/reflect"]
        assert "agents/loremaster.md" in cap["agents"]

    def test_lore_sessions_capability(self):
        m = load_manifest(_LORE_MANIFEST)
        cap = m.capabilities["sessions"]
        assert "skills/checkpoint" in cap["skills"]
        assert "skills/finish" in cap["skills"]
        assert cap["agents"] == []

    def test_lore_shared_vaults_explicitly_empty(self):
        m = load_manifest(_LORE_MANIFEST)
        cap = m.capabilities["shared-vaults"]
        assert cap["skills"] == []
        assert cap["agents"] == []

    def test_lore_capabilities_have_descriptions(self):
        m = load_manifest(_LORE_MANIFEST)
        for name, cap in m.capabilities.items():
            assert cap["description"], f"capability {name!r} has empty description"


# ---------------------------------------------------------------------------
# T2: Real lore sample validates against the on-disk tree
# ---------------------------------------------------------------------------


class TestLoreManifestValidatesAgainstDisk:
    def test_lore_manifest_validates_without_error(self):
        # validate=true is default; must not raise
        load_manifest(_LORE_MANIFEST)

    def test_lore_base_dirs_exist(self):
        m = load_manifest(_LORE_MANIFEST)
        plugin_root = _LORE_MANIFEST.parent / "plugins" / m.tool_name
        for entry in m.base:
            p = plugin_root / entry
            assert p.exists(), f"base dir missing: {p}"
            assert p.is_dir(), f"base entry is not a dir: {p}"

    def test_lore_capture_skill_dirs_exist(self):
        m = load_manifest(_LORE_MANIFEST)
        plugin_root = _LORE_MANIFEST.parent / "plugins" / m.tool_name
        for skill in m.capabilities["capture"]["skills"]:
            p = plugin_root / skill
            assert p.is_dir(), f"skill dir missing: {p}"

    def test_lore_recall_agent_file_exists(self):
        m = load_manifest(_LORE_MANIFEST)
        plugin_root = _LORE_MANIFEST.parent / "plugins" / m.tool_name
        p = plugin_root / "agents/loremaster.md"
        assert p.is_file(), f"agent file missing: {p}"

    def test_lore_hooks_json_exists_as_file(self):
        m = load_manifest(_LORE_MANIFEST)
        plugin_root = _LORE_MANIFEST.parent / "plugins" / m.tool_name
        p = plugin_root / m.hooks_json
        assert p.is_file(), f"hooks_json missing: {p}"


# ---------------------------------------------------------------------------
# T3: Real forge sample validates against the on-disk tree
# ---------------------------------------------------------------------------


class TestForgeManifestValidatesAgainstDisk:
    def test_forge_manifest_validates_without_error(self):
        load_manifest(_FORGE_MANIFEST)

    def test_forge_tool_name(self):
        m = load_manifest(_FORGE_MANIFEST)
        assert m.tool_name == "forge"

    def test_forge_has_no_hooks_json(self):
        m = load_manifest(_FORGE_MANIFEST)
        assert m.hooks_json is None

    def test_forge_base_dirs(self):
        m = load_manifest(_FORGE_MANIFEST)
        assert set(m.base) == {"skills/handoff", "skills/pickup", "skills/followup"}

    def test_forge_planning_capability(self):
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["planning"]
        assert "skills/planning" in cap["skills"]
        assert "agents/planner.md" in cap["agents"]
        assert "agents/architect.md" in cap["agents"]

    def test_forge_execute_capability(self):
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["execute"]
        assert "skills/subagent-driven-development" in cap["skills"]
        assert "agents/scout.md" in cap["agents"]
        assert "agents/trailblazer.md" in cap["agents"]

    def test_forge_circle_capability_agents_exist(self):
        m = load_manifest(_FORGE_MANIFEST)
        plugin_root = _FORGE_MANIFEST.parent / "plugins" / m.tool_name
        cap = m.capabilities["circle"]
        for agent in cap["agents"]:
            p = plugin_root / agent
            assert p.is_file(), f"agent file missing: {p}"

    def test_forge_helpers_capability_agents_exist(self):
        m = load_manifest(_FORGE_MANIFEST)
        plugin_root = _FORGE_MANIFEST.parent / "plugins" / m.tool_name
        cap = m.capabilities["helpers"]
        for agent in cap["agents"]:
            p = plugin_root / agent
            assert p.is_file(), f"agent file missing: {p}"

    def test_forge_design_capability_has_artist_agent(self):
        """forge design has no skills and exactly one agent: agents/artist.md."""
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["design"]
        assert cap["skills"] == []
        assert cap["agents"] == ["agents/artist.md"]

    def test_forge_release_capability_has_skills_and_agents(self):
        """forge release has 7 release skills and 4 release agents."""
        m = load_manifest(_FORGE_MANIFEST)
        cap = m.capabilities["release"]
        assert cap["skills"] == [
            "skills/create-pr",
            "skills/update-pr",
            "skills/watch-pr",
            "skills/watch-preview",
            "skills/merge-pr",
            "skills/github-pr",
            "skills/post-merge-decide",
        ]
        assert cap["agents"] == [
            "agents/pr-updater.md",
            "agents/watch-pr.md",
            "agents/watch-preview.md",
            "agents/diagnose-preview.md",
        ]


# ---------------------------------------------------------------------------
# T4: Camp placeholder loads with validate=false
# ---------------------------------------------------------------------------


class TestCampPlaceholderManifest:
    def test_camp_loads_without_error(self):
        m = load_manifest(_CAMP_MANIFEST)
        assert m.tool_name == "camp"

    def test_camp_validate_false_suppresses_existence_errors(self):
        # camp's dirs don't exist; must not raise even though they're missing
        load_manifest(_CAMP_MANIFEST)

    def test_camp_has_dev_env_capability(self):
        m = load_manifest(_CAMP_MANIFEST)
        assert "dev-env" in m.capabilities

    def test_camp_validate_true_with_missing_dirs_raises(self, tmp_path):
        # Same structure as camp but with validate=true (or no validate key) → must error
        content = """
[tool]
name = "camp"
base = ["skills/worktree"]

[capabilities.dev-env]
description = "provision/teardown dev-env instances"
skills = ["skills/dev-env"]
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError):
            load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# T5: Missing required fields / malformed TOML
# ---------------------------------------------------------------------------


class TestMalformedManifests:
    def test_malformed_toml_raises_manifest_error(self, tmp_path):
        manifest_path = _write_manifest(tmp_path, "this is [not valid toml ][[\n")
        with pytest.raises(ManifestError, match=str(manifest_path)):
            load_manifest(manifest_path)

    def test_missing_tool_section_raises_manifest_error(self, tmp_path):
        content = """
[capabilities.foo]
description = "something"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="\\[tool\\]"):
            load_manifest(manifest_path)

    def test_missing_tool_name_raises_manifest_error(self, tmp_path):
        content = """
[tool]
base = []

[capabilities.foo]
description = "something"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="name"):
            load_manifest(manifest_path)

    def test_missing_capability_description_raises_manifest_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        content = """
[tool]
name = "mytool"
base = []

[capabilities.foo]
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="description"):
            load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# T6: Empty-vs-missing capability rule
# ---------------------------------------------------------------------------


class TestEmptyVsMissingCapabilityKeys:
    def test_capability_with_explicit_empty_skills_and_agents_is_valid(self, tmp_path):
        # [] is valid — future/not-yet-built capability
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        content = """
[tool]
name = "mytool"
base = []

[capabilities.future]
description = "not yet built"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        m = load_manifest(manifest_path)
        assert "future" in m.capabilities

    def test_capability_missing_skills_key_entirely_raises_manifest_error(self, tmp_path):
        # Missing 'skills' key entirely (not []) → ManifestError
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        content = """
[tool]
name = "mytool"
base = []

[capabilities.broken]
description = "missing skills key"
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="skills"):
            load_manifest(manifest_path)

    def test_capability_missing_agents_key_entirely_raises_manifest_error(self, tmp_path):
        # Missing 'agents' key entirely (not []) → ManifestError
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        content = """
[tool]
name = "mytool"
base = []

[capabilities.broken]
description = "missing agents key"
skills = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="agents"):
            load_manifest(manifest_path)

    def test_capability_missing_both_keys_raises_manifest_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        content = """
[tool]
name = "mytool"
base = []

[capabilities.broken]
description = "no skills or agents keys"
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError):
            load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# T7: D-F path confinement guard
# ---------------------------------------------------------------------------


class TestConfinement:
    def _make_valid_manifest_with_bad_entry(
        self, tmp_path: Path, entry: str, in_section: str = "skills"
    ) -> Path:
        """Build a manifest with a traversal/absolute entry in skills or base."""
        plugin_root = _make_plugin_dir(tmp_path, "badtool")
        if in_section == "base":
            content = f"""
[tool]
name = "badtool"
base = ["{entry}"]

[capabilities.cap]
description = "a capability"
skills = []
agents = []
"""
        else:
            content = f"""
[tool]
name = "badtool"
base = []

[capabilities.cap]
description = "a capability"
skills = ["{entry}"]
agents = []
"""
        return _write_manifest(tmp_path, content)

    def test_dotdot_traversal_in_skills_raises_confine_error(self, tmp_path):
        manifest_path = self._make_valid_manifest_with_bad_entry(tmp_path, "../escape")
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)

    def test_dotdot_traversal_in_base_raises_confine_error(self, tmp_path):
        manifest_path = self._make_valid_manifest_with_bad_entry(
            tmp_path, "../escape", in_section="base"
        )
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)

    def test_absolute_path_in_skills_raises_confine_error(self, tmp_path):
        # Python's Path("/a") / "/b" drops /a — the confinement check must catch this
        manifest_path = self._make_valid_manifest_with_bad_entry(tmp_path, "/etc/passwd")
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)

    def test_absolute_path_in_base_raises_confine_error(self, tmp_path):
        manifest_path = self._make_valid_manifest_with_bad_entry(
            tmp_path, "/etc/passwd", in_section="base"
        )
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)

    def test_hooks_json_traversal_raises_confine_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "badtool")
        content = """
[tool]
name = "badtool"
base = []
hooks_json = "../../../etc/passwd"

[capabilities.cap]
description = "a capability"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)

    def test_absolute_hooks_json_raises_confine_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "badtool")
        content = """
[tool]
name = "badtool"
base = []
hooks_json = "/etc/passwd"

[capabilities.cap]
description = "a capability"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)

    def test_agents_traversal_raises_confine_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "badtool")
        content = """
[tool]
name = "badtool"
base = []

[capabilities.cap]
description = "a capability"
skills = []
agents = ["../../escape.md"]
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# T8: Wrong type errors (skills must be dirs; agents must be files; hooks_json must be file)
# ---------------------------------------------------------------------------


class TestWrongTypeErrors:
    def test_skill_entry_that_is_a_file_raises_manifest_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        # Create a FILE where a skill dir is expected
        skill_file = plugin_root / "skills" / "notadir"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("not a dir")

        content = """
[tool]
name = "mytool"
base = []

[capabilities.cap]
description = "a capability"
skills = ["skills/notadir"]
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="directory"):
            load_manifest(manifest_path)

    def test_hooks_json_that_is_a_dir_raises_manifest_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        hooks_dir = plugin_root / "hooks" / "hooks.json"
        hooks_dir.mkdir(parents=True)

        content = """
[tool]
name = "mytool"
base = []
hooks_json = "hooks/hooks.json"

[capabilities.cap]
description = "a capability"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="file"):
            load_manifest(manifest_path)

    def test_agent_entry_that_is_a_dir_raises_manifest_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        # Create a directory where an agent .md file is expected
        agent_dir = plugin_root / "agents" / "myagent.md"
        agent_dir.mkdir(parents=True)

        content = """
[tool]
name = "mytool"
base = []

[capabilities.cap]
description = "a capability"
skills = []
agents = ["agents/myagent.md"]
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="file"):
            load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# T9: Duplicate capability table — tomllib behavior
# ---------------------------------------------------------------------------


class TestDuplicateCapabilityTable:
    def test_duplicate_capability_table_raises_manifest_error(self, tmp_path):
        """tomllib raises TOMLDecodeError on duplicate tables;
        the loader must catch and wrap it as ManifestError citing the file."""
        content = """
[tool]
name = "mytool"
base = []

[capabilities.foo]
description = "first"
skills = []
agents = []

[capabilities.foo]
description = "duplicate"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match=str(manifest_path)):
            load_manifest(manifest_path)

    def test_tomllib_itself_raises_on_duplicate_tables(self, tmp_path):
        """Confirm that the underlying tomllib.TOMLDecodeError is the mechanism."""
        content = """
[capabilities.foo]
description = "first"
skills = []
agents = []

[capabilities.foo]
description = "duplicate"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(tomllib.TOMLDecodeError):
            with open(manifest_path, "rb") as f:
                tomllib.load(f)


# ---------------------------------------------------------------------------
# T10: Missing path (validate=true, dir doesn't exist) → named error
# ---------------------------------------------------------------------------


class TestMissingPathError:
    def test_missing_skill_dir_raises_manifest_error_naming_tool_cap_path(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        content = """
[tool]
name = "mytool"
base = []

[capabilities.myfeature]
description = "needs a skill"
skills = ["skills/nonexistent"]
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        err = pytest.raises(ManifestError, load_manifest, manifest_path)
        # Error must name the tool, capability, and missing path
        assert "mytool" in str(err.value)
        assert "myfeature" in str(err.value)
        assert "nonexistent" in str(err.value)

    def test_missing_agent_file_raises_manifest_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        content = """
[tool]
name = "mytool"
base = []

[capabilities.myfeature]
description = "needs an agent"
skills = []
agents = ["agents/nonexistent.md"]
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="nonexistent"):
            load_manifest(manifest_path)

    def test_missing_base_dir_raises_manifest_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        content = """
[tool]
name = "mytool"
base = ["skills/nonexistent-base"]

[capabilities.cap]
description = "a capability"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="nonexistent-base"):
            load_manifest(manifest_path)

    def test_missing_hooks_json_raises_manifest_error(self, tmp_path):
        plugin_root = _make_plugin_dir(tmp_path, "mytool")
        content = """
[tool]
name = "mytool"
base = []
hooks_json = "hooks/hooks.json"

[capabilities.cap]
description = "a capability"
skills = []
agents = []
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ManifestError, match="hooks"):
            load_manifest(manifest_path)
