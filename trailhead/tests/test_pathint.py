"""Tests for trailhead/pathint.py — PATH integration (shim dir + shell-rc block).

TDD: these tests are written BEFORE the implementation. All must pass after
trailhead/pathint.py is implemented.

Hermeticity contract (Step-4 non-hermetic trap, called out explicitly):
  Every test that touches rc files or the shim dir MUST use tmp_path +
  TRAILHEAD_STATE_DIR env override. NO test may touch the real
  ~/.config/fish/config.fish, ~/.zshrc, ~/.bashrc, or the real
  state_dir("trailhead")/bin/.

  The live shim-resolution smoke test reads the real tools/camp/.../bin/camp
  (read-only — fine) but creates the shim under tmp_path.
"""

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from trailhead.pathint import (
    PathIntegrationError,
    ShimDenylistError,
    ShimDirResult,
    SymlinkRefusalError,
    create_shims,
    inject_path_block,
    remove_path_block,
    resolve_shim_dir,
    install_path_integration,
    remove_path_integration,
)

# ---------------------------------------------------------------------------
# Markers / constants
# ---------------------------------------------------------------------------

OPEN_MARKER = "# >>> trailhead managed PATH >>>"
CLOSE_MARKER = "# <<< trailhead managed PATH <<<"

_REPO_ROOT = Path(__file__).parent.parent.parent  # trailhead/ repo root


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state_env(tmp_path: Path) -> dict[str, str]:
    """Redirect TRAILHEAD_STATE_DIR to tmp_path for test hermeticity."""
    return {"TRAILHEAD_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}


def _count_marker_blocks(text: str) -> int:
    """Count the number of complete open+close marker block pairs."""
    return text.count(OPEN_MARKER)


# ---------------------------------------------------------------------------
# resolve_shim_dir
# ---------------------------------------------------------------------------


class TestResolveShimDir:
    def test_shim_dir_is_under_state_dir(self, tmp_path):
        env = _state_env(tmp_path)
        shim_dir = resolve_shim_dir(env=env)
        assert shim_dir == tmp_path / "bin"

    def test_shim_dir_not_created_by_resolver(self, tmp_path):
        env = _state_env(tmp_path)
        shim_dir = resolve_shim_dir(env=env)
        assert not shim_dir.exists()


# ---------------------------------------------------------------------------
# create_shims — shim dir + permissions
# ---------------------------------------------------------------------------


class TestShimDirCreation:
    def test_shim_dir_is_created(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        wired_tools = {"lore": Path(_REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore")}
        result = create_shims(wired_tools, trailhead_root, env=env)
        assert result.shim_dir.exists()

    def test_shim_dir_mode_is_0o700(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        wired_tools = {"lore": Path(_REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore")}
        result = create_shims(wired_tools, trailhead_root, env=env)
        mode = stat.S_IMODE(result.shim_dir.stat().st_mode)
        assert mode == 0o700

    def test_shim_file_is_created_for_each_tool(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        wired_tools = {
            "lore": Path(_REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore"),
            "camp": Path(_REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"),
        }
        result = create_shims(wired_tools, trailhead_root, env=env)
        assert (result.shim_dir / "lore").exists()
        assert (result.shim_dir / "camp").exists()

    def test_shim_file_mode_is_0o700(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        wired_tools = {"camp": Path(_REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp")}
        result = create_shims(wired_tools, trailhead_root, env=env)
        shim = result.shim_dir / "camp"
        mode = stat.S_IMODE(shim.stat().st_mode)
        assert mode == 0o700


# ---------------------------------------------------------------------------
# S-5: shim content — absolute hardcoded TRAILHEAD_ROOT, not env pass-through
# ---------------------------------------------------------------------------


class TestShimContent:
    def test_shim_exports_absolute_trailhead_root(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        bin_path = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
        wired_tools = {"camp": bin_path}
        result = create_shims(wired_tools, trailhead_root, env=env)
        content = (result.shim_dir / "camp").read_text()
        assert f'export TRAILHEAD_ROOT="{trailhead_root}"' in content

    def test_shim_does_not_use_env_var_passthrough(self, tmp_path):
        """S-5: shim must not pass through $TRAILHEAD_ROOT from the caller's env."""
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        bin_path = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
        wired_tools = {"camp": bin_path}
        result = create_shims(wired_tools, trailhead_root, env=env)
        content = (result.shim_dir / "camp").read_text()
        # Must NOT contain the variable reference (only the literal value)
        assert "${TRAILHEAD_ROOT}" not in content

    def test_shim_execs_absolute_bin_path(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        bin_path = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
        wired_tools = {"camp": bin_path}
        result = create_shims(wired_tools, trailhead_root, env=env)
        content = (result.shim_dir / "camp").read_text()
        assert f'exec "{bin_path}"' in content

    def test_shim_has_bash_shebang(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        bin_path = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
        wired_tools = {"camp": bin_path}
        result = create_shims(wired_tools, trailhead_root, env=env)
        content = (result.shim_dir / "camp").read_text()
        assert content.startswith("#!/usr/bin/env bash")

    def test_shim_passes_all_args(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        bin_path = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
        wired_tools = {"camp": bin_path}
        result = create_shims(wired_tools, trailhead_root, env=env)
        content = (result.shim_dir / "camp").read_text()
        assert '"$@"' in content


# ---------------------------------------------------------------------------
# Smoke test: shim resolves from a TRAILHEAD_ROOT-less, CLAUDE_PLUGIN_ROOT-less env
# ---------------------------------------------------------------------------


class TestShimSmokeResolution:
    """U-2: a generated shim invoked from a bare env sets TRAILHEAD_ROOT and
    the bootstrap tier-2 picks it up so trailhead.paths is importable."""

    def test_camp_shim_resolves_trailhead_paths(self, tmp_path):
        """Run the generated shim and confirm it can import trailhead.paths."""
        env_override = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        bin_path = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
        wired_tools = {"camp": bin_path}
        result = create_shims(wired_tools, trailhead_root, env=env_override)
        shim = result.shim_dir / "camp"

        # Invoke via bash in a minimal env (no CLAUDE_PLUGIN_ROOT, no TRAILHEAD_ROOT)
        # Run `camp --version` — the bootstrap will attempt to import trailhead.paths;
        # if it fails the script exits 1 with a legible message.
        clean_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
        }
        proc = subprocess.run(
            ["bash", str(shim), "--version"],
            capture_output=True,
            text=True,
            env=clean_env,
            timeout=15,
        )
        # camp --version exits 0 and prints a version line
        assert proc.returncode == 0, (
            f"camp shim failed (rc={proc.returncode}):\n"
            f"stdout: {proc.stdout!r}\n"
            f"stderr: {proc.stderr!r}"
        )


# ---------------------------------------------------------------------------
# S-6: shim denylist
# ---------------------------------------------------------------------------


class TestShimDenylist:
    @pytest.mark.parametrize("name", [
        "python", "python3", "git", "ssh", "curl",
        "install", "update", "sh", "bash", "fish", "zsh",
    ])
    def test_denylisted_name_raises(self, tmp_path, name):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        wired_tools = {name: Path("/usr/bin/somebin")}
        with pytest.raises(ShimDenylistError) as exc_info:
            create_shims(wired_tools, trailhead_root, env=env)
        assert name in str(exc_info.value)

    def test_camp_is_not_denylisted(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        bin_path = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
        wired_tools = {"camp": bin_path}
        # must not raise
        result = create_shims(wired_tools, trailhead_root, env=env)
        assert (result.shim_dir / "camp").exists()

    def test_lore_is_not_denylisted(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        bin_path = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore"
        wired_tools = {"lore": bin_path}
        result = create_shims(wired_tools, trailhead_root, env=env)
        assert (result.shim_dir / "lore").exists()


# ---------------------------------------------------------------------------
# inject_path_block — fish idiom
# ---------------------------------------------------------------------------


class TestInjectPathBlockFish:
    def test_inject_creates_block_in_empty_rc(self, tmp_path):
        rc = tmp_path / "config.fish"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="fish")
        content = rc.read_text()
        assert OPEN_MARKER in content
        assert CLOSE_MARKER in content

    def test_inject_fish_uses_fish_add_path(self, tmp_path):
        """A-11: fish block must use fish_add_path, not set -gx PATH."""
        rc = tmp_path / "config.fish"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="fish")
        content = rc.read_text()
        assert "fish_add_path" in content
        assert "set -gx PATH" not in content

    def test_inject_fish_includes_shim_dir_path(self, tmp_path):
        rc = tmp_path / "config.fish"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="fish")
        content = rc.read_text()
        assert str(shim_dir) in content

    def test_idempotent_inject_fish_twice_leaves_one_block(self, tmp_path):
        rc = tmp_path / "config.fish"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="fish")
        inject_path_block(rc, shim_dir, shell="fish")
        content = rc.read_text()
        assert _count_marker_blocks(content) == 1

    def test_inject_preserves_existing_content_before_block(self, tmp_path):
        rc = tmp_path / "config.fish"
        existing = "# existing fish config\nset -x FOO bar\n"
        rc.write_text(existing)
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="fish")
        content = rc.read_text()
        assert content.startswith(existing)

    def test_fish_block_passes_fish_n_syntax_check(self, tmp_path):
        """The injected fish block must pass `fish -n` syntax validation."""
        rc = tmp_path / "config.fish"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="fish")
        content = rc.read_text()
        # Extract just the block
        start = content.index(OPEN_MARKER)
        end = content.index(CLOSE_MARKER) + len(CLOSE_MARKER)
        block = content[start:end]
        proc = subprocess.run(
            ["fish", "--no-execute", "-c", block],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"fish syntax error: {proc.stderr}"


# ---------------------------------------------------------------------------
# inject_path_block — zsh idiom
# ---------------------------------------------------------------------------


class TestInjectPathBlockZsh:
    def test_inject_creates_block_in_empty_rc(self, tmp_path):
        rc = tmp_path / ".zshrc"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="zsh")
        content = rc.read_text()
        assert OPEN_MARKER in content
        assert CLOSE_MARKER in content

    def test_inject_zsh_uses_export_path(self, tmp_path):
        rc = tmp_path / ".zshrc"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="zsh")
        content = rc.read_text()
        assert f'export PATH="{shim_dir}:$PATH"' in content

    def test_idempotent_inject_zsh_twice_leaves_one_block(self, tmp_path):
        rc = tmp_path / ".zshrc"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="zsh")
        inject_path_block(rc, shim_dir, shell="zsh")
        content = rc.read_text()
        assert _count_marker_blocks(content) == 1

    def test_inject_zsh_passes_zsh_n_syntax_check(self, tmp_path):
        """The injected zsh block must pass `zsh -n` syntax validation."""
        rc = tmp_path / ".zshrc"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="zsh")
        content = rc.read_text()
        start = content.index(OPEN_MARKER)
        end = content.index(CLOSE_MARKER) + len(CLOSE_MARKER)
        block = content[start:end]
        proc = subprocess.run(
            ["zsh", "-n", "-c", block],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"zsh syntax error: {proc.stderr}"


# ---------------------------------------------------------------------------
# inject_path_block — bash idiom
# ---------------------------------------------------------------------------


class TestInjectPathBlockBash:
    def test_inject_creates_block_in_empty_rc(self, tmp_path):
        rc = tmp_path / ".bashrc"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="bash")
        content = rc.read_text()
        assert OPEN_MARKER in content
        assert CLOSE_MARKER in content

    def test_inject_bash_uses_export_path(self, tmp_path):
        rc = tmp_path / ".bashrc"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="bash")
        content = rc.read_text()
        assert f'export PATH="{shim_dir}:$PATH"' in content

    def test_idempotent_inject_bash_twice_leaves_one_block(self, tmp_path):
        rc = tmp_path / ".bashrc"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="bash")
        inject_path_block(rc, shim_dir, shell="bash")
        content = rc.read_text()
        assert _count_marker_blocks(content) == 1


# ---------------------------------------------------------------------------
# R-4: corrupt marker repair
# ---------------------------------------------------------------------------


class TestCorruptMarkerRepair:
    def test_open_marker_no_close_is_repaired_to_one_block(self, tmp_path):
        """R-4: an rc with only the open marker is repaired to exactly one block."""
        rc = tmp_path / ".zshrc"
        rc.write_text(f"# before\n{OPEN_MARKER}\nexport PATH=/stale:$PATH\n")
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="zsh")
        content = rc.read_text()
        assert _count_marker_blocks(content) == 1
        assert CLOSE_MARKER in content

    def test_repair_includes_correct_new_content(self, tmp_path):
        rc = tmp_path / ".zshrc"
        rc.write_text(f"{OPEN_MARKER}\nexport PATH=/old/path:$PATH\n")
        shim_dir = tmp_path / "newbin"
        inject_path_block(rc, shim_dir, shell="zsh")
        content = rc.read_text()
        assert str(shim_dir) in content
        assert "/old/path" not in content

    def test_content_before_open_marker_is_preserved_on_repair(self, tmp_path):
        rc = tmp_path / ".zshrc"
        preamble = "# top of file\nexport FOO=bar\n"
        rc.write_text(preamble + f"{OPEN_MARKER}\n")
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="zsh")
        content = rc.read_text()
        assert content.startswith(preamble)


# ---------------------------------------------------------------------------
# remove_path_block
# ---------------------------------------------------------------------------


class TestRemovePathBlock:
    def _rc_with_block(self, tmp_path: Path, shell: str = "zsh") -> Path:
        rc = tmp_path / ".zshrc"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell=shell)
        return rc

    def test_remove_deletes_block(self, tmp_path):
        rc = self._rc_with_block(tmp_path)
        remove_path_block(rc)
        content = rc.read_text()
        assert OPEN_MARKER not in content
        assert CLOSE_MARKER not in content

    def test_remove_leaves_surrounding_content_byte_identical(self, tmp_path):
        """frozen-fixture diff: rest of file is byte-identical after removal."""
        rc = tmp_path / ".zshrc"
        before = "# line 1\nexport FOO=bar\n"
        after = "# line 2\nexport BAZ=qux\n"
        shim_dir = tmp_path / "bin"
        rc.write_text(before)
        inject_path_block(rc, shim_dir, shell="zsh")
        with open(rc, "a") as fh:
            fh.write(after)
        remove_path_block(rc)
        result = rc.read_text()
        assert result == before + after

    def test_remove_when_absent_is_noop_no_crash(self, tmp_path):
        rc = tmp_path / ".zshrc"
        rc.write_text("# no block here\n")
        original = rc.read_text()
        remove_path_block(rc)  # must not raise
        assert rc.read_text() == original

    def test_remove_missing_rc_is_noop_no_crash(self, tmp_path):
        rc = tmp_path / ".zshrc"  # does not exist
        remove_path_block(rc)  # must not raise


# ---------------------------------------------------------------------------
# R-7: missing rc → create; symlink edges
# ---------------------------------------------------------------------------


class TestRcEdgeCases:
    def test_missing_rc_is_created(self, tmp_path):
        rc = tmp_path / ".zshrc"
        assert not rc.exists()
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="zsh")
        assert rc.exists()
        assert OPEN_MARKER in rc.read_text()

    def test_missing_rc_in_nonexistent_parent_is_created(self, tmp_path):
        rc = tmp_path / "nested" / "dir" / ".zshrc"
        shim_dir = tmp_path / "bin"
        inject_path_block(rc, shim_dir, shell="zsh")
        assert rc.exists()

    def test_symlink_within_home_is_written_through(self, tmp_path):
        """R-7: symlink that resolves inside ~ must be written through."""
        real_rc = tmp_path / "real_zshrc"
        real_rc.write_text("")
        symlink_rc = tmp_path / ".zshrc"
        symlink_rc.symlink_to(real_rc)
        shim_dir = tmp_path / "bin"
        inject_path_block(symlink_rc, shim_dir, shell="zsh", home=tmp_path)
        assert OPEN_MARKER in real_rc.read_text()

    def test_symlink_outside_home_is_refused(self, tmp_path):
        """R-7: symlink that resolves outside ~ must be refused with named message."""
        outside = tmp_path / "outside"
        outside.mkdir()
        real_rc = outside / "zshrc"
        real_rc.write_text("")
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        symlink_rc = home_dir / ".zshrc"
        symlink_rc.symlink_to(real_rc)
        shim_dir = tmp_path / "bin"
        with pytest.raises(SymlinkRefusalError) as exc_info:
            inject_path_block(symlink_rc, shim_dir, shell="zsh", home=home_dir)
        assert str(real_rc) in str(exc_info.value) or "outside" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# A-8: non-TTY skips rc write
# ---------------------------------------------------------------------------


class TestNonTtySkipsRcWrite:
    def test_non_tty_skips_rc_write(self, tmp_path):
        """A-8: when is_tty=False, the rc file must NOT be written."""
        rc = tmp_path / ".zshrc"
        shim_dir = tmp_path / "bin"
        msg = inject_path_block(rc, shim_dir, shell="zsh", is_tty=False)
        assert not rc.exists()

    def test_non_tty_returns_skip_message(self, tmp_path):
        """A-8: non-TTY must return the prescribed skip message."""
        rc = tmp_path / ".zshrc"
        shim_dir = tmp_path / "bin"
        msg = inject_path_block(rc, shim_dir, shell="zsh", is_tty=False)
        assert "PATH integration skipped" in msg
        assert "non-interactive" in msg

    def test_non_tty_shim_dir_still_created(self, tmp_path):
        """A-8: shim dir may still be created even when rc write is skipped."""
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        bin_path = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
        wired_tools = {"camp": bin_path}
        result = create_shims(wired_tools, trailhead_root, env=env)
        # Shim dir should exist even when rc write is not called
        assert result.shim_dir.exists()


# ---------------------------------------------------------------------------
# Unwritable rc
# ---------------------------------------------------------------------------


class TestUnwritableRc:
    def test_unwritable_rc_raises_named_error(self, tmp_path):
        """An unwritable rc must raise PathIntegrationError naming the rc file."""
        rc = tmp_path / ".zshrc"
        rc.write_text("# existing\n")
        rc.chmod(0o444)  # read-only
        shim_dir = tmp_path / "bin"
        try:
            with pytest.raises(PathIntegrationError) as exc_info:
                inject_path_block(rc, shim_dir, shell="zsh")
            msg = str(exc_info.value)
            assert str(rc) in msg or "could not write" in msg.lower()
            assert str(shim_dir) in msg or "manually" in msg.lower()
        finally:
            rc.chmod(0o644)  # restore for cleanup


# ---------------------------------------------------------------------------
# Preset gating
# ---------------------------------------------------------------------------


class TestPresetGating:
    def test_minimal_wires_lore_shim_not_camp(self, tmp_path):
        """minimal preset → lore shim present, camp shim absent."""
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        # minimal: only lore
        lore_bin = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore"
        wired_tools = {"lore": lore_bin}
        result = create_shims(wired_tools, trailhead_root, env=env)
        assert (result.shim_dir / "lore").exists()
        assert not (result.shim_dir / "camp").exists()

    def test_standard_wires_camp_shim(self, tmp_path):
        """standard preset → camp shim present (the forcing case)."""
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        camp_bin = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
        lore_bin = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore"
        wired_tools = {"lore": lore_bin, "camp": camp_bin}
        result = create_shims(wired_tools, trailhead_root, env=env)
        assert (result.shim_dir / "camp").exists()
        assert (result.shim_dir / "lore").exists()


# ---------------------------------------------------------------------------
# install_path_integration public API (for Slices 4/5)
# ---------------------------------------------------------------------------


class TestInstallPathIntegrationPublicApi:
    def test_returns_shim_dir_and_rc_path(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        lore_bin = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore"
        wired_tools = {"lore": lore_bin}
        rc = tmp_path / ".zshrc"
        result = install_path_integration(
            wired_tools,
            trailhead_root,
            shell="zsh",
            rc_path=rc,
            is_tty=True,
            env=env,
        )
        assert result.shim_dir.exists()
        assert result.rc_path == rc

    def test_install_writes_rc_block(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        lore_bin = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore"
        wired_tools = {"lore": lore_bin}
        rc = tmp_path / ".zshrc"
        install_path_integration(
            wired_tools,
            trailhead_root,
            shell="zsh",
            rc_path=rc,
            is_tty=True,
            env=env,
        )
        assert OPEN_MARKER in rc.read_text()

    def test_remove_path_integration_cleans_block(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        lore_bin = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore"
        wired_tools = {"lore": lore_bin}
        rc = tmp_path / ".zshrc"
        install_path_integration(
            wired_tools,
            trailhead_root,
            shell="zsh",
            rc_path=rc,
            is_tty=True,
            env=env,
        )
        remove_path_integration(rc_path=rc)
        assert OPEN_MARKER not in rc.read_text()

    def test_install_idempotent_twice_one_block(self, tmp_path):
        env = _state_env(tmp_path)
        trailhead_root = str(_REPO_ROOT)
        lore_bin = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore"
        wired_tools = {"lore": lore_bin}
        rc = tmp_path / ".zshrc"
        for _ in range(2):
            install_path_integration(
                wired_tools,
                trailhead_root,
                shell="zsh",
                rc_path=rc,
                is_tty=True,
                env=env,
            )
        assert _count_marker_blocks(rc.read_text()) == 1
