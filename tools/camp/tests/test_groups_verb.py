"""Test contract: `camp groups [--json]` — read-only group enumeration.

- `camp groups --json` with two configured groups emits a JSON array of
  {"name": ..., "members": [<member names>]} sorted by name, exit 0.
- Human mode prints exactly one line per group, `<name>: <members>`, in name
  order.
- No groups configured: exit 0, `[]` / "no groups configured" — not an error.
- A group config file that cannot be loaded — unparseable TOML, non-UTF-8
  bytes, a directory wearing a `.toml` name — degrades that one entry with a
  stderr notice rather than failing the whole listing, and never prints a
  traceback.
- The verb runs without a resolved group/workspace: no --group flag, and cwd
  need not resolve to any configured group's member repo.
- The verb is listed in camp's help menu.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"


def _run_cli(
    args: list[str], *, env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess:
    base_env = {**os.environ}
    base_env.update(env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(cwd) if cwd else None,
    )


def _write_group(groups_dir: Path, name: str, members: list[str]) -> None:
    groups_dir.mkdir(parents=True, exist_ok=True)
    member_tables = "\n\n".join(
        f'[[members]]\nname = "{m}"\nrepo_root = "/tmp/fake-{m}"' for m in members
    )
    (groups_dir / f"{name}.toml").write_text(f'[group]\nname = "{name}"\n\n{member_tables}\n')


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "CAMP_CONFIG_DIR": str(tmp_path / "camp-config"),
        "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
    }


# ---------------------------------------------------------------------------
# --json mode
# ---------------------------------------------------------------------------


def test_camp_groups_json_lists_two_groups_sorted_by_name(tmp_path: Path) -> None:
    env = _env(tmp_path)
    groups_dir = Path(env["CAMP_CONFIG_DIR"]) / "groups"
    _write_group(groups_dir, "zebra", ["repo-z"])
    _write_group(groups_dir, "alpha", ["repo-a", "repo-b"])

    result = _run_cli(["groups", "--json"], env=env)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    data = json.loads(result.stdout)
    assert data == [
        {"name": "alpha", "members": ["repo-a", "repo-b"]},
        {"name": "zebra", "members": ["repo-z"]},
    ]


def test_camp_groups_json_no_groups_configured_is_empty_array(tmp_path: Path) -> None:
    env = _env(tmp_path)

    result = _run_cli(["groups", "--json"], env=env)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert json.loads(result.stdout) == []


# ---------------------------------------------------------------------------
# Human mode
# ---------------------------------------------------------------------------


def test_camp_groups_human_mode_prints_one_line_per_group(tmp_path: Path) -> None:
    """One line per group, in name order — asserted as lines, not as substrings
    that a single collapsed line would also satisfy."""
    env = _env(tmp_path)
    groups_dir = Path(env["CAMP_CONFIG_DIR"]) / "groups"
    _write_group(groups_dir, "zebra", ["repo-z"])
    _write_group(groups_dir, "alpha", ["repo-a", "repo-b"])

    result = _run_cli(["groups"], env=env)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stdout.splitlines() == [
        "alpha: repo-a, repo-b",
        "zebra: repo-z",
    ]


def test_camp_groups_human_no_groups_configured_prints_plain_line(tmp_path: Path) -> None:
    env = _env(tmp_path)

    result = _run_cli(["groups"], env=env)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stdout.strip() == "no groups configured"


# ---------------------------------------------------------------------------
# An unloadable config degrades, doesn't fail the whole listing
# ---------------------------------------------------------------------------


def _assert_degraded_to_good_only(result: subprocess.CompletedProcess) -> None:
    """The one good group still lists, the bad file is named, no traceback."""
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert json.loads(result.stdout) == [{"name": "good", "members": ["repo-a"]}]
    assert "bad.toml" in result.stderr
    assert "Traceback" not in result.stderr


def test_camp_groups_malformed_config_degrades_with_stderr_notice(tmp_path: Path) -> None:
    env = _env(tmp_path)
    groups_dir = Path(env["CAMP_CONFIG_DIR"]) / "groups"
    _write_group(groups_dir, "good", ["repo-a"])
    (groups_dir / "bad.toml").write_text("not valid toml [[[")

    _assert_degraded_to_good_only(_run_cli(["groups", "--json"], env=env))


def test_camp_groups_non_utf8_config_degrades_with_stderr_notice(tmp_path: Path) -> None:
    """A file that is not UTF-8 at all never reaches the TOML parser — the read
    itself raises, and that must degrade like any other unloadable file."""
    env = _env(tmp_path)
    groups_dir = Path(env["CAMP_CONFIG_DIR"]) / "groups"
    _write_group(groups_dir, "good", ["repo-a"])
    (groups_dir / "bad.toml").write_bytes(b'[group]\nname = "\xff\xfe caf\xe9"\n')

    _assert_degraded_to_good_only(_run_cli(["groups", "--json"], env=env))


def test_camp_groups_directory_named_toml_degrades_with_stderr_notice(tmp_path: Path) -> None:
    """A directory matching the `*.toml` glob is not a config file, and saying
    so must not cost the rest of the listing."""
    env = _env(tmp_path)
    groups_dir = Path(env["CAMP_CONFIG_DIR"]) / "groups"
    _write_group(groups_dir, "good", ["repo-a"])
    (groups_dir / "bad.toml").mkdir()

    _assert_degraded_to_good_only(_run_cli(["groups", "--json"], env=env))


# ---------------------------------------------------------------------------
# Groupless: no --group, no cwd-resolved group needed
# ---------------------------------------------------------------------------


def test_camp_groups_runs_without_resolved_group_or_cwd_context(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _write_group(Path(env["CAMP_CONFIG_DIR"]) / "groups", "alpha", ["repo-a"])

    # cwd is tmp_path itself — not inside any configured member repo, no --group
    # flag passed, and no group resolves from this cwd.
    result = _run_cli(["groups", "--json"], env=env, cwd=tmp_path)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert json.loads(result.stdout) == [{"name": "alpha", "members": ["repo-a"]}]


def test_camp_groups_is_listed_in_the_help_menu() -> None:
    """A live verb absent from the menu is a verb nobody finds."""
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--help"], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert "camp groups [--json]" in result.stdout


def test_groups_verb_not_in_needs_group_verbs() -> None:
    if str(_PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_DIR))
    from camp.workspace.verb_taxonomy import NEEDS_GROUP_VERBS

    assert "groups" not in NEEDS_GROUP_VERBS
