"""Tests for the ``session_start`` harness seam, its Claude Code implementation, and the
``trailhead session-start`` command. Every test fences HOME, XDG roots and CLAUDE_CONFIG_DIR
into tmp_path, so nothing reaches the live install or the live user settings.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from trailhead import cli
from trailhead.harness.base import Harness, HarnessError, SessionStart
from trailhead.harness.claude_code import ClaudeCodeHarness
from trailhead.launch_profile import (
    HUB_PROFILE,
    LaunchProfile,
    hub_home,
    resolve_profile,
)
from trailhead.ranger.config import HubConfig, write_hub_config

REPO = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "fixtures" / "session_start_hub.json"
ROOT_TOKEN = "<ROOT>"


def _fence(tmp_path: Path) -> dict[str, str]:
    root = tmp_path.resolve() / "fence"
    return {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_STATE_HOME": str(root / "state"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "RANGER_CONFIG_DIR": str(root / "ranger-config"),
        "RANGER_STATE_DIR": str(root / "ranger-state"),
        "RANGER_CACHE_DIR": str(root / "ranger-cache"),
        "LORE_STATE_DIR": str(root / "lore-state"),
        "OUTPOST_STATE_DIR": str(root / "outpost-state"),
        "PATH": os.environ.get("PATH", ""),
    }


def _write_user_settings(env: dict[str, str], data, *, account_dir: Path | None = None) -> Path:
    base = account_dir if account_dir is not None else Path(env["HOME"]) / ".claude"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "settings.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def _user_sandbox(**fs_and_net) -> dict:
    sandbox: dict = {"enabled": True}
    if "allowWrite" in fs_and_net or "denyWrite" in fs_and_net or "denyRead" in fs_and_net:
        sandbox["filesystem"] = {
            k: fs_and_net[k] for k in ("allowWrite", "denyWrite", "denyRead") if k in fs_and_net
        }
    if "allowedDomains" in fs_and_net:
        sandbox["network"] = {"allowedDomains": fs_and_net["allowedDomains"]}
    return {"sandbox": sandbox}


def _resolved(env: dict[str, str], profile: LaunchProfile = HUB_PROFILE):
    return resolve_profile(profile, "a", env=env, platform="linux")


def _start(env, *, profile=HUB_PROFILE, prompt=None, interactive=True, account=None, **extra):
    resolved = _resolved(env, profile)
    home = hub_home("a", env=env, platform="linux")
    home.mkdir(parents=True, exist_ok=True)
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    return ClaudeCodeHarness().session_start(
        resolved, home, prompt, interactive=interactive, account=account, env=env, **extra
    )


def _settings_of(start: SessionStart) -> dict:
    return json.loads(start.argv[start.argv.index("--settings") + 1])


def _hub_roots(env) -> tuple[str, str, str]:
    r = _resolved(env)
    return tuple(str(p) for p in r.writable)  # hub-home, lore-state, cache


def _protected(env) -> list[str]:
    """The hub's own future-settings paths, which the hub may never write."""
    home = _hub_roots(env)[0]
    return [f"{home}/.claude", f"{home}/.mcp.json"]


def _user_allowing_hub(env) -> dict:
    home, lore, cache = _hub_roots(env)
    return _user_sandbox(allowWrite=[".", cache, lore])


@pytest.fixture
def off_temp(tmp_path):
    """A scratch directory that is not under the system temp dir, so a user allowWrite of
    /tmp is not an ancestor of the fenced hub roots (as on a real account)."""
    temp = Path(os.path.realpath(tempfile.gettempdir()))
    if temp not in tmp_path.resolve().parents and Path("/tmp").resolve() not in tmp_path.resolve().parents:
        yield tmp_path
        return
    base = Path(tempfile.mkdtemp(prefix="trailhead-offtemp-", dir=REPO))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _real_shaped_user(env, tmp_path) -> dict:
    """User settings shaped like a real account's: no '.', system temp, the lore and cache
    roots, and some repository .git directories."""
    home, lore, cache = _hub_roots(env)
    gits = [str(tmp_path.resolve() / "code" / name / ".git") for name in ("one", "two")]
    return _user_sandbox(
        allowWrite=["/tmp", "/private/tmp", lore, cache, *gits]
    )


# -- never-widen -------------------------------------------------------------


def test_launch_directory_counts_as_user_allowed_when_the_user_list_has_no_dot(off_temp):
    tmp_path = off_temp
    env = _fence(tmp_path)
    _write_user_settings(env, _real_shaped_user(env, tmp_path))
    fs = _settings_of(_start(env))["sandbox"]["filesystem"]
    assert fs["allowWrite"] == list(_hub_roots(env))


def test_a_root_outside_the_launch_directory_still_needs_the_users_list(off_temp):
    tmp_path = off_temp
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(env, _user_sandbox(allowWrite=["/tmp", cache]))
    with pytest.raises(HarnessError, match=lore):
        _start(env)


def test_system_temp_is_shared_scratch_and_never_derived_into_denywrite(off_temp):
    tmp_path = off_temp
    env = _fence(tmp_path)
    user = _real_shaped_user(env, tmp_path)
    _write_user_settings(env, user)
    deny = _settings_of(_start(env))["sandbox"]["filesystem"]["denyWrite"]
    gits = [p for p in user["sandbox"]["filesystem"]["allowWrite"] if p.endswith("/.git")]
    assert not {"/tmp", "/private/tmp", os.path.realpath("/tmp")} & set(deny)
    assert set(gits) <= set(deny)


