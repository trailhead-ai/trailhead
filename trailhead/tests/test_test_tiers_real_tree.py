"""The opt-in tiers and the CI areas over this repository's real test tree.

Each check collects the whole tree several times, which costs seconds per collection, so these
run in the `seam_real` tier instead of the default run.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from ._tier_runs import REPO, TIERS, collected_ids, tier_ids_outside_its_paths

pytestmark = pytest.mark.seam_real

AREAS = ("craft", "lore-camp", "rest")


def _area_ids(area):
    args = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "test-area-args"), area],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return collected_ids("-c", "pyproject.toml", *args)


@pytest.mark.parametrize("tier", TIERS)
def test_default_selection_leaves_a_tier_out_and_the_tier_selection_finds_it(tier):
    selected = collected_ids("-m", tier)
    assert selected, f"no test carries the {tier} marker"
    assert not collected_ids() & selected


def test_every_collected_test_is_in_exactly_one_place():
    everything = collected_ids("-m", "")
    places = {
        "default": collected_ids(),
        "benchmark": collected_ids("-m", "benchmark"),
        **{t: collected_ids("-m", t) for t in TIERS},
    }
    assert frozenset().union(*places.values()) == everything
    assert sum(len(ids) for ids in places.values()) == len(everything)


def test_the_ci_areas_together_select_exactly_the_default_selection_with_no_overlap():
    areas = {area: _area_ids(area) for area in AREAS}
    assert all(areas.values())
    assert frozenset().union(*areas.values()) == collected_ids()
    assert sum(len(ids) for ids in areas.values()) == len(collected_ids())


@pytest.mark.parametrize(
    ("area", "tier"), [("craft", "seam_real"), ("lore", "seam_real"), ("outpost", "chromium")]
)
def test_a_path_scoped_run_honours_the_exclusion(area, tier):
    path = f"tools/{area}/tests"
    selected = collected_ids(path, "-m", tier)
    assert selected, f"{path} holds no {tier} test"
    assert not collected_ids(path) & selected


@pytest.mark.parametrize("tier", TIERS)
def test_every_test_of_a_tier_is_inside_the_paths_its_ci_job_collects(tier):
    assert tier_ids_outside_its_paths(tier, REPO / "pyproject.toml", REPO) == frozenset()
