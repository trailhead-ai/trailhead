"""Tests for the harness-profile seam (config-shaped, claude default).

A group-level optional [harness] block declares doc_files / inject / pretrust /
cwd, with {slug} / {workspace} substitution. When absent the baked-in claude
default applies. The launch/resume/session surface was removed — this
module is config only.

Test contract (profile portion):
- no [harness] config → claude defaults (binary "claude", cwd=workspace,
  doc_files=["CLAUDE.md"], inject="claude-hook", pretrust=True).
- a partial [harness] block merges per-field over the claude default.
- group_config parses + validates the [harness] block.
- should_pretrust()/is_claude_launch() scope the claude trust pre-seed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _group(harness=None):
    g = {"group": {"name": "g"}, "members": [{"name": "r", "repo_root": "/tmp/r"}]}
    if harness is not None:
        g["harness"] = harness
    return g


# ---------------------------------------------------------------------------
# group_config parses + validates the [harness] block
# ---------------------------------------------------------------------------


class TestHarnessConfigValidation:
    def _load(self, tmp_path, body):
        from camp.group.config import load_group

        f = tmp_path / "g.toml"
        f.write_text("[group]\nname='g'\n[[members]]\nname='r'\nrepo_root='/tmp/r'\n" + body)
        return load_group(f)

    def test_valid_harness_block_parses(self, tmp_path):
        cfg = self._load(
            tmp_path,
            "[harness]\n"
            'binary = "claude"\n'
            'cwd = "{workspace}"\n',
        )
        assert cfg["harness"]["binary"] == "claude"
        assert cfg["harness"]["cwd"] == "{workspace}"

    def test_no_harness_block_absent_from_config(self, tmp_path):
        cfg = self._load(tmp_path, "")
        assert "harness" not in cfg or cfg.get("harness") is None

    def test_harness_binary_must_be_string(self, tmp_path):
        from camp.group.config import GroupConfigError

        # A list is not accepted — binary is scalar.
        with pytest.raises(GroupConfigError):
            self._load(tmp_path, '[harness]\nbinary = ["claude"]\n')

    def test_harness_empty_binary_rejected(self, tmp_path):
        from camp.group.config import GroupConfigError

        with pytest.raises(GroupConfigError) as exc:
            self._load(tmp_path, '[harness]\nbinary = "  "\n')
        assert "non-empty" in str(exc.value).lower() or "empty" in str(exc.value).lower()

    def test_harness_cwd_unknown_placeholder_rejected(self, tmp_path):
        from camp.group.config import GroupConfigError

        with pytest.raises(GroupConfigError) as exc:
            self._load(
                tmp_path,
                '[harness]\ncwd = "{nope}"\n',
            )
        assert "nope" in str(exc.value)

    def test_harness_inject_stdout_parses(self, tmp_path):
        cfg = self._load(tmp_path, '[harness]\ninject = "stdout"\n')
        assert cfg["harness"]["inject"] == "stdout"

    def test_harness_inject_claude_hook_parses(self, tmp_path):
        cfg = self._load(tmp_path, '[harness]\ninject = "claude-hook"\n')
        assert cfg["harness"]["inject"] == "claude-hook"

    def test_harness_inject_absent_not_in_parsed(self, tmp_path):
        cfg = self._load(tmp_path, '[harness]\ndoc_files = ["AGENTS.md"]\n')
        assert "inject" not in cfg["harness"]

    def test_harness_inject_unknown_value_rejected(self, tmp_path):
        from camp.group.config import GroupConfigError

        with pytest.raises(GroupConfigError) as exc:
            self._load(tmp_path, '[harness]\ninject = "telepathy"\n')
        assert "telepathy" in str(exc.value)

    def test_harness_inject_non_string_rejected(self, tmp_path):
        from camp.group.config import GroupConfigError

        with pytest.raises(GroupConfigError):
            self._load(tmp_path, "[harness]\ninject = 42\n")

    def test_harness_pretrust_true_parses(self, tmp_path):
        cfg = self._load(tmp_path, "[harness]\npretrust = true\n")
        assert cfg["harness"]["pretrust"] is True

    def test_harness_pretrust_false_parses(self, tmp_path):
        cfg = self._load(tmp_path, "[harness]\npretrust = false\n")
        assert cfg["harness"]["pretrust"] is False

    def test_harness_pretrust_absent_not_in_parsed(self, tmp_path):
        cfg = self._load(tmp_path, '[harness]\ndoc_files = ["AGENTS.md"]\n')
        assert "pretrust" not in cfg["harness"]

    def test_harness_pretrust_non_bool_rejected(self, tmp_path):
        from camp.group.config import GroupConfigError

        with pytest.raises(GroupConfigError) as exc:
            self._load(tmp_path, '[harness]\npretrust = "yes"\n')
        assert "pretrust" in str(exc.value)


# ---------------------------------------------------------------------------
# profile.inject — mid-session context-injection strategy
# ---------------------------------------------------------------------------


class TestResolveInject:
    """resolve_harness_profile(...).inject per-field merge.

    - no [harness] block (claude default) → "claude-hook".
    - [harness] block WITHOUT inject → "stdout" (safe default).
    - configured inject value honored when present.
    """

    def test_no_harness_block_defaults_to_claude_hook(self):
        from camp.harness.profile import resolve_harness_profile

        assert resolve_harness_profile(_group()).inject == "claude-hook"

    def test_harness_block_without_inject_defaults_to_stdout(self):
        from camp.harness.profile import resolve_harness_profile

        group = _group({"doc_files": ["AGENTS.md"]})
        assert resolve_harness_profile(group).inject == "stdout"

    def test_configured_stdout_honored(self):
        from camp.harness.profile import resolve_harness_profile

        group = _group({"inject": "stdout"})
        assert resolve_harness_profile(group).inject == "stdout"

    def test_configured_claude_hook_honored(self):
        from camp.harness.profile import resolve_harness_profile

        group = _group({"inject": "claude-hook"})
        assert resolve_harness_profile(group).inject == "claude-hook"


# ---------------------------------------------------------------------------
# resolve_harness_profile — unified resolved profile
# ---------------------------------------------------------------------------


class TestResolveHarnessProfile:
    """resolve_harness_profile merges config over the claude default ONCE.

    Callers read doc_files / inject / pretrust / cwd off the single resolved
    profile. Behavior includes the intentional inject asymmetry. The retained
    `binary` field carries only the binary name that pretrust scoping reads.
    """

    def test_no_harness_block_is_all_claude_defaults(self):
        from camp.harness.profile import resolve_harness_profile

        p = resolve_harness_profile(_group())
        assert p.binary == "claude"
        assert p.cwd == "{workspace}"
        assert p.doc_files == ["CLAUDE.md"]
        assert p.inject == "claude-hook"

    def test_block_without_inject_defaults_to_stdout(self):
        from camp.harness.profile import resolve_harness_profile

        p = resolve_harness_profile(_group({"doc_files": ["AGENTS.md"]}))
        assert p.doc_files == ["AGENTS.md"]
        assert p.inject == "stdout"
        # other fields still fall back per-field
        assert p.binary == "claude"

    def test_configured_fields_honored(self):
        from camp.harness.profile import resolve_harness_profile

        p = resolve_harness_profile(_group({"binary": "myh", "inject": "claude-hook"}))
        assert p.binary == "myh"
        assert p.inject == "claude-hook"

    def test_profile_is_frozen(self):
        import dataclasses

        from camp.harness.profile import resolve_harness_profile

        p = resolve_harness_profile(_group())
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.inject = "stdout"  # type: ignore[misc]

    def test_resolved_cwd_default_is_workspace(self):
        from camp.harness.profile import resolve_harness_profile

        p = resolve_harness_profile(_group())
        assert p.resolved_cwd(slug="feat-x", workspace="/work/space") == Path("/work/space")

    def test_resolved_cwd_custom_substitutes_workspace(self):
        from camp.harness.profile import resolve_harness_profile

        p = resolve_harness_profile(_group({"cwd": "{workspace}/sub"}))
        assert p.resolved_cwd(slug="feat-x", workspace="/work/space") == Path("/work/space/sub")

    def test_no_harness_block_pretrust_defaults_true(self):
        from camp.harness.profile import resolve_harness_profile

        p = resolve_harness_profile(_group())
        assert p.pretrust is True

    def test_block_without_pretrust_defaults_true(self):
        from camp.harness.profile import resolve_harness_profile

        p = resolve_harness_profile(_group({"doc_files": ["AGENTS.md"]}))
        assert p.pretrust is True

    def test_configured_pretrust_false_honored(self):
        from camp.harness.profile import resolve_harness_profile

        p = resolve_harness_profile(_group({"pretrust": False}))
        assert p.pretrust is False


# ---------------------------------------------------------------------------
# should_pretrust / is_claude_launch — claude trust pre-seed scoping
# ---------------------------------------------------------------------------


class TestPretrustScoping:
    def test_default_claude_profile_pretrusts(self):
        from camp.harness.profile import resolve_harness_profile

        assert resolve_harness_profile(_group()).should_pretrust() is True

    def test_explicit_claude_block_pretrusts(self):
        from camp.harness.profile import resolve_harness_profile

        assert resolve_harness_profile(_group({"binary": "claude"})).should_pretrust() is True

    def test_non_claude_stdout_block_does_not_pretrust(self):
        from camp.harness.profile import resolve_harness_profile

        p = resolve_harness_profile(_group({"binary": "myharness", "inject": "stdout"}))
        assert p.is_claude_launch() is False
        assert p.should_pretrust() is False

    def test_non_claude_claude_hook_block_pretrusts(self):
        from camp.harness.profile import resolve_harness_profile

        # inject="claude-hook" is the declarative opt-in for a renamed claude binary.
        p = resolve_harness_profile(_group({"binary": "myharness", "inject": "claude-hook"}))
        assert p.should_pretrust() is True

    def test_pretrust_opt_out_suppresses_default(self):
        from camp.harness.profile import resolve_harness_profile

        assert resolve_harness_profile(_group({"pretrust": False})).should_pretrust() is False

    def test_empty_binary_is_not_claude_launch_no_raise(self):
        from camp.harness.profile import HarnessProfile

        p = HarnessProfile(
            binary="",
            cwd="{workspace}",
            doc_files=["CLAUDE.md"],
            inject="stdout",
            pretrust=True,
        )
        assert p.is_claude_launch() is False
        assert p.should_pretrust() is False