def test_a_user_entry_aliasing_system_temp_is_not_denied_but_other_entries_are(off_temp):
    tmp_path = off_temp
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    alias = tmp_path.resolve() / "tmp-alias"
    alias.symlink_to(tempfile.gettempdir())
    other = str(tmp_path.resolve() / "other")
    _write_user_settings(env, _user_sandbox(allowWrite=[str(alias), lore, cache, other]))
    deny = _settings_of(_start(env))["sandbox"]["filesystem"]["denyWrite"]
    assert os.path.realpath(tempfile.gettempdir()) not in deny
    assert other in deny


def _deny_of(env, **extra) -> list[str]:
    return _settings_of(_start(env, **extra))["sandbox"]["filesystem"]["denyWrite"]


def test_tmuxs_socket_directory_under_allowed_system_temp_is_denied(off_temp):
    env = _fence(off_temp)
    _write_user_settings(env, _real_shaped_user(env, off_temp))
    socket_dir = os.path.join(os.path.realpath("/tmp"), "tmux-4242")
    assert socket_dir in _deny_of(env, uid=4242)


def test_tmuxs_socket_directory_follows_the_uid(off_temp):
    env = _fence(off_temp)
    _write_user_settings(env, _real_shaped_user(env, off_temp))
    tmp = os.path.realpath("/tmp")
    first, second = _deny_of(env, uid=1), _deny_of(env, uid=2)
    assert f"{tmp}/tmux-1" in first and f"{tmp}/tmux-2" not in first
    assert f"{tmp}/tmux-2" in second and f"{tmp}/tmux-1" not in second


def test_tmuxs_socket_directory_follows_tmux_tmpdir_when_it_is_under_an_allowed_root(off_temp):
    env = _fence(off_temp)
    home, lore, cache = _hub_roots(env)
    socket_root = Path(lore) / "tmuxroot"
    socket_root.mkdir(parents=True)
    env["TMUX_TMPDIR"] = str(socket_root)
    _write_user_settings(env, _real_shaped_user(env, off_temp))
    deny = _deny_of(env, uid=7)
    assert f"{os.path.realpath(socket_root)}/tmux-7" in deny
    assert f"{os.path.realpath('/tmp')}/tmux-7" not in deny


def test_tmuxs_socket_directory_outside_every_allowed_root_is_not_added(off_temp):
    env = _fence(off_temp)
    elsewhere = off_temp.resolve() / "sockets"
    env["TMUX_TMPDIR"] = str(elsewhere)
    _write_user_settings(env, _real_shaped_user(env, off_temp))
    assert not [d for d in _deny_of(env, uid=7) if "tmux-" in d]


def test_hub_profile_within_the_users_write_list_launches_and_denies_nothing(tmp_path):
    env = _fence(tmp_path)
    _write_user_settings(env, _user_allowing_hub(env))
    start = _start(env)
    fs = _settings_of(start)["sandbox"]["filesystem"]
    assert fs["allowWrite"] == list(_hub_roots(env))
    assert fs["denyWrite"] == [*_protected(env), *_later_vault_globs(env)]


def test_the_hubs_own_future_settings_are_denied_wherever_the_hub_home_is(tmp_path):
    first, second = _fence(tmp_path / "one"), _fence(tmp_path / "two")
    for env in (first, second):
        deny = _settings_of(_start(env))["sandbox"]["filesystem"]["denyWrite"]
        home = _hub_roots(env)[0]
        assert deny == [f"{home}/.claude", f"{home}/.mcp.json", *_later_vault_globs(env)]
    assert _protected(first) != _protected(second)


def test_user_writable_root_outside_the_hub_roots_is_derived_into_denywrite(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    extra = str(tmp_path.resolve() / "elsewhere")
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache, lore, extra]))
    fs = _settings_of(_start(env))["sandbox"]["filesystem"]
    assert fs["denyWrite"] == [*_protected(env), extra, *_later_vault_globs(env)]
    assert not set(fs["denyWrite"]) & set(fs["allowWrite"])


def test_user_write_entry_is_resolved_before_it_is_compared_to_a_hub_root(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    Path(cache).parent.mkdir(parents=True, exist_ok=True)
    alias = tmp_path.resolve() / "cache-alias"
    alias.symlink_to(Path(cache).parent)
    _write_user_settings(
        env, _user_sandbox(allowWrite=[".", str(alias / Path(cache).name), lore])
    )
    assert _settings_of(_start(env))["sandbox"]["filesystem"]["denyWrite"] == [
        *_protected(env), *_later_vault_globs(env)
    ]


def test_hub_write_root_the_users_list_does_not_cover_is_refused_naming_it(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache]))
    with pytest.raises(HarnessError, match=lore):
        _start(env)


def test_user_write_entry_that_contains_a_hub_root_is_refused_naming_the_entry(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    ancestor = str(Path(home).parent)
    _write_user_settings(env, _user_sandbox(allowWrite=[ancestor, cache, lore]))
    with pytest.raises(HarnessError, match=ancestor):
        _start(env)


def test_user_write_entry_sharing_only_a_string_prefix_with_a_hub_root_is_not_an_ancestor(
    tmp_path,
):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    sibling = home + "-sibling"
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache, lore, sibling]))
    assert _settings_of(_start(env))["sandbox"]["filesystem"]["denyWrite"] == [
        *_protected(env), sibling, *_later_vault_globs(env)
    ]


def test_user_deny_write_covering_a_hub_root_is_refused_naming_the_entry(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache, lore], denyWrite=[cache]))
    with pytest.raises(HarnessError, match=cache):
        _start(env)


