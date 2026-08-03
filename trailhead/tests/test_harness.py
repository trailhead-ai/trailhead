"""Tests for trailhead/harness/ — the harness interface, factory, and detection."""

import pytest

from trailhead.harness import (
    ClaudeCodeHarness,
    Harness,
    HarnessError,
    canonical_name,
    detect_harnesses,
    get_harness,
    known_harness_names,
)
from trailhead.harness.base import UNSUPPORTED_RULESET_NOTICE


class TestFactory:
    def test_known_names_includes_claude_code(self):
        assert "claude_code" in known_harness_names()

    def test_get_harness_returns_instance(self):
        h = get_harness("claude_code")
        assert isinstance(h, ClaudeCodeHarness)
        assert isinstance(h, Harness)
        assert h.name == "claude_code"

    def test_alias_claude_resolves_to_claude_code(self):
        assert canonical_name("claude") == "claude_code"
        assert isinstance(get_harness("claude"), ClaudeCodeHarness)

    def test_unknown_harness_raises(self):
        with pytest.raises(HarnessError, match="codex"):
            get_harness("codex")


class TestDetection:
    def test_detect_true_when_claude_dir_present(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        assert ClaudeCodeHarness.detect({"HOME": str(tmp_path)}) is True

    def test_detect_false_when_absent(self, tmp_path):
        assert ClaudeCodeHarness.detect({"HOME": str(tmp_path)}) is False

    def test_detect_honors_explicit_override(self, tmp_path):
        cdir = tmp_path / "custom-claude"
        cdir.mkdir()
        assert ClaudeCodeHarness.detect({"TRAILHEAD_CLAUDE_DIR": str(cdir)}) is True
        assert ClaudeCodeHarness.detect({"TRAILHEAD_CLAUDE_DIR": str(tmp_path / "nope")}) is False

    def test_detect_harnesses_finds_claude_code(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        found = detect_harnesses({"HOME": str(tmp_path)})
        assert [h.name for h in found] == ["claude_code"]

    def test_detect_harnesses_empty_when_none_present(self, tmp_path):
        assert detect_harnesses({"HOME": str(tmp_path)}) == []


class TestComposedRoot:
    def test_per_harness_root(self, tmp_path):
        h = ClaudeCodeHarness()
        assert h.composed_root(tmp_path) == tmp_path / "composed" / "claude_code"


class TestMarkers:
    def test_is_registered_reads_marker(self, tmp_path):
        h = ClaudeCodeHarness()
        assert h.is_registered(tmp_path) is False
        (tmp_path / ".trailhead-registered").write_text("{}")
        assert h.is_registered(tmp_path) is True

    def test_is_installed_reads_per_tool_marker(self, tmp_path):
        h = ClaudeCodeHarness()
        assert h.is_installed("lore", tmp_path) is False
        (tmp_path / ".trailhead-installed-lore").write_text("{}")
        assert h.is_installed("lore", tmp_path) is True


class TestInstalledTools:
    """installed_tools enumerates the per-tool markers under composed_root."""

    def test_empty_when_no_markers(self, tmp_path):
        assert ClaudeCodeHarness().installed_tools(tmp_path) == []

    def test_empty_when_root_absent(self, tmp_path):
        assert ClaudeCodeHarness().installed_tools(tmp_path / "nope") == []

    def test_enumerates_markers_sorted(self, tmp_path):
        (tmp_path / ".trailhead-installed-lore").write_text("{}")
        (tmp_path / ".trailhead-installed-camp").write_text("{}")
        assert ClaudeCodeHarness().installed_tools(tmp_path) == ["camp", "lore"]

    def test_ignores_non_install_markers(self, tmp_path):
        (tmp_path / ".trailhead-registered").write_text("{}")
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".trailhead-installed-lore").write_text("{}")
        assert ClaudeCodeHarness().installed_tools(tmp_path) == ["lore"]


class TestManifestName:
    """manifest_name reads the display name from the harness's generated manifest."""

    def test_none_when_absent(self, tmp_path):
        assert ClaudeCodeHarness().manifest_name(tmp_path) is None

    def test_reads_name_from_generated_manifest(self, tmp_path):
        ClaudeCodeHarness().generate_manifest(["lore"], tmp_path)
        assert ClaudeCodeHarness().manifest_name(tmp_path) == "trailhead"

    def test_none_when_malformed(self, tmp_path):
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "marketplace.json").write_text("{not json")
        assert ClaudeCodeHarness().manifest_name(tmp_path) is None


