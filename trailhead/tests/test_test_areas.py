"""scripts/test-area-args: the pytest arguments of each area of the default CI job."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ._tier_runs import REPO, run_pytest, tier_paths

SCRIPT = REPO / "scripts" / "test-area-args"


def _project(tmp_path: Path, testpaths: list[str]) -> Path:
    for name in ("one", "two", "three", "four"):
        (tmp_path / name).mkdir()
        (tmp_path / name / f"test_{name}.py").write_text(f"def test_{name}():\n    pass\n")
    listed = ", ".join(f'"{p}"' for p in testpaths)
    config = tmp_path / "pyproject.toml"
    config.write_text(
        f"[tool.pytest.ini_options]\ntestpaths = [{listed}]\n"
        '[tool.trailhead.test-areas]\nfirst = ["one"]\nsecond = ["two"]\n'
    )
    return config


def _selected(tmp_path: Path, config: Path, area: str) -> set[str]:
    args = subprocess.run(
        [sys.executable, str(SCRIPT), area, "--config", str(config)], capture_output=True, text=True, check=True
    ).stdout.split()
    result = run_pytest("--collect-only", "-q", "-c", str(config), "--rootdir", str(tmp_path), *args, cwd=tmp_path)
    return {line.split("::")[0] for line in result.stdout.splitlines() if "::" in line}


@pytest.mark.parametrize(
    ("area", "expected"),
    [("first", {"one/test_one.py"}), ("second", {"two/test_two.py"}), ("rest", {"three/test_three.py"})],
)
def test_an_area_selects_only_its_own_paths_and_rest_selects_the_remainder(tmp_path, area, expected):
    config = _project(tmp_path, ["one", "two", "three"])
    assert _selected(tmp_path, config, area) == expected


def test_a_path_added_to_testpaths_later_lands_in_rest_and_nowhere_else(tmp_path):
    config = _project(tmp_path, ["one", "two", "three", "four"])
    assert _selected(tmp_path, config, "rest") == {"three/test_three.py", "four/test_four.py"}
    assert "four/test_four.py" not in _selected(tmp_path, config, "first") | _selected(tmp_path, config, "second")


def test_an_unknown_area_exits_nonzero_naming_the_known_ones(tmp_path):
    config = _project(tmp_path, ["one"])
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "third", "--config", str(config)], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "first" in result.stderr and "rest" in result.stderr


def _tier_project(tmp_path: Path) -> Path:
    config = tmp_path / "pyproject.toml"
    config.write_text(
        '[tool.pytest.ini_options]\nmarkers = ["seam_real: x"]\ntestpaths = ["inside", "outside"]\n'
        '[tool.trailhead.test-areas]\nfirst = ["inside"]\n'
        '[tool.trailhead.test-tiers]\nseam_real = ["inside"]\n'
    )
    return config


def test_a_tier_names_the_paths_its_job_collects(tmp_path):
    config = _tier_project(tmp_path)
    assert tier_paths("seam_real", config) == ["inside"]


def test_an_unknown_tier_exits_nonzero_naming_the_known_ones(tmp_path):
    config = _tier_project(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--tier", "chromium", "--config", str(config)], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "seam_real" in result.stderr
