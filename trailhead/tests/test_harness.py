"""Tests for trailhead/harness/ — the harness interface, factory, and detection."""

import dataclasses
import inspect
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trailhead.harness import (
    _HARNESSES,
    ClaudeCodeHarness,
    Harness,
    HarnessError,
    SessionTranscript,
    canonical_name,
    detect_harnesses,
    get_harness,
    known_harness_names,
)
from trailhead.harness.claude_code import _ERROR_EXCERPT_LIMIT
from trailhead.harness.base import (
    MODALITIES,
    MODALITY_DETACHED_GUI,
    MODALITY_TTY_REQUIRED,
    UNSUPPORTED_RULESET_NOTICE,
    SessionRecord,
)


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
    """Markers are read from the config dir `env` resolves, not the composed tree."""

    def test_is_registered_reads_marker(self, tmp_path, claude_dir):
        h = ClaudeCodeHarness()
        assert h.is_registered(tmp_path) is False
        (claude_dir / ".trailhead-registered").write_text("{}")
        assert h.is_registered(tmp_path) is True

    def test_is_installed_reads_per_tool_marker(self, tmp_path, claude_dir):
        h = ClaudeCodeHarness()
        assert h.is_installed("lore", tmp_path) is False
        (claude_dir / ".trailhead-installed-lore").write_text("{}")
        assert h.is_installed("lore", tmp_path) is True

    def test_a_marker_in_the_composed_tree_is_not_registration(self, tmp_path, claude_dir):
        h = ClaudeCodeHarness()
        (tmp_path / ".trailhead-registered").write_text("{}")
        (tmp_path / ".trailhead-installed-lore").write_text("{}")
        assert h.is_registered(tmp_path) is False
        assert h.is_installed("lore", tmp_path) is False


class TestInstalledTools:
    """installed_tools enumerates the per-tool markers in the resolved config dir."""

    def test_empty_when_no_markers(self, tmp_path, claude_dir):
        assert ClaudeCodeHarness().installed_tools(tmp_path) == []

    def test_empty_when_config_dir_absent(self, tmp_path):
        env = {"TRAILHEAD_CLAUDE_DIR": str(tmp_path / "nope")}
        assert ClaudeCodeHarness().installed_tools(tmp_path, env=env) == []

    def test_enumerates_markers_sorted(self, tmp_path, claude_dir):
        (claude_dir / ".trailhead-installed-lore").write_text("{}")
        (claude_dir / ".trailhead-installed-camp").write_text("{}")
        assert ClaudeCodeHarness().installed_tools(tmp_path) == ["camp", "lore"]

    def test_ignores_non_install_markers(self, tmp_path, claude_dir):
        (claude_dir / ".trailhead-registered").write_text("{}")
        (claude_dir / ".claude-plugin").mkdir()
        (claude_dir / ".trailhead-installed-lore").write_text("{}")
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

    def is_registered(self, composed_root, *, env=None):
        raise NotImplementedError

    def is_installed(self, tool, composed_root, *, env=None):
        raise NotImplementedError

    def installed_tools(self, composed_root, *, env=None):
        raise NotImplementedError

    def register(self, composed_root, *, runner=None, env=None):
        raise NotImplementedError

    def install_tool(self, tool, composed_root, *, runner=None, env=None):
        raise NotImplementedError

    def rewire_tool(self, tool, composed_root, *, runner=None, env=None):
        raise NotImplementedError

    def unregister_tool(self, tool, composed_root, *, runner=None, env=None):
        raise NotImplementedError

    def unregister_marketplace(self, composed_root, *, runner=None, env=None):
        raise NotImplementedError


class TestBareHarnessTracksTheSeam:
    """The exemplar harness must be callable exactly the way the core calls it.

    ``_BareHarness`` is this repo's only worked example of implementing the
    ``Harness`` seam, so a new harness gets written from it. An ABC does not
    check signatures, so a stale parameter list here raises ``TypeError`` in the
    field — on the first real ``wire()`` run — rather than at definition time.
    This pins the exemplar to the seam so it cannot drift silently.
    """

    def test_every_seam_method_takes_the_seam_parameters(self):
        for name in sorted(Harness.__abstractmethods__):
            declared = inspect.signature(getattr(Harness, name))
            implemented = inspect.signature(getattr(_BareHarness, name))
            assert list(implemented.parameters) == list(declared.parameters), name
            for pname, param in declared.parameters.items():
                assert implemented.parameters[pname].kind == param.kind, f"{name}.{pname}"

    def test_the_core_can_call_every_seam_method_with_env(self):
        """The calls ``wire()`` makes, with the keywords it actually passes."""
        h = _BareHarness()
        env: dict[str, str] = {}
        for call in (
            lambda: h.is_registered(Path("x"), env=env),
            lambda: h.is_installed("lore", Path("x"), env=env),
            lambda: h.installed_tools(Path("x"), env=env),
            lambda: h.register(Path("x"), runner=None, env=env),
            lambda: h.install_tool("lore", Path("x"), runner=None, env=env),
            lambda: h.rewire_tool("lore", Path("x"), runner=None, env=env),
            lambda: h.unregister_tool("lore", Path("x"), runner=None, env=env),
            lambda: h.unregister_marketplace(Path("x"), runner=None, env=env),
        ):
            with pytest.raises(NotImplementedError):
                call()


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