class TestManifestExists:
    """manifest_exists distinguishes an absent manifest file from a malformed one.

    manifest_name() returns None for both cases (its contract, pinned above); doctor
    needs manifest_exists() alongside it to render "absent" vs "present but corrupt"
    distinctly.
    """

    def test_false_when_absent(self, tmp_path):
        assert ClaudeCodeHarness().manifest_exists(tmp_path) is False

    def test_true_when_malformed(self, tmp_path):
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "marketplace.json").write_text("{not json")
        assert ClaudeCodeHarness().manifest_exists(tmp_path) is True

    def test_true_when_well_formed(self, tmp_path):
        ClaudeCodeHarness().generate_manifest(["lore"], tmp_path)
        assert ClaudeCodeHarness().manifest_exists(tmp_path) is True


class TestGenerateManifestAndRegister:
    def test_generate_manifest_writes_marketplace(self, tmp_path):
        ClaudeCodeHarness().generate_manifest(["lore"], tmp_path)
        mkt = tmp_path / ".claude-plugin" / "marketplace.json"
        assert mkt.exists()

    def test_register_and_install_use_runner(self, tmp_path):
        calls = []

        def runner(args, **kw):
            return calls.append(list(args))

        h = ClaudeCodeHarness()
        h.register(tmp_path, runner=runner)
        h.install_tool("lore", tmp_path, runner=runner)
        assert any("marketplace" in c and "add" in c for c in calls)
        assert any("install" in c and "lore@trailhead" in c for c in calls)
        # markers written after success
        assert h.is_registered(tmp_path)
        assert h.is_installed("lore", tmp_path)


class _BareHarness(Harness):
    """Minimal concrete Harness that does NOT override the user-ruleset methods.

    Exercises the base-class safe defaults (degrade-visibly) in isolation.
    """

    name = "bare"

    @classmethod
    def detect(cls, env):
        return False

    def generate_manifest(self, tools, composed_root):
        raise NotImplementedError

    def is_registered(self, composed_root):
        raise NotImplementedError

    def is_installed(self, tool, composed_root):
        raise NotImplementedError

    def installed_tools(self, composed_root):
        raise NotImplementedError

    def register(self, composed_root, *, runner=None):
        raise NotImplementedError

    def install_tool(self, tool, composed_root, *, runner=None):
        raise NotImplementedError

    def rewire_tool(self, tool, composed_root, *, runner=None):
        raise NotImplementedError

    def unregister_tool(self, tool, composed_root, *, runner=None):
        raise NotImplementedError

    def unregister_marketplace(self, composed_root, *, runner=None):
        raise NotImplementedError


class TestUserRulesetBaseDefault:
    """Base default degrades VISIBLY — never silently no-ops, never crashes."""

    def test_user_ruleset_path_is_none(self):
        assert _BareHarness().user_ruleset_path("trailhead-lore") is None

    def test_user_ruleset_status_is_unsupported(self):
        assert _BareHarness().user_ruleset_status("trailhead-lore", "x") == "unsupported"

    def test_install_user_ruleset_no_write_no_error_fixed_notice(self, capsys):
        h = _BareHarness()
        # Performs no write and raises no error; emits the FIXED notice string.
        assert h.install_user_ruleset("trailhead-lore", "body") is None
        out = capsys.readouterr().out
        assert out.strip() == UNSUPPORTED_RULESET_NOTICE
        assert "trailhead-lore" not in out  # static notice, no per-name interpolation


