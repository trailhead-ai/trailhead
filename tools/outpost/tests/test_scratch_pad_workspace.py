"""Behavioral tests for ``scratch_pad.enclosing_workspace``.

The function names the camp workspace a working directory sits inside, by
path arithmetic against camp's state root, so the pad script can tell when it
runs inside a workspace. Camp's own resolver and the publisher's state-root
mirror are loaded by path so the copies cannot drift apart.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import trailhead.paths as trailhead_paths

TOOLS = Path(__file__).parent.parent.parent
SCRIPT_PATH = TOOLS / "outpost/plugins/outpost/skills/scratch-pad/scratch_pad.py"
PUBLISH_PATH = TOOLS / "outpost/plugins/outpost/skills/publish-site/publish_site.py"
INJECT_PATH = TOOLS / "camp/plugins/camp/camp/launch/inject.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pad():
    return _load("scratch_pad", SCRIPT_PATH)


@pytest.fixture
def camp(tmp_path):
    root = tmp_path / "camp-state"
    root.mkdir()
    return root, {"CAMP_STATE_DIR": str(root), "HOME": str(tmp_path / "home")}


def _member(root: Path, group="grp", slug="ws", member="repo") -> Path:
    d = root / group / "worktrees" / slug / member
    d.mkdir(parents=True)
    return d


def test_workspace_root_member_and_deep_dirs_give_the_workspace(pad, camp):
    root, env = camp
    member = _member(root)
    deep = member / "a" / "b" / "c"
    deep.mkdir(parents=True)
    expected = root.resolve() / "grp" / "worktrees" / "ws"
    for cwd in (expected, member, deep):
        assert pad.enclosing_workspace(cwd, env) == expected


def test_dirs_that_are_not_inside_a_workspace_give_none(pad, camp, tmp_path):
    root, env = camp
    _member(root)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for cwd in (root / "grp" / "worktrees", root / "grp", root, outside):
        assert pad.enclosing_workspace(cwd, env) is None, cwd


def test_a_group_without_the_worktrees_segment_gives_none(pad, camp):
    root, env = camp
    d = root / "grp" / "other" / "ws" / "repo"
    d.mkdir(parents=True)
    assert pad.enclosing_workspace(d, env) is None


def test_symlink_from_outside_into_a_member_resolves_to_the_workspace(pad, camp, tmp_path):
    root, env = camp
    member = _member(root)
    link = tmp_path / "link"
    link.symlink_to(member)
    assert pad.enclosing_workspace(link, env) == root.resolve() / "grp" / "worktrees" / "ws"


def test_symlink_from_a_workspace_to_outside_gives_none(pad, camp, tmp_path):
    root, env = camp
    member = _member(root)
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = member / "escape"
    link.symlink_to(target)
    assert pad.enclosing_workspace(link, env) is None


def test_symlinked_state_root_yields_the_resolved_workspace_path(pad, tmp_path):
    real = tmp_path / "real-state"
    (real / "grp" / "worktrees" / "ws" / "repo").mkdir(parents=True)
    alias = tmp_path / "alias-state"
    alias.symlink_to(real)
    env = {"CAMP_STATE_DIR": str(alias), "HOME": str(tmp_path / "home")}
    cwd = alias / "grp" / "worktrees" / "ws" / "repo"
    assert pad.enclosing_workspace(cwd, env) == real.resolve() / "grp" / "worktrees" / "ws"


def _env_table(tmp_path):
    return [
        {"CAMP_STATE_DIR": str(tmp_path / "custom"), "HOME": str(tmp_path / "home")},
        {
            "CAMP_STATE_DIR": str(tmp_path / "custom"),
            "XDG_STATE_HOME": str(tmp_path / "xdg"),
            "HOME": str(tmp_path / "home"),
        },
        {"XDG_STATE_HOME": str(tmp_path / "xdg"), "HOME": str(tmp_path / "home")},
        {"HOME": str(tmp_path / "home")},
    ]


def test_each_state_root_source_is_honoured_and_agrees_with_trailhead(pad, tmp_path):
    for env in _env_table(tmp_path):
        expected = trailhead_paths.state_dir("camp", env=env, platform=sys.platform)
        member = expected / "grp" / "worktrees" / "ws" / "repo"
        member.mkdir(parents=True, exist_ok=True)
        assert pad._camp_state_root(env) == expected, env
        assert pad.enclosing_workspace(member, env) == expected.resolve() / "grp" / "worktrees" / "ws", env


def test_state_root_agrees_with_the_publisher_mirror(pad, tmp_path):
    publisher = _load("publish_site", PUBLISH_PATH)
    for env in _env_table(tmp_path):
        assert pad._camp_state_root(env) == publisher._camp_state_root(env), env


def test_names_the_same_group_and_slug_as_camp(pad, camp, tmp_path):
    inject = _load("camp_inject", INJECT_PATH)
    root, env = camp
    member = _member(root, "g1", "s1")
    (root / "g2" / "worktrees").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(member)
    cwds = [
        root / "g1" / "worktrees" / "s1",
        member,
        member.parent,
        root / "g2" / "worktrees",
        root / "g1",
        root,
        outside,
        link,
    ]
    for cwd in cwds:
        theirs = inject.resolve_group_slug_from_cwd(cwd, root)
        ours = pad.enclosing_workspace(cwd, env)
        if theirs is None:
            assert ours is None, cwd
        else:
            group, slug = theirs
            assert ours == root.resolve() / group / "worktrees" / slug, cwd