def test_network_host_absent_from_the_users_allow_list_is_refused_naming_it(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(
        env,
        _user_sandbox(allowWrite=[".", cache, lore], allowedDomains=["*.github.com"]),
    )
    profile = LaunchProfile(
        writable_roles=HUB_PROFILE.writable_roles,
        network_allowed_hosts=("api.github.com", "evil.example"),
    )
    with pytest.raises(HarnessError, match="evil.example"):
        _start(env, profile=profile)


def test_network_hosts_the_users_list_covers_launch_and_reach_the_settings(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(
        env,
        _user_sandbox(
            allowWrite=[".", cache, lore], allowedDomains=["*.github.com", "pypi.org"]
        ),
    )
    profile = LaunchProfile(
        writable_roles=HUB_PROFILE.writable_roles,
        network_allowed_hosts=("api.github.com", "pypi.org"),
    )
    network = _settings_of(_start(env, profile=profile))["sandbox"]["network"]
    assert network["allowedDomains"] == ["api.github.com", "pypi.org"]


def test_wildcard_user_domain_does_not_cover_its_own_apex(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(
        env, _user_sandbox(allowWrite=[".", cache, lore], allowedDomains=["*.github.com"])
    )
    profile = LaunchProfile(
        writable_roles=HUB_PROFILE.writable_roles, network_allowed_hosts=("github.com",)
    )
    with pytest.raises(HarnessError, match="github.com"):
        _start(env, profile=profile)


def test_read_the_user_denies_but_the_profile_allows_is_refused_naming_it(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    sandbox = _user_sandbox(allowWrite=[".", cache, lore], denyRead=[lore])
    _write_user_settings(env, sandbox)
    with pytest.raises(HarnessError, match=lore):
        _start(env)


def test_user_read_permission_rule_over_a_hub_root_is_refused_naming_it(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    settings = _user_sandbox(allowWrite=[".", cache, lore])
    settings["permissions"] = {"deny": [f"Read(/{cache}/**)"]}
    _write_user_settings(env, settings)
    with pytest.raises(HarnessError, match=cache):
        _start(env)


def test_user_read_denial_elsewhere_does_not_block_the_launch(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    other = str(tmp_path.resolve() / "secrets")
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache, lore], denyRead=[other]))
    assert _start(env) is not None


@pytest.mark.parametrize("content", [None, {}, {"model": "opus"}, {"sandbox": {"enabled": True}}])
def test_with_no_user_sandbox_block_the_profiles_own_narrowing_still_applies(tmp_path, content):
    env = _fence(tmp_path)
    if content is not None:
        _write_user_settings(env, content)
    fs = _settings_of(_start(env))["sandbox"]["filesystem"]
    assert fs["allowWrite"] == list(_hub_roots(env))
    assert fs["denyWrite"] == [*_protected(env), *_later_vault_globs(env)]


@pytest.mark.parametrize(
    "content",
    ["{not json", "[]", {"sandbox": "yes"}, {"sandbox": {"enabled": "maybe"}}],
)
def test_malformed_user_settings_refuse_the_launch(tmp_path, content):
    env = _fence(tmp_path)
    _write_user_settings(env, content)
    with pytest.raises(HarnessError, match="settings.json"):
        _start(env)


def test_unreadable_user_settings_refuse_the_launch(tmp_path):
    env = _fence(tmp_path)
    (Path(env["HOME"]) / ".claude" / "settings.json").mkdir(parents=True)
    with pytest.raises(HarnessError, match="settings.json"):
        _start(env)


def test_a_declared_account_reads_its_own_user_settings_not_the_default_ones(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    acct = tmp_path.resolve() / "claude-x"
    acct.mkdir()
    _write_user_settings(env, _user_allowing_hub(env))  # default account: permissive
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache]), account_dir=acct)
    assert _start(env) is not None
    with pytest.raises(HarnessError, match=lore):
        _start(env, account=str(acct))


def test_a_refused_launch_does_not_touch_the_trust_file(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache]))
    config = Path(env["HOME"]) / ".claude.json"
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    config.write_text('{"keep": 1}')
    with pytest.raises(HarnessError):
        _start(env)
    assert json.loads(config.read_text()) == {"keep": 1}


# -- the hub home must be an existing directory ------------------------------


def _start_in(env, directory):
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    return ClaudeCodeHarness().session_start(
        _resolved(env), directory, None, interactive=True, account=None, env=env
    )


def test_a_present_hub_home_launches(tmp_path):
    env = _fence(tmp_path)
    home = hub_home("a", env=env, platform="linux")
    home.mkdir(parents=True)
    assert _start_in(env, home) is not None


@pytest.mark.parametrize("kind", ["absent", "regular-file"])
def test_a_hub_home_that_is_not_a_directory_is_refused_naming_the_setup_fix_and_seeds_no_trust(
    tmp_path, kind
):
    env = _fence(tmp_path)
    home = hub_home("a", env=env, platform="linux")
    if kind == "regular-file":
        home.parent.mkdir(parents=True)
        home.write_text("not a directory")
    config = Path(env["HOME"]) / ".claude.json"
    Path(env["HOME"]).mkdir(parents=True)
    config.write_text('{"keep": 1}')
    with pytest.raises(HarnessError, match=r"ranger setup --account <A>") as excinfo:
        _start_in(env, home)
    assert str(home) in str(excinfo.value)
    assert json.loads(config.read_text()) == {"keep": 1}


def test_cli_refuses_a_missing_hub_home_with_a_clean_message(tmp_path, monkeypatch, capsys):
    env = _fence(tmp_path)
    _configure_hub(env)
    shutil.rmtree(hub_home("a", env=env), ignore_errors=True)
    _write_user_settings(env, _user_allowing_hub(env))
    rc = _run_cli(monkeypatch, env, ["session-start", "--role", "hub", "--account", "a", "--json"])
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and "ranger setup --account <A>" in captured.err


# -- posture and account binding --------------------------------------------


def test_interactive_launch_carries_dontask_and_no_print_mode(tmp_path):
    env = _fence(tmp_path)
    argv = _start(env, interactive=True, prompt="hello").argv
    assert argv[0] == "claude"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "-p" not in argv and "--permission-prompts" not in argv
    assert argv[-1] == "hello"


