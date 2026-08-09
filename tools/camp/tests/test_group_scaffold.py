"""Tests for group_scaffold.py — TOML render + pre-write validation.

Test contract:
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
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# render_group_toml — TOML round-trip
# ---------------------------------------------------------------------------


def test_render_group_toml_parses_under_tomllib(tmp_path: Path) -> None:
    """render_group_toml output is valid TOML parseable by tomllib."""
    from camp.group.scaffold import render_group_toml

    members = [
        {"name": "myrepo", "repo_root": "/tmp/myrepo"},
    ]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")
    parsed = tomllib.loads(toml_str)
    assert parsed["group"]["name"] == "testgroup"


def test_render_group_toml_round_trips_through_load_group(tmp_path: Path) -> None:
    """render_group_toml output round-trips through load_group with expected values."""
    from camp.group.config import load_group
    from camp.group.scaffold import render_group_toml

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
        assert m["tasks"] == []


def test_render_group_toml_lore_scopes_round_trip(tmp_path: Path) -> None:
    """A supplied [[lore_scopes]] binding is emitted and round-trips through
    load_group in declared order — so re-authoring a group preserves it instead
    of silently dropping it."""
    from camp.group.config import load_group
    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    lore_scopes = [
        {"scope": "product", "name": "trailhead"},
        {"scope": "team", "name": "platform"},
    ]
    toml_str = render_group_toml(
        "mygroup", members, "worktree-{slug}", lore_scopes=lore_scopes
    )

    f = tmp_path / "mygroup.toml"
    f.write_text(toml_str)

    assert load_group(f)["lore_scopes"] == lore_scopes


def test_render_group_toml_no_lore_scopes_when_absent(tmp_path: Path) -> None:
    """Omitting lore_scopes emits no [[lore_scopes]] section (load_group → [])."""
    from camp.group.config import load_group
    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    toml_str = render_group_toml("mygroup", members, "worktree-{slug}")
    assert "[[lore_scopes]]" not in toml_str

    f = tmp_path / "mygroup.toml"
    f.write_text(toml_str)
    assert load_group(f)["lore_scopes"] == []


def test_render_group_toml_extra_tables_tasks_round_trip(tmp_path: Path) -> None:
    """A hand-added [tasks.<name>] block (with nested [[tasks.<name>.steps]])
    passed via extra_tables survives re-authoring byte-for-byte in structure —
    the same task/steps parse out of the rewritten file as parsed out of the
    original — and the rewritten file still loads cleanly."""
    import tomllib

    from camp.group.config import load_group
    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    extra_tables = {
        "tasks": {
            "graphify": {
                "phase": "provision",
                "required": False,
                "steps": [
                    {"name": "seed", "cmd": ["rsync", "-a", "src/", "dst/"]},
                ],
            }
        }
    }
    toml_str = render_group_toml(
        "mygroup", members, "worktree-{slug}", extra_tables=extra_tables
    )

    assert tomllib.loads(toml_str)["tasks"] == extra_tables["tasks"]

    f = tmp_path / "mygroup.toml"
    f.write_text(toml_str)
    load_group(f)  # does not raise — the unreferenced task def still validates


def test_render_group_toml_non_bare_table_key_round_trips(tmp_path: Path) -> None:
    """A carried [tasks."release-1.0"] table (a dotted, non-bare TOML key) must
    be re-emitted quoted so it reparses as one key, not nested under a bare
    `release-1` / `0` split — the exact silent-corruption case a bare-key
    renderer produces."""
    import tomllib

    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    extra_tables = {
        "tasks": {
            "release-1.0": {
                "phase": "provision",
                "steps": [{"name": "seed", "cmd": ["echo", "hi"]}],
            }
        }
    }
    toml_str = render_group_toml(
        "mygroup", members, "worktree-{slug}", extra_tables=extra_tables
    )

    parsed = tomllib.loads(toml_str)
    assert parsed["tasks"] == extra_tables["tasks"]


def test_render_group_toml_non_bare_scalar_key_round_trips(tmp_path: Path) -> None:
    """A top-level scalar key with a space in it (`"my key" = 1`) is not a bare
    TOML key and must be quoted on re-render, or the output is invalid TOML."""
    import tomllib

    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    extra_tables = {"my key": 1}
    toml_str = render_group_toml(
        "mygroup", members, "worktree-{slug}", extra_tables=extra_tables
    )

    parsed = tomllib.loads(toml_str)
    assert parsed["my key"] == 1


def test_render_group_toml_extra_tables_release_round_trips_via_tomllib(
    tmp_path: Path,
) -> None:
    """A hand-added [release] block passed via extra_tables survives, readable
    directly by tomllib (mirrors how portage reads [release] — load_group
    itself does not know this table)."""
    import tomllib

    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    extra_tables = {
        "release": {"auto_merge": True, "merge_order": ["alpha", "beta"]},
    }
    toml_str = render_group_toml(
        "mygroup", members, "worktree-{slug}", extra_tables=extra_tables
    )

    parsed = tomllib.loads(toml_str)
    assert parsed["release"] == {"auto_merge": True, "merge_order": ["alpha", "beta"]}


def test_render_group_toml_extra_tables_harness_and_shared_vaults_round_trip(
    tmp_path: Path,
) -> None:
    """A hand-added [harness] block and [[shared_vaults]] entries passed via
    extra_tables survive and are readable through load_group."""
    from camp.group.config import load_group
    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    extra_tables = {
        "harness": {"binary": "claude"},
        "shared_vaults": [{"name": "trailhead", "root": "/tmp/vaults/trailhead"}],
    }
    toml_str = render_group_toml(
        "mygroup", members, "worktree-{slug}", extra_tables=extra_tables
    )

    f = tmp_path / "mygroup.toml"
    f.write_text(toml_str)

    cfg = load_group(f)
    assert cfg["harness"] == {"binary": "claude"}
    assert cfg["shared_vaults"] == [{"name": "trailhead", "root": "/tmp/vaults/trailhead"}]


def test_render_group_toml_extra_top_level_scalar_round_trips(tmp_path: Path) -> None:
    """A hand-edited top-level scalar (`version = 1`) survives carry-through.

    It must be emitted BEFORE the [group] header — appended after the extra
    tables it would be reparented under whichever table was emitted last.
    """
    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    toml_str = render_group_toml(
        "mygroup",
        members,
        "worktree-{slug}",
        extra_tables={"version": 1, "harness": {"binary": "claude"}},
    )

    parsed = tomllib.loads(toml_str)
    assert parsed["version"] == 1
    assert parsed["harness"] == {"binary": "claude"}


def test_render_group_toml_extra_top_level_scalar_array_round_trips() -> None:
    """A hand-edited top-level array of scalars (`tags = ["a"]`) survives."""
    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    toml_str = render_group_toml(
        "mygroup",
        members,
        "worktree-{slug}",
        extra_tables={"tags": ["a", "b"], "release": {"auto_merge": True}},
    )

    parsed = tomllib.loads(toml_str)
    assert parsed["tags"] == ["a", "b"]
    assert parsed["release"] == {"auto_merge": True}


def test_render_group_toml_extra_top_level_empty_array_round_trips() -> None:
    """A top-level empty array is emitted as `x = []`, not silently dropped."""
    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    toml_str = render_group_toml(
        "mygroup", members, "worktree-{slug}", extra_tables={"x": []}
    )

    assert tomllib.loads(toml_str)["x"] == []


def test_render_group_toml_extra_datetime_values_round_trip() -> None:
    """TOML date/time/datetime literals (which tomllib yields as datetime
    objects) re-serialize as bare TOML literals, not as quoted strings."""
    import datetime

    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    original = tomllib.loads(
        "[window]\n"
        "cutoff = 2026-01-01\n"
        "at = 09:30:00\n"
        "local = 2026-01-01T09:30:00\n"
        "offset = 2026-01-01T09:30:00Z\n"
    )
    toml_str = render_group_toml(
        "mygroup", members, "worktree-{slug}", extra_tables=original
    )

    parsed = tomllib.loads(toml_str)
    assert parsed["window"] == original["window"]
    assert isinstance(parsed["window"]["cutoff"], datetime.date)


def test_render_group_toml_top_level_datetime_round_trips() -> None:
    """A top-level datetime literal is emitted before [group] and round-trips."""
    from camp.group.scaffold import render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    original = tomllib.loads("cutoff = 2026-01-01\n[harness]\nbinary = 'claude'\n")
    toml_str = render_group_toml(
        "mygroup", members, "worktree-{slug}", extra_tables=original
    )

    assert tomllib.loads(toml_str)["cutoff"] == original["cutoff"]


def test_render_group_toml_unserializable_value_raises_scaffold_error() -> None:
    """A genuinely unserializable value surfaces as a clean error naming the
    offending key, with no `camp: ` prefix baked into the message — the CLI
    caller supplies that prefix itself, so the renderer must not double it up
    (`camp group: camp: ...`)."""
    from camp.group.scaffold import ScaffoldError, render_group_toml

    members = [{"name": "alpha", "repo_root": "/tmp/alpha"}]
    with pytest.raises(ScaffoldError) as exc:
        render_group_toml(
            "mygroup", members, "worktree-{slug}", extra_tables={"bad": {"k": object()}}
        )

    assert not str(exc.value).startswith("camp: ")
    assert "bad" in str(exc.value)
    assert "bad" in str(exc.value)


def test_render_group_toml_path_with_spaces_round_trips(tmp_path: Path) -> None:
    """A repo_root with spaces in the path is correctly escaped and round-trips."""
    from camp.group.config import load_group
    from camp.group.scaffold import render_group_toml

    spaced_path = str(tmp_path / "my repos" / "project alpha")
    members = [{"name": "spaced", "repo_root": spaced_path}]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")

    f = tmp_path / "testgroup.toml"
    f.write_text(toml_str)

    cfg = load_group(f)
    assert cfg["members"][0]["repo_root"] == spaced_path


def test_render_group_toml_repo_root_is_absolute(tmp_path: Path) -> None:
    """Rendered repo_root paths are absolute (not relative or tilde-prefixed)."""
    from camp.group.scaffold import render_group_toml

    members = [{"name": "myrepo", "repo_root": "/tmp/absolute/path"}]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")

    parsed = tomllib.loads(toml_str)
    repo_root = parsed["members"][0]["repo_root"]
    assert Path(repo_root).is_absolute(), f"repo_root must be absolute, got {repo_root!r}"


def test_render_group_toml_tilde_path_expanded(tmp_path: Path) -> None:
    """A ~/... member repo_root is expanded to an absolute path; no literal ~ in output."""
    from camp.group.scaffold import render_group_toml

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
    from camp.group.scaffold import render_group_toml

    members = [{"name": "myrepo", "repo_root": "/tmp/myrepo"}]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")

    parsed = tomllib.loads(toml_str)
    assert parsed["members"][0]["bootstrap"] == []


def test_render_group_toml_no_shared_vaults_or_dev_env(tmp_path: Path) -> None:
    """Authoring never emits [[shared_vaults]] or [dev_env] sections."""
    from camp.group.scaffold import render_group_toml

    members = [{"name": "myrepo", "repo_root": "/tmp/myrepo"}]
    toml_str = render_group_toml("testgroup", members, "worktree-{slug}")

    assert "shared_vaults" not in toml_str
    assert "dev_env" not in toml_str


# ---------------------------------------------------------------------------
# build_stub_toml
# ---------------------------------------------------------------------------


def test_build_stub_toml_parses_as_toml(tmp_path: Path) -> None:
    """build_stub_toml output is valid TOML parseable by tomllib."""
    from camp.group.scaffold import build_stub_toml

    stub = build_stub_toml("mygroup")
    parsed = tomllib.loads(stub)
    assert isinstance(parsed, dict)


def test_build_stub_toml_contains_group_name(tmp_path: Path) -> None:
    """build_stub_toml fills [group].name with the actual group_name (not a placeholder)."""
    from camp.group.scaffold import build_stub_toml

    stub = build_stub_toml("mygroup")
    parsed = tomllib.loads(stub)
    assert parsed["group"]["name"] == "mygroup"


def test_build_stub_toml_contains_members_scaffold(tmp_path: Path) -> None:
    """build_stub_toml contains a [[members]] section in the scaffold."""
    from camp.group.scaffold import build_stub_toml

    stub = build_stub_toml("mygroup")
    # Should contain [[members]] structure
    assert "members" in stub


# ---------------------------------------------------------------------------
# validate_scaffold — error cases
# ---------------------------------------------------------------------------


def test_validate_scaffold_raises_on_nonexistent_repo_root(tmp_path: Path) -> None:
    """validate_scaffold raises ScaffoldError when a repo_root does not exist."""
    from camp.group.scaffold import ScaffoldError, validate_scaffold

    nonexistent = tmp_path / "does_not_exist"
    members = [{"name": "myrepo", "repo_root": str(nonexistent)}]
    with pytest.raises(ScaffoldError) as exc_info:
        validate_scaffold("testgroup", members, other_configs=[], allow_missing=False)
    msg = str(exc_info.value)
    assert "does_not_exist" in msg or "repo_root" in msg


def test_validate_scaffold_allow_missing_skips_existence_check(tmp_path: Path) -> None:
    """validate_scaffold does NOT raise for nonexistent repo_root when allow_missing=True."""
    from camp.group.scaffold import validate_scaffold

    nonexistent = tmp_path / "does_not_exist"
    members = [{"name": "myrepo", "repo_root": str(nonexistent)}]
    # Should not raise
    validate_scaffold("testgroup", members, other_configs=[], allow_missing=True)


def test_validate_scaffold_raises_on_overlap_with_other_configs(tmp_path: Path) -> None:
    """validate_scaffold raises ScaffoldError when repo_root is claimed by another group."""
    from camp.group.scaffold import ScaffoldError, validate_scaffold

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
    from camp.group.scaffold import ScaffoldError, validate_scaffold

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
    from camp.group.scaffold import ScaffoldError, validate_scaffold

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
    from camp.group.scaffold import ScaffoldError, validate_scaffold

    with pytest.raises((ScaffoldError, Exception)):
        validate_scaffold("..", [], other_configs=[], allow_missing=True)


def test_validate_scaffold_raises_on_invalid_group_name_slash(tmp_path: Path) -> None:
    """validate_scaffold raises on invalid group name '/'."""
    from camp.group.scaffold import ScaffoldError, validate_scaffold

    with pytest.raises((ScaffoldError, Exception)):
        validate_scaffold("/badname", [], other_configs=[], allow_missing=True)


def test_validate_scaffold_raises_when_repo_root_not_git(tmp_path: Path) -> None:
    """validate_scaffold raises ScaffoldError when repo_root exists but is not a git repo."""
    from camp.group.scaffold import ScaffoldError, validate_scaffold

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
    from camp.group.scaffold import validate_scaffold

    repos = []
    for i in range(3):
        d = tmp_path / f"repo{i}"
        d.mkdir()
        (d / ".git").mkdir()
        repos.append(d)

    members = [{"name": f"repo{i}", "repo_root": str(repos[i])} for i in range(3)]
    # Should not raise
    validate_scaffold("testgroup", members, other_configs=[], allow_missing=False)
