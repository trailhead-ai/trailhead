"""The opt-in test tiers, observed through pytest's own collection output.

Every check runs pytest as a subprocess against this repository's real config, because the
subject is what that config selects, not a function. Collection is serial (`-n 0`) and
`--collect-only`, so no test body runs.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TIERS = ("chromium", "seam_real")
ALL_TIERS_COMMAND = '-m "not benchmark"'
CLOSING_PREFIX = "deselected tiers: "
_ID = re.compile(r"^\S+\.py::\S")


def _pytest(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n", "0", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _ids(*args: str) -> set[str]:
    result = _pytest("--collect-only", "-q", *args)
    assert result.returncode in (0, 5), result.stdout + result.stderr
    return {line for line in result.stdout.splitlines() if _ID.match(line)}


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


@pytest.mark.parametrize("tier", TIERS)
def test_default_selection_leaves_a_tier_out_and_the_tier_selection_finds_it(tier):
    default = _ids()
    selected = _ids("-m", tier)
    assert selected, f"no test carries the {tier} marker"
    assert not default & selected


def test_every_collected_test_is_in_exactly_one_place():
    everything = _ids("-m", "")
    places = {"default": _ids(), "benchmark": _ids("-m", "benchmark"), **{t: _ids("-m", t) for t in TIERS}}
    assert set().union(*places.values()) == everything
    assert sum(len(ids) for ids in places.values()) == len(everything)


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


@pytest.mark.parametrize(
    ("area", "tier"), [("craft", "seam_real"), ("lore", "seam_real"), ("outpost", "chromium")]
)
def test_a_path_scoped_run_honours_the_exclusion(area, tier):
    path = f"tools/{area}/tests"
    default = _ids(path)
    selected = _ids(path, "-m", tier)
    assert selected, f"{path} holds no {tier} test"
    assert not default & selected


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
