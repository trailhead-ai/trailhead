"""Tests for group_config.py — tomllib loader + schema validation.

Test contract (Slice 1):
- Loads a valid trailhead.toml config.
- A malformed config (missing required field) → error naming file + failing field.
- A malformed config (bad type) → error naming file + failing field.
- bootstrap commands are parsed as a LIST (not a shell string) for shell=False.
- [dev_env] block present → warn-and-continue (prints deferred note, does not crash).
- No group config file → legible first-run scaffold/point message.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
_GROUPS_EXAMPLE_DIR = _REPO_ROOT / "tools" / "camp" / "groups.example"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Valid config loads
# ---------------------------------------------------------------------------

_VALID_TOML = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
bootstrap = ["pip", "install", "-e", "."]

[branch]
pattern = "worktree-{slug}"
"""

_VALID_TOML_NO_BOOTSTRAP = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
"""


def test_load_valid_config(tmp_path: Path) -> None:
    """Loads a valid TOML config and returns a structured dict."""
    from group_config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML)
    cfg = load_group(f)
    assert cfg["group"]["name"] == "testgroup"
    assert len(cfg["members"]) == 1
    assert cfg["members"][0]["name"] == "myrepo"
    assert cfg["members"][0]["repo_root"] == "/tmp/myrepo"


def test_load_bootstrap_is_list(tmp_path: Path) -> None:
    """bootstrap is parsed as a list (for subprocess shell=False), not a shell string."""
    from group_config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML)
    cfg = load_group(f)
    bootstrap = cfg["members"][0]["bootstrap"]
    assert isinstance(bootstrap, list), "bootstrap must be a list"
    assert bootstrap == ["pip", "install", "-e", "."]


def test_load_bootstrap_defaults_to_empty_list(tmp_path: Path) -> None:
    """When bootstrap is absent, it defaults to []."""
    from group_config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_NO_BOOTSTRAP)
    cfg = load_group(f)
    assert cfg["members"][0]["bootstrap"] == []


def test_load_branch_pattern_defaults(tmp_path: Path) -> None:
    """When [branch] is absent, branch_pattern defaults to 'worktree-{slug}'."""
    from group_config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_NO_BOOTSTRAP)
    cfg = load_group(f)
    assert cfg["branch_pattern"] == "worktree-{slug}"


# ---------------------------------------------------------------------------
# Per-member branch base (Slice 2)
# ---------------------------------------------------------------------------


def test_member_base_defaults_to_origin_main(tmp_path: Path) -> None:
    """When a member omits `base`, it defaults to 'origin/main'."""
    from group_config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_NO_BOOTSTRAP)
    cfg = load_group(f)
    assert cfg["members"][0]["base"] == "origin/main"


def test_member_base_override_honored(tmp_path: Path) -> None:
    """A per-member `base` string overrides the default."""
    from group_config import load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
base = "origin/trunk"
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    cfg = load_group(f)
    assert cfg["members"][0]["base"] == "origin/trunk"


def test_member_base_non_string_errors(tmp_path: Path) -> None:
    """A non-string `base` → error naming the file + field."""
    from group_config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
base = 42
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    assert "base" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Malformed config → field-named errors
# ---------------------------------------------------------------------------

_MISSING_GROUP_NAME = """\
[group]
# name is missing

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
"""

_MISSING_MEMBER_REPO_ROOT = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
# repo_root is missing
"""

_MISSING_MEMBER_NAME = """\
[group]
name = "testgroup"

[[members]]
repo_root = "/tmp/myrepo"
"""

_BOOTSTRAP_NOT_LIST = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
bootstrap = "pip install -e ."
"""


def test_missing_group_name_errors_with_field(tmp_path: Path) -> None:
    """Missing group.name → error naming file + failing field."""
    from group_config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_MISSING_GROUP_NAME)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "name" in msg or "group" in msg


def test_missing_member_repo_root_errors_with_field(tmp_path: Path) -> None:
    """Missing member repo_root → error naming file + failing field."""
    from group_config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_MISSING_MEMBER_REPO_ROOT)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "repo_root" in msg


def test_missing_member_name_errors_with_field(tmp_path: Path) -> None:
    """Missing member name → error naming file + failing field."""
    from group_config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_MISSING_MEMBER_NAME)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "name" in msg


def test_bootstrap_not_list_errors_with_field(tmp_path: Path) -> None:
    """bootstrap as a string (not a list) → error naming file + failing field."""
    from group_config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_BOOTSTRAP_NOT_LIST)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "bootstrap" in msg


# ---------------------------------------------------------------------------
# [dev_env] block → warn-and-continue
# ---------------------------------------------------------------------------

_CONFIG_WITH_DEV_ENV = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[dev_env]
port_base = 4100
"""


