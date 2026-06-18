"""Tests for Slice 6: harness-launch seam (config-shaped, claude default).

A group-level optional [harness] block declares `new` + `resume` argv templates
+ `cwd`, with {slug} / {workspace} / {session_id} substitution. When absent the
baked-in claude default applies (new→["claude","--session-id","{session_id}"]
cwd=workspace; resume→["claude","--resume","{session_id}"]). The launch seam os.execvp's;
tests stub the exec (no real claude).

Test contract (harness portion):
- no [harness] config → claude default argv (new seeds the deterministic session
  id with --session-id, resume continues it with --resume; cwd=workspace).
- custom [harness] block → configured argv with {slug}/{workspace}/{session_id}
  substituted.
- unknown {placeholder} or empty token → legible GroupConfigError.
- launch() resolves then os.execvp's the resolved argv + chdir to cwd (stubbed).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _group(harness=None):
    g = {"group": {"name": "g"}, "members": [{"name": "r", "repo_root": "/tmp/r"}]}
    if harness is not None:
        g["harness"] = harness
    return g


def _launch(group, slug, ws, *, is_resume):
    """Resolve the profile and substitute → (argv, cwd), as the camp ai tail does."""
    from harness_launch import resolve_harness_profile
    from session_identity import session_id_for

    return resolve_harness_profile(group).launch(
        slug=slug,
        workspace=str(ws),
        is_resume=is_resume,
        session_id=session_id_for(group["group"]["name"], slug),
    )


# ---------------------------------------------------------------------------
# profile.launch — claude default (no [harness] config)
# ---------------------------------------------------------------------------


class TestClaudeDefault:
    def test_default_new_argv_seeds_session_id(self):
        from session_identity import session_id_for

        ws = Path("/work/space")
        sid = session_id_for("g", "feat-x")
        argv, cwd = _launch(_group(), "feat-x", ws, is_resume=False)
        assert argv == ["claude", "--session-id", sid]
        assert cwd == ws

    def test_default_resume_argv_resumes_session_id(self):
        from session_identity import session_id_for

        ws = Path("/work/space")
        sid = session_id_for("g", "feat-x")
        argv, cwd = _launch(_group(), "feat-x", ws, is_resume=True)
        assert argv == ["claude", "--resume", sid]
        assert cwd == ws


# ---------------------------------------------------------------------------
# profile.launch — custom [harness] block
# ---------------------------------------------------------------------------


class TestCustomHarness:
    def test_custom_new_argv_substitutes_workspace(self):
        ws = Path("/work/space")
        # Configured via the parsed-config shape (post load_group).
        group = _group(
            {
                "new": ["myharness", "--root", "{workspace}"],
                "resume": ["myharness", "--session", "{slug}"],
                "cwd": "{workspace}",
            }
        )
        argv, cwd = _launch(group, "feat-x", ws, is_resume=False)
        assert argv == ["myharness", "--root", "/work/space"]
        assert cwd == ws

    def test_custom_resume_argv_substitutes_slug(self):
        ws = Path("/work/space")
        group = _group(
            {
                "new": ["myharness", "--root", "{workspace}"],
                "resume": ["myharness", "--session", "{slug}"],
                "cwd": "{workspace}",
            }
        )
        argv, cwd = _launch(group, "feat-x", ws, is_resume=True)
        assert argv == ["myharness", "--session", "feat-x"]

    def test_custom_cwd_substitution(self):
        ws = Path("/work/space")
        group = _group(
            {
                "new": ["h"],
                "resume": ["h"],
                "cwd": "{workspace}/sub",
            }
        )
        _, cwd = _launch(group, "feat-x", ws, is_resume=False)
        assert cwd == Path("/work/space/sub")


# ---------------------------------------------------------------------------
# group_config parses + validates the [harness] block
# ---------------------------------------------------------------------------


class TestHarnessConfigValidation:
    def _load(self, tmp_path, body):
        from group_config import load_group

        f = tmp_path / "g.toml"
        f.write_text(
            "[group]\nname='g'\n"
            "[[members]]\nname='r'\nrepo_root='/tmp/r'\n" + body
        )
        return load_group(f)

    def test_valid_harness_block_parses(self, tmp_path):
        cfg = self._load(
            tmp_path,
            "[harness]\n"
            'new = ["claude"]\n'
            'resume = ["claude", "-r", "{slug}"]\n'
            'cwd = "{workspace}"\n',
        )
        assert cfg["harness"]["new"] == ["claude"]
        assert cfg["harness"]["resume"] == ["claude", "-r", "{slug}"]
        assert cfg["harness"]["cwd"] == "{workspace}"

    def test_no_harness_block_absent_from_config(self, tmp_path):
        cfg = self._load(tmp_path, "")
        assert "harness" not in cfg or cfg.get("harness") is None

    def test_harness_new_must_be_list(self, tmp_path):
        from group_config import GroupConfigError

        with pytest.raises(GroupConfigError):
            self._load(tmp_path, "[harness]\nnew = \"claude\"\nresume = [\"claude\"]\n")

    def test_harness_empty_token_rejected(self, tmp_path):
        from group_config import GroupConfigError

        with pytest.raises(GroupConfigError) as exc:
            self._load(
                tmp_path,
                "[harness]\nnew = [\"claude\", \"  \"]\nresume = [\"claude\"]\n",
            )
        assert "empty" in str(exc.value).lower() or "whitespace" in str(exc.value).lower()

    def test_harness_non_string_token_rejected(self, tmp_path):
        from group_config import GroupConfigError

        with pytest.raises(GroupConfigError):
            self._load(tmp_path, "[harness]\nnew = [\"claude\", 3]\nresume = [\"claude\"]\n")

    def test_harness_unknown_placeholder_rejected(self, tmp_path):
        from group_config import GroupConfigError

        with pytest.raises(GroupConfigError) as exc:
            self._load(
                tmp_path,
                "[harness]\nnew = [\"claude\", \"{bogus}\"]\nresume = [\"claude\"]\n",
            )
        assert "bogus" in str(exc.value)

    def test_harness_cwd_unknown_placeholder_rejected(self, tmp_path):
        from group_config import GroupConfigError

        with pytest.raises(GroupConfigError) as exc:
            self._load(
                tmp_path,
                "[harness]\nnew = [\"claude\"]\nresume = [\"claude\"]\ncwd = \"{nope}\"\n",
            )
        assert "nope" in str(exc.value)

    def test_harness_inject_stdout_parses(self, tmp_path):
        cfg = self._load(tmp_path, "[harness]\ninject = \"stdout\"\n")
        assert cfg["harness"]["inject"] == "stdout"

    def test_harness_inject_claude_hook_parses(self, tmp_path):
        cfg = self._load(tmp_path, "[harness]\ninject = \"claude-hook\"\n")
        assert cfg["harness"]["inject"] == "claude-hook"

    def test_harness_inject_absent_not_in_parsed(self, tmp_path):
        cfg = self._load(tmp_path, "[harness]\ndoc_files = [\"AGENTS.md\"]\n")
        assert "inject" not in cfg["harness"]

    def test_harness_inject_unknown_value_rejected(self, tmp_path):
        from group_config import GroupConfigError

        with pytest.raises(GroupConfigError) as exc:
            self._load(tmp_path, "[harness]\ninject = \"telepathy\"\n")
        assert "telepathy" in str(exc.value)

    def test_harness_inject_non_string_rejected(self, tmp_path):
        from group_config import GroupConfigError

        with pytest.raises(GroupConfigError):
            self._load(tmp_path, "[harness]\ninject = 42\n")

    def test_harness_pretrust_true_parses(self, tmp_path):
        cfg = self._load(tmp_path, "[harness]\npretrust = true\n")
        assert cfg["harness"]["pretrust"] is True

    def test_harness_pretrust_false_parses(self, tmp_path):
        cfg = self._load(tmp_path, "[harness]\npretrust = false\n")
        assert cfg["harness"]["pretrust"] is False

    def test_harness_pretrust_absent_not_in_parsed(self, tmp_path):
        cfg = self._load(tmp_path, "[harness]\ndoc_files = [\"AGENTS.md\"]\n")
        assert "pretrust" not in cfg["harness"]

    def test_harness_pretrust_non_bool_rejected(self, tmp_path):
        from group_config import GroupConfigError

        with pytest.raises(GroupConfigError) as exc:
            self._load(tmp_path, "[harness]\npretrust = \"yes\"\n")
        assert "pretrust" in str(exc.value)


# ---------------------------------------------------------------------------
# launch() — resolves then os.execvp (stubbed)
# ---------------------------------------------------------------------------


class TestLaunch:
    def test_launch_execs_resolved_argv_and_chdir(self, monkeypatch, tmp_path):
        import harness_launch

        ws = tmp_path / "ws"
        ws.mkdir()

        execd = {}
        chdir_to = {}
        monkeypatch.setattr(harness_launch.os, "execvp",
                            lambda file, args: execd.setdefault("call", (file, args)))
        monkeypatch.setattr(harness_launch.os, "chdir",
                            lambda d: chdir_to.setdefault("dir", d))

        harness_launch.launch(_group(), "feat-x", ws, is_resume=True)

        from session_identity import session_id_for
        sid = session_id_for("g", "feat-x")
        assert execd["call"] == ("claude", ["claude", "--resume", sid])
        assert chdir_to["dir"] == str(ws)


# ---------------------------------------------------------------------------
# profile.launch — per-field merge over claude default (Fix 1)
# ---------------------------------------------------------------------------


class TestPartialHarnessMerge:
    """[harness] blocks that omit some fields must fall back per-field to claude defaults."""

    def test_doc_files_only_block_uses_default_new(self):
        """A [harness] block with only doc_files → new falls back to the claude default."""
        from session_identity import session_id_for

        ws = Path("/work/space")
        sid = session_id_for("g", "feat-x")
        # Simulate what load_group returns for [harness]\ndoc_files = ["AGENTS.md"]
        group = _group({"doc_files": ["AGENTS.md"]})
        argv, cwd = _launch(group, "feat-x", ws, is_resume=False)
        assert argv == ["claude", "--session-id", sid]
        assert cwd == ws

    def test_doc_files_only_block_uses_default_resume(self):
        """A [harness] block with only doc_files → resume falls back to claude default."""
        from session_identity import session_id_for

        ws = Path("/work/space")
        sid = session_id_for("g", "feat-x")
        group = _group({"doc_files": ["AGENTS.md"]})
        argv, cwd = _launch(group, "feat-x", ws, is_resume=True)
        assert argv == ["claude", "--resume", sid]
        assert cwd == ws

    def test_cwd_only_block_uses_default_new(self):
        """A [harness] block with only cwd → new falls back to claude default."""
        from session_identity import session_id_for

        ws = Path("/work/space")
        sid = session_id_for("g", "feat-x")
        group = _group({"cwd": "{workspace}/sub"})
        argv, cwd = _launch(group, "feat-x", ws, is_resume=False)
        assert argv == ["claude", "--session-id", sid]
        assert cwd == Path("/work/space/sub")

    def test_new_only_block_resume_falls_back_to_default(self):
        """A [harness] block with only new → resume falls back to claude default."""
        from session_identity import session_id_for

        ws = Path("/work/space")
        sid = session_id_for("g", "feat-x")
        group = _group({"new": ["myharness"]})
        argv, cwd = _launch(group, "feat-x", ws, is_resume=True)
        assert argv == ["claude", "--resume", sid]
        assert cwd == ws

    def test_new_only_block_uses_configured_new(self):
        """A [harness] block with only new → new uses the configured argv."""
        ws = Path("/work/space")
        group = _group({"new": ["myharness"]})
        argv, _ = _launch(group, "feat-x", ws, is_resume=False)
        assert argv == ["myharness"]


# ---------------------------------------------------------------------------
# profile.inject — mid-session context-injection strategy (Slice 9)
# ---------------------------------------------------------------------------


class TestResolveInject:
    """resolve_harness_profile(...).inject per-field merge.

    - no [harness] block (claude default) → "claude-hook".
    - [harness] block WITHOUT inject → "stdout" (safe default).
    - configured inject value honored when present.
    """

    def test_no_harness_block_defaults_to_claude_hook(self):
        from harness_launch import resolve_harness_profile

        assert resolve_harness_profile(_group()).inject == "claude-hook"

    def test_harness_block_without_inject_defaults_to_stdout(self):
        from harness_launch import resolve_harness_profile

        group = _group({"doc_files": ["AGENTS.md"]})
        assert resolve_harness_profile(group).inject == "stdout"

    def test_configured_stdout_honored(self):
        from harness_launch import resolve_harness_profile

        group = _group({"inject": "stdout"})
        assert resolve_harness_profile(group).inject == "stdout"

    def test_configured_claude_hook_honored(self):
        from harness_launch import resolve_harness_profile

        group = _group({"inject": "claude-hook"})
        assert resolve_harness_profile(group).inject == "claude-hook"


# ---------------------------------------------------------------------------
# resolve_harness_profile — unified resolved profile
# ---------------------------------------------------------------------------


class TestResolveHarnessProfile:
    """resolve_harness_profile merges config over the claude default ONCE.

    Callers read launch argv / cwd / doc_files / inject off the single resolved
    profile. Behavior includes the intentional inject asymmetry.
    """

    def test_no_harness_block_is_all_claude_defaults(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group())
        assert p.new == ["claude", "--session-id", "{session_id}"]
        assert p.resume == ["claude", "--resume", "{session_id}"]
        assert p.cwd == "{workspace}"
        assert p.doc_files == ["CLAUDE.md"]
        assert p.inject == "claude-hook"

    def test_block_without_inject_defaults_to_stdout(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group({"doc_files": ["AGENTS.md"]}))
        assert p.doc_files == ["AGENTS.md"]
        assert p.inject == "stdout"
        # other fields still fall back per-field
        assert p.new == ["claude", "--session-id", "{session_id}"]
        assert p.resume == ["claude", "--resume", "{session_id}"]

    def test_configured_fields_honored(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(
            _group({"new": ["myh"], "inject": "claude-hook"})
        )
        assert p.new == ["myh"]
        assert p.inject == "claude-hook"
        assert p.resume == ["claude", "--resume", "{session_id}"]

    def test_profile_is_frozen(self):
        import dataclasses

        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group())
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.inject = "stdout"  # type: ignore[misc]

    def test_launch_substitutes_new(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group())
        argv, cwd = p.launch(
            slug="feat-x", workspace="/work/space", is_resume=False, session_id="sid-123"
        )
        assert argv == ["claude", "--session-id", "sid-123"]
        assert cwd == Path("/work/space")

    def test_launch_substitutes_resume(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group())
        argv, cwd = p.launch(
            slug="feat-x", workspace="/work/space", is_resume=True, session_id="sid-123"
        )
        assert argv == ["claude", "--resume", "sid-123"]
        assert cwd == Path("/work/space")

    def test_no_harness_block_pretrust_defaults_true(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group())
        assert p.pretrust is True

    def test_block_without_pretrust_defaults_true(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group({"doc_files": ["AGENTS.md"]}))
        assert p.pretrust is True

    def test_configured_pretrust_false_honored(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group({"pretrust": False}))
        assert p.pretrust is False


class TestHasResumableSession:
    """The profile answers new-vs-resume so the core need not branch on harness."""

    def test_claude_profile_consults_session_file(self, tmp_path):
        from harness_launch import resolve_harness_profile

        env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}
        p = resolve_harness_profile(_group())  # claude default
        assert p.has_resumable_session("sid-x", env=env) is False  # absent

        proj = tmp_path / "claude" / "projects" / "-enc"
        proj.mkdir(parents=True)
        (proj / "sid-x.jsonl").write_text("{}\n")
        assert p.has_resumable_session("sid-x", env=env) is True

    def test_non_claude_profile_returns_none(self, tmp_path):
        from harness_launch import resolve_harness_profile

        env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}
        p = resolve_harness_profile(_group({"new": ["myharness"]}))
        # None → caller falls back to its own signal (workspace-dir existence).
        assert p.has_resumable_session("sid-x", env=env) is None


# ---------------------------------------------------------------------------
# session_identity — deterministic resumable session id
# ---------------------------------------------------------------------------


class TestSessionIdentity:
    def test_deterministic_for_same_inputs(self):
        from session_identity import session_id_for

        assert session_id_for("g", "feat-x") == session_id_for("g", "feat-x")

    def test_distinct_across_slug_and_group(self):
        from session_identity import session_id_for

        assert session_id_for("g", "feat-x") != session_id_for("g", "feat-y")
        assert session_id_for("g", "feat-x") != session_id_for("h", "feat-x")

    def test_is_a_uuid_string(self):
        import uuid

        from session_identity import session_id_for

        sid = session_id_for("g", "feat-x")
        assert str(uuid.UUID(sid)) == sid  # round-trips → valid UUID


# ---------------------------------------------------------------------------
# claude_session_exists — new-vs-resume signal (glob by id, encoding-independent)
# ---------------------------------------------------------------------------


class TestClaudeSessionExists:
    def test_false_when_projects_dir_absent(self, tmp_path):
        from harness_launch import claude_session_exists

        env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}  # nothing created
        assert claude_session_exists("any-id", env=env) is False

    def test_false_when_session_file_absent(self, tmp_path):
        from harness_launch import claude_session_exists

        (tmp_path / "claude" / "projects" / "proj").mkdir(parents=True)
        env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}
        assert claude_session_exists("missing-id", env=env) is False

    def test_true_when_session_file_present_any_project(self, tmp_path):
        from harness_launch import claude_session_exists

        # The id is globally unique, so a glob across projects finds it regardless
        # of which encoded-cwd project dir it lives under.
        proj = tmp_path / "claude" / "projects" / "-some-encoded-cwd"
        proj.mkdir(parents=True)
        (proj / "sid-abc.jsonl").write_text("{}\n")
        env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}
        assert claude_session_exists("sid-abc", env=env) is True
