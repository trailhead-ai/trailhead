"""Tests for group_config.py — tomllib loader + schema validation.

Test contract:
- Loads a valid trailhead.toml config.
- A malformed config (missing required field) → error naming file + failing field.
- A malformed config (bad type) → error naming file + failing field.
- bootstrap commands are parsed as a LIST (not a shell string) for shell=False.
- [dev_env] block present → warn-and-continue (prints deferred note, does not crash).
- No group config file → legible first-run scaffold/point message.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_GROUPS_EXAMPLE_DIR = _REPO_ROOT / "tools" / "camp" / "groups.example"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


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
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML)
    cfg = load_group(f)
    assert cfg["group"]["name"] == "testgroup"
    assert len(cfg["members"]) == 1
    assert cfg["members"][0]["name"] == "myrepo"
    assert cfg["members"][0]["repo_root"] == "/tmp/myrepo"


def test_load_bootstrap_is_list(tmp_path: Path) -> None:
    """Legacy bootstrap argv survives normalization as a list (for subprocess
    shell=False), not a shell string — preserved on the implicit bootstrap task."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML)
    cfg = load_group(f)
    bootstrap_task = cfg["members"][0]["tasks"][0]
    cmd = bootstrap_task["steps"][0]["cmd"]
    assert isinstance(cmd, list), "bootstrap argv must be a list"
    assert cmd == ["pip", "install", "-e", "."]


def test_load_bootstrap_defaults_to_empty_list(tmp_path: Path) -> None:
    """When bootstrap is absent, no implicit bootstrap task is created."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_NO_BOOTSTRAP)
    cfg = load_group(f)
    assert cfg["members"][0]["tasks"] == []


def test_load_branch_pattern_defaults(tmp_path: Path) -> None:
    """When [branch] is absent, branch_pattern defaults to 'worktree-{slug}'."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_NO_BOOTSTRAP)
    cfg = load_group(f)
    assert cfg["branch_pattern"] == "worktree-{slug}"


# ---------------------------------------------------------------------------
# Per-member branch base
# ---------------------------------------------------------------------------


def test_member_base_defaults_to_origin_main(tmp_path: Path) -> None:
    """When a member omits `base`, it defaults to 'origin/main'."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_NO_BOOTSTRAP)
    cfg = load_group(f)
    assert cfg["members"][0]["base"] == "origin/main"


def test_member_base_override_honored(tmp_path: Path) -> None:
    """A per-member `base` string overrides the default."""
    from camp.group.config import load_group

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
    from camp.group.config import GroupConfigError, load_group

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
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_MISSING_GROUP_NAME)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "name" in msg or "group" in msg


def test_missing_member_repo_root_errors_with_field(tmp_path: Path) -> None:
    """Missing member repo_root → error naming file + failing field."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_MISSING_MEMBER_REPO_ROOT)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "repo_root" in msg


def test_missing_member_name_errors_with_field(tmp_path: Path) -> None:
    """Missing member name → error naming file + failing field."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_MISSING_MEMBER_NAME)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "name" in msg


def test_bootstrap_not_list_errors_with_field(tmp_path: Path) -> None:
    """bootstrap as a string (not a list) → error naming file + failing field."""
    from camp.group.config import GroupConfigError, load_group

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
    from camp.group.config import load_group

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
    from camp.group.config import GroupConfigNotFound, load_group

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
    from camp.group.config import load_all_groups

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    result = load_all_groups(groups_dir)
    assert result == []


def test_load_all_groups_loads_files(tmp_path: Path) -> None:
    """load_all_groups loads all .toml files in the directory."""
    from camp.group.config import load_all_groups

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
    from camp.group.config import load_group

    f = _GROUPS_EXAMPLE_DIR / "trailhead.toml"
    if not f.is_file():
        pytest.skip("groups.example/trailhead.toml not yet created")
    cfg = load_group(f)
    assert cfg["group"]["name"] == "trailhead"
    # The trailhead fleet group spans exactly three sibling repos.
    member_names = {m["name"] for m in cfg["members"]}
    assert member_names == {"trailhead", "trailhead-ai.github.io", "outpost"}
    # Must NOT carry a [dev_env] block. load_group strips dev_env via
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
    from camp.group.config import load_group

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
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_NO_SHARED_VAULTS)
    cfg = load_group(f)
    assert cfg.get("shared_vaults") == []


def test_shared_vaults_missing_name_raises(tmp_path: Path) -> None:
    """Missing shared_vaults[i].name → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_SHARED_VAULTS_MISSING_NAME)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "name" in msg


