"""Tests for bin/camp's interpreter selection against the declared requires-python floor.

Test contract:
- Given a PATH whose only `python3` is below the floor and a second, satisfying
  interpreter present elsewhere on PATH, the launcher invokes the satisfying one.
- Given a PATH whose only interpreter is below the floor and no satisfying one
  available, the launcher exits non-zero with a message naming both the required
  floor and the version it found.
- Given a satisfying bare `python3`, the launcher invokes it unchanged.
- The floor is read from the repository's declared `requires-python` rather than
  hardcoded a second time — varying the declared value moves the accepted set.

Each fixture builds a throwaway `tools/camp/{pyproject.toml,plugins/camp/{bin,cli}}`
tree, copies the real `bin/camp` source into it unmodified, and puts fake
interpreter stubs (plain shell scripts answering `--version` and otherwise
recording that they ran) on a synthetic PATH. Real Python is never depended on
for version selection — only `bash` and the stub scripts run.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_REAL_BIN_CAMP = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
_BASH = shutil.which("bash")
assert _BASH is not None, "bash must be on PATH to run these tests"

# The real coreutils bin/camp needs for its own path resolution (dirname,
# readlink) plus grep, without exposing the real python3 that lives alongside
# them on the developer's PATH — that would silently satisfy the floor in the
# "no satisfying interpreter available" case and falsify the test.
_COREUTILS = ["dirname", "readlink", "grep", "head"]


def _coreutils_dir(tmp_path_factory_root: Path) -> Path:
    utils_dir = tmp_path_factory_root / "_coreutils"
    if utils_dir.exists():
        return utils_dir
    utils_dir.mkdir(parents=True)
    for name in _COREUTILS:
        real = shutil.which(name)
        assert real is not None, f"{name} must be on PATH to run these tests"
        (utils_dir / name).symlink_to(real)
    return utils_dir


def _write_stub_interpreter(dir_: Path, *, version: str, marker: str) -> Path:
    """Write a fake `python3` stub in *dir_* that answers --version and, for any
    other invocation, prints a line starting with `RAN:<marker>` followed by its
    argv so a test can tell which stub was actually exec'd as the interpreter.
    """
    dir_.mkdir(parents=True, exist_ok=True)
    stub = dir_ / "python3"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            if [ "$1" = "--version" ]; then
                echo "Python {version}"
                exit 0
            fi
            echo "RAN:{marker}:$@"
            exit 0
            """
        )
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _build_fixture(tmp_path: Path, *, requires_python: str) -> tuple[Path, Path]:
    """Build a throwaway tools/camp tree and return (bin_camp_path, pyproject_path)."""
    tool_dir = tmp_path / "tools" / "camp"
    plugin_dir = tool_dir / "plugins" / "camp"
    bin_dir = plugin_dir / "bin"
    cli_dir = plugin_dir / "cli"
    bin_dir.mkdir(parents=True)
    cli_dir.mkdir(parents=True)

    pyproject = tool_dir / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            f"""\
            [project]
            name = "camp"
            version = "0.1.0"
            requires-python = "{requires_python}"
            """
        )
    )

    (cli_dir / "camp").write_text("# dummy cli entry point, never actually run by these tests\n")

    bin_camp = bin_dir / "camp"
    bin_camp.write_text(_REAL_BIN_CAMP.read_text())
    bin_camp.chmod(bin_camp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return bin_camp, pyproject


def _run(
    bin_camp: Path,
    path_dirs: list[Path],
    *,
    coreutils_root: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    coreutils = _coreutils_dir(coreutils_root)
    path_value = ":".join(str(d) for d in [*path_dirs, coreutils])
    env = {"PATH": path_value}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, str(bin_camp), "--help"],
        capture_output=True,
        text=True,
        env=env,
    )


