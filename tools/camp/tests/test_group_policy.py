"""Tests for camp.group.policy — reducing a loaded group config to its
comparable policy projection, and comparing two projections.

Test contract (see task/a-group-config-reduces-to-a-comparable-policy-projection):
- repo_root-only difference projects identically and compares as matching.
- branch-pattern difference compares as differing, naming branch_pattern.
- a task's step-command difference compares as differing, naming that task.
- a member declared on one side and absent on the other is reported, both
  directions, as two separate assertions.
- excluded None vs () compares per U1's resolved answer (asymmetric: distinct
  raw values, but neither excluded is in the projection, so they compare
  identically as policy).
- one test per loader normalization (bootstrap/hooks legacy vs modern; lore
  scope whitespace; base/branch.pattern default-fill; [harness] presence).
- projection output is deterministic across repeated calls and stable under
  TOML key reordering.
- every excluded path field (repo_root, launch.roots, shared_vault root) is
  pinned individually: changing it alone causes no divergence.
- schema-coverage: every key a loaded group config carries is classified,
  including member-level and task-level keys, not only top-level ones.
- a member's task list is compared order-sensitively (it is an execution
  order); shared_vaults and lore_scopes are compared order-insensitively
  (they are declarations, not a sequence).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.group.config import load_group  # noqa: E402
from camp.group.policy import (  # noqa: E402
    EXCLUDED_MEMBER_KEYS,
    EXCLUDED_TASK_KEYS,
    EXCLUDED_TOP_LEVEL_KEYS,
    INCLUDED_MEMBER_KEYS,
    INCLUDED_TASK_KEYS,
    INCLUDED_TOP_LEVEL_KEYS,
    compare_group_policy,
    project_group_policy,
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    f = tmp_path / f"{name}.toml"
    f.write_text(text)
    return f


# ---------------------------------------------------------------------------
# repo_root-only difference: projects identically, compares as matching.
# ---------------------------------------------------------------------------


def test_repo_root_only_difference_projects_identically_and_matches(tmp_path: Path) -> None:
    linux = _write(
        tmp_path,
        "linux",
        """\
[group]
name = "g"
[[members]]
name = "trailhead"
repo_root = "/home/tomduffield/code/trailhead"
""",
    )
    mac = _write(
        tmp_path,
        "mac",
        """\
