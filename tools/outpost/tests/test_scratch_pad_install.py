"""Seam smoke: composing the outpost plugin installs a working scratch-pad skill.

The composition runs through trailhead's own ``compose_plan``/``apply_plan`` into
a ``tmp_path`` target, and the installed script runs with an injected state dir,
so nothing here touches a live install or the real Outpost state.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest
from trailhead.compose import apply_plan, compose_plan

OUTPOST_TOOL = Path(__file__).parent.parent
SOURCE_KIT = OUTPOST_TOOL / "plugins" / "outpost" / "skills" / "scratch-pad" / "kit"
FONT = "ArchitectsDaughter-Regular.ttf"

ROUND = {
    "title": "Settings page",
    "questions": [
        {
            "prompt": "Header layout",
            "options": [
                {"name": "Split", "cost": "One more component", "pick": True, "layout": {"rule": True}},
                {"name": "Stacked", "cost": "Simplest", "pick": False, "layout": {"rule": True}},
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def _outside_any_workspace(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)


def _compose(tool_dir: Path, dest: Path, skills=("scratch-pad",)) -> Path:
    manifest = load_manifest(tool_dir / "capabilities.toml")
    apply_plan(compose_plan(manifest, None, {name: None for name in skills}, dest))
    return dest / "skills" / "scratch-pad"


def _differing_kit_files(installed_skill: Path) -> list[str]:
    return sorted(
        src.name
        for src in SOURCE_KIT.iterdir()
        if not (installed_skill / "kit" / src.name).is_file()
        or (installed_skill / "kit" / src.name).read_bytes() != src.read_bytes()
    )


def _run_installed(installed_skill: Path, tmp_path: Path, state_dir: Path):
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(ROUND))
    env = {
        **os.environ,
        "OUTPOST_STATE_DIR": str(state_dir),
        "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
        "CAMP_CONFIG_DIR": str(tmp_path / "camp-config"),
    }
    return subprocess.run(
        [
            sys.executable,
            str(installed_skill / "scratch_pad.py"),
            "--pad",
            "settings-header",
            "--round",
            str(round_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_composed_scratch_pad_builds_a_pad_with_byte_identical_kit(tmp_path):
    installed = _compose(OUTPOST_TOOL, tmp_path / "composed")

    assert _differing_kit_files(installed) == []

    state = tmp_path / "state"
    result = _run_installed(installed, tmp_path, state)

    assert result.returncode == 0, result.stderr
    pad = state / "pads" / "settings-header"
    assert result.stdout.splitlines()[0] == (pad / "index.html").as_uri()
    for src in SOURCE_KIT.iterdir():
        assert (pad / "round-1" / src.name).read_bytes() == src.read_bytes(), src.name
    assert "Header layout" in (pad / "round-1" / "round.js").read_text()


def test_corrupted_source_font_fails_the_byte_comparison(tmp_path):
    tool_copy = tmp_path / "outpost-copy"
    shutil.copytree(OUTPOST_TOOL, tool_copy, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    font = tool_copy / "plugins" / "outpost" / "skills" / "scratch-pad" / "kit" / FONT
    font.write_bytes(font.read_bytes()[:-1] + b"\x00")

    installed = _compose(tool_copy, tmp_path / "composed")

    assert _differing_kit_files(installed) == [FONT]


def test_seam_workspace_publish(tmp_path):  # inert-gate: allow seam smoke, installed publisher
    installed = _compose(OUTPOST_TOOL, tmp_path / "composed", ("scratch-pad", "publish-site"))
    camp_root = tmp_path / "camp-state"
    member = camp_root / "grp" / "worktrees" / "ws" / "repo"
    member.mkdir(parents=True)
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(ROUND))
    env = {
        **os.environ,
        "CAMP_STATE_DIR": str(camp_root),
        "CAMP_CONFIG_DIR": str(tmp_path / "camp-config"),
        "OUTPOST_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
    }

    result = subprocess.run(
        [sys.executable, str(installed / "scratch_pad.py"), "--pad", "settings-header", "--round", str(round_path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=member,
    )

    site = camp_root.resolve() / "grp" / "worktrees" / "ws" / "sites" / "settings-header"
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "http://127.0.0.1:7314/grp-ws/settings-header/"
    assert lines[-1] == f"pad folder: {site}"
    assert "Header layout" in (site / "round-1" / "round.js").read_text()
    assert not (tmp_path / "state" / "pads").exists()