def test_shared_vaults_empty_name_raises(tmp_path: Path) -> None:
    """Empty shared_vaults[i].name → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_SHARED_VAULTS_EMPTY_NAME)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "name" in msg


def test_shared_vaults_missing_root_raises(tmp_path: Path) -> None:
    """Missing shared_vaults[i].root → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_SHARED_VAULTS_MISSING_ROOT)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "root" in msg


def test_shared_vaults_empty_root_raises(tmp_path: Path) -> None:
    """Empty shared_vaults[i].root → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

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
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(
        "[group]\nname = 'testgroup'\n\n"
        "[[members]]\nname = 'myrepo'\nrepo_root = '/tmp/myrepo'\n\n" + toml_body
    )
    return load_group(f)


def test_harness_doc_files_only_loads(tmp_path: Path) -> None:
    """[harness] with only doc_files (no binary/cwd) must load without error."""
    cfg = _write_and_load(tmp_path, '[harness]\ndoc_files = ["AGENTS.md"]\n')
    assert "harness" in cfg
    assert cfg["harness"]["doc_files"] == ["AGENTS.md"]


def test_harness_doc_files_only_no_binary_key(tmp_path: Path) -> None:
    """[harness] with only doc_files must not populate binary/cwd keys."""
    cfg = _write_and_load(tmp_path, '[harness]\ndoc_files = ["AGENTS.md"]\n')
    h = cfg["harness"]
    assert "binary" not in h
    assert "cwd" not in h


def test_harness_cwd_only_loads(tmp_path: Path) -> None:
    """[harness] with only cwd must load without error."""
    cfg = _write_and_load(tmp_path, '[harness]\ncwd = "{workspace}/sub"\n')
    assert "harness" in cfg
    assert cfg["harness"]["cwd"] == "{workspace}/sub"


def test_harness_binary_only_loads(tmp_path: Path) -> None:
    """[harness] with only binary must load without error."""
    cfg = _write_and_load(tmp_path, '[harness]\nbinary = "myharness"\n')
    assert "harness" in cfg
    assert cfg["harness"]["binary"] == "myharness"


# ---------------------------------------------------------------------------
# [[lore_scopes]] block — parse + validate lore routing scope bindings
# ---------------------------------------------------------------------------

_VALID_TOML_WITH_ONE_LORE_SCOPE = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[lore_scopes]]
scope = "product"
name = "trailhead"
"""

_VALID_TOML_WITH_MULTIPLE_LORE_SCOPES = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[lore_scopes]]
scope = "product"
name = "trailhead"

[[lore_scopes]]
scope = "repo"
name = "myrepo-vault"
"""

_LORE_SCOPES_NOT_LIST = """\
[group]
name = "testgroup"

[lore_scopes]
scope = "product"
name = "trailhead"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
"""

_LORE_SCOPES_ENTRY_NOT_TABLE = """\
lore_scopes = ["product"]

[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
"""

_LORE_SCOPES_MISSING_NAME = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[lore_scopes]]
scope = "product"
"""

_LORE_SCOPES_MISSING_SCOPE = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[lore_scopes]]
name = "trailhead"
"""

_LORE_SCOPES_INVALID_SCOPE = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[lore_scopes]]
scope = "workspace"
name = "trailhead"
"""

_LORE_SCOPES_DEFAULT_SCOPE = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[lore_scopes]]
scope = "default"
name = "trailhead"
"""

_LORE_SCOPES_EMPTY_NAME = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[lore_scopes]]
scope = "product"
name = ""
"""

_LORE_SCOPES_DUPLICATE_SCOPE = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[lore_scopes]]
scope = "product"
name = "trailhead"

[[lore_scopes]]
scope = "product"
name = "other-vault"
"""


def test_no_lore_scopes_defaults_to_empty_list(tmp_path: Path) -> None:
    """A config without [[lore_scopes]] returns lore_scopes=[]."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_NO_BOOTSTRAP)
    cfg = load_group(f)
    assert cfg.get("lore_scopes") == []