[group]
name = "g"
[[members]]
name = "trailhead"
repo_root = "/Users/tomduffield/code/trailhead"
""",
    )
    proj_linux = project_group_policy(load_group(linux))
    proj_mac = project_group_policy(load_group(mac))
    assert proj_linux == proj_mac

    result = compare_group_policy(proj_linux, proj_mac)
    assert result.matches is True
    assert result.differences == ()


# ---------------------------------------------------------------------------
# branch pattern difference compares as differing, naming branch_pattern.
# ---------------------------------------------------------------------------


def test_branch_pattern_difference_compares_as_differing(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "a",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[branch]
pattern = "worktree-{slug}"
""",
    )
    b = _write(
        tmp_path,
        "b",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[branch]
pattern = "wt-{slug}"
""",
    )
    result = compare_group_policy(
        project_group_policy(load_group(a)), project_group_policy(load_group(b))
    )
    assert result.matches is False
    assert "branch_pattern" in result.differences


# ---------------------------------------------------------------------------
# a task's step-command difference compares as differing, naming that task.
# ---------------------------------------------------------------------------


def test_task_step_command_difference_names_that_task(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "a",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
tasks = ["seed"]
[tasks.seed]
[[tasks.seed.steps]]
name = "seed"
cmd = ["make", "seed"]
""",
    )
    b = _write(
        tmp_path,
        "b",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
tasks = ["seed"]
[tasks.seed]
[[tasks.seed.steps]]
name = "seed"
cmd = ["make", "seed-different"]
""",
    )
    result = compare_group_policy(
        project_group_policy(load_group(a)), project_group_policy(load_group(b))
    )
    assert result.matches is False
    assert any("seed" in d for d in result.differences)
    assert not any("branch_pattern" == d for d in result.differences)


# ---------------------------------------------------------------------------
# member declared on one side, absent on the other: both directions reported,
# as two separate assertions.
# ---------------------------------------------------------------------------


def test_member_only_in_a_is_reported(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "a",
        """\
[group]
name = "g"
[[members]]
name = "m1"
repo_root = "/tmp/m1"
[[members]]
name = "m2"
repo_root = "/tmp/m2"
""",
    )
    b = _write(
        tmp_path,
        "b",
        """\
[group]
name = "g"
[[members]]
name = "m1"
repo_root = "/tmp/m1-elsewhere"
""",
    )
    result = compare_group_policy(
        project_group_policy(load_group(a)), project_group_policy(load_group(b))
    )
    assert result.matches is False
    assert any("m2" in d and "only in a" in d for d in result.differences)


def test_member_only_in_b_is_reported(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "a",
        """\
[group]
name = "g"
[[members]]
name = "m1"
repo_root = "/tmp/m1"
""",
    )
    b = _write(
        tmp_path,
        "b",
        """\
[group]
name = "g"
[[members]]
name = "m1"
repo_root = "/tmp/m1-elsewhere"
[[members]]
name = "m2"
repo_root = "/tmp/m2"
""",
    )
    result = compare_group_policy(
        project_group_policy(load_group(a)), project_group_policy(load_group(b))
    )
    assert result.matches is False
    assert any("m2" in d and "only in b" in d for d in result.differences)


# ---------------------------------------------------------------------------
# excluded: None vs () — neither is in the projection, so both compare
# identically as policy (U1's resolved answer: excluded is not a compared
# dimension at all).
# ---------------------------------------------------------------------------


def test_excluded_none_vs_empty_tuple_compares_identically_as_policy(tmp_path: Path) -> None:
    absent = _write(
        tmp_path,
        "absent",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
""",
    )
    declared_empty = _write(
        tmp_path,
        "empty",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
excluded = []
""",
    )
    cfg_absent = load_group(absent)
    cfg_empty = load_group(declared_empty)
    # Sanity: U1's raw asymmetry still holds at the loader.
    assert cfg_absent["members"][0]["excluded"] is None
    assert cfg_empty["members"][0]["excluded"] == ()

    proj_absent = project_group_policy(cfg_absent)
    proj_empty = project_group_policy(cfg_empty)
    assert proj_absent == proj_empty

    result = compare_group_policy(proj_absent, proj_empty)
    assert result.matches is True
    assert result.differences == ()


# ---------------------------------------------------------------------------
# One test per loader normalization — legacy spelling and modern spelling of
# the same policy project identically.
# ---------------------------------------------------------------------------


def test_bootstrap_legacy_and_modern_task_project_identically(tmp_path: Path) -> None:
    legacy = _write(
        tmp_path,
        "legacy",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
bootstrap = ["pip", "install", "-e", "."]
""",
    )
    modern = _write(
        tmp_path,
        "modern",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
tasks = ["bootstrap"]

[tasks.bootstrap]
phase = "provision"
required = true

[[tasks.bootstrap.steps]]
name = "bootstrap"
cmd = ["pip", "install", "-e", "."]
""",
    )
    proj_legacy = project_group_policy(load_group(legacy))
    proj_modern = project_group_policy(load_group(modern))
    assert proj_legacy == proj_modern

    result = compare_group_policy(proj_legacy, proj_modern)
    assert result.matches is True
    assert result.differences == ()


def test_hooks_legacy_and_modern_task_project_identically(tmp_path: Path) -> None:
    legacy = _write(
        tmp_path,
        "legacy",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"

[[members.hooks]]
kind = "dep-install"
cmd = ["npm", "install"]
""",
    )
    modern = _write(
        tmp_path,
        "modern",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
tasks = ["dep-install"]

[tasks.dep-install]
phase = "activate"
required = true

[[tasks.dep-install.steps]]
name = "dep-install"
cmd = ["npm", "install"]
""",
    )
    proj_legacy = project_group_policy(load_group(legacy))
    proj_modern = project_group_policy(load_group(modern))
    assert proj_legacy == proj_modern

    result = compare_group_policy(proj_legacy, proj_modern)
    assert result.matches is True
    assert result.differences == ()


def test_lore_scopes_padded_and_trimmed_spelling_project_identically(tmp_path: Path) -> None:
    padded = _write(
        tmp_path,
        "padded",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[[lore_scopes]]
scope = "product "
name = " trailhead "
""",
    )
    trimmed = _write(
        tmp_path,
        "trimmed",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[[lore_scopes]]
scope = "product"
name = "trailhead"
""",
    )
    proj_padded = project_group_policy(load_group(padded))
    proj_trimmed = project_group_policy(load_group(trimmed))
    assert proj_padded == proj_trimmed
    assert proj_padded["lore_scopes"] == [{"scope": "product", "name": "trailhead"}]

    result = compare_group_policy(proj_padded, proj_trimmed)
    assert result.matches is True


def test_base_default_fill_absent_and_explicit_project_identically(tmp_path: Path) -> None:
    absent = _write(
        tmp_path,
        "absent",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
""",
    )
    explicit = _write(
        tmp_path,
        "explicit",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
base = "origin/main"
""",
    )
    proj_absent = project_group_policy(load_group(absent))
    proj_explicit = project_group_policy(load_group(explicit))
    assert proj_absent == proj_explicit
    assert proj_absent["members"]["m"]["base"] == "origin/main"

    result = compare_group_policy(proj_absent, proj_explicit)
    assert result.matches is True


def test_branch_pattern_default_fill_absent_and_explicit_project_identically(
    tmp_path: Path,
) -> None:
    absent = _write(
        tmp_path,
        "absent",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
""",
    )
    explicit = _write(
        tmp_path,
        "explicit",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[branch]
pattern = "worktree-{slug}"
""",
    )
    proj_absent = project_group_policy(load_group(absent))
    proj_explicit = project_group_policy(load_group(explicit))
    assert proj_absent == proj_explicit
    assert proj_absent["branch_pattern"] == "worktree-{slug}"

    result = compare_group_policy(proj_absent, proj_explicit)
    assert result.matches is True


def test_harness_block_absent_vs_present_with_defaults_project_identically(
    tmp_path: Path,
) -> None:
    """[harness] stays out of the projection entirely — the raw loaded dict is
    not a safe basis for comparison, since its effective inject default
    depends on block presence, not contents (see module docstring)."""
    no_block = _write(
        tmp_path,
        "no_block",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
""",
    )
    explicit_block = _write(
        tmp_path,
        "explicit_block",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"

[harness]
binary = "claude"
cwd = "{workspace}"
""",
    )
    proj_absent = project_group_policy(load_group(no_block))
    proj_explicit = project_group_policy(load_group(explicit_block))
    assert proj_absent == proj_explicit

    result = compare_group_policy(proj_absent, proj_explicit)
    assert result.matches is True


# ---------------------------------------------------------------------------
# Deterministic across repeated calls; stable under TOML key reordering.
# ---------------------------------------------------------------------------


def test_projection_deterministic_across_repeated_calls(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "g",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
tasks = ["seed"]
[tasks.seed]
[[tasks.seed.steps]]
name = "seed"
cmd = ["make", "seed"]
""",
    )
    cfg = load_group(cfg_path)
    proj1 = project_group_policy(cfg)
    proj2 = project_group_policy(cfg)
    assert proj1 == proj2
    assert json.dumps(proj1, sort_keys=True) == json.dumps(proj2, sort_keys=True)


