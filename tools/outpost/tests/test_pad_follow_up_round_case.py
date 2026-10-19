"""Tests for the ``pad-follow-up-round`` eval case: its fixture builder.

The builder is run by its real path, then the real ``scratch_pad.py`` (the copy the builder
placed in the run directory, the one an arm loads through ``--plugin-dir``) is run with the env
the builder emitted. Nothing is inherited from the developer's HOME or XDG state. Nothing here
asserts anything about the eval's measured result.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import arms_contract as ac

CASE_DIR = ac.EVALS_DIR / "pad-follow-up-round"
BUILDER = CASE_DIR / "fixtures" / "make-fixture-env.sh"
VARIANTS = ("follow-up-same-design", "follow-up-new-topic")

ROUND_2 = {
    "title": "Audit log rows",
    "settled": ["Filters: left rail"],
    "questions": [
        {
            "prompt": "How does an entry look?",
            "options": [
                {"name": "One line", "cost": "Dense.", "layout": {}, "pick": True},
                {"name": "Card", "cost": "Roomy.", "layout": {}},
            ],
        }
    ],
}


def _build(tmp_path: Path, variant: str, skill: Path | None = None):
    run_dir = tmp_path / "run"
    args = ["bash", str(BUILDER), str(run_dir), variant] + ([str(skill)] if skill else [])
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "unused"), "EVAL_PYTHON3": sys.executable}
    return subprocess.run(args, capture_output=True, text=True, env=env), run_dir


def _arm_env(run_dir: Path) -> dict[str, str]:
    lines = (run_dir / "run.env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if line)


def _script(run_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = _arm_env(run_dir)
    script = run_dir / "plugin" / "skills" / "scratch-pad" / "scratch_pad.py"
    return subprocess.run(
        [str(run_dir / "bin" / "python3"), str(script), *args],
        cwd=Path((run_dir / "cwd").read_text().strip()),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _pad(run_dir: Path) -> Path:
    return run_dir / "xdg-state" / "outpost" / "pads" / "audit-filters"


@pytest.mark.parametrize("variant", VARIANTS)
def test_the_fixture_holds_a_one_round_pad_the_real_script_can_add_a_round_to(tmp_path, variant):
    result, run_dir = _build(tmp_path, variant)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in _pad(run_dir).glob("round-*")) == ["round-1"]
    round_file = tmp_path / "round-2.json"
    round_file.write_text(json.dumps(ROUND_2))
    added = _script(run_dir, "--pad", "audit-filters", "--round", str(round_file), "--add")
    assert added.returncode == 0, added.stderr
    assert sorted(p.name for p in _pad(run_dir).glob("round-*")) == ["round-1", "round-2"]
    assert "Filters: left rail" in (_pad(run_dir) / "round-2" / "round.js").read_text()


def test_each_variant_leaves_its_own_brief_as_the_prompt(tmp_path):
    prompts = {}
    for variant in VARIANTS:
        result, run_dir = _build(tmp_path / variant, variant)
        assert result.returncode == 0, result.stderr
        prompts[variant] = (run_dir / "prompt.md").read_text()
    assert len(set(prompts.values())) == len(VARIANTS)
    assert all("audit-filters" in p for p in prompts.values())


def test_a_skill_file_given_to_the_builder_replaces_the_plugin_copys_skill(tmp_path):
    skill = tmp_path / "other-skill.md"
    skill.write_text("---\nname: scratch-pad\n---\nother\n")
    result, run_dir = _build(tmp_path, VARIANTS[0], skill)
    assert result.returncode == 0, result.stderr
    assert (run_dir / "plugin" / "skills" / "scratch-pad" / "SKILL.md").read_text() == skill.read_text()
    assert (run_dir / "plugin" / "skills" / "scratch-pad" / "scratch_pad.py").is_file()


def test_the_baseline_skill_is_the_skill_before_rounds_were_described():
    before = ac.git_show("5512014a", "tools/outpost/plugins/outpost/skills/scratch-pad/SKILL.md")
    assert (CASE_DIR / "arms" / "baseline-SKILL.md").read_text() == before


def test_an_unknown_variant_or_a_missing_skill_file_is_refused_and_builds_nothing(tmp_path):
    result, run_dir = _build(tmp_path / "a", "no-such-variant")
    assert result.returncode == 2
    assert "no-such-variant" in result.stderr
    assert not (run_dir / "run.env").exists()
    result, run_dir = _build(tmp_path / "b", VARIANTS[0], tmp_path / "missing.md")
    assert result.returncode == 2
    assert not (run_dir / "run.env").exists()


def test_a_non_empty_run_directory_is_refused_and_left_untouched(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "keep").write_text("keep me")
    result, _ = _build(tmp_path, VARIANTS[0])
    assert result.returncode == 2
    assert [p.name for p in run_dir.iterdir()] == ["keep"]


def test_the_system_prompt_is_the_operating_brief_then_todays_rules():
    brief = ac.read_arm(CASE_DIR / "arms", "operating-brief.md")
    assert ac.read_arm(CASE_DIR / "arms", "system-prompt.md") == ac.build_arm(brief, ac.current_rules())
