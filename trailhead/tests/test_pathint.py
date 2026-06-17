"""Tests for trailhead/pathint.py — shim dir + brew-style shellenv.

trailhead no longer edits the shell rc; it builds a shim dir and `shellenv`
prints the export lines the user adds to their profile.
"""

from pathlib import Path

import pytest

from trailhead.pathint import (
    ShimDenylistError,
    create_shims,
    detect_shell,
    resolve_shim_dir,
    shellenv_lines,
)


def _env(tmp_path: Path) -> dict[str, str]:
    return {"TRAILHEAD_STATE_DIR": str(tmp_path)}


# ---------------------------------------------------------------------------
# resolve_shim_dir
# ---------------------------------------------------------------------------


class TestResolveShimDir:
    def test_under_state_dir(self, tmp_path):
        assert resolve_shim_dir(env=_env(tmp_path)) == tmp_path / "bin"

    def test_pure_does_not_create(self, tmp_path):
        resolve_shim_dir(env=_env(tmp_path))
        assert not (tmp_path / "bin").exists()


# ---------------------------------------------------------------------------
# create_shims
# ---------------------------------------------------------------------------


class TestCreateShims:
    def test_creates_one_shim_per_tool(self, tmp_path):
        tools = {
            "camp": Path("/repo/tools/camp/bin/camp"),
            "lore": Path("/repo/tools/lore/bin/lore"),
        }
        res = create_shims(tools, "/repo", env=_env(tmp_path))
        assert (res.shim_dir / "camp").is_file()
        assert (res.shim_dir / "lore").is_file()
        assert set(res.shims) == {"camp", "lore"}

    def test_shim_is_executable(self, tmp_path):
        res = create_shims({"camp": Path("/repo/bin/camp")}, "/repo", env=_env(tmp_path))
        mode = (res.shim_dir / "camp").stat().st_mode
        assert mode & 0o100  # owner-executable

    def test_shim_hardcodes_trailhead_root_and_exec(self, tmp_path):
        res = create_shims({"camp": Path("/repo/bin/camp")}, "/the/root", env=_env(tmp_path))
        content = (res.shim_dir / "camp").read_text()
        assert 'export TRAILHEAD_ROOT="/the/root"' in content
        assert 'exec "/repo/bin/camp" "$@"' in content

    def test_denylisted_name_rejected(self, tmp_path):
        with pytest.raises(ShimDenylistError):
            create_shims({"python3": Path("/x")}, "/repo", env=_env(tmp_path))

    def test_only_selected_tools_shimmed(self, tmp_path):
        # The shim dir's contents encode the selection (--no-lore omits lore).
        res = create_shims({"camp": Path("/repo/bin/camp")}, "/repo", env=_env(tmp_path))
        assert (res.shim_dir / "camp").exists()
        assert not (res.shim_dir / "lore").exists()


# ---------------------------------------------------------------------------
# detect_shell
# ---------------------------------------------------------------------------


class TestDetectShell:
    @pytest.mark.parametrize("shell,expected", [
        ("/usr/bin/fish", "fish"),
        ("/bin/zsh", "zsh"),
        ("/bin/bash", "bash"),
        ("/usr/local/bin/weird", "bash"),
        ("", "bash"),
    ])
    def test_detects_from_shell_env(self, shell, expected):
        assert detect_shell({"SHELL": shell}) == expected


# ---------------------------------------------------------------------------
# shellenv_lines
# ---------------------------------------------------------------------------


class TestShellenv:
    def test_posix_exports_root_and_path(self, tmp_path):
        out = shellenv_lines(shell="zsh", env=_env(tmp_path), trailhead_root="/repo")
        assert 'export TRAILHEAD_ROOT="/repo"' in out
        assert f'export PATH="{tmp_path / "bin"}:$PATH"' in out

    def test_bash_same_as_zsh(self, tmp_path):
        z = shellenv_lines(shell="zsh", env=_env(tmp_path), trailhead_root="/repo")
        b = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        assert z == b

    def test_fish_uses_set_gx_and_fish_add_path(self, tmp_path):
        out = shellenv_lines(shell="fish", env=_env(tmp_path), trailhead_root="/repo")
        assert 'set -gx TRAILHEAD_ROOT "/repo"' in out
        assert f'fish_add_path "{tmp_path / "bin"}"' in out

    def test_shell_detected_from_env_when_unspecified(self, tmp_path):
        env = {**_env(tmp_path), "SHELL": "/usr/bin/fish"}
        out = shellenv_lines(env=env, trailhead_root="/repo")
        assert "fish_add_path" in out

    def test_default_root_is_repo(self, tmp_path):
        # No trailhead_root → the repo containing pathint.py.
        out = shellenv_lines(shell="zsh", env=_env(tmp_path))
        assert "TRAILHEAD_ROOT=" in out
        assert "trailhead" in out