class TestClaudeCodeSessionLaunch:
    """Claude Code launches a brand-new session by caller-chosen id. The seam
    OWNS the argv the same way ``session_resume`` does."""

    def test_returns_launch_argv_for_the_session(self, tmp_path):
        assert ClaudeCodeHarness().session_launch(tmp_path, "sess-1") == [
            "claude",
            "--remote-control",
            "--session-id",
            "sess-1",
        ]

    def test_argv_is_a_token_list_needing_no_shell(self, tmp_path):
        """Every element is a separate token — nothing is pre-joined or quoted, so
        an exec-style caller passes it through untouched."""
        argv = ClaudeCodeHarness().session_launch(tmp_path, "sess-1")
        assert all(isinstance(tok, str) for tok in argv)
        assert not any(" " in tok for tok in argv)
        # A shell-active character would have to be quoted before a shell saw it;
        # its absence is what lets an exec-style caller skip quoting entirely.
        shell_active = set("|&;<>()$`\\\"'\t\n*?[]{}#~")
        assert not any(shell_active & set(tok) for tok in argv)

    def test_malformed_id_raises_unlike_session_resume_which_returns_none(self, tmp_path):
        """Pin the deliberate divergence: session_resume degrades to None on a bad
        id, session_launch raises — a caller who learned "check for None" from
        session_resume must not silently pass a malformed id through to argv."""
        for bad in ("", "a b", "a;rm -rf /", "$(whoami)", "../escape", "a/b", "-x"):
            assert ClaudeCodeHarness().session_resume(bad) is None, bad
            with pytest.raises(HarnessError):
                ClaudeCodeHarness().session_launch(tmp_path, bad)

    def test_rejects_a_non_string_session_id(self, tmp_path):
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().session_launch(tmp_path, None)

    def test_rejects_a_leading_dash_session_id_as_flag_injection(self, tmp_path):
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().session_launch(tmp_path, "--dangerously-skip-permissions")

    def test_session_name_appends_the_name_flag(self, tmp_path):
        assert ClaudeCodeHarness().session_launch(
            tmp_path, "sess-1", session_name="camp-feat-x-abcd1234"
        ) == [
            "claude",
            "--remote-control",
            "--session-id",
            "sess-1",
            "--name",
            "camp-feat-x-abcd1234",
        ]

    def test_no_session_name_means_no_name_flag(self, tmp_path):
        assert "--name" not in ClaudeCodeHarness().session_launch(tmp_path, "sess-1")

    def test_malformed_session_name_raises_like_a_malformed_id(self, tmp_path):
        """The name lands in the same argv as the id, so it is held to the same
        inert-token predicate — including the leading-dash flag-injection case."""
        bads = ("", "a b", "a;rm -rf /", "$(whoami)", "../escape", "a/b", "-x", "--dangerously-skip-permissions")
        for bad in bads:
            with pytest.raises(HarnessError):
                ClaudeCodeHarness().session_launch(tmp_path, "sess-1", session_name=bad)

    def test_no_filesystem_validation_of_workspace(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        assert ClaudeCodeHarness().session_launch(missing, "sess-1") == [
            "claude",
            "--remote-control",
            "--session-id",
            "sess-1",
        ]


    def test_session_launch_carries_a_settings_file(self, tmp_path):
        """A launch rooted where the harness will not DISCOVER camp's settings can
        still be handed them.

        The harness resolves project settings by first-match-wins upward search
        that stops at a git repository boundary, so a session rooted at a member
        repo inside a workspace never finds the workspace's own hooks. `--settings`
        loads them additionally, without anything being written into the repo the
        session is rooted in.
        """
        settings = tmp_path / "settings.json"
        argv = ClaudeCodeHarness().session_launch(
            tmp_path, "sess-1", settings_path=settings
        )
        assert argv[-2:] == ["--settings", str(settings)]

    def test_session_launch_omits_settings_when_not_asked(self, tmp_path):
        assert "--settings" not in ClaudeCodeHarness().session_launch(tmp_path, "sess-1")

    def test_session_launch_refuses_a_flag_shaped_settings_path(self, tmp_path):
        """Same flag-injection surface as session_id and session_name.

        A path that reads as a flag lands in the same argv and is refused there
        for the same reason, rather than being passed to the harness to interpret.
        """
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().session_launch(
                tmp_path, "sess-1", settings_path=Path("--dangerously-skip-permissions")
            )


class TestClaudeCodeSessionLaunchModality:
    def test_returns_tty_required(self):
        assert ClaudeCodeHarness().session_launch_modality() == MODALITY_TTY_REQUIRED

    def test_is_a_member_of_modalities(self):
        assert ClaudeCodeHarness().session_launch_modality() in MODALITIES


class TestClaudeCodeSessionLaunchEnvUnset:
    def test_the_account_variable_is_scrubbed_so_the_default_is_absence(self):
        """The undeclared-account default is the variable being ABSENT, which only
        a caller-applied scrub can express — see ``session_launch_env_set``."""
        assert "CLAUDE_CONFIG_DIR" in ClaudeCodeHarness().session_launch_env_unset()

    def test_contains_the_documented_leaking_vars(self):
        unset = ClaudeCodeHarness().session_launch_env_unset()
        for var in (
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_SESSION_ACCESS_TOKEN",
            "CLAUDE_CODE_MESSAGING_SOCKET",
            "CLAUDE_CODE_MESSAGING_TOKEN",
        ):
            assert var in unset


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


class TestLaunchEnumerationBaseDefaults:
    """The launch/enumeration seam is CONCRETE with degrading defaults: a harness
    with no launch or enumeration concept answers None for all six, never
    raises, and never requires implementing anything to instantiate."""

    def test_session_launch_returns_none(self, tmp_path):
        assert _BareHarness().session_launch(tmp_path, "sess-1") is None

    def test_session_launch_modality_returns_none(self):
        assert _BareHarness().session_launch_modality() is None

    def test_session_launch_env_unset_returns_none(self):
        assert _BareHarness().session_launch_env_unset() is None

    def test_session_launch_env_set_returns_none(self):
        assert _BareHarness().session_launch_env_set(None) is None

    def test_session_launch_env_set_returns_none_for_a_declared_account(self):
        assert _BareHarness().session_launch_env_set("/somewhere") is None

    def test_session_enumerate_returns_none(self, tmp_path):
        assert _BareHarness().session_enumerate(tmp_path) is None

    def test_session_enumerate_returns_none_with_no_workspace(self):
        assert _BareHarness().session_enumerate() is None

    def test_parse_session_list_returns_none(self):
        assert _BareHarness().parse_session_list("anything") is None

    def test_bare_harness_instantiates_without_implementing_any_of_the_six(self, tmp_path):
        """All six are non-abstract: subclassing Harness without overriding them
        must not raise TypeError at instantiation."""
        h = _BareHarness()
        assert h.session_launch(tmp_path, "sess-1") is None
        assert h.session_launch_modality() is None
        assert h.session_launch_env_unset() is None
        assert h.session_launch_env_set(None) is None
        assert h.session_enumerate() is None
        assert h.parse_session_list("x") is None


class TestModalityVocabulary:
    def test_constants_have_exact_spec_values(self):
        assert MODALITY_TTY_REQUIRED == "tty-required"
        assert MODALITY_DETACHED_GUI == "detached-gui"

    def test_modalities_frozenset_is_exactly_the_two_constants(self):
        assert MODALITIES == {MODALITY_TTY_REQUIRED, MODALITY_DETACHED_GUI}
        assert isinstance(MODALITIES, frozenset)


class _LaunchOnlyBrokenHarness(_BareHarness):
    """Implements session_launch but not the other two launch-trio members —
    the base defaults leave modality/env_unset at None, breaking the triple."""

    name = "launch-only-broken"

    def session_launch(self, workspace, session_id):
        return ["fake", "argv"]


class _BadModalityHarness(_BareHarness):
    """Implements the full launch quartet, but the modality is spelled outside
    MODALITIES — the membership assertion, not just non-None, must catch it."""

    name = "bad-modality"

    def session_launch(self, workspace, session_id):
        return ["fake", "argv"]

    def session_launch_modality(self):
        return "headless"

    def session_launch_env_unset(self):
        return []

    def session_launch_env_set(self, account, *, env=None):
        return {"FAKE_ACCOUNT_DIR": account or "/default"}


class _NoScrubListHarness(_BareHarness):
    """Overrides the whole launch quartet but answers None for the scrub list —
    so override-detection alone passes it. The value assertion must catch it:
    a launch-capable harness with nothing to scrub returns [], and None here
    would make a caller skip the credential scrub."""

    name = "no-scrub-list"

    def session_launch(self, workspace, session_id):
        return ["fake", "argv"]

    def session_launch_modality(self):
        return MODALITY_TTY_REQUIRED

    def session_launch_env_unset(self):
        return None

    def session_launch_env_set(self, account, *, env=None):
        return {"FAKE_ACCOUNT_DIR": account or "/default"}


class _NoLaunchEnvSetHarness(_BareHarness):
    """Overrides the whole launch quartet but answers None for the account
    binding — override-detection alone passes it. The value assertion must
    catch it: None there means 'launch unsupported', so a caller reading it as
    'nothing to set' would let the child inherit whichever account the ambient
    environment carried, which is precisely the defect this seam removes."""

    name = "no-launch-env-set"

    def session_launch(self, workspace, session_id):
        return ["fake", "argv"]

    def session_launch_modality(self):
        return MODALITY_TTY_REQUIRED

    def session_launch_env_unset(self):
        return []

    def session_launch_env_set(self, account, *, env=None):
        return None


class _EnumerateOnlyBrokenHarness(_BareHarness):
    """Implements session_enumerate but not parse_session_list — the base
    default leaves parse_session_list at None, breaking the pair."""

    name = "enumerate-only-broken"

    def session_enumerate(self, workspace=None):
        return ["fake"]


class TestBothOrNeitherInvariants:
    """Both-or-neither contracts on the launch quartet and the enumeration pair.

    (a) session_launch / session_launch_modality / session_launch_env_unset /
    session_launch_env_set must be non-None together or None together, and a
    non-None modality must be a MEMBER of MODALITIES — not merely non-None.
    (b) session_enumerate and parse_session_list must likewise be non-None
    together or None together.

    ``test_every_registered_harness_satisfies_both_invariants`` iterates the
    real registry (``_HARNESSES``), which holds exactly ONE entry
    (ClaudeCodeHarness) today. Passing that test proves the invariant holds for
    that one harness — it is the CONTRACT every future harness added to the
    registry must satisfy, NOT evidence that this test exercises cross-harness
    coverage. The fixture-based tests above it are what prove the assertions
    inside the helper actually bite, by breaking each half deliberately and
    watching the helper fail.
    """

    @staticmethod
    def _assert_launch_quartet(harness: Harness, *, env: dict[str, str] | None = None) -> None:
        # Detects implementation by OVERRIDE, not by probing with a fixed
        # session_id/workspace: a real harness's id guard may reject
        # "sess-1" outright (raising HarnessError, not returning None),
        # which would misreport a correctly-implemented trio as violating
        # the invariant.
        cls = type(harness)
        overrides = (
            cls.session_launch is not Harness.session_launch,
            cls.session_launch_modality is not Harness.session_launch_modality,
            cls.session_launch_env_unset is not Harness.session_launch_env_unset,
            cls.session_launch_env_set is not Harness.session_launch_env_set,
        )
        assert all(overrides) or not any(overrides), (
            f"{type(harness).__name__}: session_launch/session_launch_modality/"
            f"session_launch_env_unset/session_launch_env_set must be overridden "
            f"together or not at all, got {overrides!r}"
        )
        if any(overrides):
            modality = harness.session_launch_modality()
            assert modality in MODALITIES, (
                f"{type(harness).__name__}: session_launch_modality() returned "
                f"{modality!r}, which is not a member of MODALITIES"
            )
            # The VALUE half, not just the override half. A harness that
            # advertises launch but answers None here would have its caller
            # skip the credential scrub entirely — and unlike session_launch,
            # this takes no probe input, so the override-detection rationale
            # above does not apply. `[]` is the honest "nothing to scrub".
            assert harness.session_launch_env_unset() is not None, (
                f"{type(harness).__name__}: advertises launch but "
                f"session_launch_env_unset() returned None; a harness with "
                f"nothing to scrub must return [], since None means "
                f"'launch unsupported'"
            )
            # The same VALUE half for the account binding. `env` is injected so
            # a real harness resolves its default inside the sandbox rather than
            # from the developer's own environment (Axiom 6).
            assert harness.session_launch_env_set(None, env=env) is not None, (
                f"{type(harness).__name__}: advertises launch but "
                f"session_launch_env_set() returned None; a launch-capable "
                f"harness always answers a concrete mapping, since None means "
                f"'launch unsupported'"
            )

    @staticmethod
    def _assert_enumeration_pair(harness: Harness) -> None:
        # Override detection only — the value half of this pair is NOT enforced
        # here: parse_session_list needs harness-specific input to call, and
        # base.py's contract (a concrete override must never return None) is
        # therefore left to each harness's own tests. Residual gap, stated so it
        # is not mistaken for enforced.
        cls = type(harness)
        overrides = (
            cls.session_enumerate is not Harness.session_enumerate,
            cls.parse_session_list is not Harness.parse_session_list,
        )
        assert overrides[0] == overrides[1], (
            f"{type(harness).__name__}: session_enumerate/parse_session_list "
            f"must be overridden together or not at all, got {overrides!r}"
        )

    def test_launch_only_broken_harness_fails_the_quartet_invariant(self):
        with pytest.raises(AssertionError):
            self._assert_launch_quartet(_LaunchOnlyBrokenHarness())

    def test_modality_outside_vocabulary_fails_the_membership_assertion(self):
        with pytest.raises(AssertionError):
            self._assert_launch_quartet(_BadModalityHarness())

    def test_no_scrub_list_harness_fails_the_quartet_invariant(self):
        """Override detection alone would pass this one — the value half is what
        catches it, and the credential scrub is what's at stake."""
        with pytest.raises(AssertionError):
            self._assert_launch_quartet(_NoScrubListHarness())

    def test_no_launch_env_set_harness_fails_the_quartet_invariant(self):
        """Override detection alone would pass this one too — what catches it is
        the value half, and an inherited account is what's at stake."""
        with pytest.raises(AssertionError):
            self._assert_launch_quartet(_NoLaunchEnvSetHarness())

    def test_enumerate_only_broken_harness_fails_the_pair_invariant(self):
        with pytest.raises(AssertionError):
            self._assert_enumeration_pair(_EnumerateOnlyBrokenHarness())

    def test_bare_harness_with_neither_concept_passes_both_invariants(self):
        self._assert_launch_quartet(_BareHarness())
        self._assert_enumeration_pair(_BareHarness())

    def test_every_registered_harness_satisfies_both_invariants(self, tmp_path):
        assert len(_HARNESSES) >= 1
        for cls in _HARNESSES.values():
            harness = cls()
            self._assert_launch_quartet(harness, env={"HOME": str(tmp_path)})
            self._assert_enumeration_pair(harness)


class TestSessionRecord:
    def test_is_frozen(self, tmp_path):
        rec = SessionRecord(
            session_id="sess-1",
            cwd=tmp_path,
            kind="tmux",
            controllable=True,
            name=None,
            pid=None,
            started_at=None,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            rec.session_id = "sess-2"

    def test_accepts_none_for_optional_fields(self, tmp_path):
        rec = SessionRecord(
            session_id="sess-1",
            cwd=tmp_path,
            kind="tmux",
            controllable=True,
            name=None,
            pid=None,
            started_at=None,
        )
        assert rec.name is None
        assert rec.pid is None
        assert rec.started_at is None

    def test_requires_the_four_non_optional_fields(self):
        with pytest.raises(TypeError):
            SessionRecord()


class TestClaudeCodeSessionEnumerate:
    """``session_enumerate`` returns the raw ``claude agents --json`` argv; the
    seam OWNS the argv the same way ``session_resume`` does."""

    def test_returns_bare_argv_with_no_workspace(self):
        assert ClaudeCodeHarness().session_enumerate() == ["claude", "agents", "--json"]

    def test_appends_cwd_flag_with_workspace(self, tmp_path):
        assert ClaudeCodeHarness().session_enumerate(tmp_path) == [
            "claude",
            "agents",
            "--json",
            "--cwd",
            str(tmp_path),
        ]

    def test_rejects_a_flag_shaped_workspace(self):
        """A workspace beginning with '-' occupies --cwd's value slot and reads
        as a flag: enumeration silently unscopes, or the CLI parses it as a real
        flag. Guard it the way session_launch guards its session_id."""
        for bad in ("--dangerously-skip-permissions", "-x", "--cwd"):
            with pytest.raises(HarnessError):
                ClaudeCodeHarness().session_enumerate(Path(bad))

    def test_no_filesystem_validation_of_workspace(self, tmp_path):
        """The guard above is argv safety, NOT existence checking — a missing
        workspace still yields argv, matching session_launch."""
        missing = tmp_path / "does-not-exist"
        assert ClaudeCodeHarness().session_enumerate(missing)[-1] == str(missing)


class TestClaudeCodeParseSessionListRoundTrip:
    """A captured real ``claude agents --json`` payload (live 2026-08-14 shape)
    parses into fully-typed SessionRecords."""

    def test_round_trip_parses_every_field(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)

        payload = json.dumps(
            [
                {
                    "sessionId": "sess-1",
                    "cwd": str(link),
                    "kind": "interactive",
                    "name": "my session",
                    "pid": 4242,
                    "startedAt": 1755100800123,
                }
            ]
        )

        records = ClaudeCodeHarness().parse_session_list(payload)

        assert len(records) == 1
        rec = records[0]
        assert rec.session_id == "sess-1"
        assert rec.cwd == real.resolve()
        assert rec.kind == "interactive"
        assert rec.controllable is True
        assert rec.name == "my session"
        assert rec.pid == 4242
        assert rec.started_at.tzinfo is not None
        assert rec.started_at == datetime(2025, 8, 13, 16, 0, 0, 123000, tzinfo=timezone.utc)
        assert rec.started_at.microsecond == 123000


class TestClaudeCodeParseSessionListControllable:
    def test_interactive_is_controllable(self, tmp_path):
        payload = f'[{{"sessionId": "s1", "cwd": "{tmp_path}", "kind": "interactive"}}]'
        records = ClaudeCodeHarness().parse_session_list(payload)
        assert records[0].controllable is True

    def test_background_is_not_controllable(self, tmp_path):
        payload = f'[{{"sessionId": "s1", "cwd": "{tmp_path}", "kind": "background"}}]'
        records = ClaudeCodeHarness().parse_session_list(payload)
        assert records[0].controllable is False

    def test_unknown_kind_is_kept_with_controllable_false(self, tmp_path):
        payload = f'[{{"sessionId": "s1", "cwd": "{tmp_path}", "kind": "sdk"}}]'
        records = ClaudeCodeHarness().parse_session_list(payload)
        assert len(records) == 1
        assert records[0].kind == "sdk"
        assert records[0].controllable is False


@pytest.mark.real_home  # the error excerpt redacts the real home, so it must resolve it
class TestClaudeCodeParseSessionListFailures:
    def test_non_json_stdout_raises_with_offending_field_named(self):
        with pytest.raises(HarnessError, match="decode"):
            ClaudeCodeHarness().parse_session_list("not json at all")

    def test_empty_stdout_raises(self):
        with pytest.raises(HarnessError, match="decode"):
            ClaudeCodeHarness().parse_session_list("")

    def test_whitespace_only_stdout_raises(self):
        with pytest.raises(HarnessError, match="decode"):
            ClaudeCodeHarness().parse_session_list("   \n\t  ")

    def test_json_object_instead_of_array_raises(self):
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().parse_session_list('{"sessionId": "s1"}')

    def test_non_dict_element_raises(self):
        with pytest.raises(HarnessError, match="object"):
            ClaudeCodeHarness().parse_session_list("[1, 2]")

    def test_record_missing_session_id_raises_naming_the_field(self, tmp_path):
        payload = f'[{{"cwd": "{tmp_path}", "kind": "interactive"}}]'
        with pytest.raises(HarnessError, match="sessionId"):
            ClaudeCodeHarness().parse_session_list(payload)

    def test_record_with_invalid_session_id_raises(self, tmp_path):
        payload = f'[{{"sessionId": "../evil", "cwd": "{tmp_path}", "kind": "interactive"}}]'
        with pytest.raises(HarnessError, match="sessionId"):
            ClaudeCodeHarness().parse_session_list(payload)

    def test_record_missing_cwd_raises_naming_the_field(self):
        payload = '[{"sessionId": "s1", "kind": "interactive"}]'
        with pytest.raises(HarnessError, match="cwd"):
            ClaudeCodeHarness().parse_session_list(payload)

    def test_record_missing_kind_raises_naming_the_field(self, tmp_path):
        payload = f'[{{"sessionId": "s1", "cwd": "{tmp_path}"}}]'
        with pytest.raises(HarnessError, match="kind"):
            ClaudeCodeHarness().parse_session_list(payload)

    def _assert_started_at_degrades_keeping_the_record(self, payload):
        """``startedAt`` is OPTIONAL: an unusable value maps to None and the
        record SURVIVES. Asserting the record is present is the point — raising
        here would discard every well-formed session in the same payload over
        one unreadable timestamp, turning anticipated schema drift into a total
        enumeration outage."""
        records = ClaudeCodeHarness().parse_session_list(payload)
        assert len(records) == 1
        assert records[0].session_id == "s1"
        assert records[0].started_at is None

    def test_started_at_epoch_micros_degrades_to_none(self, tmp_path):
        """A CLI that switched millis -> MICROS is the drift the spec anticipates."""
        self._assert_started_at_degrades_keeping_the_record(
            json.dumps(
                [
                    {
                        "sessionId": "s1",
                        "cwd": str(tmp_path),
                        "kind": "interactive",
                        "startedAt": 1755100800123000,
                    }
                ]
            )
        )

    def test_started_at_epoch_nanos_degrades_to_none(self, tmp_path):
        self._assert_started_at_degrades_keeping_the_record(
            json.dumps(
                [
                    {
                        "sessionId": "s1",
                        "cwd": str(tmp_path),
                        "kind": "interactive",
                        "startedAt": 1755100800123000000,
                    }
                ]
            )
        )

    def test_started_at_infinity_degrades_to_none(self, tmp_path):
        """``json.loads`` accepts a bare ``Infinity``, so it reaches the parser."""
        self._assert_started_at_degrades_keeping_the_record(
            '[{"sessionId": "s1", "cwd": "%s", "kind": "interactive", '
            '"startedAt": Infinity}]' % tmp_path
        )

    def test_started_at_nan_degrades_to_none(self, tmp_path):
        self._assert_started_at_degrades_keeping_the_record(
            '[{"sessionId": "s1", "cwd": "%s", "kind": "interactive", '
            '"startedAt": NaN}]' % tmp_path
        )

    def test_one_unreadable_started_at_does_not_lose_its_sibling_records(self, tmp_path):
        """The regression this degrade path exists to prevent: a single bad
        timestamp must not take the whole listing down with it."""
        payload = json.dumps(
            [
                {
                    "sessionId": "s1",
                    "cwd": str(tmp_path),
                    "kind": "interactive",
                    "startedAt": 1755100800123000000,
                },
                {
                    "sessionId": "s2",
                    "cwd": str(tmp_path),
                    "kind": "interactive",
                    "startedAt": 1755100800123,
                },
            ]
        )
        records = ClaudeCodeHarness().parse_session_list(payload)
        assert [r.session_id for r in records] == ["s1", "s2"]
        assert records[0].started_at is None
        assert records[1].started_at is not None


class TestClaudeCodeParseSessionListEmpty:
    def test_empty_array_yields_empty_list_not_none(self):
        result = ClaudeCodeHarness().parse_session_list("[]")
        assert result == []
        assert result is not None


class TestClaudeCodeParseSessionListOptionalFields:
    def test_absent_optional_fields_map_to_none(self, tmp_path):
        payload = f'[{{"sessionId": "s1", "cwd": "{tmp_path}", "kind": "interactive"}}]'
        rec = ClaudeCodeHarness().parse_session_list(payload)[0]
        assert rec.name is None
        assert rec.pid is None
        assert rec.started_at is None

    def test_null_optional_fields_map_to_none(self, tmp_path):
        payload = (
            f'[{{"sessionId": "s1", "cwd": "{tmp_path}", "kind": "interactive", '
            '"name": null, "pid": null, "startedAt": null}]'
        )
        rec = ClaudeCodeHarness().parse_session_list(payload)[0]
        assert rec.name is None
        assert rec.pid is None
        assert rec.started_at is None

    def test_wrongly_typed_optional_fields_map_to_none(self, tmp_path):
        payload = (
            f'[{{"sessionId": "s1", "cwd": "{tmp_path}", "kind": "interactive", '
            '"name": 5, "pid": "not-an-int", "startedAt": "not-a-number"}]'
        )
        rec = ClaudeCodeHarness().parse_session_list(payload)[0]
        assert rec.name is None
        assert rec.pid is None
        assert rec.started_at is None


@pytest.mark.real_home  # the error excerpt redacts the real home, so it must resolve it
class TestClaudeCodeParseSessionListErrorExcerpt:
    def test_error_message_is_bounded_and_truncates_path_bearing_fields(self, tmp_path):
        long_cwd = str(tmp_path / ("x" * 5000))
        payload = f'[{{"cwd": "{long_cwd}", "kind": "interactive"}}]'
        with pytest.raises(HarnessError) as exc_info:
            ClaudeCodeHarness().parse_session_list(payload)
        message = str(exc_info.value)
        # Against the ACTUAL limit, not merely against the payload: a
        # "< len(payload)" bound on a 5000-char payload would still pass if
        # _ERROR_EXCERPT_LIMIT crept up to 4000, which is exactly the
        # regression this test exists to catch.
        assert len(message) < _ERROR_EXCERPT_LIMIT + 200
        assert len(message) < len(payload)
        assert long_cwd not in message

    def test_home_path_is_redacted_not_merely_bounded(self):
        """The realistic leak is an ORDINARY path, which fits inside the bound
        intact: a benign `kind` drift raises, and the user pastes the message
        into a bug report carrying their username. Length bounding cannot catch
        that — redaction is what does."""
        home = str(Path.home())
        payload = f'[{{"sessionId": "s1", "cwd": "{home}/secretproject", "kind": null}}]'
        with pytest.raises(HarnessError) as exc_info:
            ClaudeCodeHarness().parse_session_list(payload)
        message = str(exc_info.value)
        assert len(message) < _ERROR_EXCERPT_LIMIT + 200  # bound alone would pass
        assert home not in message
        assert "~/secretproject" in message


class TestClaudeCodeParseSessionListDuplicateIds:
    def test_duplicate_session_ids_are_both_kept_in_output_order(self, tmp_path):
        payload = (
            f'[{{"sessionId": "dup", "cwd": "{tmp_path}", "kind": "interactive", "pid": 1}}, '
            f'{{"sessionId": "dup", "cwd": "{tmp_path}", "kind": "background", "pid": 2}}]'
        )
        records = ClaudeCodeHarness().parse_session_list(payload)
        assert len(records) == 2
        assert records[0].session_id == "dup"
        assert records[1].session_id == "dup"
        assert records[0].pid == 1
        assert records[1].pid == 2


class TestSessionTranscript:
    def test_is_frozen(self, tmp_path):
        row = SessionTranscript(
            session_id="sess-1", cwd=tmp_path, modified_at=datetime.now(timezone.utc)
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            row.session_id = "sess-2"

    def test_accepts_none_for_cwd(self):
        row = SessionTranscript(
            session_id="sess-1", cwd=None, modified_at=datetime.now(timezone.utc)
        )
        assert row.cwd is None

    def test_requires_the_three_fields(self):
        with pytest.raises(TypeError):
            SessionTranscript()


class TestSessionTranscriptsBaseDefault:
    """The store-enumeration seam is CONCRETE with a degrading default: a
    harness with no recovery concept answers None rather than raising."""

    def test_returns_none(self):
        assert _BareHarness().session_transcripts() is None

    def test_returns_none_with_workspace_and_env(self, tmp_path):
        env = {"TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude")}
        assert _BareHarness().session_transcripts(tmp_path, env=env) is None


class TestClaudeCodeSessionTranscripts:
    """Claude Code stores one JSONL transcript per session at
    <config_dir>/projects/<project-dir>/<session-id>.jsonl. ``session_transcripts``
    enumerates that layout at depth 2 ONLY — a recursive glob would also pick up
    the per-session subagent/tool-result transcripts nested underneath, which
    are not top-level sessions."""

    def _write(self, claude_dir, project_dirname, session_id, lines):
        d = claude_dir / "projects" / project_dirname
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{session_id}.jsonl"
        path.write_text("\n".join(lines) + "\n")
        return path

    def _env(self, claude_dir):
        return {"TRAILHEAD_CLAUDE_DIR": str(claude_dir)}

    def test_missing_projects_dir_returns_empty_list(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        assert ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir)) == []

    def test_global_call_reads_cwd_from_file_content_not_from_dirname(self, tmp_path):
        """The directory a transcript lives under is named by a LOSSY munge of
        the launch cwd ('/' and '.' both collapse to '-'); this seam must never
        try to reverse it. Prove it with a directory name that is the munge of
        a totally different path than the cwd actually recorded in the file."""
        claude_dir = tmp_path / ".claude"
        ws_a = tmp_path / "workspace-a"
        ws_a.mkdir()
        ws_b = tmp_path / "workspace-b"
        ws_b.mkdir()

        decoy_source = Path("/some/other/place")
        decoy_dirname = str(decoy_source).replace("/", "-").replace(".", "-")

        self._write(claude_dir, decoy_dirname, "sess-a", [json.dumps({"cwd": str(ws_a)})])
        self._write(claude_dir, "unrelated-project-dir", "sess-b", [json.dumps({"cwd": str(ws_b)})])

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))
        by_id = {r.session_id: r for r in rows}
        assert set(by_id) == {"sess-a", "sess-b"}
        assert by_id["sess-a"].cwd == ws_a.resolve()
        assert by_id["sess-b"].cwd == ws_b.resolve()
        assert by_id["sess-a"].cwd != decoy_source.resolve()

    def test_a_walk_that_fails_midway_yields_what_it_found_rather_than_raising(
        self, tmp_path, monkeypatch
    ):
        """The store walk is where an unreadable store actually bites.

        A project directory whose permissions deny a listing raises DURING the
        walk, after earlier entries have already come back — not at the call. The
        base contract says this seam never raises for a missing or unreadable
        store, so the rows found before that point are the answer; an exception
        escaping here surfaces to an operator as a stack trace from a command
        whose whole job is to answer in one line.
        """
        claude_dir = tmp_path / ".claude"
        readable = self._write(
            claude_dir, "some-project", "sess-a", [json.dumps({"cwd": str(tmp_path)})]
        )

        def _failing_walk(self, pattern):
            yield readable
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "glob", _failing_walk)

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))
        assert [r.session_id for r in rows] == ["sess-a"]

    def test_a_relative_recorded_cwd_is_dropped_rather_than_anchored_at_the_caller(
        self, tmp_path, monkeypatch
    ):
        """A recorded cwd that is not absolute names no directory camp can trust.

        Resolving it would anchor it at whatever directory the CLI happened to
        be invoked from, making both the reported location and the resumed
        launch root a function of the caller's cwd. Reporting no root is true;
        reporting one that moves with the caller is not.
        """
        claude_dir = tmp_path / ".claude"
        self._write(claude_dir, "some-project", "sess-rel", [json.dumps({"cwd": ".ssh"})])
        monkeypatch.chdir(tmp_path)

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))

        assert [r.session_id for r in rows] == ["sess-rel"]
        assert rows[0].cwd is None

    def test_nested_subagent_and_tool_result_transcripts_are_not_enumerated(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        proj = claude_dir / "projects" / "some-project"
        proj.mkdir(parents=True)
        self._write(claude_dir, "some-project", "uuid-1", [json.dumps({"cwd": str(tmp_path)})])

        nested_sub = proj / "uuid-1" / "subagents" / "x.jsonl"
        nested_sub.parent.mkdir(parents=True)
        nested_sub.write_text(json.dumps({"cwd": str(tmp_path)}) + "\n")

        nested_tool = proj / "uuid-1" / "tool-results" / "y.jsonl"
        nested_tool.parent.mkdir(parents=True)
        nested_tool.write_text(json.dumps({"cwd": str(tmp_path)}) + "\n")

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))
        assert {r.session_id for r in rows} == {"uuid-1"}

    def test_skips_invalid_stem_non_jsonl_file_and_bare_directory(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        proj = claude_dir / "projects" / "some-project"
        proj.mkdir(parents=True)

        self._write(claude_dir, "some-project", "sess-real", [json.dumps({"cwd": str(tmp_path)})])

        # stem fails the session-id guard: a leading '.' is not a valid first char.
        (proj / "..jsonl").write_text(json.dumps({"cwd": str(tmp_path)}) + "\n")

        # not a .jsonl file at all
        (proj / "notes.txt").write_text("hello")

        # a directory, not a file, despite the .jsonl-shaped name
        (proj / "looks-like-a-file.jsonl").mkdir()

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))
        assert {r.session_id for r in rows} == {"sess-real"}

    def test_file_directly_under_projects_root_is_not_enumerated(self, tmp_path):
        """Depth 2 only: a file sitting directly in the projects root, not
        nested under a per-project directory, is not a transcript this seam
        reports — matching how Claude Code itself keeps top-level, non-session
        files (for example a shared 'memory.jsonl') outside any project dir."""
        claude_dir = tmp_path / ".claude"
        (claude_dir / "projects").mkdir(parents=True)
        (claude_dir / "projects" / "memory.jsonl").write_text(
            json.dumps({"cwd": str(tmp_path)}) + "\n"
        )
        assert ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir)) == []

    def test_no_cwd_in_first_12_lines_yields_a_row_with_cwd_none(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        lines = [json.dumps({"type": "system", "n": i}) for i in range(12)]
        self._write(claude_dir, "proj", "sess-nocwd", lines)

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))
        assert len(rows) == 1
        assert rows[0].session_id == "sess-nocwd"
        assert rows[0].cwd is None

    def test_cwd_found_despite_an_oversized_earlier_line(self, tmp_path):
        """A line over the byte cap is skipped WITHOUT being decoded — proven by
        embedding a decoy cwd inside the oversized line: if it were decoded, its
        cwd would win (first match wins) instead of the real one on the next
        line."""
        claude_dir = tmp_path / ".claude"
        ws = tmp_path / "real-workspace"
        ws.mkdir()
        huge_line = json.dumps({"cwd": "/decoy/should/not/be/used", "junk": "x" * 2_000_000})
        real_line = json.dumps({"cwd": str(ws)})
        self._write(claude_dir, "proj", "sess-huge", [huge_line, real_line])

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))
        assert rows[0].cwd == ws.resolve()

    def test_no_single_read_exceeds_the_byte_cap(self, tmp_path, monkeypatch):
        """The byte cap bounds the READ, not merely the decode.

        Checking a line's length after reading it is not a bound at all: the
        whole record is already in memory by then, so one corrupt transcript
        with no newline in it would cost its full size. Transcripts reach
        hundreds of megabytes, so this records the largest single read and
        holds it to the cap.
        """
        from trailhead.harness import claude_code as cc

        claude_dir = tmp_path / ".claude"
        ws = tmp_path / "ws-after-a-corrupt-record"
        ws.mkdir()
        # One 8MB record carrying no newline at all, then a plain cwd line.
        no_newline_blob = "x" * 8_000_000
        self._write(claude_dir, "proj", "sess-corrupt", [no_newline_blob, json.dumps({"cwd": str(ws)})])

        reads: list[int] = []
        real_open = Path.open

        def recording_open(self, *args, **kwargs):
            handle = real_open(self, *args, **kwargs)
            real_readline = handle.readline

            def readline(*a, **k):
                chunk = real_readline(*a, **k)
                reads.append(len(chunk))
                return chunk

            handle.readline = readline
            return handle

        monkeypatch.setattr(Path, "open", recording_open)
        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))

        assert reads, "the scan never read anything"
        assert max(reads) <= cc._CWD_SCAN_MAX_LINE_BYTES + 1, (
            f"a single read took {max(reads)} bytes, over the "
            f"{cc._CWD_SCAN_MAX_LINE_BYTES} cap — the cap is not bounding the read"
        )
        # Stepping over the corrupt record must not cost the line after it.
        assert rows[0].cwd == ws.resolve()

    def test_cwd_past_the_scan_bound_is_not_found(self, tmp_path):
        """Pins the 12-line bound as CONTRACT: a cwd sitting on line 13 must not
        be found, even though nothing about it is malformed."""
        claude_dir = tmp_path / ".claude"
        ws = tmp_path / "late-workspace"
        ws.mkdir()
        filler = [json.dumps({"type": "system", "n": i}) for i in range(12)]
        sentinel = json.dumps({"cwd": str(ws)})
        self._write(claude_dir, "proj", "sess-late", filler + [sentinel])

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))
        assert rows[0].cwd is None

    def test_invalid_json_on_every_line_yields_cwd_none_no_raise(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        self._write(claude_dir, "proj", "sess-badjson", ["not json {{{", "also not json", ""])

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))
        assert len(rows) == 1
        assert rows[0].cwd is None

    def test_workspace_scoping_is_a_subtree_test_and_excludes_a_sibling(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        root = tmp_path / "root"
        inside = root / "sub"
        inside.mkdir(parents=True)
        sibling = tmp_path / "sibling"
        sibling.mkdir()

        self._write(claude_dir, "proj-a", "sess-inside", [json.dumps({"cwd": str(inside)})])
        self._write(claude_dir, "proj-a", "sess-at-root", [json.dumps({"cwd": str(root)})])
        self._write(claude_dir, "proj-b", "sess-sibling", [json.dumps({"cwd": str(sibling)})])

        rows = ClaudeCodeHarness().session_transcripts(root, env=self._env(claude_dir))
        assert {r.session_id for r in rows} == {"sess-inside", "sess-at-root"}

    def test_a_row_with_no_cwd_is_excluded_from_a_scoped_call(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        root = tmp_path / "root"
        root.mkdir()
        self._write(claude_dir, "proj", "sess-nocwd", [json.dumps({"n": 1})])

        rows = ClaudeCodeHarness().session_transcripts(root, env=self._env(claude_dir))
        assert rows == []

    def test_a_row_with_no_cwd_is_included_in_an_unscoped_call(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        self._write(claude_dir, "proj", "sess-nocwd", [json.dumps({"n": 1})])

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))
        assert {r.session_id for r in rows} == {"sess-nocwd"}

    def test_symlinked_workspace_scope_resolves_the_same_as_the_real_path(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        real_root = tmp_path / "real-root"
        real_root.mkdir()
        link_root = tmp_path / "link-root"
        link_root.symlink_to(real_root)

        self._write(claude_dir, "proj", "sess-under", [json.dumps({"cwd": str(real_root)})])

        via_real = ClaudeCodeHarness().session_transcripts(real_root, env=self._env(claude_dir))
        via_link = ClaudeCodeHarness().session_transcripts(link_root, env=self._env(claude_dir))
        assert {r.session_id for r in via_real} == {"sess-under"}
        assert {r.session_id for r in via_link} == {"sess-under"}

    def test_modified_at_is_tz_aware_utc_from_file_mtime(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        path = self._write(
            claude_dir, "proj", "sess-mtime", [json.dumps({"cwd": str(tmp_path)})]
        )
        expected = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        os.utime(path, (expected.timestamp(), expected.timestamp()))

        rows = ClaudeCodeHarness().session_transcripts(env=self._env(claude_dir))
        assert rows[0].modified_at.tzinfo is not None
        assert rows[0].modified_at == expected