def test_lore_scopes_one_valid_entry(tmp_path: Path) -> None:
    """A single valid [[lore_scopes]] entry is returned as a list with one dict."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_WITH_ONE_LORE_SCOPE)
    cfg = load_group(f)
    assert cfg.get("lore_scopes") == [{"scope": "product", "name": "trailhead"}]


def test_lore_scopes_multiple_entries_order_preserved(tmp_path: Path) -> None:
    """Multiple [[lore_scopes]] entries are returned in declared order."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_WITH_MULTIPLE_LORE_SCOPES)
    cfg = load_group(f)
    scopes = cfg.get("lore_scopes")
    assert len(scopes) == 2
    assert scopes[0] == {"scope": "product", "name": "trailhead"}
    assert scopes[1] == {"scope": "repo", "name": "myrepo-vault"}


def test_lore_scopes_not_list_raises(tmp_path: Path) -> None:
    """lore_scopes as a non-list value → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_LORE_SCOPES_NOT_LIST)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "lore_scopes" in msg


def test_lore_scopes_entry_not_table_raises(tmp_path: Path) -> None:
    """A lore_scopes entry that is not a table → GroupConfigError naming file + position."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_LORE_SCOPES_ENTRY_NOT_TABLE)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "lore_scopes" in msg


def test_lore_scopes_missing_name_raises(tmp_path: Path) -> None:
    """Missing lore_scopes[i].name → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_LORE_SCOPES_MISSING_NAME)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "name" in msg


def test_lore_scopes_missing_scope_raises(tmp_path: Path) -> None:
    """Missing lore_scopes[i].scope → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_LORE_SCOPES_MISSING_SCOPE)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "scope" in msg


def test_lore_scopes_invalid_scope_raises(tmp_path: Path) -> None:
    """A lore_scopes scope not in {repo, product, suite, team} → GroupConfigError."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_LORE_SCOPES_INVALID_SCOPE)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "scope" in msg


def test_lore_scopes_default_scope_raises(tmp_path: Path) -> None:
    """scope='default' is explicitly rejected — it is not a meaningful routing scope."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_LORE_SCOPES_DEFAULT_SCOPE)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "scope" in msg


def test_lore_scopes_empty_name_raises(tmp_path: Path) -> None:
    """Empty lore_scopes[i].name → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_LORE_SCOPES_EMPTY_NAME)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "name" in msg


def test_lore_scopes_duplicate_scope_raises(tmp_path: Path) -> None:
    """Two [[lore_scopes]] entries with the same scope → GroupConfigError."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_LORE_SCOPES_DUPLICATE_SCOPE)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "scope" in msg or "duplicate" in msg.lower()


def test_groups_example_harness_commented_block_round_trips(tmp_path: Path) -> None:
    """Round-trip the groups.example commented [harness] block.

    The example shows [harness] + doc_files = ["AGENTS.md"] alone (no binary).
    Uncommenting it must produce a valid config that loads, and the profile's
    doc_files must be ["AGENTS.md"] while the binary still falls back to the claude
    default (the launch surface was removed).
    """
    from camp.group.config import load_group
    from camp.launch.profile import resolve_harness_profile

    f = tmp_path / "trailhead.toml"
    f.write_text(
        '[group]\nname = "trailhead"\n\n'
        '[[members]]\nname = "trailhead"\nrepo_root = "/path/to/trailhead"\n\n'
        '[harness]\ndoc_files = ["AGENTS.md"]\n'
    )
    cfg = load_group(f)
    assert cfg["group"]["name"] == "trailhead"

    profile = resolve_harness_profile(cfg)
    assert profile.doc_files == ["AGENTS.md"]
    # inject defaults to "stdout" when a [harness] block is present without inject
    assert profile.inject == "stdout"
    # binary name still falls back to the claude default
    assert profile.binary == "claude"
    assert profile.is_claude_launch() is True
# ---------------------------------------------------------------------------
# [tasks.<name>] block — config-driven multi-step member tasks
# ---------------------------------------------------------------------------

_VALID_TOML_WITH_TASK = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["graphify"]

[tasks.graphify]
phase = "provision"
required = true
timeout_seconds = 30