def test_headless_launch_carries_print_mode_and_no_prompt_answering(tmp_path):
    env = _fence(tmp_path)
    argv = _start(env, interactive=False, prompt="hello").argv
    assert "-p" in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert argv[-1] == "hello"


def test_prompt_is_omitted_when_none_and_a_flag_shaped_prompt_is_refused(tmp_path):
    env = _fence(tmp_path)
    assert _start(env, prompt=None).argv[-1] != "None"
    with pytest.raises(HarnessError, match="prompt"):
        _start(env, prompt="--dangerously-skip-permissions")


def test_effort_comes_from_the_profile(tmp_path):
    env = _fence(tmp_path)
    argv = _start(env).argv
    assert argv[argv.index("--effort") + 1] == "medium"
    high = LaunchProfile(writable_roles=HUB_PROFILE.writable_roles, effort="high")
    argv = _start(env, profile=high).argv
    assert argv[argv.index("--effort") + 1] == "high"
    none = LaunchProfile(writable_roles=HUB_PROFILE.writable_roles, effort=None)
    assert "--effort" not in _start(env, profile=none).argv


def test_default_account_unsets_the_config_dir_variable(tmp_path):
    env = _fence(tmp_path)
    start = _start(env, account=None)
    assert start.env_set == {}
    assert "CLAUDE_CONFIG_DIR" in start.env_unset


def test_declared_account_sets_the_config_dir_to_the_expanded_path(tmp_path):
    env = _fence(tmp_path)
    acct = Path(env["HOME"]) / ".claude-x"
    acct.mkdir(parents=True)
    start = _start(env, account="~/.claude-x")
    assert start.env_set == {"CLAUDE_CONFIG_DIR": str(acct)}


# -- settings shape ---------------------------------------------------------


def test_settings_place_resolved_paths_under_the_keys_claude_reads(tmp_path):
    env = _fence(tmp_path)
    resolved = _resolved(env)
    token = str(resolved.paths["token-store"])
    settings = _settings_of(_start(env))
    sandbox = settings["sandbox"]
    assert sandbox["enabled"] is True and sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["filesystem"]["allowWrite"] == [str(p) for p in resolved.writable]
    assert sandbox["filesystem"]["denyRead"] == [token]
    assert settings["permissions"]["deny"] == [f"Read(/{token}/**)"]
    assert "model" not in settings
    assert settings["enabledMcpjsonServers"] == []


def _bash_denies(start: SessionStart) -> list[str]:
    return [r for r in _settings_of(start)["permissions"]["deny"] if r.startswith("Bash(")]


def test_no_user_exclusions_adds_no_bash_deny_rules(tmp_path):
    env = _fence(tmp_path)
    _write_user_settings(env, {"sandbox": {"enabled": True, "excludedCommands": []}})
    assert _bash_denies(_start(env)) == []
    _write_user_settings(env, {})
    assert _bash_denies(_start(env)) == []


def test_each_user_unsandboxed_command_exemption_is_denied_in_the_session(tmp_path):
    env = _fence(tmp_path)
    exclusions = ["claude plugin eval *", "portage eval-gate *"]
    settings = _user_allowing_hub(env)
    settings["sandbox"]["excludedCommands"] = exclusions
    _write_user_settings(env, settings)
    start = _start(env)
    assert _bash_denies(start) == [f"Bash({e})" for e in exclusions]
    assert len(_settings_of(start)["permissions"]["deny"]) == 1 + len(exclusions)


@pytest.mark.parametrize("bad", ["claude plugin eval *", [1], {"a": "b"}])
def test_malformed_user_excluded_commands_refuse_the_launch(tmp_path, bad):
    env = _fence(tmp_path)
    _write_user_settings(env, {"sandbox": {"enabled": True, "excludedCommands": bad}})
    with pytest.raises(HarnessError, match="excludedCommands"):
        _start(env)


def test_model_and_mcp_servers_in_the_profile_reach_the_settings(tmp_path):
    env = _fence(tmp_path)
    profile = LaunchProfile(
        writable_roles=HUB_PROFILE.writable_roles,
        mcp_servers=("lore",),
        model="some-model",
    )
    settings = _settings_of(_start(env, profile=profile))
    assert settings["model"] == "some-model"
    assert settings["enabledMcpjsonServers"] == ["lore"]


def test_settings_are_inline_json_so_no_file_exists_for_the_hub_to_edit(tmp_path):
    env = _fence(tmp_path)
    before = {p for p in Path(env["HOME"]).rglob("*")}
    _start(env)
    new = {p for p in Path(env["HOME"]).rglob("*")} - before
    assert {p.name for p in new if p.is_file()} <= {".claude.json"}


def test_session_start_result_is_frozen(tmp_path):
    start = _start(_fence(tmp_path))
    with pytest.raises(FrozenInstanceError):
        start.argv = []  # type: ignore[misc]


# -- folder-trust pre-seed ----------------------------------------------------


def test_trust_is_seeded_for_the_realpath_of_the_directory_preserving_other_keys(tmp_path):
    env = _fence(tmp_path)
    home = Path(env["HOME"])
    home.mkdir(parents=True)
    config = home / ".claude.json"
    config.write_text(
        json.dumps(
            {
                "numStartups": 7,
                "projects": {"/other": {"hasTrustDialogAccepted": False, "allowedTools": ["x"]}},
            }
        )
    )
    hub = hub_home("a", env=env, platform="linux")
    hub.mkdir(parents=True)
    link = tmp_path.resolve() / "hub-link"
    link.symlink_to(hub)
    ClaudeCodeHarness().session_start(
        _resolved(env), link, None, interactive=True, account=None, env=env
    )
    data = json.loads(config.read_text())
    assert data["numStartups"] == 7
    assert data["projects"]["/other"] == {"hasTrustDialogAccepted": False, "allowedTools": ["x"]}
    assert data["projects"][str(hub.resolve())]["hasTrustDialogAccepted"] is True
    assert str(link) not in data["projects"]