def _build_claude_plugin_root_fixture(
    tmp_path: Path, *, requires_python: str | None
) -> tuple[Path, Path]:
    """Build a composed ${CLAUDE_PLUGIN_ROOT}-shaped plugin tree — cli/camp present
    directly under the root, no bin/ sibling required since that branch never
    self-resolves. *requires_python* of None omits pyproject.toml entirely
    (from the root and every ancestor up to the search bound), reproducing the
    real composed-install case where no such file exists to find. Returns
    (bin_camp_path, plugin_root_path).
    """
    plugin_root = tmp_path / "composed" / "plugins" / "camp"
    cli_dir = plugin_root / "cli"
    cli_dir.mkdir(parents=True)
    cli_camp = cli_dir / "camp"
    cli_camp.write_text("# dummy cli entry point, never actually run by these tests\n")
    cli_camp.chmod(cli_camp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    if requires_python is not None:
        (plugin_root / "pyproject.toml").write_text(
            textwrap.dedent(
                f"""\
                [project]
                name = "camp"
                version = "0.1.0"
                requires-python = "{requires_python}"
                """
            )
        )

    bin_camp = tmp_path / "bin_camp_copy"
    bin_camp.write_text(_REAL_BIN_CAMP.read_text())
    bin_camp.chmod(bin_camp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return bin_camp, plugin_root


def test_satisfying_interpreter_elsewhere_on_path_is_invoked_over_low_bare_python3(
    tmp_path: Path,
) -> None:
    bin_camp, _ = _build_fixture(tmp_path, requires_python=">=3.11")

    low_dir = tmp_path / "low-bin"
    high_dir = tmp_path / "high-bin"
    _write_stub_interpreter(low_dir, version="3.9.6", marker="low")
    _write_stub_interpreter(high_dir, version="3.14.0", marker="high")

    result = _run(bin_camp, [low_dir, high_dir], coreutils_root=tmp_path)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "RAN:high:" in result.stdout, result.stdout
    assert "RAN:low:" not in result.stdout, result.stdout


def test_low_bare_python3_is_invoked_when_it_comes_first_and_satisfies_after_search(
    tmp_path: Path,
) -> None:
    """Swap the PATH order from the previous test: the satisfying interpreter now
    comes first. Invocation must track which interpreter satisfies, not merely
    which directory comes first on PATH.
    """
    bin_camp, _ = _build_fixture(tmp_path, requires_python=">=3.11")

    low_dir = tmp_path / "low-bin"
    high_dir = tmp_path / "high-bin"
    _write_stub_interpreter(low_dir, version="3.9.6", marker="low")
    _write_stub_interpreter(high_dir, version="3.14.0", marker="high")

    result = _run(bin_camp, [high_dir, low_dir], coreutils_root=tmp_path)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "RAN:high:" in result.stdout, result.stdout
    assert "RAN:low:" not in result.stdout, result.stdout


def test_no_satisfying_interpreter_exits_nonzero_naming_floor_and_found_version(
    tmp_path: Path,
) -> None:
    bin_camp, _ = _build_fixture(tmp_path, requires_python=">=3.11")

    low_dir = tmp_path / "low-bin"
    _write_stub_interpreter(low_dir, version="3.9.6", marker="low")

    result = _run(bin_camp, [low_dir], coreutils_root=tmp_path)

    assert result.returncode != 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "3.11" in result.stderr, result.stderr
    assert "3.9" in result.stderr, result.stderr


def test_satisfying_bare_python3_is_invoked_unchanged(tmp_path: Path) -> None:
    bin_camp, _ = _build_fixture(tmp_path, requires_python=">=3.11")

    only_dir = tmp_path / "only-bin"
    _write_stub_interpreter(only_dir, version="3.12.0", marker="ok")

    result = _run(bin_camp, [only_dir], coreutils_root=tmp_path)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "RAN:ok:" in result.stdout, result.stdout


def test_interpreter_exactly_at_the_floor_is_invoked(tmp_path: Path) -> None:
    """The floor is inclusive: a version equal to (not just above) the declared
    floor must satisfy it.
    """
    bin_camp, _ = _build_fixture(tmp_path, requires_python=">=3.11")

    only_dir = tmp_path / "only-bin"
    _write_stub_interpreter(only_dir, version="3.11.0", marker="exact")

    result = _run(bin_camp, [only_dir], coreutils_root=tmp_path)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "RAN:exact:" in result.stdout, result.stdout


def test_floor_moves_with_the_declared_requires_python_not_a_hardcoded_copy(
    tmp_path: Path,
) -> None:
    """The same PATH (a single 3.11 interpreter) is accepted when the declared
    floor is below it and rejected when the declared floor is raised above it —
    pinning that the floor is read from pyproject.toml, not a second hardcoded
    constant in the launcher.
    """
    mid_dir = tmp_path / "mid-bin"
    _write_stub_interpreter(mid_dir, version="3.11.0", marker="mid")

    low_floor_bin, _ = _build_fixture(tmp_path / "low-floor", requires_python=">=3.9")
    accepted = _run(low_floor_bin, [mid_dir], coreutils_root=tmp_path)
    assert accepted.returncode == 0, f"stdout: {accepted.stdout}\nstderr: {accepted.stderr}"
    assert "RAN:mid:" in accepted.stdout, accepted.stdout

    high_floor_bin, _ = _build_fixture(tmp_path / "high-floor", requires_python=">=3.13")
    rejected = _run(high_floor_bin, [mid_dir], coreutils_root=tmp_path)
    assert rejected.returncode != 0, f"stdout: {rejected.stdout}\nstderr: {rejected.stderr}"
    assert "3.13" in rejected.stderr, rejected.stderr
    assert "3.11" in rejected.stderr, rejected.stderr


def test_claude_plugin_root_branch_rejects_below_floor_and_selects_satisfying(
    tmp_path: Path,
) -> None:
    """The ${CLAUDE_PLUGIN_ROOT} resolution branch (Resolution order #1) must
    apply the same floor check as the self-relative branch — a composed plugin
    tree with a discoverable pyproject.toml rejects a below-floor bare python3
    and selects a satisfying interpreter found elsewhere on PATH.
    """
    bin_camp, plugin_root = _build_claude_plugin_root_fixture(
        tmp_path, requires_python=">=3.11"
    )

    low_dir = tmp_path / "low-bin"
    high_dir = tmp_path / "high-bin"
    _write_stub_interpreter(low_dir, version="3.9.6", marker="low")
    _write_stub_interpreter(high_dir, version="3.14.0", marker="high")

    result = _run(
        bin_camp,
        [low_dir, high_dir],
        coreutils_root=tmp_path,
        extra_env={"CLAUDE_PLUGIN_ROOT": str(plugin_root)},
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "RAN:high:" in result.stdout, result.stdout
    assert "RAN:low:" not in result.stdout, result.stdout


def test_claude_plugin_root_branch_with_no_discoverable_floor_notices_and_proceeds(
    tmp_path: Path,
) -> None:
    """A composed plugin tree with no pyproject.toml in any ancestor up to the
    search bound must not silently skip the floor check — it prints a stderr
    notice naming that it could not determine the required version, then
    proceeds on whatever python3 resolves to (not a hard failure).
    """
    bin_camp, plugin_root = _build_claude_plugin_root_fixture(
        tmp_path, requires_python=None
    )

    only_dir = tmp_path / "only-bin"
    _write_stub_interpreter(only_dir, version="3.9.6", marker="whatever")

    result = _run(
        bin_camp,
        [only_dir],
        coreutils_root=tmp_path,
        extra_env={"CLAUDE_PLUGIN_ROOT": str(plugin_root)},
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "could not determine" in result.stderr.lower(), result.stderr
    assert "RAN:whatever:" in result.stdout, result.stdout


def test_declaration_far_above_the_plugin_root_is_not_adopted_as_the_floor(
    tmp_path: Path,
) -> None:
    """An unrelated pyproject.toml well above the plugin root must not supply the
    floor. The upward search exists to find the launcher's OWN declaration, which
    sits close by; reaching far enough to pick up a stranger's file lets any
    writable ancestor directory decide which interpreter camp accepts — either
    silently lowering the floor this check exists to enforce, or raising it until
    camp refuses to run at all.
    """
    bin_camp, plugin_root = _build_claude_plugin_root_fixture(tmp_path, requires_python=None)

    # Three levels above the plugin root (camp -> plugins -> composed -> tmp_path):
    # far outside anything the launcher's own install owns.
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "somebody-elses-project"
            version = "0.1.0"
            requires-python = ">=3.99"
            """
        )
    )

    interp_dir = tmp_path / "some-bin"
    _write_stub_interpreter(interp_dir, version="3.12.0", marker="present")

    result = _run(
        bin_camp,
        [interp_dir],
        coreutils_root=tmp_path,
        extra_env={"CLAUDE_PLUGIN_ROOT": str(plugin_root)},
    )

    assert "3.99" not in result.stderr, (
        "the stranger's declaration was adopted as the floor: " + result.stderr
    )
    assert "could not determine" in result.stderr.lower(), result.stderr
    assert "RAN:present:" in result.stdout, f"stdout: {result.stdout}\nstderr: {result.stderr}"