def test_projection_stable_under_toml_field_reordering(tmp_path: Path) -> None:
    order_a = _write(
        tmp_path,
        "order_a",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
base = "origin/develop"
""",
    )
    order_b = _write(
        tmp_path,
        "order_b",
        """\
[group]
name = "g"
[[members]]
base = "origin/develop"
repo_root = "/tmp/m"
name = "m"
""",
    )
    proj_a = project_group_policy(load_group(order_a))
    proj_b = project_group_policy(load_group(order_b))
    assert proj_a == proj_b


# ---------------------------------------------------------------------------
# Every excluded path field is pinned individually: one per excluded field.
# ---------------------------------------------------------------------------


def test_member_repo_root_excluded_causes_no_divergence(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "a",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/one"
""",
    )
    b = _write(
        tmp_path,
        "b",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/two"
""",
    )
    result = compare_group_policy(
        project_group_policy(load_group(a)), project_group_policy(load_group(b))
    )
    assert result.matches is True
    assert result.differences == ()


def test_launch_roots_excluded_causes_no_divergence(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "a",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[launch]
roots = ["/srv/one"]
""",
    )
    b = _write(
        tmp_path,
        "b",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[launch]
roots = ["/srv/two", "/srv/three"]
""",
    )
    result = compare_group_policy(
        project_group_policy(load_group(a)), project_group_policy(load_group(b))
    )
    assert result.matches is True
    assert result.differences == ()


def test_shared_vault_root_excluded_causes_no_divergence(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "a",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[[shared_vaults]]
name = "trailhead"
root = "/srv/lore/one"
""",
    )
    b = _write(
        tmp_path,
        "b",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[[shared_vaults]]
name = "trailhead"
root = "/srv/lore/two"
""",
    )
    result = compare_group_policy(
        project_group_policy(load_group(a)), project_group_policy(load_group(b))
    )
    assert result.matches is True
    assert result.differences == ()


# ---------------------------------------------------------------------------
# A member's task list is an execution order and compares order-sensitively;
# shared_vaults/lore_scopes are declarations and compare order-insensitively.
# ---------------------------------------------------------------------------


def test_member_task_reorder_compares_as_differing(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "a",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
tasks = ["first", "second"]
[tasks.first]
[[tasks.first.steps]]
name = "first"
cmd = ["make", "first"]
[tasks.second]
[[tasks.second.steps]]
name = "second"
cmd = ["make", "second"]
""",
    )
    b = _write(
        tmp_path,
        "b",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
tasks = ["second", "first"]
[tasks.first]
[[tasks.first.steps]]
name = "first"
cmd = ["make", "first"]
[tasks.second]
[[tasks.second.steps]]
name = "second"
cmd = ["make", "second"]
""",
    )
    result = compare_group_policy(
        project_group_policy(load_group(a)), project_group_policy(load_group(b))
    )
    assert result.matches is False
    assert any("task order" in d for d in result.differences)


def test_shared_vaults_reorder_compares_as_matching(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "a",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[[shared_vaults]]
name = "trailhead"
root = "/srv/lore"
[[shared_vaults]]
name = "personal"
root = "/srv/lore2"
""",
    )
    b = _write(
        tmp_path,
        "b",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[[shared_vaults]]
name = "personal"
root = "/srv/lore2"
[[shared_vaults]]
name = "trailhead"
root = "/srv/lore"
""",
    )
    result = compare_group_policy(
        project_group_policy(load_group(a)), project_group_policy(load_group(b))
    )
    assert result.matches is True
    assert result.differences == ()


def test_lore_scopes_reorder_compares_as_matching(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "a",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[[lore_scopes]]
scope = "product"
name = "trailhead"
[[lore_scopes]]
scope = "team"
name = "notes"
""",
    )
    b = _write(
        tmp_path,
        "b",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
[[lore_scopes]]
scope = "team"
name = "notes"
[[lore_scopes]]
scope = "product"
name = "trailhead"
""",
    )
    result = compare_group_policy(
        project_group_policy(load_group(a)), project_group_policy(load_group(b))
    )
    assert result.matches is True
    assert result.differences == ()


# ---------------------------------------------------------------------------
# Schema-coverage: every key a loaded group config carries is classified as
# included or excluded — an unhandled key fails this test.
# ---------------------------------------------------------------------------


def test_schema_coverage_every_loaded_key_is_classified(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "g",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
excluded = ["build/"]
tasks = ["seed"]

[tasks.seed]
[[tasks.seed.steps]]
name = "seed"
cmd = ["make", "seed"]

[branch]
pattern = "worktree-{slug}"

[harness]
binary = "claude"

[launch]
roots = ["/srv/work"]
account = "~/.claude-work"

[[shared_vaults]]
name = "trailhead"
root = "/srv/lore"

[[lore_scopes]]
scope = "product"
name = "trailhead"
""",
    )
    cfg = load_group(cfg_path)
    unclassified = [
        key
        for key in cfg
        if key not in INCLUDED_TOP_LEVEL_KEYS and key not in EXCLUDED_TOP_LEVEL_KEYS
    ]
    assert unclassified == []