[[tasks.graphify.steps]]
name = "seed"
cmd = ["rsync", "-a", "{repo_root}/.code-review-graph/", "{worktree}/.code-review-graph/"]

[[tasks.graphify.steps]]
name = "update"
cmd = ["code-review-graph", "update", "--repo", "{worktree}"]
"""


def test_task_valid_config_parses_to_normalized_shape(tmp_path: Path) -> None:
    """A valid [tasks.<name>] table, referenced by a member, parses into the
    member's resolved tasks list with all fields normalized."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML_WITH_TASK)
    cfg = load_group(f)

    tasks = cfg["members"][0]["tasks"]
    assert tasks == [
        {
            "name": "graphify",
            "phase": "provision",
            "required": True,
            "timeout_seconds": 30,
            "cleanup": None,
            "steps": [
                {
                    "name": "seed",
                    "cmd": [
                        "rsync",
                        "-a",
                        "{repo_root}/.code-review-graph/",
                        "{worktree}/.code-review-graph/",
                    ],
                },
                {
                    "name": "update",
                    "cmd": ["code-review-graph", "update", "--repo", "{worktree}"],
                },
            ],
        }
    ]


def test_task_defaults_phase_and_required(tmp_path: Path) -> None:
    """A task omitting phase/required/timeout_seconds defaults to provision,
    not required, and no timeout."""
    from camp.group.config import load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["mytask"]

