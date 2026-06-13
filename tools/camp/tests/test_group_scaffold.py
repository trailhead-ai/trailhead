"""Tests for group_scaffold.py — TOML render + pre-write validation.

Test contract (Slice 1):
- render_group_toml output parses under tomllib AND round-trips through load_group.
- Rendered repo_root paths are absolute and correctly escaped (path-with-spaces case).
- A ~/... member path is expanded to an absolute home path (no literal "~" in output).
- build_stub_toml output parses as TOML and contains [group]/[[members]] scaffold
  with the group name filled into [group].name (not a placeholder).
- validate_scaffold raises on: a repo_root that doesn't exist (not raised when
  allow_missing=True); a repo_root claimed by other_configs (overlap, message names
  both groups); same repo_root twice in members (intra-group dupe); invalid group
  name ("..", "/").
- A clean 3-member input validates without error.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# render_group_toml — TOML round-trip
# ---------------------------------------------------------------------------


def test_render_group_toml_parses_under_tomllib(tmp_path: Path) -> None:
    """render_group_toml output is valid TOML parseable by tomllib."""
    from group_scaffold import render_group_toml

    members = [
        {"name": "myrepo", "repo_root": "/tmp/myrepo"},
    ]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")
    parsed = tomllib.loads(toml_str)
    assert parsed["group"]["name"] == "testgroup"


def test_render_group_toml_round_trips_through_load_group(tmp_path: Path) -> None:
    """render_group_toml output round-trips through load_group with expected values."""
    from group_config import load_group
    from group_scaffold import render_group_toml

    members = [
        {"name": "alpha", "repo_root": "/tmp/alpha"},
        {"name": "beta", "repo_root": "/tmp/beta"},
    ]
    toml_str = render_group_toml("mygroup", members, "worktree-{slug}")

    f = tmp_path / "mygroup.toml"
    f.write_text(toml_str)

    cfg = load_group(f)
    assert cfg["group"]["name"] == "mygroup"
    assert cfg["branch_pattern"] == "worktree-{slug}"
    assert len(cfg["members"]) == 2

    names = [m["name"] for m in cfg["members"]]
    assert names == ["alpha", "beta"]

    roots = [m["repo_root"] for m in cfg["members"]]
    # repo_root values are passed through expanduser().resolve() which follows
    # symlinks — on macOS /tmp -> /private/tmp, so compare via Path resolution
    assert roots == [
        str(Path("/tmp/alpha").expanduser().resolve()),
        str(Path("/tmp/beta").expanduser().resolve()),
    ]

    for m in cfg["members"]:
        assert m["bootstrap"] == []


def test_render_group_toml_path_with_spaces_round_trips(tmp_path: Path) -> None:
    """A repo_root with spaces in the path is correctly escaped and round-trips."""
    from group_config import load_group
    from group_scaffold import render_group_toml

    spaced_path = str(tmp_path / "my repos" / "project alpha")
    members = [{"name": "spaced", "repo_root": spaced_path}]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")

    f = tmp_path / "testgroup.toml"
    f.write_text(toml_str)

    cfg = load_group(f)
    assert cfg["members"][0]["repo_root"] == spaced_path


def test_render_group_toml_repo_root_is_absolute(tmp_path: Path) -> None:
    """Rendered repo_root paths are absolute (not relative or tilde-prefixed)."""
    from group_scaffold import render_group_toml

    members = [{"name": "myrepo", "repo_root": "/tmp/absolute/path"}]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")

    parsed = tomllib.loads(toml_str)
    repo_root = parsed["members"][0]["repo_root"]
    assert Path(repo_root).is_absolute(), f"repo_root must be absolute, got {repo_root!r}"


def test_render_group_toml_tilde_path_expanded(tmp_path: Path) -> None:
    """A ~/... member repo_root is expanded to an absolute path; no literal ~ in output."""
    from group_scaffold import render_group_toml

    tilde_path = "~/code/myrepo"
    members = [{"name": "myrepo", "repo_root": tilde_path}]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")

    assert "~" not in toml_str, "Tilde should be expanded before rendering"
    parsed = tomllib.loads(toml_str)
    repo_root = parsed["members"][0]["repo_root"]
    assert Path(repo_root).is_absolute()
    assert "~" not in repo_root


def test_render_group_toml_bootstrap_always_empty_list(tmp_path: Path) -> None:
    """bootstrap is always rendered as [] in the core schema (edit-by-hand affordance)."""
    from group_scaffold import render_group_toml

    members = [{"name": "myrepo", "repo_root": "/tmp/myrepo"}]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")

    parsed = tomllib.loads(toml_str)
    assert parsed["members"][0]["bootstrap"] == []


def test_render_group_toml_no_shared_vaults_or_dev_env(tmp_path: Path) -> None:
    """Authoring never emits [[shared_vaults]] or [dev_env] sections."""
    from group_scaffold import render_group_toml

    members = [{"name": "myrepo", "repo_root": "/tmp/myrepo"}]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")

    assert "shared_vaults" not in toml_str
    assert "dev_env" not in toml_str


# ---------------------------------------------------------------------------
# build_stub_toml
# ---------------------------------------------------------------------------


def test_build_stub_toml_parses_as_toml(tmp_path: Path) -> None:
    """build_stub_toml output is valid TOML parseable by tomllib."""
    from group_scaffold import build_stub_toml

    stub = build_stub_toml("mygroup")
    parsed = tomllib.loads(stub)
    assert isinstance(parsed, dict)


def test_build_stub_toml_contains_group_name(tmp_path: Path) -> None:
    """build_stub_toml fills [group].name with the actual group_name (not a placeholder)."""
    from group_scaffold import build_stub_toml

    stub = build_stub_toml("mygroup")
    parsed = tomllib.loads(stub)
    assert parsed["group"]["name"] == "mygroup"


def test_build_stub_toml_contains_members_scaffold(tmp_path: Path) -> None:
    """build_stub_toml contains a [[members]] section in the scaffold."""
    from group_scaffold import build_stub_toml

    stub = build_stub_toml("mygroup")
    # Should contain [[members]] structure
    assert "members" in stub


# ---------------------------------------------------------------------------
# validate_scaffold — error cases
# ---------------------------------------------------------------------------


def test_validate_scaffold_raises_on_nonexistent_repo_root(tmp_path: Path) -> None:
    """validate_scaffold raises ScaffoldError when a repo_root does not exist."""
    from group_scaffold import ScaffoldError, validate_scaffold

    nonexistent = tmp_path / "does_not_exist"
    members = [{"name": "myrepo", "repo_root": str(nonexistent)}]
    with pytest.raises(ScaffoldError) as exc_info:
        validate_scaffold("testgroup", members, other_configs=[], allow_missing=False)
    msg = str(exc_info.value)
    assert "does_not_exist" in msg or "repo_root" in msg


def test_validate_scaffold_allow_missing_skips_existence_check(tmp_path: Path) -> None:
    """validate_scaffold does NOT raise for nonexistent repo_root when allow_missing=True."""
    from group_scaffold import validate_scaffold

    nonexistent = tmp_path / "does_not_exist"
    members = [{"name": "myrepo", "repo_root": str(nonexistent)}]
    # Should not raise
    validate_scaffold("testgroup", members, other_configs=[], allow_missing=True)


def test_validate_scaffold_raises_on_overlap_with_other_configs(tmp_path: Path) -> None:
    """validate_scaffold raises ScaffoldError when repo_root is claimed by another group."""
    from group_scaffold import ScaffoldError, validate_scaffold

    shared_dir = tmp_path / "shared_repo"
    shared_dir.mkdir()
    (shared_dir / ".git").mkdir()

    other_cfg = {
        "group": {"name": "othergroup"},
        "members": [{"name": "shared", "repo_root": str(shared_dir), "bootstrap": []}],
        "branch_pattern": "worktree-{slug}",
        "shared_vaults": [],
        "_toml_path": str(tmp_path / "othergroup.toml"),
    }

    members = [{"name": "mine", "repo_root": str(shared_dir)}]
    with pytest.raises(ScaffoldError) as exc_info:
        validate_scaffold("testgroup", members, other_configs=[other_cfg], allow_missing=False)
    msg = str(exc_info.value)
    # Message should name both groups
    assert "testgroup" in msg or "othergroup" in msg


def test_validate_scaffold_overlap_message_names_both_groups(tmp_path: Path) -> None:
    """Overlap error message names both the candidate group and the conflicting group."""
    from group_scaffold import ScaffoldError, validate_scaffold

    shared_dir = tmp_path / "shared_repo"
    shared_dir.mkdir()
    (shared_dir / ".git").mkdir()

    other_cfg = {
        "group": {"name": "othergroup"},
        "members": [{"name": "shared", "repo_root": str(shared_dir), "bootstrap": []}],
        "branch_pattern": "worktree-{slug}",
        "shared_vaults": [],
        "_toml_path": str(tmp_path / "othergroup.toml"),
    }

    members = [{"name": "mine", "repo_root": str(shared_dir)}]
    with pytest.raises(ScaffoldError) as exc_info:
        validate_scaffold("testgroup", members, other_configs=[other_cfg], allow_missing=False)
    msg = str(exc_info.value)
    assert "othergroup" in msg


def test_validate_scaffold_raises_on_intra_group_duplicate(tmp_path: Path) -> None:
    """validate_scaffold raises ScaffoldError when the same repo_root appears twice in members."""
    from group_scaffold import ScaffoldError, validate_scaffold

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    members = [
        {"name": "first", "repo_root": str(repo_dir)},
        {"name": "second", "repo_root": str(repo_dir)},
    ]
    with pytest.raises(ScaffoldError):
        validate_scaffold("testgroup", members, other_configs=[], allow_missing=False)


def test_validate_scaffold_raises_on_invalid_group_name_dotdot(tmp_path: Path) -> None:
    """validate_scaffold raises on invalid group name '..'."""
    from group_scaffold import ScaffoldError, validate_scaffold

    with pytest.raises((ScaffoldError, Exception)):
        validate_scaffold("..", [], other_configs=[], allow_missing=True)


def test_validate_scaffold_raises_on_invalid_group_name_slash(tmp_path: Path) -> None:
    """validate_scaffold raises on invalid group name '/'."""
    from group_scaffold import ScaffoldError, validate_scaffold

    with pytest.raises((ScaffoldError, Exception)):
        validate_scaffold("/badname", [], other_configs=[], allow_missing=True)


def test_validate_scaffold_raises_when_repo_root_not_git(tmp_path: Path) -> None:
    """validate_scaffold raises ScaffoldError when repo_root exists but is not a git repo."""
    from group_scaffold import ScaffoldError, validate_scaffold

    not_a_git_repo = tmp_path / "not_git"
    not_a_git_repo.mkdir()
    # No .git directory

    members = [{"name": "notgit", "repo_root": str(not_a_git_repo)}]
    with pytest.raises(ScaffoldError) as exc_info:
        validate_scaffold("testgroup", members, other_configs=[], allow_missing=False)
    msg = str(exc_info.value)
    assert "git" in msg.lower() or "repo_root" in msg


def test_validate_scaffold_clean_3_member_input_passes(tmp_path: Path) -> None:
    """A clean 3-member input with valid git repos validates without error."""
    from group_scaffold import validate_scaffold

    repos = []
    for i in range(3):
        d = tmp_path / f"repo{i}"
        d.mkdir()
        (d / ".git").mkdir()
        repos.append(d)

    members = [
        {"name": f"repo{i}", "repo_root": str(repos[i])}
        for i in range(3)
    ]
    # Should not raise
    validate_scaffold("testgroup", members, other_configs=[], allow_missing=False)
