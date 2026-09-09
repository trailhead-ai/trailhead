"""The repo's pre-commit leak gate, driven through the hook the config declares.

A private string on a shippable plugin surface is caught before the commit, not
after it ships. Two denylists feed the gate: a committed file of *structural*
seams (paths and tool prefixes that identify nobody, so they are safe to track)
and a machine-local file of identifying tokens that is never tracked anywhere.
The machine-local one is layered on as optional, so a fresh clone or a CI runner
without it still gets the committed patterns rather than being blocked outright.

`.pre-commit-config.yaml` is the INPUT here: the hook command is read out of it
and executed. Nothing below asserts that a line appears in the config.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _REPO_ROOT / ".pre-commit-config.yaml"

# A structural seam that the committed denylist carries. Built at runtime: this
# file would otherwise trip the very gate it tests, wherever the gate is pointed
# at a tree containing it.
_SEAM = "".join(["mcp", "__", "brain", "__"])


def _configured_hook_command() -> list[str]:
    """The local hook's command, lifted out of `.pre-commit-config.yaml`.

    A `repo: local` hook names an `entry:` that resolves to a file in this repo;
    the hooks pulled from upstream repos name a bare executable that does not.
    That distinction is what selects the leak gate without parsing YAML.
    """
    commands = []
    for line in _CONFIG.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*entry:\s*(\S.*?)\s*$", line)
        if match and (_REPO_ROOT / match.group(1)).is_file():
            commands.append(str(_REPO_ROOT / match.group(1)))
    assert len(commands) == 1, (
        f"expected exactly one local hook whose entry resolves in-repo, got {commands}"
    )
    return commands


def _run(*args: str, local_denylist: str = "/nonexistent/machine-local.denylist"):
    """Run the configured hook with the machine-local layer always pinned.

    Pinning it keeps these tests off the developer's real home: what they pin is
    the committed denylist, which is the half that exists on every machine and
    on CI. The machine-local half is exercised where it lives, in craft's
    `test_leak_gate.py`.
    """
    return subprocess.run(
        [*_configured_hook_command(), *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "LEAK_GATE_LOCAL_DENYLIST": local_denylist,
        },
    )


def test_the_configured_hook_blocks_a_seeded_structural_seam(tmp_path: Path):
    """The whole chain — config entry, wrapper, gate, committed denylist."""
    (tmp_path / "shipped.md").write_text(f"see {_SEAM}list for details\n", encoding="utf-8")
    result = _run(str(tmp_path))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "shipped.md" in result.stdout, result.stdout


def test_the_configured_hook_passes_a_clean_tree(tmp_path: Path):
    (tmp_path / "shipped.md").write_text("ordinary prose\n", encoding="utf-8")
    result = _run(str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_shippable_surfaces_this_repo_ships_are_clean():
    """No arguments — the hook scans exactly what a commit would publish."""
    result = _run()
    assert result.returncode == 0, (
        f"a private string reached a shippable plugin surface:\n{result.stdout}"
    )


def test_a_present_machine_local_denylist_is_layered_on_top(tmp_path: Path):
    """The committed file is the floor; the machine-local one adds to it."""
    local = tmp_path / "machine-local.denylist"
    local.write_text("apricotcorp\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "shipped.md").write_text("we use apricotcorp\n", encoding="utf-8")

    blocked = _run(str(tree), local_denylist=str(local))
    assert blocked.returncode == 1, blocked.stdout + blocked.stderr

    without = _run(str(tree))
    assert without.returncode == 0, (
        "the machine-local token must not be enforced when that denylist is "
        f"absent, or a fresh clone cannot commit:\n{without.stdout}"
    )


def test_the_committed_denylist_is_not_itself_scanned(tmp_path: Path):
    """The committed seam file necessarily contains the seams. Scanning it would
    make the gate trip on its own configuration, so it must sit outside every
    default tree."""
    result = _run()
    assert result.returncode == 0
    assert "leak-gate-seams" not in result.stdout
