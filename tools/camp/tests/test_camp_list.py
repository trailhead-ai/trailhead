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

import importlib.machinery
import importlib.util
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
_SCRIPTS_DIR = _PLUGIN_DIR / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _load_cli_module():
    spec = importlib.util.spec_from_loader(
        "camp_cli", importlib.machinery.SourceFileLoader("camp_cli", str(_CLI_CAMP))
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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

    _FIXED_KEYS = {"slug", "branch", "workspace_path", "group"}

    def test_json_carries_workspace_path(self, camp_cli, tmp_path, capsys):
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        ws = _seed_manifest("listgrp", "feat-x", env=env)

        camp_cli._cmd_ls_group_cli(["--json"], group, env)

        rows = json.loads(capsys.readouterr().out)
        assert len(rows) == 1
        assert rows[0]["workspace_path"] == str(ws)
        assert rows[0]["slug"] == "feat-x"

    def test_json_schema_is_the_fixed_key_set(self, camp_cli, tmp_path, capsys):
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        _seed_manifest("listgrp", "feat-x", env=env)

        camp_cli._cmd_ls_group_cli(["--json"], group, env)

        rows = json.loads(capsys.readouterr().out)
        assert set(rows[0].keys()) == self._FIXED_KEYS, (
            "camp list --json must emit exactly the shared schema"
        )
        assert rows[0]["group"] == "listgrp"

    def test_json_empty_group_is_empty_array(self, camp_cli, tmp_path, capsys):
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}

        camp_cli._cmd_ls_group_cli(["--json"], group, env)

        assert json.loads(capsys.readouterr().out) == []

    def test_shared_renderer_projects_both_sources_to_one_schema(self, capsys):
        """The renderer projects group-style and spine-style entries (different
        source keys) onto the SAME fixed schema."""
        sys.path.insert(0, str(_SCRIPTS_DIR))
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

    def test_empty_group_no_error_message_on_stdout(self, camp_cli, tmp_path, capsys):
        """The 'no camps' message (if any) must not appear on stdout."""
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}

        camp_cli._cmd_ls_group_cli([], group, env)

        out = capsys.readouterr().out
        assert "no camps" not in out.lower(), (
            f"'no camps' message must not go to stdout, got {out!r}"
        )

    def test_empty_group_exits_zero(self, camp_cli, tmp_path):
        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}

        try:
            camp_cli._cmd_ls_group_cli([], group, env)
        except SystemExit as e:
            assert e.code == 0 or e.code is None, (
                f"empty group list must exit 0, got SystemExit({e.code})"
            )


# ---------------------------------------------------------------------------
# Purity tests: no state mutation, no harness exec
# ---------------------------------------------------------------------------


class TestListPureRead:
    """camp list is a pure read: no state mutation and no harness launch."""

    def test_handler_source_has_no_exec_call(self):
        """The list handler must not call os.execvp or os.execv."""
        mod = _load_cli_module()
        src = inspect.getsource(mod._cmd_ls_group_cli)
        assert "execvp" not in src, "_cmd_ls_group_cli must not call os.execvp"
        assert "execv" not in src, "_cmd_ls_group_cli must not call os.execv"

    def test_list_does_not_call_write_manifest(self, camp_cli, tmp_path, monkeypatch):
        """camp list must not call write_central_manifest (pure read)."""
        import camp.group.manifest as _manifest

        group = _make_group("listgrp")
        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        # Seed the manifest BEFORE patching so the seed itself doesn't trip the spy.
        _seed_manifest("listgrp", "feat-x", env=env)

        writes: list = []
        original = _manifest.write_central_manifest
        monkeypatch.setattr(
            _manifest,
            "write_central_manifest",
            lambda *a, **k: (writes.append(a), original(*a, **k))[1],
        )

        camp_cli._cmd_ls_group_cli([], group, env)

        assert writes == [], (
            f"camp list must not call write_central_manifest; got {len(writes)} call(s)"
        )


# ---------------------------------------------------------------------------
# Subprocess integration tests
# ---------------------------------------------------------------------------


def _write_group_toml(groups_dir: Path, group_name: str) -> None:
    """Write a minimal group config TOML with a non-existent (fake) repo_root."""
    (groups_dir / f"{group_name}.toml").write_text(
        f'[group]\nname = "{group_name}"\n\n'
        f"[[members]]\nname = \"repo_a\"\nrepo_root = \"/nonexistent/repo\"\n\n"
        f'[branch]\npattern = "worktree-{{slug}}"\n'
    )


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