[tasks.mytask]
[[tasks.mytask.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    cfg = load_group(f)
    task = cfg["members"][0]["tasks"][0]
    assert task["phase"] == "provision"
    assert task["required"] is False
    assert task["timeout_seconds"] is None
    assert task["cleanup"] is None


def test_task_step_unknown_placeholder_raises(tmp_path: Path) -> None:
    """An unknown {placeholder} in a task step's argv → GroupConfigError naming
    file + field, reusing _reject_unknown_placeholders (not a parallel check)."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["graphify"]

[tasks.graphify]
[[tasks.graphify.steps]]
name = "seed"
cmd = ["echo", "{bogus}"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "bogus" in msg
    assert "graphify" in msg


def test_task_unknown_member_reference_raises(tmp_path: Path) -> None:
    """A member referencing a task name with no matching [tasks.<name>] table
    → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["ghost"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "ghost" in msg
    assert "tasks" in msg


def test_task_name_collision_with_implicit_bootstrap_raises(tmp_path: Path) -> None:
    """A member with legacy bootstrap AND an explicit tasks=["bootstrap"]
    reference collides with the implicit legacy bootstrap task name."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
bootstrap = ["pip", "install", "-e", "."]
tasks = ["bootstrap"]

[tasks.bootstrap]
[[tasks.bootstrap.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "bootstrap" in msg


def test_task_name_collision_with_implicit_dep_install_raises(tmp_path: Path) -> None:
    """A member with legacy dep-install hooks AND an explicit
    tasks=["dep-install"] reference collides with the implicit legacy task name."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["dep-install"]

[[members.hooks]]
kind = "dep-install"
cmd = ["npm", "install"]

[tasks.dep-install]
[[tasks.dep-install.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "dep-install" in msg


def test_task_empty_steps_list_raises(tmp_path: Path) -> None:
    """A task with an empty steps list → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["mytask"]

[tasks.mytask]
steps = []
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "steps" in msg


def test_task_bad_phase_type_raises(tmp_path: Path) -> None:
    """A task with an invalid phase value → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["mytask"]

[tasks.mytask]
phase = "deploy"
[[tasks.mytask.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "phase" in msg


def test_task_bad_required_type_raises(tmp_path: Path) -> None:
    """A task with a non-boolean required value → GroupConfigError naming file + field."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["mytask"]

[tasks.mytask]
required = "yes"
[[tasks.mytask.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "required" in msg


def test_task_bad_timeout_seconds_type_raises(tmp_path: Path) -> None:
    """A task with a non-positive-int timeout_seconds → GroupConfigError naming
    file + field."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["mytask"]

[tasks.mytask]
timeout_seconds = 0
[[tasks.mytask.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "timeout_seconds" in msg


def test_legacy_bootstrap_normalizes_to_required_provision_task(tmp_path: Path) -> None:
    """A legacy bootstrap-only member (no [tasks.*]) normalizes to a required
    provision-phase task named 'bootstrap', preserving the original argv."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML)
    cfg = load_group(f)
    tasks = cfg["members"][0]["tasks"]
    assert tasks == [
        {
            "name": "bootstrap",
            "phase": "provision",
            "required": True,
            "timeout_seconds": None,
            "steps": [{"name": "bootstrap", "cmd": ["pip", "install", "-e", "."]}],
        }
    ]


def test_legacy_hooks_normalizes_to_required_activate_task(tmp_path: Path) -> None:
    """A legacy dep-install hook normalizes to a required activate-phase task
    named 'dep-install', preserving the original argv."""
    from camp.group.config import load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[members.hooks]]
kind = "dep-install"
cmd = ["npm", "install"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    cfg = load_group(f)
    tasks = cfg["members"][0]["tasks"]
    assert tasks == [
        {
            "name": "dep-install",
            "phase": "activate",
            "required": True,
            "timeout_seconds": None,
            "steps": [{"name": "dep-install", "cmd": ["npm", "install"]}],
        }
    ]


def test_normalized_member_has_no_bootstrap_or_hooks_keys(tmp_path: Path) -> None:
    """After load_group, a member dict never carries the legacy 'bootstrap' or
    'hooks' keys — the rest of the codebase sees exactly one resolved tasks list."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML)
    cfg = load_group(f)
    member = cfg["members"][0]
    assert "bootstrap" not in member
    assert "hooks" not in member
    assert "tasks" in member


def test_implicit_legacy_tasks_ordered_before_referenced_group_tasks(
    tmp_path: Path,
) -> None:
    """A member with both legacy bootstrap and an explicit group-task reference
    gets the implicit legacy task ordered first in the resolved tasks list."""
    from camp.group.config import load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
bootstrap = ["pip", "install", "-e", "."]
tasks = ["graphify"]

[tasks.graphify]
[[tasks.graphify.steps]]
name = "seed"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    cfg = load_group(f)
    task_names = [t["name"] for t in cfg["members"][0]["tasks"]]
    assert task_names == ["bootstrap", "graphify"]


# ---------------------------------------------------------------------------
# [tasks.<name>].cleanup — optional retry-cleanup argv
# ---------------------------------------------------------------------------


def test_task_cleanup_list_resolves_to_argv(tmp_path: Path) -> None:
    """A [tasks.<name>] block with cleanup = [...] resolves to a task carrying
    that argv list, validated in the same shape as a step's cmd."""
    from camp.group.config import load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["mytask"]

[tasks.mytask]
cleanup = ["make", "clean"]
[[tasks.mytask.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    cfg = load_group(f)
    task = cfg["members"][0]["tasks"][0]
    assert task["cleanup"] == ["make", "clean"]


def test_task_no_cleanup_key_resolves_to_none_not_empty_list(tmp_path: Path) -> None:
    """A task with no cleanup key resolves with cleanup absent/None — not an
    empty list, which would later read as 'run nothing' rather than 'nothing
    declared'."""
    from camp.group.config import load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["mytask"]

[tasks.mytask]
[[tasks.mytask.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    cfg = load_group(f)
    task = cfg["members"][0]["tasks"][0]
    assert task["cleanup"] is None


def test_task_cleanup_string_raises(tmp_path: Path) -> None:
    """cleanup declared as a bare string (not a list) is a config error naming
    the task, not a silent coercion into a one-element argv."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["mytask"]

[tasks.mytask]
cleanup = "make clean"
[[tasks.mytask.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "cleanup" in msg
    assert "mytask" in msg


def test_task_cleanup_nested_list_raises(tmp_path: Path) -> None:
    """cleanup declared as a nested list is a config error naming the task."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["mytask"]

[tasks.mytask]
cleanup = [["make", "clean"]]
[[tasks.mytask.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "cleanup" in msg
    assert "mytask" in msg


def test_task_cleanup_non_list_scalar_raises(tmp_path: Path) -> None:
    """cleanup declared as a non-list scalar (e.g. an int) is a config error
    naming the task."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
tasks = ["mytask"]

[tasks.mytask]
cleanup = 42
[[tasks.mytask.steps]]
name = "step1"
cmd = ["echo", "hi"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert str(f) in msg or "testgroup.toml" in msg
    assert "cleanup" in msg
    assert "mytask" in msg


def test_legacy_bootstrap_task_carries_no_cleanup_key(tmp_path: Path) -> None:
    """The bootstrap = [...] shorthand emits no cleanup key by construction —
    cleanup is impossible on a legacy-normalized task. Also pins the rest of
    the normalized shape unchanged: phase="provision", required=True,
    timeout_seconds=None."""
    from camp.group.config import load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(_VALID_TOML)
    cfg = load_group(f)
    task = cfg["members"][0]["tasks"][0]
    assert task["name"] == "bootstrap"
    assert task["phase"] == "provision"
    assert task["required"] is True
    assert task["timeout_seconds"] is None
    assert "cleanup" not in task


def test_legacy_hooks_task_carries_no_cleanup_key(tmp_path: Path) -> None:
    """The [[members.hooks]] kind="dep-install" shorthand emits no cleanup key
    by construction. Also pins the rest of the normalized shape unchanged:
    phase="activate", required=True, timeout_seconds=None."""
    from camp.group.config import load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[members.hooks]]
kind = "dep-install"
cmd = ["npm", "install"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    cfg = load_group(f)
    task = cfg["members"][0]["tasks"][0]
    assert task["name"] == "dep-install"
    assert task["phase"] == "activate"
    assert task["required"] is True
    assert task["timeout_seconds"] is None
    assert "cleanup" not in task


# ---------------------------------------------------------------------------
# [launch] block — the directory-launch roots allowlist
# ---------------------------------------------------------------------------


def test_no_launch_block_omits_launch_key(tmp_path: Path) -> None:
    """No [launch] block → no 'launch' key at all (absence, not an empty default).

    Directory-rooted launch is off by default, and the eligibility gate keys off
    the key being absent — an empty dict would read as "configured, but empty".
    """
    cfg = _write_and_load(tmp_path, "[branch]\npattern = 'worktree-{slug}'\n")
    assert "launch" not in cfg


def test_launch_roots_parsed_unexpanded(tmp_path: Path) -> None:
    """roots entries are stored exactly as written — '~' is expanded at check
    time against the injected environment, not at config-load time."""
    cfg = _write_and_load(tmp_path, '[launch]\nroots = ["~/code", "/srv/work"]\n')
    assert cfg["launch"]["roots"] == ["~/code", "/srv/work"]


@pytest.mark.parametrize(
    "roots_line",
    [
        "roots = []",
        'roots = "x"',
        "roots = [1]",
        'roots = ["", "  "]',
    ],
)
def test_launch_roots_invalid_raises(tmp_path: Path, roots_line: str) -> None:
    """An empty list, a non-list, a non-string entry, or a blank entry each
    raise GroupConfigError naming launch.roots."""
    from camp.group.config import GroupConfigError

    with pytest.raises(GroupConfigError) as exc_info:
        _write_and_load(tmp_path, f"[launch]\n{roots_line}\n")
    assert "launch.roots" in str(exc_info.value)


@pytest.mark.parametrize(
    "entry",
    [
        "code",
        "./code",
        "../code",
        "~user/code",
    ],
)
def test_launch_roots_rejects_entries_without_a_fixed_anchor(tmp_path: Path, entry: str) -> None:
    """A roots entry must name a fixed location, not one relative to the caller.

    A relative entry is resolved against whatever directory camp runs from, so
    one config fences differently per invocation — and an unexpected working
    directory widens the boundary rather than narrowing it. '~user' is rejected
    alongside them: it reads as anchored but expands nowhere, leaving a literal
    relative path with the same defect.
    """
    from camp.group.config import GroupConfigError

    with pytest.raises(GroupConfigError) as exc_info:
        _write_and_load(tmp_path, f'[launch]\nroots = ["{entry}"]\n')
    assert "launch.roots" in str(exc_info.value)


def test_launch_roots_accepts_absolute_and_home_anchored_entries(tmp_path: Path) -> None:
    """The two anchored spellings both load: absolute, and '~'-anchored."""
    cfg = _write_and_load(tmp_path, '[launch]\nroots = ["/srv/work", "~/code", "~"]\n')
    assert cfg["launch"]["roots"] == ["/srv/work", "~/code", "~"]


def test_launch_unknown_key_raises(tmp_path: Path) -> None:
    """An unrecognized key inside [launch] fails closed — [launch] configures a
    containment boundary, so a typo must never be silently ignored."""
    from camp.group.config import GroupConfigError

    with pytest.raises(GroupConfigError) as exc_info:
        _write_and_load(tmp_path, '[launch]\nrootz = ["~/code"]\n')
    msg = str(exc_info.value)
    assert "launch" in msg
    assert "rootz" in msg


def test_launch_not_a_table_raises(tmp_path: Path) -> None:
    """A scalar 'launch' key is a malformed config, not a table."""
    from camp.group.config import GroupConfigError, load_group

    f = tmp_path / "testgroup.toml"
    f.write_text(
        "launch = 'x'\n\n"
        "[group]\nname = 'testgroup'\n\n"
        "[[members]]\nname = 'myrepo'\nrepo_root = '/tmp/myrepo'\n"
    )
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    assert "launch" in str(exc_info.value)


def test_launch_account_parsed_unexpanded(tmp_path: Path) -> None:
    """account is stored exactly as written — camp does not interpret it as a
    path; expansion and validation belong to the harness seam."""
    cfg = _write_and_load(tmp_path, '[launch]\naccount = "~/.claude-levr"\n')
    assert cfg["launch"]["account"] == "~/.claude-levr"


def test_launch_account_absent_is_none_with_no_launch_block(tmp_path: Path) -> None:
    cfg = _write_and_load(tmp_path, "[branch]\npattern = 'worktree-{slug}'\n")
    assert "launch" not in cfg


def test_launch_account_absent_with_roots_present_is_none(tmp_path: Path) -> None:
    """roots keeps working unchanged when account is not declared."""
    cfg = _write_and_load(tmp_path, '[launch]\nroots = ["~/code"]\n')
    assert cfg["launch"]["roots"] == ["~/code"]
    assert "account" not in cfg["launch"]


@pytest.mark.parametrize(
    "account_line",
    [
        "account = 42",
        'account = ""',
        'account = "   "',
    ],
)
def test_launch_account_invalid_raises(tmp_path: Path, account_line: str) -> None:
    """A non-string, empty, or whitespace-only account raises GroupConfigError
    naming launch.account and the config file path."""
    from camp.group.config import GroupConfigError

    with pytest.raises(GroupConfigError) as exc_info:
        _write_and_load(tmp_path, f"[launch]\n{account_line}\n")
    msg = str(exc_info.value)
    assert "launch.account" in msg
    assert str(tmp_path) in msg or "testgroup.toml" in msg


@pytest.mark.parametrize(
    "escape",
    ["\\u0000", "\\u0007", "\\u001b", "\\u009b", "\\u007f"],
)
def test_launch_account_with_a_syscall_hostile_character_raises(
    tmp_path: Path, escape: str
) -> None:
    """An account value becomes a path camp resolves and an operand handed to a
    process spawn, and both reject a NUL by raising rather than refusing. The
    value must be refused where it is declared, so the failure is a named
    misconfiguration in one file instead of a raw traceback out of an unrelated
    group's launch."""
    from camp.group.config import GroupConfigError

    with pytest.raises(GroupConfigError) as exc_info:
        _write_and_load(tmp_path, f'[launch]\naccount = "/accounts/{escape}levr"\n')
    assert "launch.account" in str(exc_info.value)


def test_launch_account_keeps_ordinary_non_ascii(tmp_path: Path) -> None:
    """The refusal is aimed at control characters, not at anything non-ASCII: a
    home directory can legitimately be spelled in any script."""
    cfg = _write_and_load(tmp_path, '[launch]\naccount = "~/Comptes/café/.claude"\n')
    assert cfg["launch"]["account"] == "~/Comptes/café/.claude"


def test_launch_unknown_key_still_raises_with_account_known(tmp_path: Path) -> None:
    """Regression on _LAUNCH_KEYS: an unrelated unknown key still fails closed
    now that 'account' is a recognized key too."""
    from camp.group.config import GroupConfigError

    with pytest.raises(GroupConfigError) as exc_info:
        _write_and_load(tmp_path, '[launch]\naccount = "~/.claude-levr"\nbogus = "x"\n')
    msg = str(exc_info.value)
    assert "launch" in msg
    assert "bogus" in msg
