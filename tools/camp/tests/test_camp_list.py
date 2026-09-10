"""Tests for camp list (alias ls) — slug + absolute path output.

Test contract:
1. `camp list` prints one `slug abs-path` line per workspace to stdout, exit 0.
2. Empty group → no stdout, exit 0.
3. `ls` alias → identical output to `list`; no harness launched, no state mutated.

In-process tests call `cmd_ls_group` and `_cmd_ls_group_cli` directly for unit
coverage; subprocess tests exercise the alias dispatch path through the full
`cli/camp` binary.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _load_cli_module():
    """Import the CLI command-group module holding the list handler.

    `_cmd_ls_group_cli` moved out of the monolithic `cli/camp` into the
    `camp.cli.workspace` command-group module (enter/pwd/list all act on an
    existing workspace); the in-process tests call it there directly.
    """
    return importlib.import_module("camp.cli.workspace")


@pytest.fixture()
def camp_cli():
    return _load_cli_module()


def _make_group(name: str) -> dict:
    """Minimal in-memory group config (no real git repos needed for list)."""
    return {
        "group": {"name": name},
        "members": [
            {
                "name": "repo_a",
                "repo_root": "/nonexistent/repo_a",
                "bootstrap": [],
                "base": "origin/main",
            }
        ],
        "branch_pattern": "worktree-{slug}",
    }


def _seed_manifest(group_name: str, slug: str, *, env: dict) -> Path:
    """Create a workspace dir + manifest with no git operations.

    Returns the workspace dir path.
    """
    from camp.group.manifest import manifest_path_for, workspace_dir, write_central_manifest

    ws = workspace_dir(group_name, slug, env=env)
    ws.mkdir(parents=True, exist_ok=True)
    mpath = manifest_path_for(group_name, slug, env=env)
    write_central_manifest(
        mpath,
        {
            "schema_version": 1,
            "group": group_name,
            "slug": slug,
            "branch": f"worktree-{slug}",
            "members": [
                {
                    "name": "repo_a",
                    "repo_root": "/nonexistent/repo_a",
                    "worktree_path": str(ws / "repo_a"),
                    "provision_state": "pending",
                }
            ],
        },
    )
    return ws


# ---------------------------------------------------------------------------
# Unit tests: cmd_ls_group workspace_path field
# ---------------------------------------------------------------------------


class TestCmdLsGroupWorkspacePath:
    """cmd_ls_group entries must carry a workspace_path field that matches
    the canonical workspace_dir computation."""

    def test_entry_has_workspace_path(self, tmp_path):
        from camp.provision.lifecycle import cmd_ls_group

        group = _make_group("wpg")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        _seed_manifest("wpg", "feat-q", env=env)

        entries = cmd_ls_group(group, env=env)

        assert len(entries) == 1
        assert "workspace_path" in entries[0], (
            "cmd_ls_group entry must have a 'workspace_path' field"
        )

    def test_workspace_path_is_absolute(self, tmp_path):
        from camp.provision.lifecycle import cmd_ls_group

        group = _make_group("wpg")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        _seed_manifest("wpg", "feat-q", env=env)

        entries = cmd_ls_group(group, env=env)

        path = entries[0]["workspace_path"]
        assert path, "workspace_path must not be empty"
        assert Path(path).is_absolute(), f"workspace_path must be absolute, got {path!r}"

    def test_workspace_path_agrees_with_workspace_dir(self, tmp_path):
        from camp.provision.lifecycle import cmd_ls_group
        from camp.group.manifest import workspace_dir

        group = _make_group("wpg")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        _seed_manifest("wpg", "feat-q", env=env)

        entries = cmd_ls_group(group, env=env)

        expected = str(workspace_dir("wpg", "feat-q", env=env))
        assert entries[0]["workspace_path"] == expected, (
            f"workspace_path must equal workspace_dir(); "
            f"got {entries[0]['workspace_path']!r}, expected {expected!r}"
        )

    def test_empty_group_returns_empty_list(self, tmp_path):
        from camp.provision.lifecycle import cmd_ls_group

        group = _make_group("wpg")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}

        entries = cmd_ls_group(group, env=env)

        assert entries == [], f"empty group must return [], got {entries!r}"

    def test_multiple_workspaces_all_have_workspace_path(self, tmp_path):
        from camp.provision.lifecycle import cmd_ls_group

        group = _make_group("wpg")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        _seed_manifest("wpg", "alpha", env=env)
        _seed_manifest("wpg", "beta", env=env)

        entries = cmd_ls_group(group, env=env)

        assert len(entries) == 2
        for e in entries:
            assert "workspace_path" in e, f"entry {e['slug']!r} missing workspace_path"
            assert Path(e["workspace_path"]).is_absolute()


# ---------------------------------------------------------------------------
# In-process tests: _cmd_ls_group_cli output format
# ---------------------------------------------------------------------------


class TestListOutput:
    """_cmd_ls_group_cli prints one 'slug abs-path' line per workspace to stdout."""

    def test_single_workspace_stdout(self, camp_cli, tmp_path, capsys):
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        ws = _seed_manifest("listgrp", "feat-x", env=env)

        camp_cli._cmd_ls_group_cli([], group, env)

        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln]
        assert len(lines) == 1, f"expected 1 line, got {len(lines)}: {lines!r}"
        slug, path = lines[0].split(None, 1)
        assert slug == "feat-x", f"slug mismatch: {slug!r}"
        assert path == str(ws), f"path mismatch: {path!r}"

    def test_path_in_output_is_absolute(self, camp_cli, tmp_path, capsys):
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        _seed_manifest("listgrp", "feat-x", env=env)

        camp_cli._cmd_ls_group_cli([], group, env)

        out = capsys.readouterr().out
        line = out.strip()
        _, path = line.split(None, 1)
        assert Path(path).is_absolute(), f"path in output must be absolute, got {path!r}"

    def test_multiple_workspaces_one_line_each(self, camp_cli, tmp_path, capsys):
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        ws1 = _seed_manifest("listgrp", "alpha", env=env)
        ws2 = _seed_manifest("listgrp", "beta", env=env)

        camp_cli._cmd_ls_group_cli([], group, env)

        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln]
        assert len(lines) == 2, f"expected 2 lines, got {len(lines)}: {lines!r}"
        slugs = {ln.split(None, 1)[0] for ln in lines}
        assert slugs == {"alpha", "beta"}
        paths = {ln.split(None, 1)[1] for ln in lines}
        assert str(ws1) in paths, f"{ws1} not in output paths {paths}"
        assert str(ws2) in paths, f"{ws2} not in output paths {paths}"

    def test_no_header_lines_in_output(self, camp_cli, tmp_path, capsys):
        """stdout must contain only 'slug path' lines — no table headers."""
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        _seed_manifest("listgrp", "feat-x", env=env)

        camp_cli._cmd_ls_group_cli([], group, env)

        out = capsys.readouterr().out
        assert "SLUG" not in out, "output must not contain a 'SLUG' header"
        assert "BRANCH" not in out, "output must not contain a 'BRANCH' header"
        assert "GROUP" not in out, "output must not contain a 'GROUP' header"
        assert "---" not in out, "output must not contain a separator line"


class TestListJson:
    """`camp list --json` — fixed schema, shared with the spine fallback.

    Covers the group --json path and the convergence (both entry points emit
    the SAME key set)."""

    # Every row carries `ok` — a success row states `ok: true` alongside the
    # already-existing keys, so `ok` alone distinguishes a success row from
    # an unparsable-group failure row (`{"ok": False, "group": None,
    # "reason": ...}`) and a consumer never has to test for a key's absence.
    # See docs/design/cross-group-cross-account-listing.md, lines 165-170.
    _FIXED_KEYS = {"slug", "branch", "workspace_path", "group", "ok"}

    def test_json_carries_workspace_path(self, camp_cli, tmp_path, capsys):
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        ws = _seed_manifest("listgrp", "feat-x", env=env)

        camp_cli._cmd_ls_group_cli(["--json"], group, env)

        rows = json.loads(capsys.readouterr().out)
        assert len(rows) == 1
        assert rows[0]["workspace_path"] == str(ws)
        assert rows[0]["slug"] == "feat-x"

    def test_json_empty_group_is_empty_array(self, camp_cli, tmp_path, capsys):
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}

        camp_cli._cmd_ls_group_cli(["--json"], group, env)

        assert json.loads(capsys.readouterr().out) == []

    def test_plain_list_json_never_carries_a_host_key(self, camp_cli, tmp_path, capsys):
        """AC6 non-regression pin: a plain local `camp list --json` — no
        `--host` in play — carries EXACTLY the pre-existing key set and no
        `host` key. The `--host` axis stamps `host` only on a relayed row
        (camp.host.relay); this path must never gain it, unconditionally or
        otherwise. Expected keys are spelled out literally here rather than
        imported from the module under test, so a mutation to the module's
        own key set constant cannot also mutate this test's expectation."""
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        _seed_manifest("listgrp", "feat-x", env=env)

        camp_cli._cmd_ls_group_cli(["--json"], group, env)

        rows = json.loads(capsys.readouterr().out)
        assert len(rows) == 1
        assert set(rows[0].keys()) == {"ok", "slug", "branch", "workspace_path", "group"}
        assert "host" not in rows[0]

    def test_shared_renderer_projects_both_sources_to_one_schema(self, capsys):
        """The renderer projects group-style and spine-style entries (different
        source keys) onto the SAME fixed schema."""
        from camp.provision.lifecycle import render_workspace_list

        group_entry = {  # carries source-specific manifest_path (must be dropped)
            "slug": "g", "branch": "b", "workspace_path": "/ws/g",
            "group": "grp", "manifest_path": "/ws/g/manifest.json",
        }
        spine_entry = {  # legacy source: group is None, no manifest_path
            "slug": "s", "branch": "b2", "workspace_path": "/ws/s", "group": None,
        }
        render_workspace_list([group_entry, spine_entry], as_json=True)

        rows = json.loads(capsys.readouterr().out)
        assert {frozenset(r) for r in rows} == {frozenset(self._FIXED_KEYS)}
        assert rows[0]["group"] == "grp" and rows[1]["group"] is None


