"""The opt-in test tiers, observed through pytest's own collection output.

Every check runs pytest as a subprocess against this repository's real config, because the
subject is what that config selects, not a function. Collection is serial (`-n 0`) and
`--collect-only`, so no test body runs.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from ._tier_runs import REPO, TIERS, run_pytest

ALL_TIERS_COMMAND = '-m "not benchmark"'
CLOSING_PREFIX = "deselected tiers: "


def _pytest(*args, cwd=REPO):
    return run_pytest(*args, cwd=cwd)


def _config_of(area: str) -> Path:
    return REPO / "pyproject.toml" if area == "root" else REPO / "tools" / area / "pyproject.toml"


def _scratch_project(tmp_path: Path, body: str) -> Path:
    (tmp_path / "test_scratch.py").write_text(textwrap.dedent(body))
    return tmp_path


def _collect_scratch(config: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return _pytest(
        "--collect-only", "-q", "-c", str(config), "--rootdir", str(tmp_path), str(tmp_path / "test_scratch.py"),
        cwd=tmp_path,
    )


@pytest.mark.parametrize("area", ["root", "craft", "lore", "camp"])
def test_a_misspelled_marker_fails_collection_under_each_config(area, tmp_path):
    _scratch_project(tmp_path, "import pytest\n\n@pytest.mark.chromum\ndef test_one():\n    pass\n")
    result = _collect_scratch(_config_of(area), tmp_path)
    assert result.returncode != 0
    assert "chromum" in result.stdout + result.stderr


@pytest.mark.parametrize("area", ["root", "craft", "lore", "camp"])
def test_a_test_left_unmarked_by_mistake_is_still_in_the_default_selection(area, tmp_path):
    _scratch_project(tmp_path, "def test_one():\n    pass\n")
    result = _collect_scratch(_config_of(area), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_scratch.py::test_one" in result.stdout


@pytest.mark.parametrize("area", ["root", "craft", "lore", "camp"])
def test_a_tier_marked_test_is_left_out_of_the_default_selection_under_each_config(area, tmp_path):
    _scratch_project(tmp_path, "import pytest\n\n@pytest.mark.seam_real\ndef test_one():\n    pass\n")
    default = _collect_scratch(_config_of(area), tmp_path)
    assert "test_scratch.py::test_one" not in default.stdout
    tier = _pytest(
        "--collect-only", "-q", "-c", str(_config_of(area)), "--rootdir", str(tmp_path), "-m", "seam_real",
        str(tmp_path / "test_scratch.py"), cwd=tmp_path,
    )
    assert "test_scratch.py::test_one" in tier.stdout


@pytest.mark.parametrize("area", ["craft", "lore", "camp"])
def test_a_path_scoped_run_registers_the_tier_markers(area):
    result = _pytest("--markers", f"tools/{area}/tests")
    assert result.returncode == 0
    for tier in TIERS:
        assert f"@pytest.mark.{tier}:" in result.stdout


def test_a_default_run_ends_by_naming_each_deselected_tier_and_the_all_tiers_command():
    result = _pytest("--collect-only", "-q", "trailhead/tests/test_paths.py")
    last = [line for line in result.stdout.splitlines() if line.strip()][-1]
    assert last == f"{CLOSING_PREFIX}{', '.join(TIERS)} — run them with .venv/bin/python -m pytest {ALL_TIERS_COMMAND}"


def test_the_all_tiers_command_ends_without_the_closing_line():
    result = _pytest("--collect-only", "-q", "-m", "not benchmark", "trailhead/tests/test_paths.py")
    assert CLOSING_PREFIX not in result.stdout


def test_the_closing_line_names_only_the_tiers_still_deselected():
    result = _pytest(
        "--collect-only", "-q", "-m", "not benchmark and not seam_real", "trailhead/tests/test_paths.py"
    )
    assert f"{CLOSING_PREFIX}seam_real " in result.stdout
    assert "chromium" not in result.stdout.split(CLOSING_PREFIX)[-1]


def test_a_tier_selection_that_matches_no_test_exits_nonzero():
    result = _pytest("-m", "chromium", "trailhead/tests/test_paths.py")
    assert result.returncode != 0


@pytest.mark.parametrize(
    "path",
    [
        "tools/craft/tests/test_found_work.py",
        "tools/lore/tests/test_lore_cli.py",
        "tools/camp/tests/test_activation.py",
    ],
)
def test_a_path_scoped_run_ends_with_the_same_closing_line(path):
    result = _pytest("--collect-only", "-q", path)
    last = [line for line in result.stdout.splitlines() if line.strip()][-1]
    assert last == f"{CLOSING_PREFIX}{', '.join(TIERS)} — run them with .venv/bin/python -m pytest {ALL_TIERS_COMMAND}"


def test_a_run_that_loads_a_member_conftest_prints_the_closing_line_once():
    result = _pytest("--collect-only", "-q", "trailhead/tests/test_paths.py", "tools/lore/tests/test_lore_cli.py")
    assert result.stdout.count(CLOSING_PREFIX) == 1