def test_schema_coverage_every_member_key_is_classified(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "g",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
excluded = ["build/"]
tasks = ["seed"]

[tasks.seed]
[[tasks.seed.steps]]
name = "seed"
cmd = ["make", "seed"]
""",
    )
    cfg = load_group(cfg_path)
    member = cfg["members"][0]
    unclassified = [
        key for key in member if key not in INCLUDED_MEMBER_KEYS and key not in EXCLUDED_MEMBER_KEYS
    ]
    assert unclassified == []


def test_schema_coverage_every_task_key_is_classified(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "g",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
tasks = ["seed"]

[tasks.seed]
timeout_seconds = 30
capability = "seeds the db"
cleanup = ["make", "clean"]
[[tasks.seed.steps]]
name = "seed"
cmd = ["make", "seed"]
""",
    )
    cfg = load_group(cfg_path)
    task = cfg["members"][0]["tasks"][0]
    unclassified = [
        key for key in task if key not in INCLUDED_TASK_KEYS and key not in EXCLUDED_TASK_KEYS
    ]
    assert unclassified == []


# ---------------------------------------------------------------------------
# The handoff: JSON-serializable with stdlib json.dumps.
# ---------------------------------------------------------------------------


def test_projection_is_json_serializable(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "g",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
tasks = ["seed"]

[tasks.seed]
timeout_seconds = 30
capability = "seeds the db"
cleanup = ["make", "clean"]

[[tasks.seed.steps]]
name = "seed"
cmd = ["make", "seed"]

[launch]
account = "~/.claude-work"

[[shared_vaults]]
name = "trailhead"
root = "/srv/lore"

[[lore_scopes]]
scope = "product"
name = "trailhead"
""",
    )
    projection = project_group_policy(load_group(cfg_path))
    encoded = json.dumps(projection)
    assert json.loads(encoded) == projection