def test_trust_keeps_the_keys_of_an_existing_project_entry(tmp_path):
    env = _fence(tmp_path)
    hub = hub_home("a", env=env, platform="linux")
    hub.mkdir(parents=True)
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    config = Path(env["HOME"]) / ".claude.json"
    config.write_text(json.dumps({"projects": {str(hub.resolve()): {"lastCost": 3}}}))
    _start(env)
    entry = json.loads(config.read_text())["projects"][str(hub.resolve())]
    assert entry == {"lastCost": 3, "hasTrustDialogAccepted": True}


def test_trust_creates_the_config_when_absent(tmp_path):
    env = _fence(tmp_path)
    _start(env)
    hub = hub_home("a", env=env, platform="linux").resolve()
    data = json.loads((Path(env["HOME"]) / ".claude.json").read_text())
    assert data == {"projects": {str(hub): {"hasTrustDialogAccepted": True}}}


def test_trust_for_a_declared_account_is_written_in_its_config_dir_not_home(tmp_path):
    env = _fence(tmp_path)
    acct = tmp_path.resolve() / "claude-x"
    acct.mkdir()
    _start(env, account=str(acct))
    hub = hub_home("a", env=env, platform="linux").resolve()
    data = json.loads((acct / ".claude.json").read_text())
    assert data["projects"][str(hub)]["hasTrustDialogAccepted"] is True
    assert not (Path(env["HOME"]) / ".claude.json").exists()


@pytest.mark.parametrize("content", ["{not json", "[]", '{"projects": 3}'])
def test_malformed_trust_config_refuses_and_is_left_untouched(tmp_path, content):
    env = _fence(tmp_path)
    Path(env["HOME"]).mkdir(parents=True)
    config = Path(env["HOME"]) / ".claude.json"
    config.write_text(content)
    with pytest.raises(HarnessError, match=r"\.claude\.json"):
        _start(env)
    assert config.read_text() == content


def test_declared_account_whose_config_dir_is_absent_is_refused(tmp_path):
    env = _fence(tmp_path)
    with pytest.raises(HarnessError, match="claude-missing"):
        _start(env, account=str(tmp_path.resolve() / "claude-missing"))


# -- base class and CLI ------------------------------------------------------


class _NoLaunchHarness(Harness):
    name = "nolaunch"

    @classmethod
    def detect(cls, env):
        return True

    def generate_manifest(self, tools, composed_root):
        raise NotImplementedError

    def is_registered(self, composed_root, *, env=None):
        raise NotImplementedError

    def is_installed(self, tool, composed_root, *, env=None):
        raise NotImplementedError

    def installed_tools(self, composed_root, *, env=None):
        raise NotImplementedError

    def register(self, composed_root, *, runner=None, env=None):
        raise NotImplementedError

    def install_tool(self, tool, composed_root, *, runner=None, env=None):
        raise NotImplementedError

    def rewire_tool(self, tool, composed_root, *, runner=None, env=None):
        raise NotImplementedError

    def unregister_tool(self, tool, composed_root, *, runner=None, env=None):
        raise NotImplementedError

    def unregister_marketplace(self, *, runner=None, env=None):
        raise NotImplementedError


def _configure_hub(env, launch="default"):
    write_hub_config(HubConfig(name="Hub A", account="a", launch_account=launch), env=env, platform="linux")


def _run_cli(monkeypatch, env, argv):
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.argv", ["trailhead", *argv])
    return cli.main()


def test_cli_refuses_when_the_seam_answers_none(tmp_path, monkeypatch, capsys):
    env = _fence(tmp_path)
    _configure_hub(env)
    monkeypatch.setattr(cli, "detect_harnesses", lambda e: [_NoLaunchHarness()])
    rc = _run_cli(monkeypatch, env, ["session-start", "--role", "hub", "--account", "a", "--json"])
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and "unexpected" not in captured.err


def test_cli_refuses_when_no_hub_is_configured(tmp_path, monkeypatch, capsys):
    env = _fence(tmp_path)
    (Path(env["HOME"]) / ".claude").mkdir(parents=True)
    rc = _run_cli(monkeypatch, env, ["session-start", "--role", "hub", "--account", "a", "--json"])
    err = capsys.readouterr().err
    assert rc != 0 and err.startswith("trailhead: ") and "a.json" in err


def test_cli_refuses_a_never_widen_violation_with_a_clean_message(tmp_path, monkeypatch, capsys):
    env = _fence(tmp_path)
    _configure_hub(env)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache]))
    rc = _run_cli(monkeypatch, env, ["session-start", "--role", "hub", "--account", "a", "--json"])
    err = capsys.readouterr().err
    assert rc != 0 and err.startswith("trailhead: ") and lore in err


@pytest.mark.parametrize("kind", ["missing", "directory", "not-utf8"])
def test_cli_refuses_an_unreadable_prompt_file_with_a_clean_message(
    tmp_path, monkeypatch, capsys, kind
):
    env = _fence(tmp_path)
    _configure_hub(env)
    _write_user_settings(env, _user_allowing_hub(env))
    path = tmp_path / "prompt"
    if kind == "directory":
        path.mkdir()
    elif kind == "not-utf8":
        path.write_bytes(b"\xff\xfe\x00")
    rc = _run_cli(
        monkeypatch,
        env,
        ["session-start", "--role", "hub", "--account", "a", "--json", "--headless",
         "--prompt-file", str(path)],
    )
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and str(path) in captured.err
    assert "unexpected" not in captured.err and "Traceback" not in captured.err


