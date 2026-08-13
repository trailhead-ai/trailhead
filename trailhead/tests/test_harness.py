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

    def test_detect_honors_claude_config_dir(self, tmp_path):
        """``CLAUDE_CONFIG_DIR`` is Claude Code's OWN relocation variable: when it
        is set the config dir really has moved, so detection must look there and
        must NOT fall back to ``$HOME/.claude``."""
        moved = tmp_path / "elsewhere"
        moved.mkdir()
        (tmp_path / ".claude").mkdir()
        env = {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(moved)}
        assert ClaudeCodeHarness.detect(env) is True
        assert (
            ClaudeCodeHarness.detect(
                {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path / "nope")}
            )
            is False
        )

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

    def test_defaults_accept_an_injected_env_and_still_degrade(self, tmp_path, capsys):
        # Callers pass ``env=`` so tests never touch the real Claude dir; a harness
        # without ruleset support must accept it and degrade all the same.
        h = _BareHarness()
        env = {"TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude")}
        assert h.user_ruleset_path("trailhead-outpost", env=env) is None
        assert h.user_ruleset_status("trailhead-outpost", "body", env=env) == "unsupported"
        assert h.install_user_ruleset("trailhead-outpost", "body", env=env) is None
        assert capsys.readouterr().out.strip() == UNSUPPORTED_RULESET_NOTICE
        assert not (tmp_path / "claude").exists()  # nothing written anywhere


class TestClaudeConfigDirRelocation:
    """Every path derived from the Claude config dir follows ``CLAUDE_CONFIG_DIR``.

    That variable is Claude Code's own relocation switch — when a user sets it,
    the config dir HAS moved, so a ruleset written to ``$HOME/.claude`` would land
    where Claude Code will never read it. These pin that the relocation reaches
    the whole surface, not just the session-transcript lookup it was added for.
    """

    def test_user_ruleset_path_follows_the_relocation(self, tmp_path):
        moved = tmp_path / "moved-claude"
        h = ClaudeCodeHarness()
        env = {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(moved)}
        assert h.user_ruleset_path("trailhead-lore", env=env) == (
            moved / "rules" / "trailhead-lore.md"
        )

    def test_install_user_ruleset_writes_under_the_relocation(self, tmp_path):
        moved = tmp_path / "moved-claude"
        moved.mkdir()
        h = ClaudeCodeHarness()
        env = {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(moved)}
        h.install_user_ruleset("trailhead-lore", "body\n", env=env)
        assert (moved / "rules" / "trailhead-lore.md").read_text() == "body\n"
        assert not (tmp_path / ".claude").exists()

    def test_user_ruleset_status_reads_the_relocated_file(self, tmp_path):
        moved = tmp_path / "moved-claude"
        (moved / "rules").mkdir(parents=True)
        (moved / "rules" / "trailhead-lore.md").write_text("body\n")
        h = ClaudeCodeHarness()
        env = {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(moved)}
        assert h.user_ruleset_status("trailhead-lore", "body\n", env=env) == "current"
        assert h.user_ruleset_status("trailhead-lore", "other\n", env=env) == "stale"

    def test_trailhead_override_still_wins_over_the_relocation(self, tmp_path):
        """``TRAILHEAD_CLAUDE_DIR`` is the test/redirect hatch and stays the
        highest-precedence answer, so a developer's own relocation cannot leak
        into a hermetic run."""
        moved = tmp_path / "moved-claude"
        override = tmp_path / "override-claude"
        h = ClaudeCodeHarness()
        env = {
            "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(moved),
            "TRAILHEAD_CLAUDE_DIR": str(override),
        }
        assert h.user_ruleset_path("x", env=env) == override / "rules" / "x.md"


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


