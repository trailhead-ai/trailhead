"""bin/camp picks which tree's cli/camp to run.

Test contract:
- Given a wrapper with a sibling ``cli/camp`` AND a ``$CLAUDE_PLUGIN_ROOT``
  naming a different tree that also has one, the sibling is what runs — the
  wrapper belongs to one tree and a caller reaching it means that tree.
- Given a wrapper with no sibling ``cli/camp``, that is a failure in its own
  tree rather than a silent hop to the one the env var names.

Each fixture plants a throwaway ``{bin,cli}`` tree, copies the real ``bin/camp``
into it unmodified, and gives each tree a ``cli/camp`` that prints which tree it
is — so the assertion reads back the launcher's actual choice.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_REAL_BIN_CAMP = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"


def _plant_tree(root: Path, marker: str, *, with_cli: bool = True) -> Path:
    """Build a synthetic plugin tree whose CLI prints *marker*; return its bin/camp."""
    (root / "bin").mkdir(parents=True)
    wrapper = root / "bin" / "camp"
    wrapper.write_bytes(_REAL_BIN_CAMP.read_bytes())
    wrapper.chmod(0o755)
    if with_cli:
        (root / "cli").mkdir()
        cli = root / "cli" / "camp"
        cli.write_text(f"print({marker!r})\n", encoding="utf-8")
        cli.chmod(0o755)
    return wrapper


def _run(wrapper: Path, plugin_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(wrapper)],
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(plugin_root)},
    )


def test_sibling_cli_wins_over_plugin_root(tmp_path):
    env_tree = tmp_path / "env_tree"
    _plant_tree(env_tree, "env")
    wrapper = _plant_tree(tmp_path / "own_tree", "own")

    result = _run(wrapper, env_tree)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "own"


def test_without_a_sibling_cli_it_fails_in_its_own_tree(tmp_path):
    """No sibling cli/ is a failure in this tree, not a silent hop to another one.

    A marketplace install copies a plugin directory whole — a tree carrying bin/ has
    cli/ beside it, and $CLAUDE_PLUGIN_ROOT names that same copy. An env root can
    therefore never supply a CLI the sibling lookup would not already find in the same
    place; consulting it can only ever run a tree the caller did not name.
    """
    env_tree = tmp_path / "env_tree"
    _plant_tree(env_tree, "env")
    wrapper = _plant_tree(tmp_path / "own_tree", "own", with_cli=False)

    result = _run(wrapper, env_tree)

    assert result.returncode != 0
    assert str(tmp_path / "own_tree" / "cli" / "camp") in result.stderr
