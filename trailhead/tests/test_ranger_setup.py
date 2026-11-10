"""Tests for `ranger setup` — the hub config + home writer. Every test fences HOME,
XDG roots and every per-app *_DIR override into tmp_path, so nothing reaches the
live install.
"""

import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from trailhead.harness.claude_code import ClaudeCodeHarness
from trailhead.install import run_install
from trailhead.launch_profile import hub_config_path, hub_home
from trailhead.pathint import create_shims
from trailhead.ranger.cli import main
from trailhead.ranger.config import (
    HubConfig,
    HubConfigError,
    list_hub_accounts,
    read_hub_config,
)


def _env(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "fence"
    return {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_STATE_HOME": str(root / "state"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "RANGER_CONFIG_DIR": str(root / "ranger-config"),
        "RANGER_STATE_DIR": str(root / "ranger-state"),
        "RANGER_CACHE_DIR": str(root / "ranger-cache"),
        "CAMP_CONFIG_DIR": str(root / "camp-config"),
        "CAMP_STATE_DIR": str(root / "camp-state"),
        "CAMP_CACHE_DIR": str(root / "camp-cache"),
        "PATH": os.environ.get("PATH", ""),
    }


def _setup(env, account="a", launch="default", name="Hub A", capsys=None):
    rc = main(
        ["setup", "--account", account, "--launch-account", launch, "--name", name],
        env=env,
    )
    return rc


def _write_camp_group(env, group, account):
    groups = Path(env["CAMP_CONFIG_DIR"]) / "groups"
    groups.mkdir(parents=True, exist_ok=True)
    (groups / f"{group}.toml").write_text(f'[launch]\naccount = "{account}"\n')


def test_setup_writes_config_with_given_values_and_creates_home_0700(tmp_path):
    env = _env(tmp_path)
    assert _setup(env, "a", "default", "Hub A") == 0
    path = hub_config_path("a", env=env)
    assert json.loads(path.read_text()) == {
        "name": "Hub A",
        "account": "a",
        "launch_account": "default",
    }
    home = hub_home("a", env=env)
    assert home.is_dir()
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert str(path).startswith(str(tmp_path / "fence"))


def test_setup_for_two_accounts_keeps_each_independent(tmp_path):
    env = _env(tmp_path)
    assert _setup(env, "a", "default", "Hub A") == 0
    assert _setup(env, "b", "/srv/claude-b", "Hub B") == 0
    assert json.loads(hub_config_path("a", env=env).read_text())["name"] == "Hub A"
    assert json.loads(hub_config_path("b", env=env).read_text())["launch_account"] == "/srv/claude-b"
    assert hub_home("a", env=env).is_dir() and hub_home("b", env=env).is_dir()
    assert list_hub_accounts(env=env) == ["a", "b"]


def test_identical_rerun_succeeds_and_leaves_the_file_untouched(tmp_path, capsys):
    env = _env(tmp_path)
    assert _setup(env) == 0
    path = hub_config_path("a", env=env)
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    assert _setup(env) == 0
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime


def test_different_values_refuse_naming_the_file_and_leave_it_unchanged(tmp_path, capsys):
    env = _env(tmp_path)
    assert _setup(env, "a", "default") == 0
    path = hub_config_path("a", env=env)
    before = path.read_bytes()
    capsys.readouterr()
    assert _setup(env, "a", "/srv/other") != 0
    err = capsys.readouterr().err
    assert err.startswith("ranger: ")
    assert str(path) in err
    assert path.read_bytes() == before


def test_a_different_name_also_refuses(tmp_path, capsys):
    env = _env(tmp_path)
    assert _setup(env, "a", "default", "Hub A") == 0
    path = hub_config_path("a", env=env)
    before = path.read_bytes()
    assert _setup(env, "a", "default", "Renamed") != 0
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "content",
    ["{not json", "[]", '{"name": "x"}', '{"name": 1, "account": "a", "launch_account": "default"}'],
)
def test_malformed_existing_config_refuses_naming_the_file(tmp_path, capsys, content):
    env = _env(tmp_path)
    path = hub_config_path("a", env=env)
    path.parent.mkdir(parents=True)
    path.write_text(content)
    assert _setup(env, "a", "default") != 0
    err = capsys.readouterr().err
    assert err.startswith("ranger: ")
    assert str(path) in err
    assert path.read_text() == content


@pytest.mark.parametrize(
    "account,launch",
    [
        ("Bad_Account", "default"),
        ("-lead", "default"),
        ("a", "relative/path"),
        ("a", "~user/x"),
        ("a", "/srv/ok\x07bell"),
        ("a", "/srv/ok\x00nul"),
        ("a", ""),
    ],
)
def test_invalid_account_or_launch_account_is_refused_and_nothing_is_written(
    tmp_path, capsys, account, launch
):
    env = _env(tmp_path)
    assert _setup(env, account, launch) != 0
    assert capsys.readouterr().err.startswith("ranger: ")
    for key in ("RANGER_CONFIG_DIR", "RANGER_STATE_DIR"):
        assert not Path(env[key]).exists()


@pytest.mark.parametrize("launch", ["default", "/srv/claude-a", "~", "~/accounts/a"])
def test_launch_account_shapes_that_are_accepted(tmp_path, launch):
    env = _env(tmp_path)
    assert _setup(env, "a", launch) == 0
    assert read_hub_config("a", env=env).launch_account == launch


