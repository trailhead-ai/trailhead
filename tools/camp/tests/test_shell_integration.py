"""Tests for camp pwd shell integration (previously 'camp cd', renamed per PR review).

Test contract:
- camp pwd <slug> prints exactly one line = the resolved absolute workspace path
  (under the state dir), no trailing whitespace; diagnostics go to stderr, never
  stdout.
- unknown slug → legible stderr error, non-zero, NOTHING on stdout.
- outside a member dir without --group → the standard "pass --group" error.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
_CLI_CAMP = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "cli" / "camp"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_group_config(tmp_path: Path, group_name: str, member_name: str) -> Path:
    """Write a minimal group TOML config and return the config dir."""
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    (groups_dir / f"{group_name}.toml").write_text(
        f'[group]\nname = "{group_name}"\n\n'
        f'[[members]]\nname = "{member_name}"\n'
        f'repo_root = "{tmp_path / "fake-repo"}"\n'
    )
    return tmp_path


def _make_workspace(tmp_path: Path, group_name: str, slug: str) -> Path:
    """Create a workspace directory under the state dir and return its path."""
    state_dir = tmp_path / "state"
    ws_dir = state_dir / group_name / "worktrees" / slug
    ws_dir.mkdir(parents=True, exist_ok=True)
    return ws_dir


def _run_cli(
    args: list[str], *, env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess:
    base = {**os.environ}
    base.update(env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=base,
        cwd=str(cwd) if cwd is not None else None,
    )


# ---------------------------------------------------------------------------
# shell_integration module — cmd_pwd
# ---------------------------------------------------------------------------


class TestCmdPwd:
    def test_cmd_pwd_returns_workspace_dir(self, tmp_path: Path) -> None:
        """cmd_pwd returns the resolved workspace dir for a known slug."""
        from shell_integration import cmd_pwd

        group_name = "mygroup"
        member_name = "myrepo"
        slug = "my-slug"

        _make_group_config(tmp_path, group_name, member_name)
        ws_dir = _make_workspace(tmp_path, group_name, slug)

        env = {
            "CAMP_STATE_DIR": str(tmp_path / "state"),
            "CAMP_CONFIG_DIR": str(tmp_path),
        }

        group = {
            "group": {"name": group_name},
            "members": [{"name": member_name, "repo_root": str(tmp_path / "fake-repo")}],
        }

        result = cmd_pwd(group, slug, env=env)
        assert result == ws_dir

    def test_cmd_pwd_is_absolute(self, tmp_path: Path) -> None:
        """The path returned by cmd_pwd is absolute."""
        from shell_integration import cmd_pwd

        group_name = "mygroup"
        member_name = "myrepo"
        slug = "my-slug"

        _make_workspace(tmp_path, group_name, slug)

        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}

        group = {
            "group": {"name": group_name},
            "members": [{"name": member_name, "repo_root": str(tmp_path / "fake-repo")}],
        }

        result = cmd_pwd(group, slug, env=env)
        assert result.is_absolute()


# ---------------------------------------------------------------------------
# CLI: camp pwd <slug> — stdout is exactly one line, no trailing whitespace
# ---------------------------------------------------------------------------


class TestCampPwdCLI:
    def test_camp_pwd_prints_one_line_workspace_path(self, tmp_path: Path) -> None:
        """camp pwd <slug> prints exactly one line: the resolved workspace path."""
        group_name = "mygroup"
        member_name = "myrepo"
        slug = "my-slug"

        _make_group_config(tmp_path, group_name, member_name)
        ws_dir = _make_workspace(tmp_path, group_name, slug)

        env = {
            "CAMP_CONFIG_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
        }

        result = _run_cli(["pwd", slug, "--group", group_name], env=env)

        assert result.returncode == 0, (
            f"Expected exit 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        lines = result.stdout.splitlines()
        assert len(lines) == 1, (
            f"Expected exactly one stdout line, got {len(lines)}:\n{result.stdout!r}"
        )
        assert lines[0] == str(ws_dir), f"Expected path {ws_dir!s}, got {lines[0]!r}"

    def test_camp_pwd_stdout_has_no_trailing_whitespace(self, tmp_path: Path) -> None:
        """The single stdout line from camp pwd has no trailing whitespace."""
        group_name = "mygroup"
        member_name = "myrepo"
        slug = "my-slug"

        _make_group_config(tmp_path, group_name, member_name)
        _make_workspace(tmp_path, group_name, slug)

        env = {
            "CAMP_CONFIG_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
        }

        result = _run_cli(["pwd", slug, "--group", group_name], env=env)

        assert result.returncode == 0
        # stdout must end with exactly one newline (from print), no trailing spaces
        assert not result.stdout.rstrip("\n").endswith(" "), (
            f"Trailing whitespace on stdout line: {result.stdout!r}"
        )

    def test_camp_pwd_diagnostics_go_to_stderr_not_stdout(self, tmp_path: Path) -> None:
        """Any diagnostic output goes to stderr, not stdout."""
        group_name = "mygroup"
        member_name = "myrepo"
        slug = "my-slug"

        _make_group_config(tmp_path, group_name, member_name)
        _make_workspace(tmp_path, group_name, slug)

        env = {
            "CAMP_CONFIG_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
        }

        result = _run_cli(["pwd", slug, "--group", group_name], env=env)

        assert result.returncode == 0
        # stdout contains ONLY the path (one line)
        lines = result.stdout.splitlines()
        assert len(lines) == 1

    def test_camp_pwd_path_is_absolute(self, tmp_path: Path) -> None:
        """The path printed by camp pwd is absolute."""
        group_name = "mygroup"
        member_name = "myrepo"
        slug = "my-slug"

        _make_group_config(tmp_path, group_name, member_name)
        _make_workspace(tmp_path, group_name, slug)

        env = {
            "CAMP_CONFIG_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
        }

        result = _run_cli(["pwd", slug, "--group", group_name], env=env)

        assert result.returncode == 0
        path_str = result.stdout.strip()
        assert Path(path_str).is_absolute(), f"Expected absolute path, got: {path_str!r}"


# ---------------------------------------------------------------------------
# CLI: camp pwd — error cases
# ---------------------------------------------------------------------------


class TestCampPwdErrors:
    def test_unknown_slug_exits_nonzero(self, tmp_path: Path) -> None:
        """camp pwd with an unknown slug exits non-zero."""
        group_name = "mygroup"
        member_name = "myrepo"

        _make_group_config(tmp_path, group_name, member_name)

        env = {
            "CAMP_CONFIG_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
        }

        result = _run_cli(["pwd", "nonexistent-slug", "--group", group_name], env=env)

        assert result.returncode != 0, (
            f"Expected non-zero exit for unknown slug.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_unknown_slug_error_on_stderr_not_stdout(self, tmp_path: Path) -> None:
        """camp pwd unknown slug: error is on stderr, nothing on stdout."""
        group_name = "mygroup"
        member_name = "myrepo"

        _make_group_config(tmp_path, group_name, member_name)

        env = {
            "CAMP_CONFIG_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
        }

        result = _run_cli(["pwd", "nonexistent-slug", "--group", group_name], env=env)

        assert result.returncode != 0
        assert result.stdout == "", (
            f"Expected empty stdout for unknown slug, got: {result.stdout!r}"
        )
        assert result.stderr.strip() != "", (
            "Expected non-empty stderr for unknown slug error, got empty"
        )

    def test_unknown_slug_stderr_is_legible(self, tmp_path: Path) -> None:
        """camp pwd unknown slug: stderr contains a legible error message."""
        group_name = "mygroup"
        member_name = "myrepo"

        _make_group_config(tmp_path, group_name, member_name)

        env = {
            "CAMP_CONFIG_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
        }

        result = _run_cli(["pwd", "nonexistent-slug", "--group", group_name], env=env)

        assert result.returncode != 0
        # The error message should reference the slug or 'workspace' or 'not found'
        assert (
            "nonexistent-slug" in result.stderr
            or "not found" in result.stderr.lower()
            or "does not exist" in result.stderr.lower()
            or "no workspace" in result.stderr.lower()
        ), f"Expected legible error naming the slug in stderr: {result.stderr!r}"

    def test_outside_member_dir_without_group_exits_nonzero(self, tmp_path: Path) -> None:
        """camp pwd without --group from outside a member dir exits non-zero."""
        # Create a config, but run from a non-member tmpdir
        group_name = "mygroup"
        member_name = "myrepo"

        _make_group_config(tmp_path, group_name, member_name)

        # cwd is the tmp_path itself (not a member dir, no --group)
        non_member_dir = tmp_path / "not-a-member"
        non_member_dir.mkdir()

        env = {
            "CAMP_CONFIG_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
        }

        result = _run_cli(["pwd", "any-slug"], env=env, cwd=non_member_dir)

        assert result.returncode != 0, (
            f"Expected non-zero exit without --group outside member dir.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_outside_member_dir_without_group_error_mentions_group(self, tmp_path: Path) -> None:
        """camp pwd without --group from outside a member dir: error mentions --group."""
        group_name = "mygroup"
        member_name = "myrepo"

        _make_group_config(tmp_path, group_name, member_name)

        non_member_dir = tmp_path / "not-a-member"
        non_member_dir.mkdir()

        env = {
            "CAMP_CONFIG_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
        }

        result = _run_cli(["pwd", "any-slug"], env=env, cwd=non_member_dir)

        assert result.returncode != 0
        assert "--group" in result.stderr or "group" in result.stderr.lower(), (
            f"Expected error mentioning --group in stderr: {result.stderr!r}"
        )