class TestClaudeCodeUserRulesetNameConfinement:
    """A ruleset name may only ever address a file DIRECTLY inside the rules dir.

    ``~/.claude`` is outside any trailhead-owned tree, and the files under it are
    loaded into every session on the machine — so a name carrying separators or
    ``..`` must be refused BEFORE any directory is created or any byte written,
    not merely be unlikely to resolve.
    """

    def _env(self, claude_dir):
        return {"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}

    ESCAPES = [
        "/../../CLAUDE",
        "../CLAUDE",
        "../../CLAUDE",
        "sub/nested",
        "/abs",
        "..",
        ".",
        "",
    ]

    @pytest.mark.parametrize("name", ESCAPES)
    def test_path_rejects_an_escaping_name(self, tmp_path, name):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        h = ClaudeCodeHarness()
        with pytest.raises(HarnessError, match="ruleset name"):
            h.user_ruleset_path(name, env=self._env(claude_dir))

    @pytest.mark.parametrize("name", ESCAPES)
    def test_install_rejects_an_escaping_name_and_creates_nothing(self, tmp_path, name):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        h = ClaudeCodeHarness()
        with pytest.raises(HarnessError, match="ruleset name"):
            h.install_user_ruleset(name, "payload\n", env=self._env(claude_dir))
        assert list(claude_dir.iterdir()) == []  # no rules/, no attacker-directed dirs
        assert not (tmp_path / "CLAUDE.md").exists()

    @pytest.mark.parametrize("name", ESCAPES)
    def test_status_rejects_an_escaping_name(self, tmp_path, name):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        h = ClaudeCodeHarness()
        with pytest.raises(HarnessError, match="ruleset name"):
            h.user_ruleset_status(name, "payload\n", env=self._env(claude_dir))

    def test_ordinary_name_still_works(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        h = ClaudeCodeHarness()
        h.install_user_ruleset("trailhead-outpost", "body\n", env=self._env(claude_dir))
        assert (claude_dir / "rules" / "trailhead-outpost.md").read_text() == "body\n"


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


class TestSessionResumeBaseDefault:
    """The resume-argv seam is CONCRETE with a degrading default: a harness with
    no resume concept answers None rather than raising."""

    def test_returns_none(self):
        assert _BareHarness().session_resume("abc123") is None


class TestClaudeCodeSessionResume:
    """Claude Code resumes a session by id. The seam OWNS the argv: callers never
    compose it, they receive a ready-to-exec token list or None."""

    def test_returns_resume_argv_for_the_session(self):
        assert ClaudeCodeHarness().session_resume("sess-1") == [
            "claude",
            "--resume",
            "sess-1",
        ]

    def test_argv_is_a_token_list_needing_no_shell(self):
        """Every element is a separate token — nothing is pre-joined or quoted, so
        an exec-style caller passes it through untouched."""
        argv = ClaudeCodeHarness().session_resume("sess-1")
        assert all(isinstance(tok, str) for tok in argv)
        assert not any(" " in tok for tok in argv)

    def test_rejects_a_session_id_that_is_not_a_plain_token(self):
        """A malformed id must never reach an argv the caller will exec."""
        for bad in ("", "a b", "a;rm -rf /", "$(whoami)", "../escape", "a/b", "-x"):
            assert ClaudeCodeHarness().session_resume(bad) is None, bad

    def test_rejects_a_non_string_session_id(self):
        assert ClaudeCodeHarness().session_resume(None) is None


class TestSessionRetentionDaysBaseDefault:
    """The retention seam is CONCRETE with a degrading default: a harness that
    does not clean up transcripts on a schedule answers None, and a caller must
    skip its retention warning entirely rather than invent a window."""

    def test_returns_none(self):
        assert _BareHarness().session_retention_days() is None


class TestClaudeCodeSessionRetentionDays:
    """Claude Code deletes transcripts older than the top-level `cleanupPeriodDays`
    setting; absent, its own default is 30 days."""

    def _write_settings(self, claude_dir, body):
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.json").write_text(body)

    def test_reads_explicit_cleanup_period_days(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        self._write_settings(claude_dir, '{"cleanupPeriodDays": 7}')
        got = ClaudeCodeHarness().session_retention_days(
            env={"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}
        )
        assert got == 7

    def test_defaults_to_thirty_when_key_absent(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        self._write_settings(claude_dir, '{"theme": "dark"}')
        got = ClaudeCodeHarness().session_retention_days(
            env={"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}
        )
        assert got == 30

    def test_defaults_to_thirty_when_settings_file_absent(self, tmp_path):
        got = ClaudeCodeHarness().session_retention_days(
            env={"TRAILHEAD_CLAUDE_DIR": str(tmp_path / "nope")}
        )
        assert got == 30

    def test_defaults_to_thirty_when_settings_are_unreadable(self, tmp_path):
        """A corrupt settings file must not crash a caller that only wants a hint."""
        claude_dir = tmp_path / ".claude"
        self._write_settings(claude_dir, "{not json")
        got = ClaudeCodeHarness().session_retention_days(
            env={"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}
        )
        assert got == 30

    def test_ignores_a_non_integer_or_out_of_range_value(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        for body in ('{"cleanupPeriodDays": "7"}', '{"cleanupPeriodDays": 0}',
                     '{"cleanupPeriodDays": -3}', '{"cleanupPeriodDays": true}'):
            self._write_settings(claude_dir, body)
            got = ClaudeCodeHarness().session_retention_days(
                env={"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}
            )
            assert got == 30, body

    def test_honors_claude_config_dir_env(self, tmp_path):
        claude_dir = tmp_path / "elsewhere"
        self._write_settings(claude_dir, '{"cleanupPeriodDays": 5}')
        got = ClaudeCodeHarness().session_retention_days(
            env={"CLAUDE_CONFIG_DIR": str(claude_dir), "HOME": str(tmp_path)}
        )
        assert got == 5


class TestSessionRetentionSetting:
    """The remedy a retention warning names is harness knowledge: the base seam
    reports no setting, Claude Code names its own key."""

    def test_base_default_is_none(self):
        assert _BareHarness().session_retention_setting() is None

    def test_claude_code_names_its_cleanup_key(self):
        assert ClaudeCodeHarness().session_retention_setting() == "cleanupPeriodDays"