def test_dev_env_block_warns_and_continues(tmp_path: Path, capsys) -> None:
    """[dev_env] block present → prints deferred note, does NOT raise."""
    from group_config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_CONFIG_WITH_DEV_ENV)
    cfg = load_group(f)  # must not raise
    assert cfg["group"]["name"] == "testgroup"

    captured = capsys.readouterr()
    # The warning is printed to stderr
    assert "dev_env" in captured.err or "dev-env" in captured.err
    # Must mention "deferred" or "not yet supported"
    assert "deferred" in captured.err or "not yet supported" in captured.err


# ---------------------------------------------------------------------------
# No config file → first-run scaffold message
# ---------------------------------------------------------------------------


def test_no_config_file_first_run_message(tmp_path: Path) -> None:
    """When no group config file exists, a legible first-run message is returned."""
    from group_config import GroupConfigNotFound, load_group

    missing = tmp_path / "nonexistent.toml"
    with pytest.raises(GroupConfigNotFound) as exc_info:
        load_group(missing)
    msg = str(exc_info.value)
    # Must mention the expected path
    assert "nonexistent.toml" in msg or str(missing) in msg
    # Must point at groups.example or copy instruction
    assert "groups.example" in msg or "copy" in msg.lower() or "example" in msg


# ---------------------------------------------------------------------------
# load_all_groups — scans the groups dir and loads every .toml
# ---------------------------------------------------------------------------


def test_load_all_groups_empty_dir(tmp_path: Path) -> None:
    """load_all_groups on an empty dir returns empty list."""
    from group_config import load_all_groups

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    result = load_all_groups(groups_dir)
    assert result == []


def test_load_all_groups_loads_files(tmp_path: Path) -> None:
    """load_all_groups loads all .toml files in the directory."""
    from group_config import load_all_groups

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    (groups_dir / "alpha.toml").write_text(
        '[group]\nname = "alpha"\n\n[[members]]\nname = "r"\nrepo_root = "/tmp/r"\n'
    )
    (groups_dir / "beta.toml").write_text(
        '[group]\nname = "beta"\n\n[[members]]\nname = "r"\nrepo_root = "/tmp/r2"\n'
    )
    result = load_all_groups(groups_dir)
    names = [c["group"]["name"] for c in result]
    assert sorted(names) == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# groups.example/trailhead.toml — verify it loads
# ---------------------------------------------------------------------------


def test_groups_example_trailhead_toml_exists() -> None:
    """The groups.example/trailhead.toml example file exists."""
    assert (_GROUPS_EXAMPLE_DIR / "trailhead.toml").is_file(), (
        f"groups.example/trailhead.toml not found at {_GROUPS_EXAMPLE_DIR}"
    )


def test_groups_example_trailhead_toml_loads() -> None:
    """The groups.example/trailhead.toml example loads as the 3-member fleet group."""
    from group_config import load_group

    f = _GROUPS_EXAMPLE_DIR / "trailhead.toml"
    if not f.is_file():
        pytest.skip("groups.example/trailhead.toml not yet created")
    cfg = load_group(f)
    assert cfg["group"]["name"] == "trailhead"
    # The trailhead fleet group spans exactly three sibling repos.
    member_names = {m["name"] for m in cfg["members"]}
    assert member_names == {"trailhead", "trailhead-ai.github.io", "outpost"}
    # Must NOT carry a [dev_env] block (per D-D). load_group strips dev_env via
    # warn-and-continue, so `"dev_env" not in cfg` is trivially true — assert on
    # the SOURCE TEXT instead so the check actually proves the block is absent.
    # Match an actual table header (a line that is exactly `[dev_env]`), not the
    # substring, which legitimately appears in the explanatory comment.
    header_lines = {ln.strip() for ln in f.read_text().splitlines()}
    assert "[dev_env]" not in header_lines


# ---------------------------------------------------------------------------
# [[shared_vaults]] block — B-2: parse + thread into returned dict
# ---------------------------------------------------------------------------

_VALID_TOML_WITH_SHARED_VAULTS = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[shared_vaults]]
name = "team-vault"
root = "/tmp/team-vault"

[[shared_vaults]]
name = "another-vault"
root = "/tmp/another-vault"
"""

_VALID_TOML_NO_SHARED_VAULTS = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
"""

_SHARED_VAULTS_MISSING_NAME = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[shared_vaults]]
root = "/tmp/team-vault"
"""

_SHARED_VAULTS_EMPTY_NAME = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[shared_vaults]]
name = ""
root = "/tmp/team-vault"
"""

_SHARED_VAULTS_MISSING_ROOT = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[shared_vaults]]
name = "team-vault"
"""

_SHARED_VAULTS_EMPTY_ROOT = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[shared_vaults]]
name = "team-vault"
root = ""
"""


