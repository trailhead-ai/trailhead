"""EPHEMERAL assumption-probe tests for U1/U2 of
task/a-group-config-reduces-to-a-comparable-policy-projection.

Not part of the permanent suite — delete once the real projection module ships
its own behavioral tests covering the same ground.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.group.config import load_group  # noqa: E402


def _write(tmp_path: Path, name: str, text: str) -> Path:
    f = tmp_path / f"{name}.toml"
    f.write_text(text)
    return f


# ---------------------------------------------------------------------------
# U1a — excluded: None vs () (known instance, confirmed for completeness)
# ---------------------------------------------------------------------------


def test_excluded_none_vs_empty_list_distinct(tmp_path: Path) -> None:
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
    assert cfg_absent["members"][0]["excluded"] is None
    assert cfg_empty["members"][0]["excluded"] == ()
    assert cfg_absent["members"][0]["excluded"] != cfg_empty["members"][0]["excluded"]


# ---------------------------------------------------------------------------
# U1b — bootstrap legacy synthesis vs modern equivalent: raw dicts diverge on
# key PRESENCE (cleanup/capability), but the value each field resolves to via
# .get() is identical. The projection must extract fields, not compare dicts.
# ---------------------------------------------------------------------------


def test_bootstrap_legacy_vs_modern_equivalent_task_shape(tmp_path: Path) -> None:
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
    legacy_task = load_group(legacy)["members"][0]["tasks"][0]
    modern_task = load_group(modern)["members"][0]["tasks"][0]

    # Same policy fields, extracted field-by-field via .get(): symmetric.
    for field in ("name", "phase", "required", "timeout_seconds"):
        assert legacy_task.get(field) == modern_task.get(field), field
    assert legacy_task.get("cleanup") is None
    assert modern_task.get("cleanup") is None
    assert legacy_task.get("capability") is None
    assert modern_task.get("capability") is None
    assert [s["cmd"] for s in legacy_task["steps"]] == [s["cmd"] for s in modern_task["steps"]]

    # But the raw loaded dicts are NOT equal: the legacy-synthesized task is
    # missing the cleanup/capability keys entirely, while the modern task
    # (produced by _parse_tasks) always carries them, even when None.
    assert "cleanup" not in legacy_task
    assert "capability" not in legacy_task
    assert "cleanup" in modern_task
    assert "capability" in modern_task
    assert legacy_task != modern_task  # whole-dict equality is NOT symmetric


# ---------------------------------------------------------------------------
# U1c — [[members.hooks]] dep-install synthesis vs modern equivalent: same
# key-presence asymmetry as bootstrap.
# ---------------------------------------------------------------------------


def test_hooks_legacy_vs_modern_equivalent_task_shape(tmp_path: Path) -> None:
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
    legacy_task = load_group(legacy)["members"][0]["tasks"][0]
    modern_task = load_group(modern)["members"][0]["tasks"][0]

    for field in ("name", "phase", "required", "timeout_seconds"):
        assert legacy_task.get(field) == modern_task.get(field), field
    assert legacy_task.get("cleanup") is None
    assert modern_task.get("cleanup") is None
    assert [s["cmd"] for s in legacy_task["steps"]] == [s["cmd"] for s in modern_task["steps"]]

    assert "cleanup" not in legacy_task
    assert "cleanup" in modern_task
    assert legacy_task != modern_task


# ---------------------------------------------------------------------------
# U1d — lore_scopes whitespace normalization IS symmetric (control case: a
# normalization that behaves correctly).
# ---------------------------------------------------------------------------


def test_lore_scopes_whitespace_trimmed_symmetrically(tmp_path: Path) -> None:
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
    clean = _write(
        tmp_path,
        "clean",
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
    assert load_group(padded)["lore_scopes"] == load_group(clean)["lore_scopes"]


# ---------------------------------------------------------------------------
# U1e — base / branch.pattern default-fill IS symmetric between "absent" and
# "explicitly declared as the default value".
# ---------------------------------------------------------------------------


def test_base_and_branch_pattern_default_fill_symmetric(tmp_path: Path) -> None:
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
[branch]
pattern = "worktree-{slug}"
""",
    )
    cfg_a, cfg_e = load_group(absent), load_group(explicit)
    assert cfg_a["members"][0]["base"] == cfg_e["members"][0]["base"] == "origin/main"
    assert cfg_a["branch_pattern"] == cfg_e["branch_pattern"] == "worktree-{slug}"


