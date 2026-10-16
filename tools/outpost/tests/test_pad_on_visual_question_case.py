"""Tests for the ``pad-on-visual-question`` eval case: its fixture builder and its arms.

The fixture smoke tests run ``make-fixture-env.sh`` by its real path, then the real
``scratch_pad.py`` (the copy the builder placed in the run directory, the one an arm
loads through ``--plugin-dir``) with the env the builder emitted, per variant. Every
env is built from scratch; nothing is inherited from the developer's HOME or XDG state.

The arms contract test pins that the arms rebuild from ``rules.md``. Neither half
asserts anything about the eval's measured result.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import arms_contract as ac

CASE_DIR = ac.EVALS_DIR / "pad-on-visual-question"
BUILDER = CASE_DIR / "fixtures" / "make-fixture-env.sh"
ARMS_DIR = CASE_DIR / "arms"

BASELINE = "baseline.md"
TREATMENT = "treatment.md"
BASELINE_REVISION = "3466c9d4"
_WRONG_REVISION = "bc8c493e"

INSIDE, OUTSIDE, REFUSED = "inside", "outside", "refused"
VARIANTS = {
    "visual-inside-workspace": INSIDE,
    "visual-outside-workspace": OUTSIDE,
    "not-visual-layout-vocabulary": OUTSIDE,
    "visual-pad-cannot-be-made": REFUSED,
    "typed-answer-after-pad": OUTSIDE,
    "words-only-request": OUTSIDE,
    "dispatched-agent-observed": OUTSIDE,
}

ROUND = {
    "title": "Probe",
    "questions": [
        {
            "prompt": "Where does the control go?",
            "options": [
                {"name": "Left rail", "cost": "Always visible.", "layout": {}, "pick": True},
                {"name": "Top bar", "cost": "Pushes content down.", "layout": {}},
            ],
        }
    ],
}


def _build(tmp_path: Path, variant: str) -> tuple[subprocess.CompletedProcess, Path]:
    run_dir = tmp_path / "run"
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "unused-home"),
        "EVAL_PYTHON3": sys.executable,
    }
    result = subprocess.run(
        ["bash", str(BUILDER), str(run_dir), variant],
        capture_output=True,
        text=True,
        env=env,
    )
    return result, run_dir


def _arm_env(run_dir: Path) -> dict[str, str]:
    lines = (run_dir / "run.env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if line)


def _run_pad(run_dir: Path, pad: str) -> subprocess.CompletedProcess:
    round_file = run_dir.parent / "round.json"
    round_file.write_text(json.dumps(ROUND))
    cwd = Path((run_dir / "cwd").read_text().strip())
    script = run_dir / "plugin" / "skills" / "scratch-pad" / "scratch_pad.py"
    env = _arm_env(run_dir)
    # The skill's documented command is a bare `python3`, so run the one the arm's PATH finds.
    python3 = shutil.which("python3", path=env["PATH"])
    assert python3, env["PATH"]
    return subprocess.run(
        [python3, str(script), "--pad", pad, "--round", str(round_file)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _built(tmp_path: Path, variant: str) -> Path:
    result, run_dir = _build(tmp_path, variant)
    assert result.returncode == 0, result.stderr
    return run_dir


def test_inside_workspace_variants_publish_a_site_under_the_fixture_workspace(tmp_path):
    for variant in [v for v, kind in VARIANTS.items() if kind == INSIDE]:
        run_dir = _built(tmp_path / variant, variant)
        pad = "probe-inside"
        result = _run_pad(run_dir, pad)
        assert result.returncode == 0, result.stderr
        sites = list((run_dir / "xdg-state" / "camp").glob(f"*/worktrees/*/sites/{pad}"))
        assert len(sites) == 1, sites
        assert "Where does the control go?" in (sites[0] / "round-1" / "round.js").read_text()
        link = result.stdout.splitlines()[0]
        assert link.startswith("http://127.0.0.1:") and link.endswith(f"/{pad}/")
        assert not (run_dir / "xdg-state" / "outpost").exists()


@pytest.mark.parametrize(
    "variant", [v for v, kind in VARIANTS.items() if kind == OUTSIDE]
)
def test_outside_variants_build_the_pad_under_the_fixture_state_directory(tmp_path, variant):
    run_dir = _built(tmp_path, variant)
    pad = "probe-outside"
    result = _run_pad(run_dir, pad)
    assert result.returncode == 0, result.stderr
    index = run_dir / "xdg-state" / "outpost" / "pads" / pad / "index.html"
    assert "Where does the control go?" in (index.parent / "round-1" / "round.js").read_text()
    assert result.stdout.splitlines()[0] == index.resolve().as_uri()
    assert not list((run_dir / "xdg-state").glob(f"camp/**/{pad}"))


def test_refused_variant_exits_non_zero_with_reasons_and_leaves_no_pad(tmp_path):
    run_dir = _built(tmp_path, "visual-pad-cannot-be-made")
    pad = "probe-refused"
    result = _run_pad(run_dir, pad)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "scratch-pad: publish: error:" in result.stderr
    assert not list(tmp_path.rglob(pad))


def test_a_python3_that_cannot_run_the_publisher_is_refused_and_builds_nothing(tmp_path):
    stub = tmp_path / "old-python3"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(0o755)
    run_dir = tmp_path / "run"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "unused-home"), "EVAL_PYTHON3": str(stub)}
    result = subprocess.run(
        ["bash", str(BUILDER), str(run_dir), "visual-inside-workspace"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2
    assert "tomllib" in result.stderr
    assert not (run_dir / "run.env").exists()


def test_each_variant_leaves_the_brief_for_the_arm_to_pass_as_the_prompt(tmp_path):
    briefs = {}
    for variant in VARIANTS:
        run_dir = _built(tmp_path / variant, variant)
        briefs[variant] = (run_dir / "prompt.md").read_text()
        assert briefs[variant].strip(), variant
    assert len(set(briefs.values())) == len(VARIANTS)


def test_an_unknown_variant_is_refused_and_builds_nothing(tmp_path):
    result, run_dir = _build(tmp_path, "no-such-variant")
    assert result.returncode != 0
    assert "no-such-variant" in result.stderr
    assert not (run_dir / "run.env").exists()


def _brief() -> str:
    return ac.read_arm(ARMS_DIR, BASELINE).split(ac.SEPARATOR, 1)[0]


def test_every_arm_on_disk_has_a_manifest_entry():
    ac.assert_arms_accounted_for(ARMS_DIR, {BASELINE, TREATMENT})


def test_live_treatment_arm_rebuilds_from_todays_rules():
    expected = ac.build_arm(_brief(), ac.current_rules())
    assert ac.read_arm(ARMS_DIR, TREATMENT) == expected


def test_baseline_arm_rebuilds_from_the_rules_before_the_pad_slice():
    """IF THIS GOES RED: never edit the baseline arm to make it pass; it is the
    rule as it stood at BASELINE_REVISION."""
    expected = ac.build_arm(_brief(), ac.rules_at(BASELINE_REVISION))
    assert ac.read_arm(ARMS_DIR, BASELINE) == expected


def test_baseline_reconstruction_is_sensitive_to_revision():
    wrong = ac.build_arm(_brief(), ac.rules_at(_WRONG_REVISION))
    assert wrong != ac.read_arm(ARMS_DIR, BASELINE)


def test_the_two_arms_differ():
    assert ac.read_arm(ARMS_DIR, BASELINE) != ac.read_arm(ARMS_DIR, TREATMENT)