class TestListEmpty:
    """Empty group → no stdout, exit 0."""

    def test_empty_group_produces_no_stdout(self, camp_cli, tmp_path, capsys):
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}

        camp_cli._cmd_ls_group_cli([], group, env)

        out = capsys.readouterr().out
        assert out == "", f"empty group must produce no stdout, got {out!r}"

# ---------------------------------------------------------------------------
# Purity tests: no state mutation, no harness exec
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Subprocess integration tests
# ---------------------------------------------------------------------------


def _write_group_toml_named(
    groups_dir: Path, *, file_stem: str, group_name: str
) -> None:
    """Write a minimal group config TOML whose FILENAME may differ from the
    `[group].name` it declares — the general form, used directly to prove
    ordering is by the declared group name and not by config-file load order
    (which `load_all_groups` walks alphabetically by FILENAME)."""
    (groups_dir / f"{file_stem}.toml").write_text(
        f'[group]\nname = "{group_name}"\n\n'
        f"[[members]]\nname = \"repo_a\"\nrepo_root = \"/nonexistent/repo\"\n\n"
        f'[branch]\npattern = "worktree-{{slug}}"\n'
    )


def _write_group_toml(groups_dir: Path, group_name: str) -> None:
    """Write a minimal group config TOML with a non-existent (fake) repo_root,
    in the ordinary shape where the filename matches the declared name."""
    _write_group_toml_named(groups_dir, file_stem=group_name, group_name=group_name)