def test_shared_vaults_round_trips_into_dict(tmp_path: Path) -> None:
    """B-2/B-3: a valid [[shared_vaults]] block is returned in the dict."""
    from group_config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_WITH_SHARED_VAULTS)
    cfg = load_group(f)
    assert "shared_vaults" in cfg, "shared_vaults key must be in returned dict"
    svs = cfg["shared_vaults"]
    assert len(svs) == 2
    assert svs[0]["name"] == "team-vault"
    assert svs[0]["root"] == "/tmp/team-vault"
    assert svs[1]["name"] == "another-vault"
    assert svs[1]["root"] == "/tmp/another-vault"


def test_no_shared_vaults_defaults_to_empty_list(tmp_path: Path) -> None:
    """Back-compat: a config with no [[shared_vaults]] returns shared_vaults=[]."""
    from group_config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_NO_SHARED_VAULTS)
    cfg = load_group(f)
    assert cfg.get("shared_vaults") == []


def test_shared_vaults_missing_name_raises(tmp_path: Path) -> None:
    """Missing shared_vaults[i].name → GroupConfigError naming file + field."""
    from group_config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_SHARED_VAULTS_MISSING_NAME)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "name" in msg


def test_shared_vaults_empty_name_raises(tmp_path: Path) -> None:
    """Empty shared_vaults[i].name → GroupConfigError naming file + field."""
    from group_config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_SHARED_VAULTS_EMPTY_NAME)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "name" in msg


def test_shared_vaults_missing_root_raises(tmp_path: Path) -> None:
    """Missing shared_vaults[i].root → GroupConfigError naming file + field."""
    from group_config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_SHARED_VAULTS_MISSING_ROOT)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "root" in msg


def test_shared_vaults_empty_root_raises(tmp_path: Path) -> None:
    """Empty shared_vaults[i].root → GroupConfigError naming file + field."""
    from group_config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_SHARED_VAULTS_EMPTY_ROOT)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "root" in msg


# ---------------------------------------------------------------------------
# [harness] partial / composable block (Fix 1)
# ---------------------------------------------------------------------------


def _write_and_load(tmp_path: Path, toml_body: str):
    from group_config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(
        "[group]\nname = 'testgroup'\n\n"
        "[[members]]\nname = 'myrepo'\nrepo_root = '/tmp/myrepo'\n\n"
        + toml_body
    )
    return load_group(f)


def test_harness_doc_files_only_loads(tmp_path: Path) -> None:
    """[harness] with only doc_files (no new/resume/cwd) must load without error."""
    cfg = _write_and_load(tmp_path, '[harness]\ndoc_files = ["AGENTS.md"]\n')
    assert "harness" in cfg
    assert cfg["harness"]["doc_files"] == ["AGENTS.md"]


def test_harness_doc_files_only_no_new_key(tmp_path: Path) -> None:
    """[harness] with only doc_files must not populate new/resume/cwd keys."""
    cfg = _write_and_load(tmp_path, '[harness]\ndoc_files = ["AGENTS.md"]\n')
    h = cfg["harness"]
    assert "new" not in h
    assert "resume" not in h


def test_harness_cwd_only_loads(tmp_path: Path) -> None:
    """[harness] with only cwd must load without error."""
    cfg = _write_and_load(tmp_path, '[harness]\ncwd = "{workspace}/sub"\n')
    assert "harness" in cfg
    assert cfg["harness"]["cwd"] == "{workspace}/sub"


def test_harness_new_only_loads(tmp_path: Path) -> None:
    """[harness] with only new (resume falls back) must load without error."""
    cfg = _write_and_load(tmp_path, '[harness]\nnew = ["myharness"]\n')
    assert "harness" in cfg
    assert cfg["harness"]["new"] == ["myharness"]
    assert "resume" not in cfg["harness"]


def test_groups_example_harness_commented_block_round_trips(tmp_path: Path) -> None:
    """Round-trip the groups.example commented [harness] block.

    The example shows [harness] + doc_files = ["AGENTS.md"] alone (no new/resume).
    Uncommenting it must produce a valid config that loads, and resolve_doc_files
    must return ["AGENTS.md"] while resolve_launch falls back to claude defaults.
    """
    from group_config import load_group
    from harness_launch import resolve_doc_files, resolve_launch

    f = tmp_path / "trailhead.toml"
    f.write_text(
        '[group]\nname = "trailhead"\n\n'
        '[[members]]\nname = "trailhead"\nrepo_root = "/path/to/trailhead"\n\n'
        '[harness]\ndoc_files = ["AGENTS.md"]\n'
    )
    cfg = load_group(f)
    assert cfg["group"]["name"] == "trailhead"

    doc_files = resolve_doc_files(cfg)
    assert doc_files == ["AGENTS.md"]

    ws = tmp_path / "ws"
    ws.mkdir()
    argv, cwd = resolve_launch(cfg, "feat-x", ws, is_resume=False)
    assert argv == ["claude"]
    assert cwd == ws

    argv_resume, _ = resolve_launch(cfg, "feat-x", ws, is_resume=True)
    assert argv_resume == ["claude", "-r", "feat-x"]
