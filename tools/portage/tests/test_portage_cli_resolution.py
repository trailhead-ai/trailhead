"""bin/portage → cli/portage resolves symlink-safely (macOS, no GNU readlink -f).

The PATH wrapper must find its sibling ``cli/portage`` shim even when invoked
through a symlink from an arbitrary directory — Claude Code adds the plugin's
``bin/`` to PATH and users may symlink it. This drives the real wrapper through
a symlink and asserts the CLI actually runs (help exits 0; a bad manifest exits
2 through the full shim → dispatch → provider path).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _portage_cli  # noqa: F401  (defines PLUGIN_ROOT)

_BIN_PORTAGE = _portage_cli.PLUGIN_ROOT / "bin" / "portage"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)


class TestBinResolvesThroughSymlink:
    def test_help_through_symlink_exits_zero(self, tmp_path):
        link = tmp_path / "portage"
        link.symlink_to(_BIN_PORTAGE)
        result = _run([str(link), "--help"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # The resolved shim reached the real dispatch and listed its subcommands.
        assert "detect-repos" in result.stdout and "merge" in result.stdout

    def test_command_through_symlink_reaches_provider(self, tmp_path):
        link = tmp_path / "portage"
        link.symlink_to(_BIN_PORTAGE)
        missing = tmp_path / "nope" / "manifest.json"
        result = _run([str(link), "detect-repos", "--manifest", str(missing)], cwd=tmp_path)
        # Full path exercised: wrapper → cli/portage shim → dispatch → provider.
        assert result.returncode == 2, f"{result.returncode}\n{result.stdout}\n{result.stderr}"
        assert str(missing) in result.stderr


class TestCliShimResolvesPackage:
    def test_cli_shim_runs_directly(self, tmp_path):
        """cli/portage run directly (as bin/portage execs it) resolves the package."""
        cli = _portage_cli.PLUGIN_ROOT / "cli" / "portage"
        result = _run([sys.executable, str(cli), "--help"], cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "wait-for-actionable" in result.stdout


class TestSiblingCliWinsOverPluginRoot:
    """Which tree answers: the wrapper's own sibling cli/, or $CLAUDE_PLUGIN_ROOT."""

    @staticmethod
    def _plant_tree(root: Path, marker: str, *, with_cli: bool = True) -> Path:
        """Build a synthetic plugin tree whose CLI prints *marker*; return its bin/portage.

        The real wrapper is copied in verbatim, so the tree exercises the shipped
        resolution logic rather than a restatement of it.
        """
        (root / "bin").mkdir(parents=True)
        wrapper = root / "bin" / "portage"
        wrapper.write_bytes(_BIN_PORTAGE.read_bytes())
        wrapper.chmod(0o755)
        if with_cli:
            (root / "cli").mkdir()
            cli = root / "cli" / "portage"
            cli.write_text(f"print({marker!r})\n", encoding="utf-8")
            cli.chmod(0o755)
        return wrapper

    def _run_with_plugin_root(self, wrapper: Path, plugin_root: Path):
        return subprocess.run(
            [str(wrapper)],
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(plugin_root)},
        )

    def test_runs_its_own_tree_not_the_env_tree(self, tmp_path):
        """A wrapper invoked out of a checkout runs that checkout's CLI."""
        env_tree = tmp_path / "env_tree"
        self._plant_tree(env_tree, "env")
        wrapper = self._plant_tree(tmp_path / "own_tree", "own")

        result = self._run_with_plugin_root(wrapper, env_tree)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "own"

    def test_without_a_sibling_cli_it_fails_in_its_own_tree(self, tmp_path):
        """No sibling cli/ is a failure in this tree, not a silent hop to another one.

    A marketplace install copies a plugin directory whole — a tree carrying bin/ has
    cli/ beside it, and $CLAUDE_PLUGIN_ROOT names that same copy. An env root can
    therefore never supply a CLI the sibling lookup would not already find in the same
    place; consulting it can only ever run a tree the caller did not name.
    """
        env_tree = tmp_path / "env_tree"
        self._plant_tree(env_tree, "env")
        wrapper = self._plant_tree(tmp_path / "own_tree", "own", with_cli=False)

        result = self._run_with_plugin_root(wrapper, env_tree)

        assert result.returncode != 0
        assert str(tmp_path / "own_tree" / "cli" / "portage") in result.stderr