class TestClaudeCodeUserRuleset:
    """ClaudeCodeHarness writes ~/.claude/rules/<name>.md, atomically + idempotently."""

    def _env(self, claude_dir):
        return {"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}

    def test_install_writes_byte_exact(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        h = ClaudeCodeHarness()
        content = "## rules\nbody\n"
        h.install_user_ruleset("trailhead-lore", content, env=self._env(claude_dir))
        target = claude_dir / "rules" / "trailhead-lore.md"
        assert target.read_text() == content

    def test_path_points_at_rules_file(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        h = ClaudeCodeHarness()
        assert h.user_ruleset_path("trailhead-lore", env=self._env(claude_dir)) == (
            claude_dir / "rules" / "trailhead-lore.md"
        )

    def test_reinstall_identical_is_noop(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        h = ClaudeCodeHarness()
        content = "## rules\nbody\n"
        env = self._env(claude_dir)
        h.install_user_ruleset("trailhead-lore", content, env=env)
        target = claude_dir / "rules" / "trailhead-lore.md"
        before = target.stat().st_mtime_ns
        # No leftover temp files from the first write.
        assert list((claude_dir / "rules").iterdir()) == [target]
        h.install_user_ruleset("trailhead-lore", content, env=env)
        assert target.read_text() == content
        assert target.stat().st_mtime_ns == before  # untouched: true no-op
        assert list((claude_dir / "rules").iterdir()) == [target]

    def test_status_current_after_clean_install(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        h = ClaudeCodeHarness()
        content = "## rules\nbody\n"
        env = self._env(claude_dir)
        h.install_user_ruleset("trailhead-lore", content, env=env)
        assert h.user_ruleset_status("trailhead-lore", content, env=env) == "current"

    def test_status_stale_after_mutation(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        h = ClaudeCodeHarness()
        content = "## rules\nbody\n"
        env = self._env(claude_dir)
        h.install_user_ruleset("trailhead-lore", content, env=env)
        target = claude_dir / "rules" / "trailhead-lore.md"
        target.write_text(content + "drift\n")
        assert h.user_ruleset_status("trailhead-lore", content, env=env) == "stale"

    def test_status_missing_after_removal(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        h = ClaudeCodeHarness()
        content = "## rules\nbody\n"
        env = self._env(claude_dir)
        h.install_user_ruleset("trailhead-lore", content, env=env)
        (claude_dir / "rules" / "trailhead-lore.md").unlink()
        assert h.user_ruleset_status("trailhead-lore", content, env=env) == "missing"


class TestSessionTranscriptPathBaseDefault:
    """The transcript-path seam is CONCRETE with a degrading default: a harness
    with no session-transcript concept answers None rather than raising."""

    def test_returns_none(self, tmp_path):
        assert _BareHarness().session_transcript_path("abc123", tmp_path) is None


class TestClaudeCodeSessionTranscriptPath:
    """Claude Code stores transcripts at <config_dir>/projects/<munged-cwd>/<id>.jsonl,
    where <munged-cwd> is the session's start cwd with BOTH '/' and '.' replaced by '-'."""

    def _seed(self, claude_dir, workspace, session_id):
        munged = str(workspace).replace("/", "-").replace(".", "-")
        d = claude_dir / "projects" / munged
        d.mkdir(parents=True)
        t = d / f"{session_id}.jsonl"
        t.write_text("{}\n")
        return t

    def test_resolves_existing_transcript(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        ws = tmp_path / "state" / "camp" / "g" / "worktrees" / "slug"
        ws.mkdir(parents=True)
        expected = self._seed(claude_dir, ws, "sess-1")
        got = ClaudeCodeHarness().session_transcript_path(
            "sess-1", ws, env={"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}
        )
        assert got == expected

    def test_munges_dot_and_slash_to_dash(self, tmp_path):
        """A '/.'-containing path munges to a DOUBLE dash — both characters map."""
        claude_dir = tmp_path / ".claude"
        ws = tmp_path / ".local" / "state"
        ws.mkdir(parents=True)
        expected = self._seed(claude_dir, ws, "sess-2")
        assert "--local" in expected.parent.name
        got = ClaudeCodeHarness().session_transcript_path(
            "sess-2", ws, env={"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}
        )
        assert got == expected

    def test_none_when_transcript_absent(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        assert (
            ClaudeCodeHarness().session_transcript_path(
                "missing-id", ws, env={"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}
            )
            is None
        )

    def test_honors_claude_config_dir_env(self, tmp_path):
        """Claude Code's own CLAUDE_CONFIG_DIR relocates the whole config dir."""
        claude_dir = tmp_path / "elsewhere"
        ws = tmp_path / "ws"
        ws.mkdir()
        expected = self._seed(claude_dir, ws, "sess-3")
        got = ClaudeCodeHarness().session_transcript_path(
            "sess-3", ws, env={"CLAUDE_CONFIG_DIR": str(claude_dir), "HOME": str(tmp_path)}
        )
        assert got == expected

    def test_rejects_traversal_in_session_id(self, tmp_path):
        """A session id is a path COMPONENT; anything else must not reach the filesystem."""
        claude_dir = tmp_path / ".claude"
        ws = tmp_path / "ws"
        ws.mkdir()
        (claude_dir / "projects").mkdir(parents=True)
        for bad in ("../escape", "a/b", "", "."):
            assert (
                ClaudeCodeHarness().session_transcript_path(
                    bad, ws, env={"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}
                )
                is None
            )