def _seed_manifest_raw(group_name: str, slug: str, *, state_dir: Path) -> Path:
    """Seed a workspace manifest via env override pointing at state_dir."""
    env = {"CAMP_STATE_DIR": str(state_dir)}
    return _seed_manifest(group_name, slug, env=env)


@pytest.fixture()
def list_cli_env(tmp_path):
    """Subprocess environment with a group config and two seeded workspaces."""
    config_dir = tmp_path / "camp-config"
    groups_dir = config_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "camp-state"
    state_dir.mkdir(parents=True, exist_ok=True)

    _write_group_toml(groups_dir, "listgroup")

    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)

    ws_alpha = _seed_manifest_raw("listgroup", "ws-alpha", state_dir=state_dir)
    ws_beta = _seed_manifest_raw("listgroup", "ws-beta", state_dir=state_dir)

    return {
        "env": env,
        "state_dir": state_dir,
        "config_dir": config_dir,
        "ws_alpha": ws_alpha,
        "ws_beta": ws_beta,
    }


def _camp(env_dict: dict, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=env_dict["env"],
    )


class TestListSubprocess:
    def test_list_exits_zero(self, list_cli_env):
        r = _camp(list_cli_env, "list", "--group", "listgroup")
        assert r.returncode == 0, (
            f"camp list must exit 0\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_list_prints_slug_and_abs_path(self, list_cli_env):
        r = _camp(list_cli_env, "list", "--group", "listgroup")
        assert r.returncode == 0
        lines = [ln for ln in r.stdout.splitlines() if ln]
        slugs = {ln.split(None, 1)[0] for ln in lines}
        assert "ws-alpha" in slugs, f"ws-alpha missing from output: {r.stdout!r}"
        assert "ws-beta" in slugs, f"ws-beta missing from output: {r.stdout!r}"
        paths = {ln.split(None, 1)[1] for ln in lines}
        assert str(list_cli_env["ws_alpha"]) in paths, (
            f"ws_alpha path missing from output: {r.stdout!r}"
        )
        assert str(list_cli_env["ws_beta"]) in paths, (
            f"ws_beta path missing from output: {r.stdout!r}"
        )

    def test_list_stdout_only_slug_path_lines(self, list_cli_env):
        """stdout contains only 'slug abs-path' lines, no headers or noise."""
        r = _camp(list_cli_env, "list", "--group", "listgroup")
        assert r.returncode == 0
        for line in r.stdout.splitlines():
            if not line:
                continue
            parts = line.split(None, 1)
            assert len(parts) == 2, f"line {line!r} is not 'slug path'"
            slug, path = parts
            assert Path(path).is_absolute(), f"path {path!r} in output must be absolute"


class TestListAliasLs:
    def test_ls_alias_exits_zero(self, list_cli_env):
        r = _camp(list_cli_env, "ls", "--group", "listgroup")
        assert r.returncode == 0, (
            f"camp ls must exit 0\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_ls_alias_identical_output_to_list(self, list_cli_env):
        """`camp ls` produces byte-for-byte identical stdout to `camp list`."""
        r_list = _camp(list_cli_env, "list", "--group", "listgroup")
        r_ls = _camp(list_cli_env, "ls", "--group", "listgroup")
        assert r_list.returncode == 0
        assert r_ls.returncode == 0
        assert r_ls.stdout == r_list.stdout, (
            f"ls alias must produce identical stdout to list\n"
            f"list: {r_list.stdout!r}\n"
            f"ls:   {r_ls.stdout!r}"
        )

    def test_ls_alias_does_not_launch_harness(self):
        """Source-level: _cmd_ls_group_cli has no execvp (no harness launch path)."""
        mod = _load_cli_module()
        src = inspect.getsource(mod._cmd_ls_group_cli)
        assert "execvp" not in src, "ls/list handler must not call os.execvp"


class TestListEmptySubprocess:
    def test_empty_group_no_stdout_exits_zero(self, tmp_path):
        """Empty group (no workspaces) → no stdout, exit 0."""
        config_dir = tmp_path / "camp-config"
        groups_dir = config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)
        state_dir = tmp_path / "camp-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        _write_group_toml(groups_dir, "emptygroup")

        env = {**os.environ}
        env["CAMP_CONFIG_DIR"] = str(config_dir)
        env["CAMP_STATE_DIR"] = str(state_dir)

        r = subprocess.run(
            [sys.executable, str(_CLI_CAMP), "list", "--group", "emptygroup"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, (
            f"empty group list must exit 0\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert r.stdout == "", (
            f"empty group list must produce no stdout, got {r.stdout!r}"
        )


# ---------------------------------------------------------------------------
# camp list --all-groups / -g — every configured group's workspaces, merged.
# ---------------------------------------------------------------------------


@pytest.fixture()
def two_group_list_cli_env(tmp_path):
    """Two configured groups, each with one seeded workspace, and a cwd
    (tmp_path itself) from which NEITHER group resolves."""
    config_dir = tmp_path / "camp-config"
    groups_dir = config_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "camp-state"
    state_dir.mkdir(parents=True, exist_ok=True)

    _write_group_toml(groups_dir, "groupa")
    _write_group_toml(groups_dir, "groupb")

    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)

    ws_a = _seed_manifest_raw("groupa", "ws-a", state_dir=state_dir)
    ws_b = _seed_manifest_raw("groupb", "ws-b", state_dir=state_dir)

    return {
        "env": env,
        "tmp_path": tmp_path,
        "ws_a": ws_a,
        "ws_b": ws_b,
    }


def _camp_from(env_dict: dict, cwd: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=env_dict["env"],
        cwd=str(cwd),
    )


class TestListAllGroups:
    def test_merges_every_configured_groups_workspaces(
        self, two_group_list_cli_env
    ) -> None:
        r = _camp_from(
            two_group_list_cli_env, two_group_list_cli_env["tmp_path"], "list",
            "--all-groups",
        )
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        lines = [ln for ln in r.stdout.splitlines() if ln]
        slugs = {ln.split(None, 1)[0] for ln in lines}
        assert slugs == {"ws-a", "ws-b"}

    def test_json_carries_the_group_each_row_belongs_to(
        self, two_group_list_cli_env
    ) -> None:
        r = _camp_from(
            two_group_list_cli_env, two_group_list_cli_env["tmp_path"], "list",
            "--all-groups", "--json",
        )
        assert r.returncode == 0, r.stderr
        rows = json.loads(r.stdout)
        assert {row["group"] for row in rows} == {"groupa", "groupb"}
        assert {row["slug"] for row in rows} == {"ws-a", "ws-b"}
        assert set(rows[0].keys()) == {"ok", "slug", "branch", "workspace_path", "group"}
        assert rows[0]["ok"] is True

    def test_short_and_long_spellings_produce_byte_identical_output(
        self, two_group_list_cli_env
    ) -> None:
        """Compares TWO REAL invocations' captured stdout — not two calls into
        the same formatting helper — so a spelling that silently diverged in
        argv handling would actually be caught."""
        long_form = _camp_from(
            two_group_list_cli_env, two_group_list_cli_env["tmp_path"], "list",
            "--all-groups",
        )
        short_form = _camp_from(
            two_group_list_cli_env, two_group_list_cli_env["tmp_path"], "list", "-g",
        )
        assert long_form.returncode == 0, long_form.stderr
        assert short_form.returncode == 0, short_form.stderr
        assert long_form.stdout == short_form.stdout
        assert long_form.stdout != ""

    def test_rows_are_ordered_by_group_name_not_by_config_file_load_order(
        self, tmp_path
    ) -> None:
        """`load_all_groups` walks config files alphabetically by FILENAME.
        Here the file that sorts FIRST declares the group that sorts LAST by
        NAME, so a merge that forgot to sort by group would print zzzgroup
        before aaagroup — the opposite of what this asserts."""
        config_dir = tmp_path / "camp-config"
        groups_dir = config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)
        state_dir = tmp_path / "camp-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        _write_group_toml_named(groups_dir, file_stem="aaa-file", group_name="zzzgroup")
        _write_group_toml_named(groups_dir, file_stem="zzz-file", group_name="aaagroup")

        env = {**os.environ}
        env["CAMP_CONFIG_DIR"] = str(config_dir)
        env["CAMP_STATE_DIR"] = str(state_dir)
        env_dict = {"env": env}

        _seed_manifest_raw("zzzgroup", "slug-in-zzz", state_dir=state_dir)
        _seed_manifest_raw("aaagroup", "slug-in-aaa", state_dir=state_dir)

        r = _camp_from(env_dict, tmp_path, "list", "--all-groups", "--json")
        assert r.returncode == 0, r.stderr
        rows = json.loads(r.stdout)
        assert [row["group"] for row in rows] == ["aaagroup", "zzzgroup"]

    def test_absent_the_option_output_is_unchanged(self, list_cli_env) -> None:
        """No `--all-groups`/`-g` anywhere → identical to the pre-existing
        single-group `camp list` surface."""
        r = _camp(list_cli_env, "list", "--group", "listgroup")
        assert r.returncode == 0, r.stderr
        lines = {ln.split(None, 1)[0] for ln in r.stdout.splitlines() if ln}
        assert lines == {"ws-alpha", "ws-beta"}

    def test_all_groups_with_a_named_group_refuses(self, two_group_list_cli_env) -> None:
        r = _camp_from(
            two_group_list_cli_env, two_group_list_cli_env["tmp_path"],
            "list", "--all-groups", "--group", "groupa",
        )
        assert r.returncode != 0, r.stdout
        assert r.stdout == ""
        assert "camp list: " in r.stderr
        assert "--all-groups" in r.stderr


# ---------------------------------------------------------------------------
# camp list --all-groups — narrowed answers say why: no groups configured,
# legacy suppression, unparsable sibling configs, and the silent empty case.
# ---------------------------------------------------------------------------


def _seed_legacy_worktree(workspace_root: Path, slug: str) -> Path:
    """Populate the LEGACY standalone worktree registry `spine.py`'s no-group
    `cmd_ls` reads — a row with no group, distinct from every group-config row."""
    wt = workspace_root / "trailhead" / ".claude" / "worktrees" / slug
    wt.mkdir(parents=True, exist_ok=True)
    (wt / ".workspace-manifest.json").write_text(
        json.dumps({"name": slug, "branch": f"worktree-{slug}"})
    )
    return wt


@pytest.fixture()
def no_group_env(tmp_path):
    """No group config directory at all, plus a populated legacy registry, and
    a cwd from which no group would resolve anyway."""
    config_dir = tmp_path / "camp-config"
    state_dir = tmp_path / "camp-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = tmp_path / "workspace-root"
    _seed_legacy_worktree(workspace_root, "legacy-slug")

    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)
    env["WORKSPACE_ROOT"] = str(workspace_root)

    return {"env": env, "tmp_path": tmp_path}


