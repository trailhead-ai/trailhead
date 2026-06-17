"""Tests for Slice 6: harness-launch seam (config-shaped, claude default).

A group-level optional [harness] block declares `new` + `resume` argv templates
+ `cwd`, with {slug} / {workspace} substitution. When absent the baked-in claude
default applies (new=["claude"] cwd=workspace; resume=["claude","-r","{slug}"]).
resolve_launch resolves (config | default) + is_resume → (argv, cwd). The launch
seam os.execvp's; tests stub the exec (no real claude).

Test contract (Slice 6, harness portion):
- no [harness] config → claude default argv (new→["claude"] cwd=workspace;
  resume→["claude","-r",<slug>]).
- custom [harness] block → configured argv with {slug}/{workspace} substituted.
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


# ---------------------------------------------------------------------------
# resolve_launch — claude default (no [harness] config)
# ---------------------------------------------------------------------------


class TestClaudeDefault:
    def test_default_new_argv_is_claude_cwd_workspace(self):
        from harness_launch import resolve_launch

        ws = Path("/work/space")
        argv, cwd = resolve_launch(_group(), "feat-x", ws, is_resume=False)
        assert argv == ["claude"]
        assert cwd == ws

    def test_default_resume_argv_is_claude_dash_r_slug(self):
        from harness_launch import resolve_launch

        ws = Path("/work/space")
        argv, cwd = resolve_launch(_group(), "feat-x", ws, is_resume=True)
        assert argv == ["claude", "-r", "feat-x"]
        assert cwd == ws


# ---------------------------------------------------------------------------
# resolve_launch — custom [harness] block
# ---------------------------------------------------------------------------


class TestCustomHarness:
    def test_custom_new_argv_substitutes_workspace(self):
        from harness_launch import resolve_launch
        from group_config import load_group

        ws = Path("/work/space")
        # Configured via the parsed-config shape (post load_group).
        group = _group(
            {
                "new": ["myharness", "--root", "{workspace}"],
                "resume": ["myharness", "--session", "{slug}"],
                "cwd": "{workspace}",
            }
        )
        argv, cwd = resolve_launch(group, "feat-x", ws, is_resume=False)
        assert argv == ["myharness", "--root", "/work/space"]
        assert cwd == ws

    def test_custom_resume_argv_substitutes_slug(self):
        from harness_launch import resolve_launch

        ws = Path("/work/space")
        group = _group(
            {
                "new": ["myharness", "--root", "{workspace}"],
                "resume": ["myharness", "--session", "{slug}"],
                "cwd": "{workspace}",
            }
        )
        argv, cwd = resolve_launch(group, "feat-x", ws, is_resume=True)
        assert argv == ["myharness", "--session", "feat-x"]

    def test_custom_cwd_substitution(self):
        from harness_launch import resolve_launch

        ws = Path("/work/space")
        group = _group(
            {
                "new": ["h"],
                "resume": ["h"],
                "cwd": "{workspace}/sub",
            }
        )
        _, cwd = resolve_launch(group, "feat-x", ws, is_resume=False)
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

        assert execd["call"] == ("claude", ["claude", "-r", "feat-x"])
        assert chdir_to["dir"] == str(ws)


# ---------------------------------------------------------------------------
# resolve_launch — per-field merge over claude default (Fix 1)
# ---------------------------------------------------------------------------


class TestPartialHarnessMerge:
    """[harness] blocks that omit some fields must fall back per-field to claude defaults."""

    def test_doc_files_only_block_uses_default_new(self):
        """A [harness] block with only doc_files → new falls back to ["claude"]."""
        from harness_launch import resolve_launch

        ws = Path("/work/space")
        # Simulate what load_group returns for [harness]\ndoc_files = ["AGENTS.md"]
        group = _group({"doc_files": ["AGENTS.md"]})
        argv, cwd = resolve_launch(group, "feat-x", ws, is_resume=False)
        assert argv == ["claude"]
        assert cwd == ws

    def test_doc_files_only_block_uses_default_resume(self):
        """A [harness] block with only doc_files → resume falls back to claude default."""
        from harness_launch import resolve_launch

        ws = Path("/work/space")
        group = _group({"doc_files": ["AGENTS.md"]})
        argv, cwd = resolve_launch(group, "feat-x", ws, is_resume=True)
        assert argv == ["claude", "-r", "feat-x"]
        assert cwd == ws

    def test_cwd_only_block_uses_default_new(self):
        """A [harness] block with only cwd → new falls back to claude default."""
        from harness_launch import resolve_launch

        ws = Path("/work/space")
        group = _group({"cwd": "{workspace}/sub"})
        argv, cwd = resolve_launch(group, "feat-x", ws, is_resume=False)
        assert argv == ["claude"]
        assert cwd == Path("/work/space/sub")

    def test_new_only_block_resume_falls_back_to_default(self):
        """A [harness] block with only new → resume falls back to claude default."""
        from harness_launch import resolve_launch

        ws = Path("/work/space")
        group = _group({"new": ["myharness"]})
        argv, cwd = resolve_launch(group, "feat-x", ws, is_resume=True)
        assert argv == ["claude", "-r", "feat-x"]
        assert cwd == ws

    def test_new_only_block_uses_configured_new(self):
        """A [harness] block with only new → new uses the configured argv."""
        from harness_launch import resolve_launch

        ws = Path("/work/space")
        group = _group({"new": ["myharness"]})
        argv, _ = resolve_launch(group, "feat-x", ws, is_resume=False)
        assert argv == ["myharness"]


# ---------------------------------------------------------------------------
# resolve_inject — mid-session context-injection strategy (Slice 9)
# ---------------------------------------------------------------------------


class TestResolveInject:
    """resolve_inject mirrors resolve_launch/resolve_doc_files per-field merge.

    - no [harness] block (claude default) → "claude-hook".
    - [harness] block WITHOUT inject → "stdout" (safe default).
    - configured inject value honored when present.
    """

    def test_no_harness_block_defaults_to_claude_hook(self):
        from harness_launch import resolve_inject

        assert resolve_inject(_group()) == "claude-hook"

    def test_harness_block_without_inject_defaults_to_stdout(self):
        from harness_launch import resolve_inject

        group = _group({"doc_files": ["AGENTS.md"]})
        assert resolve_inject(group) == "stdout"

    def test_configured_stdout_honored(self):
        from harness_launch import resolve_inject

        group = _group({"inject": "stdout"})
        assert resolve_inject(group) == "stdout"

    def test_configured_claude_hook_honored(self):
        from harness_launch import resolve_inject

        group = _group({"inject": "claude-hook"})
        assert resolve_inject(group) == "claude-hook"


# ---------------------------------------------------------------------------
# resolve_harness_profile — unified resolved profile (cleanup refactor)
# ---------------------------------------------------------------------------


class TestResolveHarnessProfile:
    """resolve_harness_profile merges config over the claude default ONCE.

    The three legacy resolvers (resolve_launch/resolve_doc_files/resolve_inject)
    become thin views over this single resolved profile. Behavior must match them
    field-for-field, including the intentional inject asymmetry.
    """

    def test_no_harness_block_is_all_claude_defaults(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group())
        assert p.new == ["claude"]
        assert p.resume == ["claude", "-r", "{slug}"]
        assert p.cwd == "{workspace}"
        assert p.doc_files == ["CLAUDE.md"]
        assert p.inject == "claude-hook"

    def test_block_without_inject_defaults_to_stdout(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group({"doc_files": ["AGENTS.md"]}))
        assert p.doc_files == ["AGENTS.md"]
        assert p.inject == "stdout"
        # other fields still fall back per-field
        assert p.new == ["claude"]
        assert p.resume == ["claude", "-r", "{slug}"]

    def test_configured_fields_honored(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(
            _group({"new": ["myh"], "inject": "claude-hook"})
        )
        assert p.new == ["myh"]
        assert p.inject == "claude-hook"
        assert p.resume == ["claude", "-r", "{slug}"]

    def test_profile_is_frozen(self):
        import dataclasses

        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group())
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.inject = "stdout"  # type: ignore[misc]

    def test_launch_substitutes_new(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group())
        argv, cwd = p.launch(slug="feat-x", workspace="/work/space", is_resume=False)
        assert argv == ["claude"]
        assert cwd == Path("/work/space")

    def test_launch_substitutes_resume(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(_group())
        argv, cwd = p.launch(slug="feat-x", workspace="/work/space", is_resume=True)
        assert argv == ["claude", "-r", "feat-x"]
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