def test_undeclared_launch_account_is_written_with_a_warning(tmp_path, capsys):
    env = _env(tmp_path)
    _write_camp_group(env, "g", "/srv/other")
    assert _setup(env, "a", "/srv/claude-a") == 0
    err = capsys.readouterr().err
    assert "ranger: warning:" in err
    assert "/srv/claude-a" in err
    assert read_hub_config("a", env=env).launch_account == "/srv/claude-a"


def test_launch_account_a_camp_group_declares_gets_no_warning(tmp_path, capsys):
    env = _env(tmp_path)
    _write_camp_group(env, "g", "/srv/claude-a")
    assert _setup(env, "a", "/srv/claude-a") == 0
    assert "warning" not in capsys.readouterr().err


def test_tilde_launch_account_matches_a_group_declaring_its_expansion(tmp_path, capsys):
    env = _env(tmp_path)
    _write_camp_group(env, "g", f"{env['HOME']}/acct")
    assert _setup(env, "a", "~/acct") == 0
    assert "warning" not in capsys.readouterr().err


def test_default_launch_account_never_warns(tmp_path, capsys):
    env = _env(tmp_path)
    assert _setup(env, "a", "default") == 0
    assert "warning" not in capsys.readouterr().err


def test_unparseable_camp_group_does_not_block_setup(tmp_path, capsys):
    env = _env(tmp_path)
    groups = Path(env["CAMP_CONFIG_DIR"]) / "groups"
    groups.mkdir(parents=True)
    (groups / "broken.toml").write_text("[launch\n")
    assert _setup(env, "a", "/srv/claude-a") == 0
    assert "ranger: warning:" in capsys.readouterr().err


def test_default_round_trips_through_the_reader_to_the_unset_binding(tmp_path):
    env = _env(tmp_path)
    assert _setup(env, "a", "default") == 0
    stored = read_hub_config("a", env=env).launch_account
    assert stored == "default"
    harness = ClaudeCodeHarness()
    account = None if stored == "default" else stored
    assert harness.session_launch_env_set(account, env=env) == {}


def test_declared_launch_account_round_trips_to_a_config_dir_binding(tmp_path):
    env = _env(tmp_path)
    assert _setup(env, "a", "/srv/claude-a") == 0
    stored = read_hub_config("a", env=env).launch_account
    account = None if stored == "default" else stored
    assert ClaudeCodeHarness().session_launch_env_set(account, env=env) == {
        "CLAUDE_CONFIG_DIR": "/srv/claude-a"
    }


def test_reader_returns_the_record_and_refuses_absent_and_malformed(tmp_path):
    env = _env(tmp_path)
    with pytest.raises(HubConfigError, match="a.json"):
        read_hub_config("a", env=env)
    assert _setup(env, "a", "default", "Hub A") == 0
    assert read_hub_config("a", env=env) == HubConfig(
        name="Hub A", account="a", launch_account="default"
    )
    hub_config_path("a", env=env).write_text("{")
    with pytest.raises(HubConfigError, match="a.json"):
        read_hub_config("a", env=env)


def test_lister_is_empty_without_hubs_and_ignores_non_account_files(tmp_path):
    env = _env(tmp_path)
    assert list_hub_accounts(env=env) == []
    assert _setup(env, "b") == 0
    assert _setup(env, "a") == 0
    hub_config_path("a", env=env).with_name("Notes.txt").write_text("x")
    hub_config_path("a", env=env).with_name(".a.json.tmp").write_text("x")
    assert list_hub_accounts(env=env) == ["a", "b"]


def test_a_failed_write_leaves_no_partial_config(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    assert _setup(env, "a") != 0
    assert capsys.readouterr().err.startswith("ranger: ")
    assert not hub_config_path("a", env=env).exists()
    assert list(hub_config_path("a", env=env).parent.iterdir()) == []


def test_missing_arguments_print_a_ranger_prefixed_error(tmp_path, capsys):
    rc = main(["setup", "--account", "a"], env=_env(tmp_path))
    assert rc != 0
    assert capsys.readouterr().err.startswith("ranger: ")


def test_ranger_shim_from_the_install_path_runs_setup(tmp_path):  # inert-gate: allow wiring seam smoke
    env = _env(tmp_path)
    run_env = {**env, "TRAILHEAD_STATE_DIR": str(tmp_path / "th-state"),
               "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude-dir")}
    (tmp_path / "fence" / "home").mkdir(parents=True)
    with (
        patch("trailhead.install.wire"),
        patch("trailhead.install.detect_harnesses", return_value=[]),
        patch("trailhead.install.run_lore_init", return_value=(0, "")),
        patch("trailhead.install.write_stamp", return_value=None),
        patch("trailhead.install.create_shims", wraps=create_shims),
    ):
        run_install(env=run_env, quiet=True)
    shim = Path(run_env["TRAILHEAD_STATE_DIR"]) / "bin" / "ranger"
    result = subprocess.run(
        [str(shim), "setup", "--account", "a", "--launch-account", "default", "--name", "Hub A"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(hub_config_path("a", env=env).read_text())["name"] == "Hub A"
