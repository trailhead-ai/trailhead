"""Tests for bin/trailhead's interpreter selection against the declared requires-python floor.

Each fixture copies the real ``bin/trailhead`` and ``bin/_trailhead.py`` unmodified
into a throwaway ``<root>/{pyproject.toml,bin/}`` tree and puts fake ``python3``
stubs (shell scripts answering ``--version`` and otherwise recording that they ran)
on a synthetic PATH. The launcher is executed directly by path, so its shebang is
part of what is under test. Real Python is never on the synthetic PATH.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_BIN = _REPO_ROOT / "bin"

# What the launcher needs for its own resolution plus the shebang's `env bash`,
# without exposing the real python3 that lives beside them on a developer's PATH.
_TOOLS = ["bash", "dirname", "readlink", "grep", "head"]


def _tools_dir(root: Path) -> Path:
    tools = root / "_tools"
    if tools.exists():
        return tools
    tools.mkdir(parents=True)
    for name in _TOOLS:
        real = shutil.which(name)
        assert real is not None, f"{name} must be on PATH to run these tests"
        (tools / name).symlink_to(real)
    return tools


def _stub(dir_: Path, *, version: str, marker: str) -> Path:
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


def _fixture(tmp_path: Path, *, requires_python: str | None) -> Path:
    """Build <root>/{pyproject.toml,bin/} and return the copied bin/trailhead."""
    root = tmp_path / "checkout"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    body = '[project]\nname = "trailhead"\nversion = "0.1.0"\n'
    if requires_python is not None:
        body += f'requires-python = "{requires_python}"\n'
    (root / "pyproject.toml").write_text(body)
    for name in ("trailhead", "_trailhead.py"):
        shutil.copyfile(_REAL_BIN / name, bin_dir / name)
    launcher = bin_dir / "trailhead"
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


def _run(
    launcher: Path, path_dirs: list[Path], *, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    path = ":".join(str(d) for d in [*path_dirs, _tools_dir(tmp_path)])
    return subprocess.run(
        [str(launcher), "--flag", "arg"],
        capture_output=True,
        text=True,
        env={"PATH": path},
    )


def test_later_interpreter_meeting_the_floor_runs_the_shim_with_args(tmp_path: Path) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")
    low = _stub(tmp_path / "low-bin", version="3.9.6", marker="low").parent
    high = _stub(tmp_path / "high-bin", version="3.14.0", marker="high").parent

    result = _run(launcher, [low, high], tmp_path=tmp_path)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    shim = launcher.parent / "_trailhead.py"
    assert f"RAN:high:{shim} --flag arg" in result.stdout, result.stdout
    assert "RAN:low:" not in result.stdout, result.stdout


def test_only_below_floor_interpreter_exits_nonzero_with_a_clean_message(tmp_path: Path) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")
    low = _stub(tmp_path / "low-bin", version="3.9.6", marker="low").parent

    result = _run(launcher, [low], tmp_path=tmp_path)

    assert result.returncode == 1, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stderr.strip() == "trailhead: requires Python >=3.11, found Python 3.9", (
        result.stderr
    )
    assert "Traceback" not in result.stderr
    assert "RAN:" not in result.stdout


def test_no_python3_at_all_reports_none_found(tmp_path: Path) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")

    result = _run(launcher, [], tmp_path=tmp_path)

    assert result.returncode == 1, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stderr.strip() == "trailhead: requires Python >=3.11, found Python none", (
        result.stderr
    )


def test_floor_is_read_from_the_declaration_not_hardcoded(tmp_path: Path) -> None:
    only = _stub(tmp_path / "only-bin", version="3.14.0", marker="only").parent

    raised = _fixture(tmp_path / "raised", requires_python=">=3.99")
    refused = _run(raised, [only], tmp_path=tmp_path)
    assert refused.returncode == 1, f"stdout: {refused.stdout}\nstderr: {refused.stderr}"
    assert "3.99" in refused.stderr, refused.stderr
    assert "RAN:" not in refused.stdout, refused.stdout

    lowered_only = _stub(tmp_path / "old-bin", version="3.9.6", marker="old").parent
    lowered = _fixture(tmp_path / "lowered", requires_python=">=3.9")
    accepted = _run(lowered, [lowered_only], tmp_path=tmp_path)
    assert accepted.returncode == 0, f"stdout: {accepted.stdout}\nstderr: {accepted.stderr}"
    assert "RAN:old:" in accepted.stdout, accepted.stdout


def test_missing_floor_declaration_notices_then_runs_first_python3(tmp_path: Path) -> None:
    launcher = _fixture(tmp_path, requires_python=None)
    first = _stub(tmp_path / "first-bin", version="3.9.6", marker="first").parent
    second = _stub(tmp_path / "second-bin", version="3.14.0", marker="second").parent

    result = _run(launcher, [first, second], tmp_path=tmp_path)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stderr.startswith("trailhead:"), result.stderr
    assert "could not determine the required Python version" in result.stderr, result.stderr
    assert "RAN:first:" in result.stdout, result.stdout
    assert "RAN:second:" not in result.stdout, result.stdout


def test_first_interpreter_meeting_the_floor_wins_over_a_later_one(tmp_path: Path) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")
    high = _stub(tmp_path / "high-bin", version="3.14.0", marker="high").parent
    low = _stub(tmp_path / "low-bin", version="3.9.6", marker="low").parent

    result = _run(launcher, [high, low], tmp_path=tmp_path)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "RAN:high:" in result.stdout, result.stdout
    assert "RAN:low:" not in result.stdout, result.stdout


def _broken_stub(dir_: Path) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    stub = dir_ / "python3"
    stub.write_text("#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then exit 1; fi\necho \"RAN:broken:$@\"\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


@pytest.mark.parametrize("link_kind", ["absolute", "relative"])
def test_launcher_invoked_through_a_symlink_resolves_the_real_shim(
    tmp_path: Path, link_kind: str
) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")
    high = _stub(tmp_path / "high-bin", version="3.14.0", marker="high").parent
    link_dir = tmp_path / "elsewhere" / "links"
    link_dir.mkdir(parents=True)
    link = link_dir / "trailhead-link"
    target = launcher if link_kind == "absolute" else Path(os.path.relpath(launcher, link_dir))
    link.symlink_to(target)

    result = _run(link, [high], tmp_path=tmp_path)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    shim = launcher.parent / "_trailhead.py"
    assert f"RAN:high:{shim} --flag arg" in result.stdout, result.stdout


def test_interpreter_whose_version_probe_fails_is_skipped_not_recorded(tmp_path: Path) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")
    low = _stub(tmp_path / "low-bin", version="3.9.6", marker="low").parent
    broken = _broken_stub(tmp_path / "broken-bin").parent

    refused = _run(launcher, [low, broken], tmp_path=tmp_path)

    assert refused.returncode == 1, f"stdout: {refused.stdout}\nstderr: {refused.stderr}"
    assert refused.stderr.strip() == "trailhead: requires Python >=3.11, found Python 3.9", (
        refused.stderr
    )

    high = _stub(tmp_path / "high-bin", version="3.14.0", marker="high").parent
    accepted = _run(launcher, [broken, high], tmp_path=tmp_path)

    assert accepted.returncode == 0, f"stdout: {accepted.stdout}\nstderr: {accepted.stderr}"
    assert "RAN:high:" in accepted.stdout, accepted.stdout
    assert "RAN:broken:" not in accepted.stdout, accepted.stdout


def test_higher_major_version_satisfies_a_floor_with_a_higher_minor(tmp_path: Path) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")
    four = _stub(tmp_path / "four-bin", version="4.0.0", marker="four").parent

    result = _run(launcher, [four], tmp_path=tmp_path)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "RAN:four:" in result.stdout, result.stdout


def _probe_stub(dir_: Path, *, version: str, marker: str, touched: Path) -> Path:
    """A stub that records any invocation, --version included, by creating ``touched``."""
    dir_.mkdir(parents=True, exist_ok=True)
    stub = dir_ / "python3"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            : > "{touched}"
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


def _run_in(
    launcher: Path | str,
    path_entries: list[str],
    *,
    tmp_path: Path,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    path = ":".join([*path_entries, str(_tools_dir(tmp_path))])
    return subprocess.run(
        [str(launcher), "--flag", "arg"],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={"PATH": path, **(extra_env or {})},
    )


_REFUSED_39 = "trailhead: requires Python >=3.11, found Python 3.9"


def test_relative_path_entry_is_never_probed_or_run(tmp_path: Path) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")
    low = _stub(tmp_path / "low-bin", version="3.9.6", marker="low").parent
    cwd = tmp_path / "cwd"
    touched = tmp_path / "rel-touched"
    _probe_stub(cwd, version="3.99", marker="rel", touched=touched)

    result = _run_in(launcher, [str(low), "."], tmp_path=tmp_path, cwd=cwd)

    assert result.returncode == 1, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stderr.strip() == _REFUSED_39, result.stderr
    assert "RAN:rel:" not in result.stdout, result.stdout
    assert not touched.exists(), "relative PATH entry's python3 was invoked"


def test_absolute_entry_after_a_skipped_relative_one_still_wins(tmp_path: Path) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")
    low = _stub(tmp_path / "low-bin", version="3.9.6", marker="low").parent
    high = _stub(tmp_path / "high-bin", version="3.14.0", marker="high").parent
    cwd = tmp_path / "cwd"
    touched = tmp_path / "rel-touched"
    _probe_stub(cwd, version="3.99", marker="rel", touched=touched)

    result = _run_in(launcher, [str(low), ".", str(high)], tmp_path=tmp_path, cwd=cwd)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "RAN:high:" in result.stdout, result.stdout
    assert "RAN:rel:" not in result.stdout, result.stdout
    assert not touched.exists(), "relative PATH entry's python3 was invoked"


@pytest.mark.parametrize("entry_kind", ["relative-glob", "absolute-glob"])
def test_glob_characters_in_a_path_entry_are_not_expanded(
    tmp_path: Path, entry_kind: str
) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")
    low = _stub(tmp_path / "low-bin", version="3.9.6", marker="low").parent
    touched = tmp_path / "glob-touched"
    _probe_stub(tmp_path / "gl", version="3.99", marker="glob", touched=touched)
    entry = "g*" if entry_kind == "relative-glob" else f"{tmp_path}/g*"

    result = _run_in(launcher, [str(low), entry], tmp_path=tmp_path, cwd=tmp_path)

    assert result.returncode == 1, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stderr.strip() == _REFUSED_39, result.stderr
    assert "RAN:glob:" not in result.stdout, result.stdout
    assert not touched.exists(), "glob-expanded PATH entry's python3 was invoked"


def test_exported_cdpath_does_not_break_relative_invocation(tmp_path: Path) -> None:
    launcher = _fixture(tmp_path, requires_python=">=3.11")
    high = _stub(tmp_path / "high-bin", version="3.14.0", marker="high").parent

    result = _run_in(
        "bin/trailhead",
        [str(high)],
        tmp_path=tmp_path,
        cwd=launcher.parent.parent,
        extra_env={"CDPATH": "."},
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "cd:" not in result.stderr, result.stderr
    shim = launcher.parent / "_trailhead.py"
    assert f"RAN:high:{shim} --flag arg" in result.stdout, result.stdout