# ---------------------------------------------------------------------------
# U1f — [harness] block ABSENT vs explicitly declared with the semantically
# equivalent default: raw `group.get("harness")` is None (key absent) in one
# case and a dict in the other, AND the effective `inject` default differs
# (no-block -> "claude-hook"; a block without inject -> "stdout") per
# resolve_harness_profile's documented asymmetry. Confirms the design doc's
# choice to leave harness OUT of the compared field list is load-bearing: the
# raw loaded dict is not a safe basis for a harness comparison.
# ---------------------------------------------------------------------------


def test_harness_block_absence_is_not_the_same_policy_as_matching_defaults(
    tmp_path: Path,
) -> None:
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
    cfg_absent = load_group(no_block)
    cfg_explicit = load_group(explicit_block)

    assert "harness" not in cfg_absent
    assert "harness" in cfg_explicit

    from camp.launch.profile import resolve_harness_profile

    profile_absent = resolve_harness_profile(cfg_absent)
    profile_explicit = resolve_harness_profile(cfg_explicit)
    # Same binary/cwd, but the effective inject channel differs solely because
    # of block presence, not because the operator declared anything about it.
    assert profile_absent.binary == profile_explicit.binary == "claude"
    assert profile_absent.inject == "claude-hook"
    assert profile_explicit.inject == "stdout"
    assert profile_absent.inject != profile_explicit.inject


# ---------------------------------------------------------------------------
# U2 — task step cmd vectors in the loader are stored as UNEXPANDED
# placeholder templates ({repo_root}/{worktree}/{slug}/{workspace}), never
# resolved filesystem paths, so comparing them cross-host does not leak an
# absolute path — as long as the operator never hardcodes one directly.
# ---------------------------------------------------------------------------


def test_task_step_cmd_stores_unexpanded_placeholder_not_resolved_path(
    tmp_path: Path,
) -> None:
    cfg_path = _write(
        tmp_path,
        "g",
        """\
[group]
name = "g"
[[members]]
name = "m"
repo_root = "/tmp/m"
tasks = ["copy"]

[tasks.copy]
[[tasks.copy.steps]]
name = "copy"
cmd = ["rsync", "-a", "{repo_root}/x/", "{worktree}/x/"]
""",
    )
    task = load_group(cfg_path)["members"][0]["tasks"][0]
    cmd = task["steps"][0]["cmd"]
    assert cmd == ["rsync", "-a", "{repo_root}/x/", "{worktree}/x/"]
    # The literal member repo_root ("/tmp/m") never appears in the stored cmd —
    # the placeholder stays a template, not a substituted absolute path.
    assert "/tmp/m" not in " ".join(cmd)


def test_real_group_config_cmd_vectors_carry_no_hardcoded_absolute_path() -> None:
    """Scan the operator's real trailhead.toml: every task step cmd token is
    either a placeholder-templated string or a bare tool/argument name — never
    a hardcoded absolute filesystem path (which would silently diverge
    Linux-vs-Mac since the loader does not forbid this)."""
    real = Path("/home/tomduffield/.config/camp/groups/trailhead.toml")
    raw = tomllib.loads(real.read_text(encoding="utf-8"))
    offending = []
    for name, tbl in (raw.get("tasks") or {}).items():
        for step in tbl.get("steps", []):
            for token in step.get("cmd", []):
                if token.startswith("/") and "{" not in token:
                    offending.append((name, step.get("name"), token))
    assert offending == [], f"hardcoded absolute path(s) found in cmd: {offending}"


def test_repo_root_differs_across_hosts_confirming_exclusion_is_load_bearing(
    tmp_path: Path,
) -> None:
    """repo_root is genuinely host-specific — the same *policy* authored on a
    Linux box and a Mac naturally carries a different repo_root string, which
    is exactly why the design excludes it from the projection rather than
    trying to normalize it."""
    linux_cfg = _write(
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
    mac_cfg = _write(
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
    linux_root = load_group(linux_cfg)["members"][0]["repo_root"]
    mac_root = load_group(mac_cfg)["members"][0]["repo_root"]
    assert linux_root != mac_root
    # Everything else about "the same policy" is identical.
    assert (
        load_group(linux_cfg)["members"][0]["name"]
        == load_group(mac_cfg)["members"][0]["name"]
    )