def test_cli_prints_argv_env_and_directory_and_headless_reads_the_prompt_file(
    tmp_path, monkeypatch, capsys
):
    env = _fence(tmp_path)
    _configure_hub(env)
    _write_user_settings(env, _user_allowing_hub(env))
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("do the thing\n")
    rc = _run_cli(
        monkeypatch,
        env,
        ["session-start", "--role", "hub", "--account", "a", "--json", "--headless",
         "--prompt-file", str(prompt)],
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert set(out) == {"argv", "env_set", "env_unset", "directory"}
    assert "-p" in out["argv"] and out["argv"][-1] == "do the thing\n"
    assert out["directory"] == str(hub_home("a", env=env))
    rc = _run_cli(monkeypatch, env, ["session-start", "--role", "hub", "--account", "a", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert "-p" not in out["argv"]


def test_cli_maps_launch_account_default_and_declared_to_the_env_binding(
    tmp_path, monkeypatch, capsys
):
    env = _fence(tmp_path)
    acct = Path(env["HOME"]) / ".claude-x"
    acct.mkdir(parents=True)
    (Path(env["HOME"]) / ".claude").mkdir()
    _configure_hub(env, launch="~/.claude-x")
    _write_user_settings(env, _user_allowing_hub(env), account_dir=acct)
    rc = _run_cli(monkeypatch, env, ["session-start", "--role", "hub", "--account", "a", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["env_set"] == {"CLAUDE_CONFIG_DIR": str(acct)}


# -- git administrative files under the lore state root ----------------------

_GIT_ADMIN = ("config", "hooks", "info")


def _admin(git_dir) -> list[str]:
    return [f"{git_dir}/{name}" for name in _GIT_ADMIN]


def _make_vault(lore: str, name: str) -> Path:
    vault = Path(lore) / "vaults" / name
    (vault / ".git").mkdir(parents=True)
    (vault / "record.md").write_text("x")
    return vault


def _vault_denials(env) -> list[str]:
    lore = Path(_hub_roots(env)[1])
    return [d for d in _deny_of(env) if d.startswith(str(lore) + "/") and "/.git" in d]


def _later_vault_globs(env) -> list[str]:
    return _admin(f"{_hub_roots(env)[1]}/*/*/.git")


def test_no_vaults_denies_only_the_glob_for_vaults_created_later(tmp_path):
    env = _fence(tmp_path)
    assert _vault_denials(env) == _later_vault_globs(env)


def test_each_vault_gets_its_config_hooks_and_info_denied(tmp_path):
    env = _fence(tmp_path)
    one = _make_vault(_hub_roots(env)[1], "one")
    two = _make_vault(_hub_roots(env)[1], "two")
    denied = _vault_denials(env)
    assert set(_admin(one / ".git") + _admin(two / ".git")) <= set(denied)
    assert not any(d.startswith(f"{one}/record") for d in denied)


def test_a_single_vault_adds_exactly_its_three_entries(tmp_path):
    env = _fence(tmp_path)
    one = _make_vault(_hub_roots(env)[1], "one")
    assert set(_vault_denials(env)) == set(_admin(one / ".git")) | set(_later_vault_globs(env))


def test_a_directory_without_a_git_dir_gets_no_entries(tmp_path):
    env = _fence(tmp_path)
    plain = Path(_hub_roots(env)[1]) / "vaults" / "plain"
    plain.mkdir(parents=True)
    assert _vault_denials(env) == _later_vault_globs(env)


def test_a_vault_whose_git_is_a_gitdir_file_has_the_resolved_gitdir_denied(tmp_path):
    env = _fence(tmp_path)
    lore = Path(_hub_roots(env)[1])
    real = lore / "gitdirs" / "wt"
    real.mkdir(parents=True)
    vault = lore / "vaults" / "linked"
    vault.mkdir(parents=True)
    (vault / ".git").write_text(f"gitdir: {real}\n")
    denied = _deny_of(env)
    assert set(_admin(real)) <= set(denied)
    assert f"{vault}/.git" in denied


def test_a_relative_gitdir_file_is_resolved_against_the_vault(tmp_path):
    env = _fence(tmp_path)
    lore = Path(_hub_roots(env)[1])
    real = lore / "vaults" / "shared.git"
    real.mkdir(parents=True)
    vault = lore / "vaults" / "linked"
    vault.mkdir()
    (vault / ".git").write_text("gitdir: ../shared.git\n")
    assert set(_admin(real)) <= set(_deny_of(env))


def test_a_linked_worktree_also_denies_the_common_dir_it_shares_config_with(tmp_path):
    env = _fence(tmp_path)
    lore = Path(_hub_roots(env)[1])
    common = lore / "stores" / "a" / "b" / "c" / "main.git"
    (common / "worktrees" / "wt").mkdir(parents=True)
    (common / "worktrees" / "wt" / "commondir").write_text("../..\n")
    linked = lore / "vaults" / "linked"
    linked.mkdir(parents=True)
    (linked / ".git").write_text(f"gitdir: {common}/worktrees/wt\n")
    deny = _deny_of(env)
    assert set(_admin(common)) <= set(deny)
    assert set(_admin(common / "worktrees" / "wt")) <= set(deny)


def test_a_gitdir_pointing_outside_every_writable_root_adds_nothing(tmp_path):
    env = _fence(tmp_path)
    lore = Path(_hub_roots(env)[1])
    vault = lore / "vaults" / "linked"
    vault.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere" / "repo.git"
    elsewhere.mkdir(parents=True)
    (vault / ".git").write_text(f"gitdir: {elsewhere}\n")
    assert not any(str(elsewhere) in d for d in _deny_of(env))


def test_a_malformed_git_file_is_ignored(tmp_path):
    env = _fence(tmp_path)
    vault = Path(_hub_roots(env)[1]) / "vaults" / "bad"
    vault.mkdir(parents=True)
    (vault / ".git").write_text("not a gitdir pointer")
    assert _vault_denials(env) == _later_vault_globs(env)


def test_vault_denials_sit_alongside_the_lore_root_allow_write(tmp_path):
    env = _fence(tmp_path)
    one = _make_vault(_hub_roots(env)[1], "one")
    fs = _settings_of(_start(env))["sandbox"]["filesystem"]
    assert _hub_roots(env)[1] in fs["allowWrite"]
    assert set(_admin(one / ".git")) <= set(fs["denyWrite"])


def test_a_profile_without_a_guarded_root_denies_no_git_files(tmp_path):
    env = _fence(tmp_path)
    _make_vault(_hub_roots(env)[1], "one")
    bare = LaunchProfile(writable_roles=HUB_PROFILE.writable_roles)
    deny = _settings_of(_start(env, profile=bare))["sandbox"]["filesystem"]["denyWrite"]
    assert not any("/.git" in d for d in deny)


def test_a_vault_nested_deeper_than_the_scan_bound_is_left_to_the_glob_only(tmp_path):
    env = _fence(tmp_path)
    deep = Path(_hub_roots(env)[1]) / "a" / "b" / "c" / "d" / ".git"
    deep.mkdir(parents=True)
    assert not any(str(deep.parent) in d for d in _deny_of(env))


# -- seam smoke and golden --------------------------------------------------


def _golden_scenario(tmp_path: Path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    (Path(env["HOME"]) / ".claude").mkdir(parents=True, exist_ok=True)
    _make_vault(lore, "default")
    _configure_hub(env)
    _write_user_settings(
        env,
        {
            "sandbox": {
                "enabled": True,
                "filesystem": {"allowWrite": [".", cache, lore, str(Path(env["HOME"]).parent / "scratch")]},
                "network": {"allowedDomains": ["api.anthropic.com"]},
            }
        },
    )
    return env


def test_session_start_subprocess_from_a_foreign_cwd_matches_the_golden(  # inert-gate: allow wiring seam smoke
    tmp_path,
):
    env = _golden_scenario(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    result = subprocess.run(
        [str(REPO / "bin" / "trailhead"), "session-start", "--role", "hub", "--account", "a",
         "--json"],
        cwd=foreign, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout.replace(str(tmp_path.resolve()), ROOT_TOKEN))
    assert set(actual) == {"argv", "env_set", "env_unset", "directory"}
    assert actual == json.loads(GOLDEN.read_text())


# -- gated live confinement ------------------------------------------------

CLAUDE_BIN = os.environ.get("TRAILHEAD_CLAUDE_BIN") or "claude"


def _claude_present() -> bool:
    import shutil

    return shutil.which(CLAUDE_BIN) is not None


def _mock_anthropic(commands: list[str]):
    """A 127.0.0.1 server speaking just enough of the Messages API to have the model run
    each of *commands* as its own Bash tool call, in order, then finish. Returns
    (url, tool_results, shutdown); ``tool_results`` holds one entry per command."""
    seen: list = []

    def sse(events):
        return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()

    def message(block_events, stop):
        usage = {"input_tokens": 1, "output_tokens": 1}
        return sse(
            [{"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant",
              "model": "claude-sonnet-5-5", "content": [], "stop_reason": None, "usage": usage}},
             *block_events,
             {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 1}},
             {"type": "message_stop"}]
        )

    def tool_turn(command: str, n: int) -> bytes:
        return message(
            [{"type": "content_block_start", "index": 0,
              "content_block": {"type": "tool_use", "id": f"toolu_{n}", "name": "Bash", "input": {}}},
             {"type": "content_block_delta", "index": 0,
              "delta": {"type": "input_json_delta", "partial_json": json.dumps({"command": command})}},
             {"type": "content_block_stop", "index": 0}],
            "tool_use",
        )

    done_turn = message(
        [{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
         {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "done"}},
         {"type": "content_block_stop", "index": 0}],
        "end_turn",
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            results = [
                b for m in body.get("messages", []) if isinstance(m.get("content"), list)
                for b in m["content"] if b.get("type") == "tool_result"
            ]
            has_bash = any(t.get("name") == "Bash" for t in body.get("tools") or [])
            if has_bash:
                seen[:] = results
            if has_bash and len(results) < len(commands):
                out = tool_turn(commands[len(results)], len(results))
            else:
                out = done_turn
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(out)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", seen, server.shutdown


_LIVE_SKIP = pytest.mark.skipif(
    os.environ.get("TRAILHEAD_LIVE_CONFINEMENT") != "1" or not _claude_present(),
    reason="opt-in live confinement test: set TRAILHEAD_LIVE_CONFINEMENT=1 outside any sandbox "
    "with the harness binary on PATH",
)


@pytest.fixture
def tmux_root():
    """A stand-in for the system temp a tmux server keeps its sockets under, outside tmp_path."""
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="trailhead-tmux-", dir="/tmp")))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _live_hub_run(tmp_path, tmux_root, commands_for, excluded=()):
    """Start the hub profile for real against a mock API that has the model run each command
    as its own Bash call (a permission denial of one call must not hide the others).
    Returns (per-command tool results, completed process, paths)."""
    env = _fence(tmp_path)
    tmux_socket_dir = tmux_root / f"tmux-{os.getuid()}"
    tmux_socket_dir.mkdir(mode=0o700)
    env["TMUX_TMPDIR"] = str(tmux_root)
    home, lore, cache = (Path(p) for p in _hub_roots(env))
    token_store = _resolved(env).paths["token-store"]
    for d in (lore, cache, token_store):
        d.mkdir(parents=True, exist_ok=True)
    (token_store / "token").write_text("TOPSECRET")
    vault = _make_vault(str(lore), "throwaway")
    outside = tmp_path.resolve() / "outside"
    outside.mkdir()
    (Path(env["HOME"]) / ".claude").mkdir(parents=True, exist_ok=True)
    (Path(env["HOME"]) / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True}))
    _write_user_settings(
        env,
        {"sandbox": {"enabled": True, "failIfUnavailable": True,
                     "allowUnsandboxedCommands": False, "autoAllowBashIfSandboxed": True,
                     "excludedCommands": list(excluded),
                     "filesystem": {"allowWrite": ["/tmp", "/private/tmp", str(cache), str(lore),
                                                  str(outside)]}}},
    )
    start = _start(env, interactive=False, prompt="go")
    paths = dict(home=home, lore=lore, vault=vault, cache=cache, outside=outside, token_store=token_store,
                 tmux_root=tmux_root, tmux_socket_dir=tmux_socket_dir)
    commands = commands_for(paths)
    url, seen, shutdown = _mock_anthropic(commands)
    try:
        child_env = dict(env)
        child_env.update(ANTHROPIC_API_KEY="sk-fake", ANTHROPIC_BASE_URL=url,
                         CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
        argv = [CLAUDE_BIN, *start.argv[1:], "--no-session-persistence"]
        proc = subprocess.run(argv, cwd=home, env=child_env, capture_output=True, text=True,
                              timeout=180, stdin=subprocess.DEVNULL)
    finally:
        shutdown()
    assert proc.returncode == 0, proc.stderr[-500:]
    assert len(seen) == len(commands), f"expected {len(commands)} tool results, got {seen}"
    return list(seen), proc, paths


def _refused(result: dict) -> bool:
    return result.get("is_error") is True


@_LIVE_SKIP
def test_live_hub_session_writes_only_its_roots_and_cannot_read_the_token_store(tmp_path, tmux_root):
    def commands(p):
        return [
            f"echo x > {p['home']}/ok",
            f"echo x > {p['lore']}/ok",
            f"echo x > {p['cache']}/ok",
            f"echo x > {p['tmux_root']}/scratch-ok",
            f"echo x > {p['tmux_socket_dir']}/default",
            f"echo x > {p['outside']}/no",
            f"mkdir -p {p['home']}/.claude && echo x > {p['home']}/.claude/settings.json",
            f"cat {p['token_store']}/token",
            f"head -c 1 {p['token_store']}/token",
            f"python3 -c \"print(open('{p['token_store']}/token').read())\"",
        ]

    results, _, paths = _live_hub_run(tmp_path, tmux_root, commands)
    home_w, lore_w, cache_w, scratch_w, socket_w, outside_w, own_cfg, cat_tok, head_tok, py_tok = results
    assert not any(map(_refused, (home_w, lore_w, cache_w, scratch_w)))  # positive controls
    assert _refused(socket_w) and not (paths["tmux_socket_dir"] / "default").exists()
    assert (paths["home"] / "ok").exists() and (paths["lore"] / "ok").exists()
    assert (paths["cache"] / "ok").exists()
    assert _refused(outside_w) and not (paths["outside"] / "no").exists()
    assert _refused(own_cfg) and not (paths["home"] / ".claude" / "settings.json").exists()
    # cat is refused by the permission rule; head and python dodge that pattern, so their
    # refusal is the sandbox read denial
    assert _refused(cat_tok) and _refused(head_tok) and _refused(py_tok)
    assert "TOPSECRET" not in json.dumps(results)


@_LIVE_SKIP
def test_live_hub_session_is_refused_a_command_the_user_runs_unsandboxed(tmp_path, tmux_root):
    results, _, paths = _live_hub_run(
        tmp_path, tmux_root, lambda p: [f"touch {p['home']}/excluded-ran"], excluded=["touch *"]
    )
    assert _refused(results[0])
    assert not (paths["home"] / "excluded-ran").exists()


@_LIVE_SKIP
def test_live_hub_session_cannot_plant_git_config_in_a_vault_but_can_write_records(
    tmp_path, tmux_root
):
    def commands(p):
        v = p["vault"]
        return [
            f"echo x > {v}/record-ok.md",
            f"echo x > {v}/.git/COMMIT_EDITMSG",
            f"printf '[core]\\n\\tfsmonitor = true\\n' > {v}/.git/config",
            f"mkdir -p {v}/.git/hooks && echo x > {v}/.git/hooks/post-commit",
            f"mkdir -p {v}/.git/info && echo x > {v}/.git/info/attributes",
            f"mv {v}/.git {v}/.git-moved",
            f"mkdir -p {p['lore']}/vaults/late/.git && echo x > {p['lore']}/vaults/late/.git/config",
        ]

    results, _, paths = _live_hub_run(tmp_path, tmux_root, commands)
    record_w, msg_w, config_w, hook_w, info_w, move_w, late_w = results
    v = paths["vault"]
    assert not _refused(record_w) and (v / "record-ok.md").exists()
    assert not _refused(msg_w) and (v / ".git" / "COMMIT_EDITMSG").exists()
    assert _refused(config_w) and not (v / ".git" / "config").exists()
    assert _refused(hook_w) and not (v / ".git" / "hooks" / "post-commit").exists()
    assert _refused(info_w) and not (v / ".git" / "info" / "attributes").exists()
    assert _refused(move_w) and (v / ".git").is_dir()
    assert _refused(late_w) and not (paths["lore"] / "vaults" / "late" / ".git" / "config").exists()
