"""The exported Claude global-config-*file* resolver.

`claude_config_file` is the single implementation camp's launch-time pretrust and
trailhead itself share. Its precedence is deliberately NOT `_claude_dir`'s: the
trailhead-only `TRAILHEAD_CLAUDE_DIR` seam relocates the config *directory* and must
never relocate the config *file*, or a test writes trust to a path Claude Code has
never heard of and passes green over a dead write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trailhead import harness
from trailhead.harness import claude_config_file
from trailhead.harness.claude_code import _claude_dir


class TestPrecedence:
    def test_claude_config_dir_relocates_the_file(self, tmp_path):
        cfg = tmp_path / "claude-levr"
        assert claude_config_file({"CLAUDE_CONFIG_DIR": str(cfg), "HOME": str(tmp_path)}) == (
            cfg / ".claude.json"
        )

    def test_without_an_override_the_file_is_beside_home(self, tmp_path):
        assert claude_config_file({"HOME": str(tmp_path)}) == tmp_path / ".claude.json"

    def test_userprofile_stands_in_for_home(self, tmp_path):
        assert claude_config_file({"USERPROFILE": str(tmp_path)}) == tmp_path / ".claude.json"

    @pytest.mark.real_home
    def test_empty_env_falls_back_to_the_real_home(self):
        assert claude_config_file({}) == Path.home() / ".claude.json"

    @pytest.mark.real_home
    def test_none_env_falls_back_to_the_real_home(self):
        assert claude_config_file(None) == Path.home() / ".claude.json"


class TestTheDirectoryFileSplit:
    """The single most important property: the trailhead seam moves the dir, not the file."""

    def test_trailhead_seam_alone_does_not_move_the_file(self, tmp_path):
        seam = tmp_path / "seam"
        env = {"TRAILHEAD_CLAUDE_DIR": str(seam), "HOME": str(tmp_path)}
        # Mutation guard: wiring TRAILHEAD_CLAUDE_DIR into the file resolver
        # (or delegating to _claude_dir) fails both assertions.
        assert claude_config_file(env) == tmp_path / ".claude.json"
        assert seam not in claude_config_file(env).parents

    def test_the_two_resolvers_disagree_on_purpose_when_both_are_set(self, tmp_path):
        seam = tmp_path / "seam"
        cfg = tmp_path / "claude-levr"
        env = {
            "TRAILHEAD_CLAUDE_DIR": str(seam),
            "CLAUDE_CONFIG_DIR": str(cfg),
            "HOME": str(tmp_path),
        }
        assert claude_config_file(env) == cfg / ".claude.json"
        assert _claude_dir(env) == seam
        assert claude_config_file(env).parent != _claude_dir(env)

    def test_the_seam_still_moves_the_directory(self, tmp_path):
        seam = tmp_path / "seam"
        assert _claude_dir({"TRAILHEAD_CLAUDE_DIR": str(seam), "HOME": str(tmp_path)}) == seam


class TestExport:
    def test_it_is_a_pinned_public_export(self):
        assert "claude_config_file" in harness.__all__
        assert harness.claude_config_file is claude_config_file