class TestListAllGroupsNarrows:
    def test_no_groups_configured_states_that_and_emits_no_legacy_rows(
        self, no_group_env
    ) -> None:
        # Prove the fixture is capable of showing the row it denies: without
        # the option, cwd resolves no group, so spine's legacy fallback
        # answers with the seeded legacy row.
        without_option = _camp_from(no_group_env, no_group_env["tmp_path"], "list")
        assert without_option.returncode == 0, without_option.stderr
        assert "legacy-slug" in without_option.stdout

        r = _camp_from(no_group_env, no_group_env["tmp_path"], "list", "--all-groups", "--json")
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        assert json.loads(r.stdout) == []
        assert "no groups configured" in r.stderr

    def test_legacy_registry_populated_no_row_from_it_appears_under_all_groups(
        self, tmp_path
    ) -> None:
        config_dir = tmp_path / "camp-config"
        groups_dir = config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)
        state_dir = tmp_path / "camp-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        workspace_root = tmp_path / "workspace-root"

        _write_group_toml(groups_dir, "onlygroup")
        _seed_legacy_worktree(workspace_root, "legacy-slug")

        env = {**os.environ}
        env["CAMP_CONFIG_DIR"] = str(config_dir)
        env["CAMP_STATE_DIR"] = str(state_dir)
        env["WORKSPACE_ROOT"] = str(workspace_root)
        env_dict = {"env": env}

        _seed_manifest_raw("onlygroup", "ws-only", state_dir=state_dir)

        # Prove the legacy row is reachable at all: from a cwd outside every
        # group, without the option, it appears.
        without_option = _camp_from(env_dict, tmp_path, "list")
        assert without_option.returncode == 0, without_option.stderr
        assert "legacy-slug" in without_option.stdout

        r = _camp_from(env_dict, tmp_path, "list", "--all-groups", "--json")
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        rows = json.loads(r.stdout)
        assert {row["slug"] for row in rows} == {"ws-only"}
        assert {row["group"] for row in rows} == {"onlygroup"}

    def test_one_unparsable_group_among_several_the_others_still_answer(
        self, tmp_path
    ) -> None:
        config_dir = tmp_path / "camp-config"
        groups_dir = config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)
        state_dir = tmp_path / "camp-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        _write_group_toml(groups_dir, "goodgroup")
        broken = groups_dir / "brokengroup.toml"
        broken.write_text("this is not [ valid toml")

        env = {**os.environ}
        env["CAMP_CONFIG_DIR"] = str(config_dir)
        env["CAMP_STATE_DIR"] = str(state_dir)
        env_dict = {"env": env}

        _seed_manifest_raw("goodgroup", "ws-good", state_dir=state_dir)

        r = _camp_from(env_dict, tmp_path, "list", "--all-groups", "--json")
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        rows = json.loads(r.stdout)
        # Every row carries `ok` — a consumer distinguishes a workspace row
        # from a failure row by that one field alone, never by testing
        # whether `row["slug"]` would raise.
        ok_rows = [row for row in rows if row["ok"]]
        failure_rows = [row for row in rows if row["ok"] is False]
        assert {row["slug"] for row in ok_rows} == {"ws-good"}
        assert {row["group"] for row in ok_rows} == {"goodgroup"}
        assert str(broken) in r.stderr
        assert "camp list: " in r.stderr

        # The parser-visible half of the same defect: an unparsable group
        # must not disappear from a --json array that otherwise looks
        # complete — it gets an in-band ok:false row naming the config file.
        assert len(failure_rows) == 1
        assert str(broken) in failure_rows[0]["reason"]
        assert failure_rows[0]["group"] is None

    def test_every_group_unparsable_states_reason_and_exits_nonzero(
        self, tmp_path
    ) -> None:
        config_dir = tmp_path / "camp-config"
        groups_dir = config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)
        state_dir = tmp_path / "camp-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        broken_a = groups_dir / "brokena.toml"
        broken_a.write_text("not [ valid")
        broken_b = groups_dir / "brokenb.toml"
        broken_b.write_text("also not ] valid")

        env = {**os.environ}
        env["CAMP_CONFIG_DIR"] = str(config_dir)
        env["CAMP_STATE_DIR"] = str(state_dir)
        env_dict = {"env": env}

        r = _camp_from(env_dict, tmp_path, "list", "--all-groups", "--json")
        assert r.returncode != 0
        assert r.stdout == ""
        assert "camp list: " in r.stderr
        assert str(broken_a) in r.stderr
        assert str(broken_b) in r.stderr

    def test_group_with_no_workspaces_contributes_no_rows_and_no_notice(
        self, tmp_path
    ) -> None:
        config_dir = tmp_path / "camp-config"
        groups_dir = config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)
        state_dir = tmp_path / "camp-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        _write_group_toml(groups_dir, "populated")
        _write_group_toml(groups_dir, "empty")
        _seed_manifest_raw("populated", "ws-pop", state_dir=state_dir)

        env = {**os.environ}
        env["CAMP_CONFIG_DIR"] = str(config_dir)
        env["CAMP_STATE_DIR"] = str(state_dir)
        env_dict = {"env": env}

        r = _camp_from(env_dict, tmp_path, "list", "--all-groups", "--json")
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        rows = json.loads(r.stdout)
        assert {row["slug"] for row in rows} == {"ws-pop"}
        assert {row["group"] for row in rows} == {"populated"}
        assert r.stderr == ""
