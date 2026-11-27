"""Tests for the ``session_start`` harness seam, its Claude Code implementation, and the
``trailhead session-start`` command. Every test fences HOME, XDG roots and CLAUDE_CONFIG_DIR
into tmp_path, so nothing reaches the live install or the live user settings.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from trailhead import cli
from trailhead.harness.base import Harness, HarnessError, SessionStart
from trailhead.harness import claude_code
from trailhead.harness.claude_code import ClaudeCodeHarness
from trailhead.launch_profile import (
    BROKER_SERVER,
    HUB_PROFILE,
    RITUAL_PROFILE,
    RITUAL_ROLE,
    ROLE_CACHE,
    LaunchProfile,
    WorkspaceError,
    broker_launch,
    hub_home,
    resolve_profile,
    workspace_containing,
)
from trailhead.ranger import outpost_client
from trailhead.ranger.config import HubConfig, write_hub_config
from trailhead.tests.test_ranger_start import OUTPOST_REFUSAL_BODIES, _Stub

REPO = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "fixtures" / "session_start_hub.json"
ROOT_TOKEN = "<ROOT>"
_HUB_ARGV = ["session-start", "--role", "hub", "--account", "a", "--json", "--broker-session", "s1"]


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
        "CAMP_STATE_DIR": str(root / "camp-state"),
        "CAMP_CONFIG_DIR": str(root / "camp-config"),
        "TRAILHEAD_STATE_DIR": str(root / "trailhead-state"),
        "TRAILHEAD_CONFIG_DIR": str(root / "trailhead-config"),
        "TRAILHEAD_CACHE_DIR": str(root / "trailhead-cache"),
        "PATH": os.environ.get("PATH", ""),
    }


@pytest.fixture(autouse=True)
def _fenced_managed_settings(tmp_path, monkeypatch):
    """Point the managed-settings location at a fenced, initially absent directory."""
    monkeypatch.setattr(
        claude_code,
        "_MANAGED_SETTINGS_DIRS",
        {"linux": tmp_path / "managed-fence", "darwin": tmp_path / "managed-fence-darwin"},
    )


def _write_managed(
    tmp_path: Path, data, *, drop_in: str | None = None, fence: str = "managed-fence"
) -> Path:
    base = tmp_path / fence
    if drop_in:
        base = base / "managed-settings.d"
    base.mkdir(parents=True, exist_ok=True)
    path = base / (drop_in or "managed-settings.json")
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


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


def _give_user_a_write_list(env, account=None) -> None:
    """A user with no settings file yet gets the write list a real account carries (the
    working directory, cache and lore roots), since a profile may only write where its user
    allows. A declared account whose directory is absent is left absent."""
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    if account:
        settings_dir = Path(account.replace("~", env["HOME"], 1) if account.startswith("~") else account)
        if not settings_dir.is_dir():
            return
    else:
        settings_dir = Path(env["HOME"]) / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
    if not (settings_dir / "settings.json").exists():
        _write_user_settings(env, _user_allowing_hub(env), account_dir=settings_dir)


def _start(env, *, profile=HUB_PROFILE, prompt=None, interactive=True, account=None, bare=False, **extra):
    """Start the hub. Unless *bare*, the user is first given a write list (see above)."""
    if not bare:
        _give_user_a_write_list(env, account)
    extra.setdefault("platform", "linux")
    extra.setdefault("session_dir", Path(env["OUTPOST_STATE_DIR"]).resolve() / "sessions" / "s1")
    if BROKER_SERVER in profile.mcp_servers and "broker" not in extra:
        extra["broker"] = broker_launch("s1", env=env)
    resolved = _resolved(env, profile)
    home = hub_home("a", env=env, platform="linux")
    home.mkdir(parents=True, exist_ok=True)
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    return ClaudeCodeHarness().session_start(
        resolved, home, prompt, interactive=interactive, account=account, env=env, **extra
    )


def _settings_of(start: SessionStart) -> dict:
    """The settings the launch hands Claude Code: from the session file ``--settings`` names, else inline."""
    value = start.argv[start.argv.index("--settings") + 1]
    named = [f for f in start.files if f.path == value]
    return json.loads(named[0].content if named else value)


def _mcp_config_of(argv, files):
    """The session file ``--mcp-config`` names, from a SessionStart's files or a launch's JSON."""
    path = argv[argv.index("--mcp-config") + 1]
    (file,) = [f for f in files if (f["path"] if isinstance(f, dict) else f.path) == path]
    return file


def _hub_roots(env) -> tuple[str, str, str]:
    r = _resolved(env)
    return tuple(str(p) for p in r.writable)  # hub-home, lore-state, cache


def _protected(env) -> list[str]:
    """The hub's own future-settings paths, which the hub may never write."""
    home = _hub_roots(env)[0]
    session_dir = Path(broker_launch("s1", env=env).config_path).parent
    return [f"{home}/.claude", f"{home}/.mcp.json", str(session_dir)]


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
    assert fs["denyWrite"] == [*_protected(env), *_planted_patterns(env)]


def test_the_hubs_own_future_settings_are_denied_wherever_the_hub_home_is(tmp_path):
    first, second = _fence(tmp_path / "one"), _fence(tmp_path / "two")
    for env in (first, second):
        deny = _settings_of(_start(env))["sandbox"]["filesystem"]["denyWrite"]
        home = _hub_roots(env)[0]
        assert deny == [*_protected(env), *_planted_patterns(env)]
        assert deny[:2] == [f"{home}/.claude", f"{home}/.mcp.json"]
    assert _protected(first) != _protected(second)


def test_user_writable_root_outside_the_hub_roots_is_derived_into_denywrite(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    extra = str(tmp_path.resolve() / "elsewhere")
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache, lore, extra]))
    fs = _settings_of(_start(env))["sandbox"]["filesystem"]
    assert fs["denyWrite"] == [*_protected(env), extra, *_planted_patterns(env)]
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
        *_protected(env), *_planted_patterns(env)
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
        *_protected(env), sibling, *_planted_patterns(env)
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
def test_with_no_other_user_sandbox_keys_the_profiles_own_narrowing_still_applies(tmp_path, content):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    if content is not None:
        sandbox = {**content.get("sandbox", {}), "filesystem": {"allowWrite": [".", cache, lore]}}
        _write_user_settings(env, {**content, "sandbox": sandbox})
    fs = _settings_of(_start(env))["sandbox"]["filesystem"]
    assert fs["allowWrite"] == list(_hub_roots(env))
    assert fs["denyWrite"] == [*_protected(env), *_planted_patterns(env)]


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
    _give_user_a_write_list(env)
    return ClaudeCodeHarness().session_start(
        _resolved(env), directory, None, interactive=True, account=None, env=env,
        broker=broker_launch("s1", env=env),
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
    rc = _run_cli(monkeypatch, env, _HUB_ARGV)
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
    assert start.env_set == {"PATH": env["PATH"]}
    assert "CLAUDE_CONFIG_DIR" in start.env_unset


@pytest.mark.parametrize("entries", [["/usr/bin", "/bin"], ["/opt/tools/bin"]])
def test_a_broker_launch_runs_with_exactly_the_path_it_guarded(tmp_path, entries):
    env = _fence(tmp_path)
    env["PATH"] = os.pathsep.join(entries)
    start = _start(env, account=None)
    assert start.env_set == {"PATH": os.pathsep.join(entries)}


def test_a_launch_with_no_broker_leaves_path_alone(tmp_path):
    env = _fence(tmp_path)
    env["PATH"] = "/usr/bin:/bin"
    profile = LaunchProfile(writable_roles=HUB_PROFILE.writable_roles, mcp_servers=())
    assert "PATH" not in _start(env, profile=profile).env_set


def test_declared_account_sets_the_config_dir_to_the_expanded_path(tmp_path):
    env = _fence(tmp_path)
    acct = Path(env["HOME"]) / ".claude-x"
    acct.mkdir(parents=True)
    start = _start(env, account="~/.claude-x")
    assert start.env_set == {"CLAUDE_CONFIG_DIR": str(acct), "PATH": env["PATH"]}


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
    deny = settings["permissions"]["deny"]
    assert [r for r in deny if r.startswith("Read(")] == [f"Read(/{token}/**)"]
    assert f"Edit(/{token}/**)" in deny
    assert "model" not in settings
    assert settings["enabledMcpjsonServers"] == []


def _bash_denies(start: SessionStart) -> list[str]:
    return [r for r in _settings_of(start)["permissions"]["deny"] if r.startswith("Bash(")]


def test_no_user_exclusions_adds_no_bash_deny_rules(tmp_path):
    env = _fence(tmp_path)
    allow = _user_allowing_hub(env)["sandbox"]["filesystem"]
    _write_user_settings(
        env, {"sandbox": {"enabled": True, "excludedCommands": [], "filesystem": allow}}
    )
    assert _bash_denies(_start(env)) == []
    _write_user_settings(env, _user_allowing_hub(env))
    assert _bash_denies(_start(env)) == []


def test_each_user_unsandboxed_command_exemption_is_denied_in_the_session(tmp_path):
    env = _fence(tmp_path)
    exclusions = ["claude plugin eval *", "portage eval-gate *"]
    settings = _user_allowing_hub(env)
    settings["sandbox"]["excludedCommands"] = exclusions
    _write_user_settings(env, settings)
    start = _start(env)
    assert _bash_denies(start) == [f"Bash({e})" for e in exclusions]


def _gateway_shim(env, name="outpost-gateway") -> str:
    return str(Path(env["TRAILHEAD_STATE_DIR"]) / "bin" / name)


def _hub_launched_with_exclusions(env, exclusions):
    settings = _user_allowing_hub(env)
    settings["sandbox"]["excludedCommands"] = exclusions
    _write_user_settings(env, settings)
    return _settings_of(_start(env))


def _launched_with_exclusions(env, exclusions):
    """A ritual (workspace-role) launch under a user who excludes *exclusions* from the sandbox."""
    _configure_hub(env, "default")
    ws = _make_workspace(env)
    Path(env["HOME"], ".claude").mkdir(parents=True, exist_ok=True)
    _ritual_user(env, ws)
    user = json.loads((Path(env["HOME"]) / ".claude" / "settings.json").read_text())
    user["sandbox"]["excludedCommands"] = exclusions
    _write_user_settings(env, user)
    return _settings_of(_ritual_start(env, ws))


def test_a_launched_profile_does_not_deny_the_pinned_gateway_shim_and_denies_the_other_exclusions(tmp_path):
    env = _fence(tmp_path)
    shim = _gateway_shim(env)
    start = _launched_with_exclusions(env, ["claude plugin eval *", shim, f"{shim} *", "portage eval-gate *"])
    denied = [r for r in start["permissions"]["deny"] if r.startswith("Bash(")]
    assert f"Bash({shim})" not in denied and f"Bash({shim} *)" not in denied
    assert {"Bash(claude plugin eval *)", "Bash(portage eval-gate *)"} <= set(denied)


def test_a_launched_profile_allows_the_pinned_gateway_shim_the_user_excludes(tmp_path):
    env = _fence(tmp_path)
    shim = _gateway_shim(env)
    start = _launched_with_exclusions(env, [shim, f"{shim} *"])
    assert f"Bash({shim}:*)" in start["permissions"]["allow"]


def test_a_launched_profile_excludes_the_pinned_gateway_shim_itself_when_the_user_excludes_nothing(tmp_path):
    env = _fence(tmp_path)
    shim = _gateway_shim(env)
    start = _launched_with_exclusions(env, [])
    assert start["sandbox"]["excludedCommands"] == [shim, f"{shim} *"]
    assert start["permissions"]["allow"][0] == f"Bash({shim}:*)"


def test_a_launched_profile_allows_only_the_pinned_gateway_shim_whatever_else_the_user_excludes(tmp_path):
    env = _fence(tmp_path)
    shim = _gateway_shim(env)
    start = _launched_with_exclusions(env, ["claude plugin eval *"])
    assert [r for r in start["permissions"]["allow"] if r.startswith("Bash(")] == [f"Bash({shim}:*)"]
    assert start["sandbox"]["excludedCommands"] == [shim, f"{shim} *"]
    assert "Bash(claude plugin eval *)" in start["permissions"]["deny"]


def test_a_hub_launch_excludes_no_command_from_the_sandbox(tmp_path):
    env = _fence(tmp_path)
    shim = _gateway_shim(env)
    start = _hub_launched_with_exclusions(env, [shim, f"{shim} *"])
    assert "excludedCommands" not in start["sandbox"]


def test_a_gateway_command_at_another_path_is_still_denied_in_a_launched_profile(tmp_path):
    env = _fence(tmp_path)
    start = _launched_with_exclusions(env, ["/elsewhere/bin/outpost-gateway", "/elsewhere/bin/outpost-gateway *"])
    denied = [r for r in start["permissions"]["deny"] if r.startswith("Bash(")]
    assert denied[:2] == ["Bash(/elsewhere/bin/outpost-gateway)", "Bash(/elsewhere/bin/outpost-gateway *)"]
    assert [r for r in start["permissions"]["allow"] if r.startswith("Bash(")] == [
        f"Bash({_gateway_shim(env)}:*)"
    ]


def test_a_launched_profile_that_runs_the_gateway_denies_the_header_shim(tmp_path):
    env = _fence(tmp_path)
    shim = _gateway_shim(env)
    header = _gateway_shim(env, "trailhead-gateway-auth-header")
    start = _launched_with_exclusions(env, [shim, f"{shim} *"])
    assert {f"Bash({header})", f"Bash({header} *)"} <= set(start["permissions"]["deny"])


def test_a_hub_launch_keeps_the_pinned_gateway_shim_denied_and_does_not_allow_it(tmp_path):
    env = _fence(tmp_path)
    shim = _gateway_shim(env)
    start = _hub_launched_with_exclusions(env, [shim, f"{shim} *"])
    assert {f"Bash({shim})", f"Bash({shim} *)"} <= set(start["permissions"]["deny"])
    assert not [r for r in start["permissions"].get("allow", []) if r.startswith("Bash(")]


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
    _give_user_a_write_list(env)
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
    _give_user_a_write_list(env)
    ClaudeCodeHarness().session_start(
        _resolved(env), link, None, interactive=True, account=None, env=env,
        broker=broker_launch("s1", env=env),
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
    rc = _run_cli(monkeypatch, env, _HUB_ARGV)
    err = capsys.readouterr().err
    assert rc != 0 and err.startswith("trailhead: ") and lore in err


def test_the_diagnostic_ranger_prints_for_a_refused_hub_launch_reproduces_the_never_widen_refusal(
    tmp_path, monkeypatch, capsys
):
    env = _fence(tmp_path)
    _configure_hub(env)
    home, lore, cache = _hub_roots(env)
    user = _write_user_settings(env, {"model": "opus"})
    stub = _Stub(status=422, body=OUTPOST_REFUSAL_BODIES[0])
    try:
        with pytest.raises(outpost_client.OutpostRefusal) as raised:
            outpost_client.start_hub("a", "credential", port=stub.port, timeout=5.0)
    finally:
        stub.close()
    command = re.search(r"`([^`]+)`", str(raised.value)).group(1)
    argv = shlex.split(command)
    assert argv[0] == "trailhead"
    rc = _run_cli(monkeypatch, env, argv[1:])
    err = capsys.readouterr().err
    assert rc != 0 and "no broker session was given" not in err
    assert f"Add {lore} to 'sandbox.filesystem.allowWrite' in {user}" in err


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
        ["session-start", "--role", "hub", "--account", "a", "--json", "--broker-session", "s1", "--headless",
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
        ["session-start", "--role", "hub", "--account", "a", "--json", "--broker-session", "s1", "--headless",
         "--prompt-file", str(prompt)],
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert set(out) == {"argv", "env_set", "env_unset", "directory", "broker", "files"}
    assert "-p" in out["argv"] and out["argv"][-1] == "do the thing\n"
    assert out["directory"] == str(hub_home("a", env=env))
    rc = _run_cli(monkeypatch, env, _HUB_ARGV)
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
    rc = _run_cli(monkeypatch, env, _HUB_ARGV)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["env_set"] == {"CLAUDE_CONFIG_DIR": str(acct), "PATH": env["PATH"]}


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
    return _admin(f"{_hub_roots(env)[1]}/**/.git")


def _planted_patterns(env) -> list[str]:
    home, lore, _cache = _hub_roots(env)
    return [
        entry
        for root in (home, lore)
        for entry in (f"{root}/**/.claude", *_admin(f"{root}/**/.git"))
    ]


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
    one = _make_vault(_hub_roots(env)[1], "one")
    bare = LaunchProfile(writable_roles=HUB_PROFILE.writable_roles)
    deny = _settings_of(_start(env, profile=bare))["sandbox"]["filesystem"]["denyWrite"]
    assert not any(d.startswith(f"{one}/") for d in deny)


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


def _normalised(text: str, tmp_path: Path) -> str:
    return text.replace(str(tmp_path.resolve()), ROOT_TOKEN).replace(str(REPO), "<TRAILHEAD>")


PATH_TOKEN = "<PATH>"


def test_session_start_subprocess_from_a_foreign_cwd_matches_the_golden(  # inert-gate: allow wiring seam smoke
    tmp_path,
):
    env = _golden_scenario(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    result = subprocess.run(
        [str(REPO / "bin" / "trailhead"), "session-start", "--role", "hub", "--account", "a",
         "--json", "--broker-session", "sess-1"],
        cwd=foreign, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["env_set"]["PATH"] == env["PATH"]
    actual = json.loads(_normalised(result.stdout.replace(env["PATH"], PATH_TOKEN), tmp_path))
    assert set(actual) == {"argv", "env_set", "env_unset", "directory", "broker", "files"}
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
    for f in start.files:  # what Outpost writes before it runs the argv
        Path(f.path).parent.mkdir(parents=True, exist_ok=True)
        Path(f.path).write_text(f.content)
        os.chmod(f.path, int(f.mode, 8))
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
def test_live_hub_session_writes_only_its_roots_and_cannot_read_the_token_store(off_temp, tmux_root):
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

    results, _, paths = _live_hub_run(off_temp, tmux_root, commands)
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
def test_live_hub_session_is_refused_a_command_the_user_runs_unsandboxed(off_temp, tmux_root):
    results, _, paths = _live_hub_run(
        off_temp, tmux_root, lambda p: [f"touch {p['home']}/excluded-ran"], excluded=["touch *"]
    )
    assert _refused(results[0])
    assert not (paths["home"] / "excluded-ran").exists()


@_LIVE_SKIP
def test_live_hub_session_cannot_plant_git_config_in_a_vault_but_can_write_records(
    off_temp, tmux_root
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

    results, _, paths = _live_hub_run(off_temp, tmux_root, commands)
    record_w, msg_w, config_w, hook_w, info_w, move_w, late_w = results
    v = paths["vault"]
    assert not _refused(record_w) and (v / "record-ok.md").exists()
    assert not _refused(msg_w) and (v / ".git" / "COMMIT_EDITMSG").exists()
    assert _refused(config_w) and not (v / ".git" / "config").exists()
    assert _refused(hook_w) and not (v / ".git" / "hooks" / "post-commit").exists()
    assert _refused(info_w) and not (v / ".git" / "info" / "attributes").exists()
    assert _refused(move_w) and (v / ".git").is_dir()
    assert _refused(late_w) and not (paths["lore"] / "vaults" / "late" / ".git" / "config").exists()


# -- the broker section ------------------------------------------------------

CONTRACT = json.loads((Path(__file__).parent / "fixtures" / "ranger-contract.json").read_text())
VAULT_FLAG = CONTRACT["loop"]["ritual_vault_flag"]


def _broker_argv(session_id):
    return ["session-start", "--role", "hub", "--account", "a", "--json",
            f"{CONTRACT['broker_session_arg']}={session_id}"]


def _broker_ready(tmp_path):
    env = _fence(tmp_path)
    _configure_hub(env)
    _write_user_settings(env, _user_allowing_hub(env))
    return env


def test_broker_session_adds_a_broker_section_shaped_by_the_contract(tmp_path, monkeypatch, capsys):
    env = _broker_ready(tmp_path)
    rc = _run_cli(monkeypatch, env, _broker_argv("sess-1"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert list(out["broker"]) == CONTRACT["broker_output_keys"]
    assert out["broker"]["server"] == CONTRACT["broker_server"]
    assert out["broker"]["scope"] == CONTRACT["broker_scope"]
    expected = CONTRACT["broker_credential_path"].replace("<session-id>", "sess-1")
    assert out["broker"]["credential_path"] == str(Path(env["OUTPOST_STATE_DIR"]) / expected)


def test_broker_credential_path_follows_the_outpost_state_dir(tmp_path, monkeypatch, capsys):
    env = _broker_ready(tmp_path)
    moved = str(tmp_path.resolve() / "elsewhere")
    _run_cli(monkeypatch, {**env, "OUTPOST_STATE_DIR": moved}, _broker_argv("sess-1"))
    out = json.loads(capsys.readouterr().out)
    assert out["broker"]["credential_path"].startswith(moved + "/")


@pytest.mark.parametrize(
    "bad", ["a/b", "..", "a..b", "../x", "a\x00b", "a\nb", "a\tb", "a\x7fb", "", "-lead", "x" * 65, "a b"]
)
def test_a_session_id_outside_the_shape_is_refused_with_nothing_printed(
    tmp_path, monkeypatch, capsys, bad
):
    env = _broker_ready(tmp_path)
    rc = _run_cli(monkeypatch, env, _broker_argv(bad))
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and "unexpected" not in captured.err


@pytest.mark.parametrize("good", ["s", "Sess_1-x", "0" * 64])
def test_a_session_id_inside_the_shape_is_accepted(tmp_path, monkeypatch, capsys, good):
    env = _broker_ready(tmp_path)
    rc = _run_cli(monkeypatch, env, _broker_argv(good))
    assert rc == 0 and json.loads(capsys.readouterr().out)["broker"]["credential_path"].endswith(
        f"sessions/{good}/broker-client.json"
    )


def test_the_code_constants_match_the_contract_fixture():  # inert-gate: allow cross-repo contract seam
    from trailhead import launch_profile

    assert launch_profile.BROKER_SESSION_ID_PATTERN == CONTRACT["broker_session_id_pattern"]
    assert launch_profile.BROKER_SERVER == CONTRACT["broker_server"]
    assert launch_profile.BROKER_SCOPE == CONTRACT["broker_scope"]


def _setup_only_cli(monkeypatch, env, *extra, role=RITUAL_ROLE):
    return _run_cli(
        monkeypatch, env,
        ["session-start", "--role", role, "--account", "a", "--setup-sandbox-only", "--json", *extra],
    )


@pytest.mark.parametrize(
    "network",
    [
        {"allowedDomains": ["a.example", "b.example"], "deniedDomains": ["c.example"]},
        {"allowedDomains": ["d.example"]},
    ],
)
def test_setup_sandbox_only_prints_the_section_a_full_ritual_start_prints_and_nothing_else(
    tmp_path, monkeypatch, capsys, network
):
    env, ws = _ritual_ready(tmp_path)
    _user_network(env, ws, **network)
    home = Path(env["HOME"]).resolve()
    data = json.loads((home / ".claude" / "settings.json").read_text())
    data["permissions"] = {"deny": [f"Read(//{home}/.aws/**)"]}
    (home / ".claude" / "settings.json").write_text(json.dumps(data))
    assert _ritual_cli(monkeypatch, env, ws) == 0
    full = json.loads(capsys.readouterr().out)
    assert _setup_only_cli(monkeypatch, env) == 0
    only = json.loads(capsys.readouterr().out)
    assert set(only) == {"setup_sandbox"}
    assert only["setup_sandbox"] == full["setup_sandbox"]
    assert only["setup_sandbox"]["allowed_domains"] == network["allowedDomains"]
    assert str(home / ".aws") in only["setup_sandbox"]["denied_reads"]


def test_setup_sandbox_only_follows_the_users_network_lists(tmp_path, monkeypatch, capsys):
    env, ws = _ritual_ready(tmp_path)
    _user_network(env, ws, allowedDomains=["a.example"])
    assert _setup_only_cli(monkeypatch, env) == 0
    before = json.loads(capsys.readouterr().out)["setup_sandbox"]
    _user_network(env, ws, allowedDomains=["a.example", "z.example"], deniedDomains=["a.example"])
    assert _setup_only_cli(monkeypatch, env) == 0
    after = json.loads(capsys.readouterr().out)["setup_sandbox"]
    assert before["allowed_domains"] == ["a.example"]
    assert after["allowed_domains"] == ["z.example"] and after["denied_domains"] == ["a.example"]


def test_setup_sandbox_only_reads_the_launch_accounts_own_settings_and_denies_the_default_config(
    tmp_path, monkeypatch, capsys
):
    env, ws, own = _ritual_on_named_account(tmp_path)
    Path(env["HOME"], ".claude").mkdir(parents=True, exist_ok=True)
    _, lore, cache = _ritual_roots(env, ws)
    _write_user_settings(
        env, _user_sandbox(allowWrite=[".", cache, lore], allowedDomains=["own.example"]), account_dir=own
    )
    assert _ritual_cli(monkeypatch, env, ws) == 0
    full = json.loads(capsys.readouterr().out)["setup_sandbox"]
    assert _setup_only_cli(monkeypatch, env) == 0
    only = json.loads(capsys.readouterr().out)["setup_sandbox"]
    assert only == full
    assert only["allowed_domains"] == ["own.example"]
    assert str(Path(env["HOME"]).resolve() / ".claude") in only["denied_reads"]


def test_setup_sandbox_only_works_before_any_workspace_exists(tmp_path, monkeypatch, capsys):
    env = _fence(tmp_path)
    _configure_hub(env)
    Path(env["HOME"], ".claude").mkdir(parents=True)
    _write_user_settings(env, _user_sandbox(allowedDomains=["a.example"]))
    assert _setup_only_cli(monkeypatch, env) == 0
    assert json.loads(capsys.readouterr().out)["setup_sandbox"]["allowed_domains"] == ["a.example"]


@pytest.mark.parametrize(
    "role, extra",
    [
        (RITUAL_ROLE, ["--workspace", "WS"]),
        (RITUAL_ROLE, ["--prompt-file", "PROMPT"]),
        (RITUAL_ROLE, [VAULT_FLAG, "work"]),
        ("hub", []),
    ],
)
def test_setup_sandbox_only_refuses_a_workspace_a_prompt_or_another_role(
    tmp_path, monkeypatch, capsys, role, extra
):
    env, ws = _ritual_ready(tmp_path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("hello")
    extra = [str(ws) if a == "WS" else str(prompt) if a == "PROMPT" else a for a in extra]
    rc = _setup_only_cli(monkeypatch, env, *extra, role=role)
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and "--setup-sandbox-only" in captured.err


def test_setup_sandbox_only_refuses_when_the_seam_answers_none(tmp_path, monkeypatch, capsys):
    env, _ = _ritual_ready(tmp_path)
    monkeypatch.setattr(cli, "detect_harnesses", lambda e: [_NoLaunchHarness()])
    rc = _setup_only_cli(monkeypatch, env)
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == "" and captured.err.startswith("trailhead: ")


def test_the_drive_constants_match_the_contract_fixture():  # inert-gate: allow cross-repo contract seam
    from trailhead import launch_profile as lp

    assert {
        "drive_tool": lp.DRIVE_TOOL,
        "release_tool": lp.RELEASE_TOOL,
        "ritual_role": lp.RITUAL_ROLE,
        "sessions_tmux_socket": lp.SESSIONS_SOCKET,
        "ritual_session_pattern": lp.RITUAL_SESSION_PATTERN,
        "task_wrapper_env": lp.TASK_WRAPPER_ENV,
        "task_wrapper_placeholders": list(lp.TASK_WRAPPER_PLACEHOLDERS),
        "setup_sandbox_keys": list(lp.SETUP_SANDBOX_KEYS),
        "drive_groups_output_keys": list(lp.DRIVE_GROUPS_OUTPUT_KEYS),
    } == {k: CONTRACT[k] for k in (
        "drive_tool", "release_tool", "ritual_role", "sessions_tmux_socket", "ritual_session_pattern",
        "task_wrapper_env", "task_wrapper_placeholders", "setup_sandbox_keys", "drive_groups_output_keys",
    )}


def test_camps_task_wrapper_names_match_the_contract_fixture():  # inert-gate: allow cross-package contract seam
    import importlib.util
    import sys

    path = REPO / "tools" / "camp" / "plugins" / "camp" / "camp" / "provision" / "wrapper.py"
    spec = importlib.util.spec_from_file_location("camp_task_wrapper_contract", path)
    camp_wrapper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = camp_wrapper
    spec.loader.exec_module(camp_wrapper)

    assert camp_wrapper.TASK_WRAPPER_ENV == CONTRACT["task_wrapper_env"]
    assert list(camp_wrapper.TASK_WRAPPER_PLACEHOLDERS) == CONTRACT["task_wrapper_placeholders"]


# -- the broker MCP server ---------------------------------------------------

SECRET = "s3cret-client-secret-value"



def _flag_value(argv, flag):
    return argv[argv.index(flag) + 1]


def _covered_by(denied: list[str], path: Path) -> bool:
    return any(path == Path(d) or Path(d) in path.parents for d in denied)


def _run_subprocess(env, cwd, *extra):
    return subprocess.run(
        [str(REPO / "bin" / "trailhead"), "session-start", "--role", "hub", "--account", "a", "--json", *extra],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )


def test_hub_start_from_a_foreign_cwd_carries_the_broker_server_helper_and_allow_rules(tmp_path):
    env = _broker_ready(tmp_path)
    cred = Path(env["OUTPOST_STATE_DIR"]) / "sessions" / "sess-1" / "broker-client.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(json.dumps({"client_id": "cid", "client_secret": SECRET}))
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    result = _run_subprocess(env, foreign, "--broker-session", "sess-1")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    file = _mcp_config_of(out["argv"], out["files"])
    config = json.loads(file["content"])
    server = config["mcpServers"][CONTRACT["broker_server"]]
    assert server["type"] == "http"
    assert server["url"].endswith(CONTRACT["broker_mcp_path"])
    assert server["url"].startswith("http://127.0.0.1:")
    helper = server["headersHelper"]
    assert helper.startswith(str(REPO / "bin" / "trailhead") + " broker-header --credential-file ")
    assert helper.endswith(out["broker"]["credential_path"])
    assert _flag_value(out["argv"], "--mcp-config") == file["path"]
    assert "--strict-mcp-config" in out["argv"]
    settings = _launched_settings(out)
    assert sorted(settings["permissions"]["allow"]) == sorted(
        f"mcp__{CONTRACT['broker_server']}__{tool}"
        for tool in (CONTRACT["broker_tool"], CONTRACT["drive_tool"], CONTRACT["release_tool"])
    )
    assert file["mode"] == "0600"
    assert SECRET not in result.stdout
    assert "headersHelper" not in " ".join(out["argv"]) and server["url"] not in " ".join(out["argv"])
    assert SECRET not in json.dumps(out["env_set"])


def test_the_mcp_config_file_sits_in_the_session_directory_the_hub_can_neither_read_nor_write(tmp_path):
    env = _fence(tmp_path)
    start = _start(env)
    file = _mcp_config_of(start.argv, start.files)
    settings = _settings_of(start)
    fs = settings["sandbox"]["filesystem"]
    path = Path(file.path)
    assert path.parent == Path(env["OUTPOST_STATE_DIR"]).resolve() / "sessions" / "s1"
    assert _covered_by(fs["denyRead"], path)
    assert _covered_by(fs["denyWrite"], path)


def test_a_profile_naming_no_mcp_server_gets_an_empty_strict_mcp_config(tmp_path):
    env = _fence(tmp_path)
    profile = LaunchProfile(writable_roles=HUB_PROFILE.writable_roles, mcp_servers=())
    start = _start(env, profile=profile, broker=broker_launch("s1", env=env))
    file = _mcp_config_of(start.argv, start.files)
    assert json.loads(file.content) == {"mcpServers": {}}
    assert _flag_value(start.argv, "--mcp-config") == file.path
    assert "--strict-mcp-config" in start.argv
    assert "allow" not in _settings_of(start)["permissions"]


def test_a_profile_naming_no_mcp_server_is_refused_without_a_session_directory(tmp_path):
    env = _fence(tmp_path)
    profile = LaunchProfile(writable_roles=HUB_PROFILE.writable_roles, mcp_servers=())
    with pytest.raises(HarnessError, match="session directory"):
        _start(env, profile=profile, session_dir=None)


def test_a_profile_naming_the_broker_server_is_refused_without_a_broker_session(tmp_path):
    env = _fence(tmp_path)
    resolved = _resolved(env)
    home = hub_home("a", env=env, platform="linux")
    home.mkdir(parents=True)
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    with pytest.raises(HarnessError, match="broker"):
        ClaudeCodeHarness().session_start(
            resolved, home, None, interactive=True, account=None, env=env
        )


def test_cli_refuses_a_hub_start_without_a_broker_session_cleanly(tmp_path, monkeypatch, capsys):
    env = _broker_ready(tmp_path)
    rc = _run_cli(monkeypatch, env, ["session-start", "--role", "hub", "--account", "a", "--json"])
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and "broker" in captured.err


def test_the_helper_command_runs_by_absolute_path_from_a_foreign_cwd(tmp_path):
    env = _broker_ready(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    out = json.loads(_run_subprocess(env, foreign, "--broker-session", "sess-1").stdout)
    helper = json.loads(out["files"][0]["content"])["mcpServers"]["outpost"]["headersHelper"]
    ran = subprocess.run(
        ["sh", "-c", helper], cwd=foreign, env={**env, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=60,
    )
    assert ran.returncode == 1 and ran.stdout == ""
    assert "credential-missing" in ran.stderr


def test_a_helper_path_with_a_space_survives_the_shell(tmp_path):
    env = _fence(tmp_path)
    launch = broker_launch("s1", env=env, trailhead_bin="/opt/my tools/trailhead")
    start = _start(env, broker=launch)
    helper = json.loads(start.files[0].content)["mcpServers"]["outpost"]["headersHelper"]
    import shlex

    assert shlex.split(helper)[0] == "/opt/my tools/trailhead"
    assert shlex.split(helper)[-1] == launch.credential_path


def test_the_denied_reads_cover_the_database_and_session_credentials_and_follow_the_state_dir(tmp_path):
    for name in ("one", "two"):
        env = {**_fence(tmp_path), "OUTPOST_STATE_DIR": str(tmp_path.resolve() / name)}
        settings = _settings_of(_start(env))
        denied = settings["sandbox"]["filesystem"]["denyRead"]
        base = Path(env["OUTPOST_STATE_DIR"])
        assert _covered_by(denied, base / "db" / "state.db")
        assert _covered_by(denied, base / "db" / "late.db-wal")
        assert _covered_by(denied, base / "sessions" / "s1" / "broker-client.json")
        assert _covered_by(denied, base / "operator" / "credential")
        assert all(Path(d) == base for d in denied)


def test_user_allowed_domains_the_profile_does_not_name_are_denied_in_the_session(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(
        env, _user_sandbox(allowWrite=[".", cache, lore], allowedDomains=["a.example", "b.example"])
    )
    profile = LaunchProfile(
        writable_roles=HUB_PROFILE.writable_roles, network_allowed_hosts=("a.example",)
    )
    assert _settings_of(_start(env, profile=profile))["sandbox"]["network"]["deniedDomains"] == ["b.example"]
    bare = LaunchProfile(writable_roles=HUB_PROFILE.writable_roles)
    assert _settings_of(_start(env, profile=bare))["sandbox"]["network"]["deniedDomains"] == [
        "a.example",
        "b.example",
    ]


def test_a_wildcard_user_domain_covering_a_named_host_is_not_denied(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(
        env, _user_sandbox(allowWrite=[".", cache, lore], allowedDomains=["*.a.example", "b.example"])
    )
    profile = LaunchProfile(
        writable_roles=HUB_PROFILE.writable_roles, network_allowed_hosts=("x.a.example",)
    )
    assert _settings_of(_start(env, profile=profile))["sandbox"]["network"]["deniedDomains"] == ["b.example"]


def test_no_user_allow_list_renders_no_denied_domains(tmp_path):
    env = _fence(tmp_path)
    assert "deniedDomains" not in _settings_of(_start(env))["sandbox"].get("network", {})


# -- never-widen: every widening settings source (F4-F8) ------------------------


def _permissive_user(env, **extra) -> Path:
    home, lore, cache = _hub_roots(env)
    return _write_user_settings(env, {**_user_allowing_hub(env), **extra})


def test_a_managed_write_denial_over_a_hub_root_is_refused_naming_the_source(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _permissive_user(env)
    managed = _write_managed(tmp_path, {"sandbox": {"filesystem": {"denyWrite": [lore]}}})
    with pytest.raises(HarnessError, match="managed") as raised:
        _start(env)
    assert lore in str(raised.value) and str(managed) in str(raised.value)
    assert f"Remove or narrow it in {managed} so it no longer covers {lore}" in str(raised.value)


@pytest.mark.parametrize("drop_in", [None, "20-sec.json"])
def test_macos_managed_settings_are_read_from_the_application_support_directory(tmp_path, drop_in):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _permissive_user(env)
    managed = _write_managed(
        tmp_path, {"sandbox": {"filesystem": {"denyWrite": [lore]}}}, drop_in=drop_in, fence="managed-fence-darwin"
    )
    with pytest.raises(HarnessError, match="managed") as raised:
        _start(env, platform="darwin")
    assert lore in str(raised.value) and str(managed) in str(raised.value)


def test_linux_managed_settings_do_not_apply_on_macos_and_macos_ones_do_not_apply_on_linux(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _permissive_user(env)
    _write_managed(tmp_path, {"sandbox": {"filesystem": {"denyWrite": [lore]}}})
    assert _start(env, platform="darwin") is not None
    _write_managed(tmp_path, {"sandbox": {"filesystem": {"denyWrite": [cache]}}}, fence="managed-fence-darwin")
    with pytest.raises(HarnessError, match="managed"):
        _start(env, platform="linux")


def test_a_managed_drop_in_read_denial_over_a_hub_root_is_refused(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _permissive_user(env)
    managed = _write_managed(
        tmp_path, {"sandbox": {"filesystem": {"denyRead": [cache]}}}, drop_in="20-sec.json"
    )
    with pytest.raises(HarnessError, match=cache) as raised:
        _start(env)
    assert f"Remove or narrow it in {managed} so it no longer covers {cache}" in str(raised.value)


def test_a_managed_read_permission_rule_over_a_hub_root_is_refused(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _permissive_user(env)
    managed = _write_managed(tmp_path, {"permissions": {"deny": [f"Read(/{lore}/**)"]}})
    with pytest.raises(HarnessError, match=lore) as raised:
        _start(env)
    assert f"Remove or narrow it in {managed} so it no longer covers {lore}" in str(raised.value)


def test_a_managed_denial_elsewhere_does_not_block_the_launch(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env)
    other = str(tmp_path.resolve() / "elsewhere")
    _write_managed(tmp_path, {"sandbox": {"filesystem": {"denyWrite": [other], "denyRead": [other]}}})
    assert _start(env) is not None


def test_a_managed_excluded_command_is_denied_in_the_session(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env)
    _write_managed(tmp_path, {"sandbox": {"excludedCommands": ["docker"]}})
    assert "Bash(docker)" in _settings_of(_start(env))["permissions"]["deny"]


def test_a_managed_write_allowance_counts_toward_what_the_profile_may_write(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache]))
    _write_managed(tmp_path, {"sandbox": {"filesystem": {"allowWrite": [lore]}}})
    assert _start(env) is not None


@pytest.mark.parametrize("content", ["{not json", "[]"])
def test_malformed_managed_settings_refuse_the_launch_naming_the_file(tmp_path, content):
    env = _fence(tmp_path)
    _permissive_user(env)
    managed = _write_managed(tmp_path, content)
    with pytest.raises(HarnessError, match="managed-settings.json") as raised:
        _start(env)
    assert str(managed) in str(raised.value)


def test_a_managed_single_slash_read_rule_has_no_provable_anchor_and_is_refused(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env)
    _write_managed(tmp_path, {"permissions": {"deny": ["Read(/secrets/**)"]}})
    with pytest.raises(HarnessError, match=r"Read\(/secrets/\*\*\)"):
        _start(env)


def test_a_single_slash_user_read_rule_is_relative_to_the_settings_dir_not_the_filesystem_root(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _permissive_user(env, permissions={"deny": [f"Read({cache}/**)"]})
    assert _start(env) is not None


def test_a_single_slash_read_rule_covers_the_hub_root_it_resolves_to_under_the_account_dir(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    acct = tmp_path.resolve()
    rule = "Read(/" + str(Path(cache).relative_to(acct)) + "/**)"
    _write_user_settings(
        env,
        {**_user_allowing_hub(env), "permissions": {"deny": [rule]}},
        account_dir=acct,
    )
    with pytest.raises(HarnessError, match=re.escape(rule)):
        _start(env, account=str(acct))


def test_a_double_slash_read_rule_is_an_absolute_path(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _permissive_user(env, permissions={"deny": [f"Read(/{cache}/**)"]})
    with pytest.raises(HarnessError, match=cache):
        _start(env)


def test_a_wildcard_read_rule_that_matches_a_hub_root_is_refused(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    parent = str(Path(cache).parent.parent)
    rule = f"Read(/{parent}/*/{Path(cache).name}/**)"
    _permissive_user(env, permissions={"deny": [rule]})
    with pytest.raises(HarnessError, match=re.escape(rule)) as raised:
        _start(env)
    assert f"so it no longer covers {cache}" in str(raised.value)


def test_a_wildcard_read_rule_that_matches_no_hub_root_passes(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    parent = str(Path(cache).parent.parent)
    _permissive_user(env, permissions={"deny": [f"Read(/{parent}/*/no-such-dir/**)"]})
    assert _start(env) is not None


@pytest.mark.parametrize("glob", ["{{cache_name},x}", "[a-z]ache"])
def test_a_read_glob_the_check_cannot_evaluate_is_refused_naming_the_rule_and_the_remedy(tmp_path, glob):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    parent = str(Path(cache).parent)
    rule = f"Read(/{parent}/{glob.replace('{cache_name}', Path(cache).name)}/**)"
    _permissive_user(env, permissions={"deny": [rule]})
    with pytest.raises(HarnessError) as raised:
        _start(env)
    assert rule in str(raised.value)
    assert "rewrite it as Read(//absolute/path/**)" in str(raised.value)
    assert "refusing the launch" in str(raised.value)


def test_a_bare_read_everything_rule_covers_the_hub_home_and_is_refused(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env, permissions={"deny": ["Read(**)"]})
    with pytest.raises(HarnessError, match=r"Read\(\*\*\)"):
        _start(env)


def test_a_floating_filename_read_rule_does_not_block_the_hub(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env, permissions={"deny": ["Read(**/.env)", "Read(.env.*)"]})
    assert _start(env) is not None


def test_no_user_write_list_and_a_profile_root_outside_the_working_directory_is_refused(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    user = _write_user_settings(env, {"model": "opus"})
    with pytest.raises(HarnessError) as raised:
        _start(env)
    text = str(raised.value)
    assert lore in text and str(user) in text
    assert f"Add {lore} to 'sandbox.filesystem.allowWrite' in {user}" in text


def test_an_absent_user_settings_file_is_refused_for_a_profile_root_outside_the_working_directory(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    with pytest.raises(HarnessError, match="sandbox.filesystem.allowWrite"):
        _start(env, bare=True)


def test_a_profile_with_only_working_directory_roots_needs_no_user_write_list(tmp_path):
    env = _fence(tmp_path)
    only_home = LaunchProfile(writable_roles=HUB_PROFILE.writable_roles[:1])
    assert _start(env, profile=only_home) is not None


def test_the_default_account_with_an_ambient_config_dir_reads_and_trusts_where_the_session_will(tmp_path):
    env = _fence(tmp_path)
    ambient = tmp_path.resolve() / "ambient-account"
    ambient.mkdir()
    env["CLAUDE_CONFIG_DIR"] = str(ambient)
    _permissive_user(env)
    _write_user_settings(env, _user_sandbox(allowWrite=["."]), account_dir=ambient)
    start = _start(env)
    hub = hub_home("a", env=env, platform="linux").resolve()
    seeded = json.loads((Path(env["HOME"]) / ".claude.json").read_text())
    assert seeded["projects"][str(hub)]["hasTrustDialogAccepted"] is True
    assert not (ambient / ".claude.json").exists()
    assert "CLAUDE_CONFIG_DIR" in start.env_unset and start.env_set == {"PATH": env["PATH"]}


# -- the headers helper's code must not be writable by the session -----------------


def _launch_with_bin(env, binary: str):
    return _start(env, broker=broker_launch("s1", env=env, trailhead_bin=binary))


@pytest.mark.parametrize("root_index", [0, 1, 2])
def test_a_helper_checkout_under_a_hub_write_root_is_refused_naming_path_and_remedy(off_temp, root_index):
    tmp_path = off_temp
    env = _fence(tmp_path)
    _permissive_user(env)
    root = _hub_roots(env)[root_index]
    binary = f"{root}/checkout/bin/trailhead"
    with pytest.raises(HarnessError) as raised:
        _launch_with_bin(env, binary)
    assert binary in str(raised.value) and root in str(raised.value)
    assert "Run trailhead from a checkout outside" in str(raised.value)
    assert "refusing the launch" in str(raised.value)


def test_a_helper_checkout_under_system_temp_is_refused(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env)
    binary = f"{tempfile.gettempdir()}/some-checkout/bin/trailhead"
    with pytest.raises(HarnessError, match="some-checkout"):
        _launch_with_bin(env, binary)


def test_a_helper_symlinked_into_a_hub_write_root_is_refused(off_temp):
    tmp_path = off_temp
    env = _fence(tmp_path)
    _permissive_user(env)
    cache = Path(_hub_roots(env)[2])
    real = cache / "real" / "bin"
    real.mkdir(parents=True)
    (real / "trailhead").write_text("#!/bin/sh\n")
    link_dir = tmp_path.resolve() / "elsewhere" / "bin"
    link_dir.mkdir(parents=True)
    (link_dir / "trailhead").symlink_to(real / "trailhead")
    with pytest.raises(HarnessError, match="real/bin/trailhead"):
        _launch_with_bin(env, str(link_dir / "trailhead"))


def test_a_hub_write_root_inside_the_helper_package_is_refused(off_temp):
    tmp_path = off_temp
    base = tmp_path.resolve() / "checkout"
    env = {**_fence(tmp_path), "XDG_CACHE_HOME": str(base / "trailhead" / "cache-home")}
    _permissive_user(env)
    with pytest.raises(HarnessError, match="checkout"):
        _launch_with_bin(env, str(base / "bin" / "trailhead"))


def test_a_python3_on_path_under_a_hub_write_root_is_refused(off_temp):
    tmp_path = off_temp
    env = _fence(tmp_path)
    _permissive_user(env)
    pybin = Path(_hub_roots(env)[2]) / "pybin"
    pybin.mkdir(parents=True)
    python = pybin / "python3"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    env["PATH"] = f"{pybin}:{env['PATH']}"
    with pytest.raises(HarnessError, match="python3") as raised:
        _start(env)
    assert str(pybin) in str(raised.value)


@pytest.mark.parametrize("entry", ["bin", "./bin", ""])
def test_a_relative_or_empty_path_entry_is_refused_naming_the_entry_and_the_remedy(off_temp, entry):
    env = _fence(off_temp)
    _permissive_user(env)
    env["PATH"] = f"/usr/bin:{entry}:/bin"
    with pytest.raises(HarnessError) as raised:
        _start(env)
    text = str(raised.value)
    assert "PATH" in text and (repr(entry) in text or entry == "")
    assert "Remove it from the PATH Outpost runs with" in text
    assert "refusing the launch" in text


def test_a_path_entry_under_a_hub_write_root_is_refused_even_when_it_holds_no_python3(off_temp):
    env = _fence(off_temp)
    _permissive_user(env)
    entry = f"{_hub_roots(env)[2]}/tools"
    env["PATH"] = f"{entry}:{env['PATH']}"
    with pytest.raises(HarnessError) as raised:
        _start(env)
    text = str(raised.value)
    assert entry in text and "PATH" in text and "refusing the launch" in text
    assert f"Put a PATH outside {_hub_roots(env)[2]}" in text


def test_a_path_entry_that_contains_a_hub_write_root_is_refused(off_temp):
    env = _fence(off_temp)
    _permissive_user(env)
    entry = str(Path(_hub_roots(env)[2]).parent)
    env["PATH"] = f"{entry}:{env['PATH']}"
    with pytest.raises(HarnessError, match="PATH"):
        _start(env)


def test_a_path_entry_symlinked_into_a_hub_write_root_is_refused(off_temp):
    env = _fence(off_temp)
    _permissive_user(env)
    cache = Path(_hub_roots(env)[2])
    real = cache / "real-bin"
    real.mkdir(parents=True)
    link = off_temp.resolve() / "elsewhere-bin"
    link.symlink_to(real)
    env["PATH"] = f"{link}:{env['PATH']}"
    with pytest.raises(HarnessError, match="real-bin"):
        _start(env)


def test_a_path_entry_inside_a_hub_write_root_that_links_out_is_refused(off_temp):
    env = _fence(off_temp)
    _permissive_user(env)
    cache = Path(_hub_roots(env)[2])
    cache.mkdir(parents=True)
    outside = off_temp.resolve() / "outside-bin"
    outside.mkdir()
    link = cache / "link-bin"
    link.symlink_to(outside)
    env["PATH"] = f"{link}:{env['PATH']}"
    with pytest.raises(HarnessError, match="link-bin"):
        _start(env)


def test_a_path_entry_that_only_shares_a_name_prefix_with_a_write_root_passes(off_temp):
    env = _fence(off_temp)
    _permissive_user(env)
    env["PATH"] = f"{_hub_roots(env)[2]}-tools:{env['PATH']}"
    assert _start(env) is not None


def test_the_same_launch_with_the_helper_code_outside_every_write_root_passes(off_temp):
    tmp_path = off_temp
    env = _fence(tmp_path)
    _permissive_user(env)
    assert _launch_with_bin(env, "/opt/trailhead-checkout/bin/trailhead") is not None


# -- settings that let the hub write the helper's code through a tool, or leave the sandbox ---------


def _edit_denies(start: SessionStart) -> list[str]:
    return [r for r in _settings_of(start)["permissions"]["deny"] if r.startswith("Edit(")]


def test_a_user_edit_allow_over_the_checkout_leaves_the_helper_code_denied_to_every_edit_tool(off_temp):
    tmp_path = off_temp
    env = _fence(tmp_path)
    checkout = Path(env["HOME"]) / "code" / "trailhead"
    _permissive_user(env, permissions={"allow": ["Edit(~/code/**)"]})
    start = _launch_with_bin(env, f"{checkout}/bin/trailhead")
    denies = _edit_denies(start)
    for guarded in (
        f"Edit(/{checkout}/bin/**)",
        f"Edit(/{checkout}/trailhead/**)",
        f"Edit(/{checkout}/pyproject.toml)",
        f"Edit(/{checkout}/bin/trailhead)",
    ):
        assert guarded in denies


def test_the_denied_helper_code_follows_where_the_checkout_is(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env, permissions={"allow": ["Edit(~/code/**)"]})
    start = _launch_with_bin(env, "/opt/trailhead-checkout/bin/trailhead")
    denies = _edit_denies(start)
    assert "Edit(//opt/trailhead-checkout/bin/**)" in denies
    assert not any(str(Path(env["HOME"]) / "code") in r for r in denies)


def test_a_symlinked_helper_has_its_real_location_denied_too(off_temp):
    tmp_path = off_temp
    env = _fence(tmp_path)
    _permissive_user(env)
    real = tmp_path.resolve() / "real-checkout" / "bin"
    real.mkdir(parents=True)
    (real / "trailhead").write_text("#!/bin/sh\n")
    link_dir = tmp_path.resolve() / "link-checkout" / "bin"
    link_dir.mkdir(parents=True)
    (link_dir / "trailhead").symlink_to(real / "trailhead")
    denies = _edit_denies(_launch_with_bin(env, str(link_dir / "trailhead")))
    assert f"Edit(/{link_dir}/**)" in denies and f"Edit(/{real}/**)" in denies


def test_outpost_state_is_denied_to_every_edit_tool_wherever_it_is(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env)
    state = tmp_path.resolve() / "elsewhere-state"
    env["OUTPOST_STATE_DIR"] = str(state)
    denies = _edit_denies(_start(env))
    assert f"Edit(/{state}/**)" in denies


@pytest.mark.parametrize(
    "extra",
    [
        {"permissions": {"allow": ["Edit"]}},
        {"permissions": {"allow": ["Write"]}},
        {"permissions": {"allow": ["Edit(//opt/**)"]}},
        {"permissions": {"additionalDirectories": ["/opt"]}},
    ],
)
def test_path_directories_are_denied_to_edit_tools_when_user_settings_grant_file_access(tmp_path, extra):
    env = _fence(tmp_path)
    _permissive_user(env, **extra)
    env["PATH"] = f"/opt/tools/bin:{env['PATH']}"
    assert "Edit(//opt/tools/bin/**)" in _edit_denies(_start(env))


def test_path_directories_are_left_alone_when_nothing_grants_file_access(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env, permissions={"allow": ["Bash(ls)"]})
    env["PATH"] = f"/opt/tools/bin:{env['PATH']}"
    assert "Edit(//opt/tools/bin/**)" not in _edit_denies(_start(env))


def test_a_managed_edit_allow_also_denies_path_directories(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env)
    _write_managed(tmp_path, {"permissions": {"allow": ["Write"]}})
    env["PATH"] = f"/opt/tools/bin:{env['PATH']}"
    assert "Edit(//opt/tools/bin/**)" in _edit_denies(_start(env))


@pytest.mark.parametrize(
    "setting, key",
    [
        ({"enabled": False}, "sandbox.enabled"),
        ({"allowUnsandboxedCommands": True}, "sandbox.allowUnsandboxedCommands"),
    ],
)
def test_managed_settings_that_turn_the_sandbox_off_refuse_naming_file_key_and_remedy(tmp_path, setting, key):
    env = _fence(tmp_path)
    _permissive_user(env)
    managed = _write_managed(tmp_path, {"sandbox": setting})
    with pytest.raises(HarnessError) as raised:
        _start(env)
    text = str(raised.value)
    assert str(managed) in text and key in text
    assert f"Remove {key} from {managed}" in text and "refusing the launch" in text


def test_a_managed_drop_in_that_turns_the_sandbox_back_on_wins_over_the_base_file(tmp_path):
    env = _fence(tmp_path)
    _permissive_user(env)
    _write_managed(tmp_path, {"sandbox": {"enabled": False}})
    _write_managed(tmp_path, {"sandbox": {"enabled": True}}, drop_in="50-on.json")
    assert _settings_of(_start(env))["sandbox"]["enabled"] is True


def test_a_user_level_sandbox_off_is_overridden_by_the_hubs_own_settings(tmp_path):
    env = _fence(tmp_path)
    user = _user_allowing_hub(env)
    user["sandbox"].update({"enabled": False, "allowUnsandboxedCommands": True})
    _write_user_settings(env, user)
    sandbox = _settings_of(_start(env))["sandbox"]
    assert sandbox["enabled"] is True and sandbox["allowUnsandboxedCommands"] is False


_WIDENERS = [
    (("network",), "allowLocalBinding"),
    (("network",), "allowAllUnixSockets"),
    (("filesystem",), "disabled"),
]


def _sandbox_with(section: tuple[str, ...], key: str, value) -> dict:
    return {"sandbox": {"enabled": True, section[0]: {key: value}}}


@pytest.mark.parametrize("section, key", _WIDENERS)
def test_a_user_level_widening_flag_is_rendered_false_for_the_hub(tmp_path, section, key):
    env = _fence(tmp_path)
    user = _user_allowing_hub(env)
    user["sandbox"].setdefault(section[0], {})[key] = True
    _write_user_settings(env, user)
    assert _settings_of(_start(env))["sandbox"][section[0]][key] is False


@pytest.mark.parametrize("section, key", _WIDENERS)
def test_a_flag_the_user_leaves_unset_or_false_renders_nothing(tmp_path, section, key):
    env = _fence(tmp_path)
    user = _user_allowing_hub(env)
    _write_user_settings(env, user)
    assert key not in _settings_of(_start(env))["sandbox"].get(section[0], {})


@pytest.mark.parametrize("section, key", _WIDENERS)
def test_a_managed_widening_flag_refuses_naming_file_key_and_remedy(tmp_path, section, key):
    env = _fence(tmp_path)
    _permissive_user(env)
    managed = _write_managed(tmp_path, _sandbox_with(section, key, True))
    label = f"sandbox.{section[0]}.{key}"
    with pytest.raises(HarnessError) as raised:
        _start(env)
    text = str(raised.value)
    assert str(managed) in text and label in text
    assert f"Remove {label} from {managed}" in text and "refusing the launch" in text


@pytest.mark.parametrize("where", ["user", "managed"])
def test_a_non_empty_unix_socket_allow_list_refuses_because_lists_cannot_be_narrowed(tmp_path, where):
    env = _fence(tmp_path)
    sockets = {"network": {"allowUnixSockets": ["/var/run/docker.sock"]}}
    if where == "user":
        user = _user_allowing_hub(env)
        user["sandbox"].update(sockets)
        source = _write_user_settings(env, user)
    else:
        _permissive_user(env)
        source = _write_managed(tmp_path, {"sandbox": sockets})
    with pytest.raises(HarnessError) as raised:
        _start(env)
    text = str(raised.value)
    assert str(source) in text and "sandbox.network.allowUnixSockets" in text
    assert f"Remove sandbox.network.allowUnixSockets from {source}" in text


def test_an_empty_unix_socket_allow_list_launches(tmp_path):
    env = _fence(tmp_path)
    user = _user_allowing_hub(env)
    user["sandbox"]["network"] = {"allowUnixSockets": []}
    _write_user_settings(env, user)
    assert _start(env) is not None


def test_a_bare_read_rule_matches_at_any_depth_under_the_launch_directory_but_a_dot_slash_rule_does_not(tmp_path):
    env = _fence(tmp_path)
    Path(env["HOME"]).mkdir(parents=True)
    fence = Path(env["HOME"]).parent
    writable = {"sandbox": {"filesystem": {"allowWrite": list(_hub_roots(env))}}}
    _write_user_settings(env, {**writable, "permissions": {"deny": ["Read(./hubs/**)"]}})
    assert _start_in(env, fence) is not None
    _write_user_settings(env, {**writable, "permissions": {"deny": ["Read(hubs/**)"]}})
    with pytest.raises(HarnessError, match=re.escape("Read(hubs/**)")):
        _start_in(env, fence)


def test_a_helper_checkout_under_the_session_directory_is_refused(off_temp):
    env = _fence(off_temp)
    elsewhere = off_temp.resolve() / "elsewhere"
    elsewhere.mkdir()
    _write_user_settings(env, {"sandbox": {"filesystem": {"allowWrite": list(_hub_roots(env))}}})
    binary = str(elsewhere / "bin" / "trailhead")
    with pytest.raises(HarnessError, match=re.escape(binary)):
        ClaudeCodeHarness().session_start(
            _resolved(env), elsewhere, None, interactive=True, account=None, env=env,
            broker=broker_launch("s1", env=env, trailhead_bin=binary),
        )


# -- the ritual session ------------------------------------------------------


def _camp_root(env) -> Path:
    return Path(env["CAMP_STATE_DIR"])


def _make_workspace(env, group="grp", slug="ws1", *, manifest=True) -> Path:
    ws = _camp_root(env) / group / "worktrees" / slug
    ws.mkdir(parents=True, exist_ok=True)
    if manifest:
        (ws / "manifest.json").write_text("{}")
    return ws.resolve()


def _ritual_roots(env, ws) -> tuple[str, str, str]:
    r = _ritual_resolved(env, ws)
    return tuple(str(p) for p in r.writable)  # workspace, lore-state, cache


RITUAL_VAULT = "work"


def _lore_vault_dirs(env, *names) -> None:
    for name in names:
        (Path(env["LORE_STATE_DIR"]) / "vaults" / name).mkdir(parents=True, exist_ok=True)


def _ritual_resolved(env, ws, profile=RITUAL_PROFILE, platform="linux", vault=RITUAL_VAULT):
    _lore_vault_dirs(env, vault)
    return resolve_profile(profile, "a", env=env, platform=platform, workspace=str(ws), vault=vault)


def _ritual_user(env, ws, **extra) -> None:
    """The user's settings a ritual may run under: cache and lore are writable, the workspace
    is the working directory."""
    _, lore, cache = _ritual_roots(env, ws)
    data = _user_sandbox(allowWrite=[".", cache, lore], allowedDomains=["api.anthropic.com"])
    data.update(extra)
    _write_user_settings(env, data)


def _ritual_ready(tmp_path, *, launch="default"):
    env = _fence(tmp_path)
    _configure_hub(env, launch)
    Path(env["HOME"], ".claude").mkdir(parents=True, exist_ok=True)
    ws = _make_workspace(env)
    _ritual_user(env, ws)
    return env, ws


def _ritual_start(
    env, ws, *, profile=RITUAL_PROFILE, platform="linux", prompt=None, account=None, vault=RITUAL_VAULT, **extra
):
    extra.setdefault("session_dir", Path(env["OUTPOST_STATE_DIR"]).resolve() / "sessions" / "rit-1")
    return ClaudeCodeHarness().session_start(
        _ritual_resolved(env, ws, profile, platform, vault), ws, prompt,
        interactive=False, account=account, env=env, platform=platform, **extra,
    )


def _group_toml(env, name, *, account=None, vaults=(), shared=()):
    groups = Path(env["CAMP_CONFIG_DIR"]) / "groups"
    groups.mkdir(parents=True, exist_ok=True)
    text = f'name = "{name}"\n'
    if account:
        text += f'[launch]\naccount = "{account}"\n'
    for v in vaults:
        text += f'[[lore_scopes]]\nscope = "team"\nname = "{v}"\n'
    for v, root in shared:
        text += f'[[shared_vaults]]\nname = "{v}"\nroot = "{root}"\n'
    (groups / f"{name}.toml").write_text(text)


def _other_account(env, name="b", launch="~/acct-b"):
    write_hub_config(HubConfig(name=f"Hub {name}", account=name, launch_account=launch), env=env, platform="linux")


def _ritual_cli(monkeypatch, env, ws, *extra, session="rit-1", vault=RITUAL_VAULT):
    if vault is not None:
        _lore_vault_dirs(env, vault)
    return _run_cli(
        monkeypatch, env,
        ["session-start", "--role", RITUAL_ROLE, "--account", "a", "--workspace", str(ws), "--json",
         *([f"--session={session}"] if session is not None else []),
         *([f"{VAULT_FLAG}={vault}"] if vault is not None else []), *extra],
    )


def test_ritual_launch_writes_only_the_workspace_the_lore_state_and_the_cache(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    start = _ritual_start(env, ws)
    fs = _settings_of(start)["sandbox"]["filesystem"]
    assert fs["allowWrite"] == [str(ws), str(Path(env["LORE_STATE_DIR"]).resolve()),
                                str(Path(env["XDG_CACHE_HOME"]).resolve())]
    other = _make_workspace(env, slug="ws2")
    other_fs = _settings_of(_ritual_start(env, other))["sandbox"]["filesystem"]
    assert other_fs["allowWrite"][0] == str(other)


def test_ritual_launch_has_no_broker_allow_rules_and_an_empty_strict_mcp_config(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    start = _ritual_start(env, ws)
    assert not [r for r in _settings_of(start)["permissions"].get("allow", []) if r.startswith("mcp__")]
    assert "--strict-mcp-config" in start.argv
    file = _mcp_config_of(start.argv, start.files)
    assert _flag_value(start.argv, "--mcp-config") == file.path
    assert json.loads(file.content) == {"mcpServers": {}}
    assert file.mode == "0600"


@pytest.mark.parametrize("sid", ["rit-1", "Abc_9", "x" * 64])
def test_cli_places_a_rituals_empty_mcp_config_in_outposts_session_directory(
    tmp_path, monkeypatch, capsys, sid
):
    env, ws = _ritual_ready(tmp_path)
    assert _ritual_cli(monkeypatch, env, ws, "--headless", session=sid) == 0
    out = json.loads(capsys.readouterr().out)
    expected = str(Path(env["OUTPOST_STATE_DIR"]).resolve() / "sessions" / sid / "empty-mcp-config.json")
    file = _mcp_config_of(out["argv"], out["files"])
    assert file["path"] == expected and file["mode"] == "0600"
    assert json.loads(file["content"]) == {"mcpServers": {}}
    assert _flag_value(out["argv"], "--mcp-config") == expected


@pytest.mark.parametrize("sid", ["", "-lead", "a/b", "../x", "has space", "x" * 65])
def test_cli_refuses_a_ritual_session_id_outside_the_broker_session_shape(tmp_path, monkeypatch, capsys, sid):
    env, ws = _ritual_ready(tmp_path)
    rc = _ritual_cli(monkeypatch, env, ws, "--headless", session=sid)
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == "" and captured.err.startswith("trailhead: ")


def test_cli_refuses_a_ritual_start_without_a_session(tmp_path, monkeypatch, capsys):
    env, ws = _ritual_ready(tmp_path)
    rc = _ritual_cli(monkeypatch, env, ws, "--headless", session=None)
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and "--session" in captured.err


def test_cli_refuses_a_session_with_setup_sandbox_only(tmp_path, monkeypatch, capsys):
    env, _ = _ritual_ready(tmp_path)
    rc = _setup_only_cli(monkeypatch, env, "--session", "rit-1")
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and "--session" in captured.err


def test_cli_refuses_a_session_for_the_hub(tmp_path, monkeypatch, capsys):
    env = _broker_ready(tmp_path)
    rc = _run_cli(monkeypatch, env, [*_broker_argv("sess-1"), "--session", "sess-1"])
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and "--session" in captured.err


def test_ritual_file_tools_may_write_wherever_its_sandbox_lets_it_write(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    for workspace in (ws, _make_workspace(env, slug="ws2")):
        settings = _settings_of(_ritual_start(env, workspace))
        roots = settings["sandbox"]["filesystem"]["allowWrite"]
        allowed_edits = [r for r in settings["permissions"]["allow"] if r.startswith("Edit(")]
        assert sorted(allowed_edits) == sorted(f"Edit(/{r}/**)" for r in roots)
        assert f"Edit(/{workspace}/**)" in settings["permissions"]["allow"]


@pytest.mark.parametrize("denied", [(), ("craft:drive",), ("craft:drive", "other:skill")])
def test_the_skills_a_profile_denies_are_denied_to_its_session(tmp_path, denied):
    env = _fence(tmp_path)
    deny = _settings_of(_start(env, profile=replace(HUB_PROFILE, denied_skills=denied)))["permissions"]["deny"]
    assert [r for r in deny if r.startswith("Skill(")] == [f"Skill({s})" for s in denied]


def test_the_hub_drives_a_spec_through_outpost_never_the_in_session_driver(tmp_path):
    hub = _settings_of(_start(_fence(tmp_path)))["permissions"]
    assert "Skill(craft:drive)" in hub["deny"]
    assert f"mcp__{CONTRACT['broker_server']}__{CONTRACT['drive_tool']}" in hub["allow"]


def test_the_hub_may_release_its_paused_drive_without_a_prompt(tmp_path):
    hub = _settings_of(_start(_fence(tmp_path)))["permissions"]
    assert f"mcp__{CONTRACT['broker_server']}__{CONTRACT['release_tool']}" in hub["allow"]


def test_ritual_launch_never_prompts(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    argv = _ritual_start(env, ws).argv
    assert _flag_value(argv, "--permission-mode") == "dontAsk"
    assert _flag_value(argv, "--permission-prompts") == "none" and "-p" in argv


def test_ritual_launch_guards_the_git_admin_files_of_lore_vaults(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    vault = _make_vault(env["LORE_STATE_DIR"], "default")
    deny = _settings_of(_ritual_start(env, ws))["sandbox"]["filesystem"]["denyWrite"]
    assert str(vault.resolve() / ".git" / "config") in deny


@pytest.mark.parametrize("vaults", [(), ("default",), ("default", "team")])
def test_ritual_file_tools_are_refused_wherever_its_sandbox_refuses_writes(tmp_path, vaults):
    env, ws = _ritual_ready(tmp_path)
    for name in vaults:
        _make_vault(env["LORE_STATE_DIR"], name)
    settings = _settings_of(_ritual_start(env, ws))
    deny = settings["permissions"]["deny"]
    denied_writes = settings["sandbox"]["filesystem"]["denyWrite"]
    for name in vaults:
        assert str(Path(env["LORE_STATE_DIR"]).resolve() / "vaults" / name / ".git" / "hooks") in denied_writes
    assert str(ws / ".claude") in denied_writes
    for path in denied_writes:
        assert f"Edit(/{path})" in deny and f"Edit(/{path}/**)" in deny


def test_ritual_reads_deny_the_token_store_the_operator_credential_and_every_other_account(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _other_account(env)
    _group_toml(env, "mine", vaults=["avault"], shared=[("ashared", "/srv/ashared")])
    _group_toml(env, "theirs", account="~/acct-g", vaults=["bvault"], shared=[("bshared", "/srv/bshared")])
    start = _ritual_start(env, ws)
    denied = _settings_of(start)["sandbox"]["filesystem"]["denyRead"]
    lore = Path(env["LORE_STATE_DIR"]).resolve()
    credential = Path(env["OUTPOST_STATE_DIR"]).resolve() / "operator" / "credential"
    assert _covered_by(denied, credential)
    state = Path(env["RANGER_STATE_DIR"]).resolve()
    cache = Path(env["RANGER_CACHE_DIR"]).resolve()
    assert str(state / "hubs" / "b") in denied
    assert str(cache / "mirrors" / "b") in denied
    assert str(lore / "vaults" / "bvault") in denied and "/srv/bshared" in denied
    assert str(Path(env["HOME"]).resolve() / "acct-b") in denied
    assert str(Path(env["HOME"]).resolve() / "acct-g") in denied
    assert str(state / "hubs" / "a") not in denied
    assert str(lore / "vaults" / "avault") not in denied and "/srv/ashared" not in denied
    rules = _settings_of(start)["permissions"]["deny"]
    assert f"Read(/{lore}/vaults/bvault/**)" in rules


def test_a_vault_one_of_the_accounts_own_groups_binds_is_not_denied_even_when_another_group_binds_it(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _other_account(env)
    _group_toml(env, "mine", vaults=["common"])
    _group_toml(env, "theirs", account="~/acct-b", vaults=["common", "bonly"])
    denied = _settings_of(_ritual_start(env, ws))["sandbox"]["filesystem"]["denyRead"]
    lore = Path(env["LORE_STATE_DIR"]).resolve()
    assert str(lore / "vaults" / "bonly") in denied
    assert str(lore / "vaults" / "common") not in denied


def test_the_accounts_own_launch_account_is_not_denied_but_the_default_groups_vaults_are(tmp_path):
    env, ws = _ritual_ready(tmp_path, launch="~/acct-a")
    _group_toml(env, "mine", account="~/acct-a", vaults=["avault"])
    _group_toml(env, "plain", vaults=["pvault"])
    denied = _settings_of(_ritual_start(env, ws))["sandbox"]["filesystem"]["denyRead"]
    lore = Path(env["LORE_STATE_DIR"]).resolve()
    assert str(lore / "vaults" / "pvault") in denied
    assert str(lore / "vaults" / "avault") not in denied
    assert str(Path(env["HOME"]).resolve() / "acct-a") not in denied


def test_the_ritual_session_can_write_the_manifest_but_not_the_wrapper_record_camp_keeps_beside_its_workspace(
    tmp_path,
):
    env, ws = _ritual_ready(tmp_path)
    sidecar = ws.parent / f"{ws.name}.task-wrapper.json"
    grants = _ritual_resolved(env, ws).writable

    def granted(path):
        return any(g == path or g in path.parents for g in map(Path, grants))

    assert granted(ws / "manifest.json")
    assert not granted(sidecar)


def test_another_hub_sharing_the_accounts_own_launch_account_does_not_deny_it(tmp_path):
    env, ws = _ritual_ready(tmp_path, launch="~/acct-a")
    _other_account(env, "b", launch="~/acct-a")
    _other_account(env, "c", launch="~/acct-c")
    denied = _settings_of(_ritual_start(env, ws))["sandbox"]["filesystem"]["denyRead"]
    home = Path(env["HOME"]).resolve()
    assert str(home / "acct-c") in denied and str(home / "acct-a") not in denied


def _ritual_on_named_account(tmp_path):
    """A ritual whose own launch account is a named directory (not the default ~/.claude)."""
    env = _fence(tmp_path)
    home = Path(env["HOME"]).resolve()
    own = home / "acct-a"
    _configure_hub(env, str(own))
    ws = _make_workspace(env)
    _, lore, cache = _ritual_roots(env, ws)
    _write_user_settings(
        env, _user_sandbox(allowWrite=[".", cache, lore], allowedDomains=["api.anthropic.com"]),
        account_dir=own,
    )
    return env, ws, own


def test_ritual_writes_deny_another_accounts_vaults_and_mirror_but_not_its_own(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _other_account(env)
    _group_toml(env, "mine", vaults=["avault", "common"], shared=[("ashared", "/srv/ashared")])
    _group_toml(
        env, "theirs", account="~/acct-g", vaults=["bvault", "common"], shared=[("bshared", "/srv/bshared")]
    )
    settings = _settings_of(_ritual_start(env, ws))
    deny_write = settings["sandbox"]["filesystem"]["denyWrite"]
    rules = settings["permissions"]["deny"]
    lore = Path(env["LORE_STATE_DIR"]).resolve()
    mirror = Path(env["RANGER_CACHE_DIR"]).resolve() / "mirrors" / "b"
    for denied in (lore / "vaults" / "bvault", Path("/srv/bshared"), mirror):
        assert str(denied) in deny_write
        assert f"Edit(/{denied}/**)" in rules
    for own in (lore / "vaults" / "avault", lore / "vaults" / "common", Path("/srv/ashared")):
        assert str(own) not in deny_write
        assert f"Edit(/{own}/**)" not in rules


def test_a_ritual_on_a_named_account_denies_reading_the_default_accounts_config_dir(tmp_path):
    env, ws, own = _ritual_on_named_account(tmp_path)
    start = _ritual_start(env, ws, account=str(own))
    default = Path(env["HOME"]).resolve() / ".claude"
    settings = _settings_of(start)
    assert str(default) in settings["sandbox"]["filesystem"]["denyRead"]
    assert f"Read(/{default}/**)" in settings["permissions"]["deny"]
    assert str(default) in start.setup_sandbox["denied_reads"]
    assert str(own) not in settings["sandbox"]["filesystem"]["denyRead"]


def test_a_ritual_on_the_default_account_does_not_deny_its_own_config_dir(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    start = _ritual_start(env, ws)
    default = Path(env["HOME"]).resolve() / ".claude"
    settings = _settings_of(start)
    assert str(default) not in settings["sandbox"]["filesystem"]["denyRead"]
    assert f"Read(/{default}/**)" not in settings["permissions"]["deny"]
    assert str(default) not in start.setup_sandbox["denied_reads"]


def test_a_ritual_whose_launch_account_is_the_default_config_dir_named_explicitly_keeps_it_readable(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    default = Path(env["HOME"]).resolve() / ".claude"
    start = _ritual_start(env, ws, account=str(default))
    assert str(default) not in _settings_of(start)["sandbox"]["filesystem"]["denyRead"]


def test_a_hub_on_a_named_account_does_not_deny_the_default_config_dir(tmp_path):
    env = _fence(tmp_path)
    own = Path(env["HOME"]).resolve() / "acct-a"
    _give_user_a_write_list(env, str(own))
    own.mkdir(parents=True, exist_ok=True)
    _give_user_a_write_list(env, str(own))
    start = _start(env, account=str(own))
    default = Path(env["HOME"]).resolve() / ".claude"
    assert str(default) not in _settings_of(start)["sandbox"]["filesystem"]["denyRead"]


# workspace validation


def test_a_workspace_outside_camps_worktrees_root_is_refused(tmp_path):
    env, _ = _ritual_ready(tmp_path)
    outside = tmp_path / "elsewhere" / "grp" / "worktrees" / "ws"
    outside.mkdir(parents=True)
    (outside / "manifest.json").write_text("{}")
    with pytest.raises(WorkspaceError, match=r"outside camp's worktrees root"):
        _ritual_resolved(env, outside)


def test_a_workspace_reached_through_a_symlink_pointing_outside_is_refused(tmp_path):
    env, _ = _ritual_ready(tmp_path)
    real = tmp_path / "escape"
    real.mkdir()
    (real / "manifest.json").write_text("{}")
    link = _camp_root(env) / "grp" / "worktrees" / "sneaky"
    link.symlink_to(real)
    with pytest.raises(WorkspaceError, match=r"outside camp's worktrees root"):
        _ritual_resolved(env, link)


def test_a_symlink_inside_the_root_resolves_to_the_workspace_it_points_at(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    link = _camp_root(env) / "grp" / "worktrees" / "alias"
    link.symlink_to(ws)
    assert _ritual_resolved(env, link).paths["workspace"] == ws


def test_the_workspace_containing_a_member_folder_is_the_workspace_not_the_member(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    member = ws / "trailhead" / "sub"
    member.mkdir(parents=True)
    assert workspace_containing(member, env=env, platform="linux") == ws


def test_the_workspace_containing_a_folder_reached_through_a_symlink_is_where_the_link_leads(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    other = _make_workspace(env, slug="ws2")
    link = ws / "link"
    link.symlink_to(other)
    assert workspace_containing(link, env=env, platform="linux") == other


@pytest.mark.parametrize("where", ["outside-root", "root", "group", "no-manifest"])
def test_a_folder_that_is_in_no_workspace_has_none(tmp_path, where):
    env, ws = _ritual_ready(tmp_path)
    folder = {
        "outside-root": tmp_path / "elsewhere",
        "root": _camp_root(env),
        "group": _camp_root(env) / "grp",
        "no-manifest": _make_workspace(env, slug="bare", manifest=False),
    }[where]
    folder.mkdir(parents=True, exist_ok=True)
    with pytest.raises(WorkspaceError):
        workspace_containing(folder, env=env, platform="linux")


def test_a_workspace_with_no_manifest_is_refused(tmp_path):
    env, _ = _ritual_ready(tmp_path)
    bare = _make_workspace(env, slug="bare", manifest=False)
    with pytest.raises(WorkspaceError, match=r"no camp manifest"):
        _ritual_resolved(env, bare)


@pytest.mark.parametrize("sub", ["", "ws1/trailhead", "../worktrees"])
def test_a_path_that_is_not_a_workspace_directory_under_the_root_is_refused(tmp_path, sub):
    env, ws = _ritual_ready(tmp_path)
    (ws / "trailhead").mkdir()
    target = (ws.parent / sub) if sub else _camp_root(env)
    with pytest.raises(WorkspaceError, match=r"is not a camp workspace"):
        _ritual_resolved(env, target)


def test_a_directory_with_a_manifest_that_is_not_under_a_worktrees_directory_is_refused(tmp_path):
    env, _ = _ritual_ready(tmp_path)
    stray = _camp_root(env) / "grp" / "elsewhere" / "ws1"
    stray.mkdir(parents=True)
    (stray / "manifest.json").write_text("{}")
    with pytest.raises(WorkspaceError, match=r"is not a camp workspace"):
        _ritual_resolved(env, stray)


def test_a_missing_workspace_is_refused_naming_the_workspace(tmp_path):
    env, _ = _ritual_ready(tmp_path)
    missing = _camp_root(env) / "grp" / "worktrees" / "gone"
    with pytest.raises(WorkspaceError, match=r"workspace .*gone is not an existing directory"):
        _ritual_resolved(env, missing)


def test_a_symlink_loop_is_refused_cleanly_rather_than_raising(tmp_path):
    env, _ = _ritual_ready(tmp_path)
    loop = _camp_root(env) / "grp" / "worktrees" / "loop"
    loop.symlink_to(loop)
    with pytest.raises(WorkspaceError):
        _ritual_resolved(env, loop)


def test_a_ritual_profile_resolved_without_a_workspace_is_refused(tmp_path):
    env, _ = _ritual_ready(tmp_path)
    with pytest.raises(WorkspaceError, match=r"needs a workspace"):
        resolve_profile(RITUAL_PROFILE, "a", env=env, platform="linux")


def test_a_hub_profile_given_a_workspace_is_refused(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    with pytest.raises(WorkspaceError, match=r"takes no workspace"):
        resolve_profile(HUB_PROFILE, "a", env=env, platform="linux", workspace=str(ws))


def test_the_harness_names_the_workspace_when_its_directory_has_gone(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    resolved = _ritual_resolved(env, ws)
    gone = _camp_root(env) / "grp" / "worktrees" / "vanished"
    with pytest.raises(HarnessError, match=r"workspace .*vanished is not an existing directory") as info:
        ClaudeCodeHarness().session_start(
            resolved, gone, None, interactive=False, account=None, env=env, platform="linux"
        )
    assert "ranger setup" not in str(info.value)


# never-widen


def test_a_user_write_list_that_omits_the_cache_refuses_the_ritual_naming_it(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _, lore, cache = _ritual_roots(env, ws)
    _write_user_settings(env, _user_sandbox(allowWrite=[".", lore]))
    with pytest.raises(HarnessError, match=re.escape(cache)):
        _ritual_start(env, ws)


def test_a_user_read_denial_over_the_workspace_refuses_the_ritual(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _ritual_user(env, ws, permissions={"deny": [f"Read(/{ws}/**)"]})
    with pytest.raises(HarnessError, match=r"read denial"):
        _ritual_start(env, ws)


def test_a_user_read_denial_over_the_plugin_tree_refuses_the_ritual(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    composed = Path(env["TRAILHEAD_STATE_DIR"]).resolve() / "composed"
    _ritual_user(env, ws, permissions={"deny": [f"Read(/{composed}/**)"]})
    with pytest.raises(HarnessError, match=r"read denial"):
        _ritual_start(env, ws)


# setup_sandbox


def _sandbox_of(env, ws, profile=RITUAL_PROFILE, **kw):
    return _ritual_start(env, ws, profile=profile, **kw).setup_sandbox


def test_setup_sandbox_carries_every_user_level_read_denial_and_the_profiles_own(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    home = Path(env["HOME"]).resolve()
    _ritual_user(
        env, ws,
        permissions={
            "deny": ["Read(~/.aws/**)", "Read(~/.ssh/**)", "Read(//etc/secret/**)", "Edit(//ignored/**)"]
        },
    )
    data = json.loads((home / ".claude" / "settings.json").read_text())
    data["sandbox"]["filesystem"]["denyRead"] = ["~/.ssh"]
    (home / ".claude" / "settings.json").write_text(json.dumps(data))
    _write_managed(tmp_path, {"sandbox": {"filesystem": {"denyRead": ["/managed/creds"]}}})
    sandbox = _sandbox_of(env, ws)
    reads = sandbox["denied_reads"]
    for expected in (home / ".ssh", home / ".aws", Path("/etc/secret"), Path("/managed/creds"),
                     Path(env["OUTPOST_STATE_DIR"]).resolve()):
        assert str(expected) in reads
    assert "/ignored" not in reads
    assert len(reads) == len(set(reads))


def _user_network(env, ws, **network):
    _ritual_user(env, ws, sandbox={
        "enabled": True,
        "filesystem": {"allowWrite": [".", *_ritual_roots(env, ws)[1:]]},
        "network": network,
    })


def test_setup_sandbox_destinations_are_the_users_own_network_rules(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _user_network(env, ws, allowedDomains=["a.example", "b.example"], deniedDomains=["c.example"])
    start = _ritual_start(env, ws)
    assert start.setup_sandbox["allowed_domains"] == ["a.example", "b.example"]
    assert start.setup_sandbox["denied_domains"] == ["c.example"]
    network = _settings_of(start)["sandbox"].get("network", {})
    assert "allowedDomains" not in network


def test_setup_sandbox_allowed_domains_exclude_what_the_user_also_denies(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _user_network(env, ws, allowedDomains=["a.example", "b.example"], deniedDomains=["b.example"])
    sandbox = _sandbox_of(env, ws)
    assert sandbox["allowed_domains"] == ["a.example"]
    assert sandbox["denied_domains"] == ["b.example"]


def test_setup_sandbox_allowed_domains_are_empty_with_no_user_allow_list(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _user_network(env, ws, deniedDomains=["c.example"])
    sandbox = _sandbox_of(env, ws)
    assert sandbox["allowed_domains"] == []
    assert sandbox["denied_domains"] == ["c.example"]


def test_with_no_user_network_list_a_profiles_hosts_are_not_setup_destinations(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _, lore, cache = _ritual_roots(env, ws)
    _write_user_settings(env, _user_sandbox(allowWrite=[".", cache, lore]))
    import dataclasses
    sandbox = _sandbox_of(env, ws, dataclasses.replace(RITUAL_PROFILE, network_allowed_hosts=("github.com",)))
    assert sandbox["allowed_domains"] == [] and sandbox["denied_domains"] == []


# the plugin tree


def test_the_ritual_can_read_the_composed_plugin_tree_and_never_write_it(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    start = _ritual_start(env, ws)
    composed = Path(env["TRAILHEAD_STATE_DIR"]).resolve() / "composed" / "claude_code"
    argv = start.argv
    granted = [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]
    assert granted == [str(composed)]
    settings = _settings_of(start)
    assert str(composed) not in settings["sandbox"]["filesystem"]["allowWrite"]
    assert str(composed) in settings["sandbox"]["filesystem"]["denyWrite"]
    assert f"Edit(/{composed}/**)" in settings["permissions"]["deny"]
    assert str(composed) not in settings["sandbox"]["filesystem"]["denyRead"]


def test_the_plugin_tree_grant_follows_the_trailhead_state_dir(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    moved = {**env, "TRAILHEAD_STATE_DIR": str(tmp_path.resolve() / "elsewhere")}
    argv = _ritual_start(moved, ws).argv
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path.resolve() / "elsewhere" / "composed" / "claude_code")


# the command


def test_cli_prints_the_ritual_launch_shape(tmp_path, monkeypatch, capsys):
    env, ws = _ritual_ready(tmp_path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("/craft:slice spec/x --unattended")
    rc = _ritual_cli(monkeypatch, env, ws, "--headless", "--prompt-file", str(prompt))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert set(out) == {"argv", "env_set", "env_unset", "directory", "files", "setup_sandbox"}
    assert out["directory"] == str(ws)
    assert out["argv"][-1] == "/craft:slice spec/x --unattended"


def test_cli_starts_the_ritual_in_the_real_path_of_a_workspace_named_through_a_symlink(
    tmp_path, monkeypatch, capsys
):
    env, ws = _ritual_ready(tmp_path)
    alias = _camp_root(env) / "grp" / "worktrees" / "alias"
    alias.symlink_to(ws)
    rc = _ritual_cli(monkeypatch, env, alias)
    assert rc == 0 and json.loads(capsys.readouterr().out)["directory"] == str(ws)


def test_cli_binds_the_ritual_to_the_accounts_launch_account(tmp_path, monkeypatch, capsys):
    env, ws = _ritual_ready(tmp_path, launch="~/acct-a")
    _, lore, cache = _ritual_roots(env, ws)
    _write_user_settings(
        env, _user_sandbox(allowWrite=[".", cache, lore]), account_dir=Path(env["HOME"]) / "acct-a"
    )
    rc = _ritual_cli(monkeypatch, env, ws, "--headless")
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["env_set"]["CLAUDE_CONFIG_DIR"] == str(Path(env["HOME"]) / "acct-a")


def test_cli_refuses_a_broker_session_for_the_ritual(tmp_path, monkeypatch, capsys):
    env, ws = _ritual_ready(tmp_path)
    rc = _ritual_cli(monkeypatch, env, ws, "--broker-session", "s1")
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and "ritual" in captured.err and "--broker-session" in captured.err


def test_cli_refuses_a_ritual_without_a_workspace(tmp_path, monkeypatch, capsys):
    env, _ = _ritual_ready(tmp_path)
    rc = _run_cli(monkeypatch, env, ["session-start", "--role", RITUAL_ROLE, "--account", "a", "--json"])
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == "" and "--workspace" in captured.err


def test_cli_refuses_a_workspace_for_the_hub(tmp_path, monkeypatch, capsys):
    env, ws = _ritual_ready(tmp_path)
    rc = _run_cli(monkeypatch, env, [*_HUB_ARGV, "--workspace", str(ws)])
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == "" and "--workspace" in captured.err


def test_cli_refuses_a_workspace_outside_the_root_cleanly(tmp_path, monkeypatch, capsys):
    env, _ = _ritual_ready(tmp_path)
    rc = _ritual_cli(monkeypatch, env, tmp_path)
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and "outside camp's worktrees root" in captured.err


def test_cli_refuses_a_missing_workspace_with_the_workspace_wording(tmp_path, monkeypatch, capsys):
    env, _ = _ritual_ready(tmp_path)
    missing = _camp_root(env) / "grp" / "worktrees" / "gone"
    rc = _ritual_cli(monkeypatch, env, missing)
    err = capsys.readouterr().err
    assert rc != 0 and "gone is not an existing directory" in err and "ranger setup" not in err


def test_cli_refuses_a_widening_ritual_with_a_clean_message(tmp_path, monkeypatch, capsys):
    env, ws = _ritual_ready(tmp_path)
    _, lore, _ = _ritual_roots(env, ws)
    _write_user_settings(env, _user_sandbox(allowWrite=[".", lore]))
    rc = _ritual_cli(monkeypatch, env, ws)
    err = capsys.readouterr().err
    assert rc != 0 and err.startswith("trailhead: ") and "never widen" in err


def test_the_ritual_launch_needs_a_configured_hub_for_its_account(tmp_path, monkeypatch, capsys):
    env = _fence(tmp_path)
    ws = _make_workspace(env)
    Path(env["HOME"], ".claude").mkdir(parents=True)
    rc = _ritual_cli(monkeypatch, env, ws)
    assert rc != 0 and "a.json" in capsys.readouterr().err


# the platform's case rules for read denials (the Read-rule matcher)


def _upper_rule(path: str) -> str:
    return f"Read(/{path.upper()}/**)"


def test_a_mixed_case_read_rule_covering_a_root_refuses_on_darwin(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _permissive_user(env, permissions={"deny": [_upper_rule(cache)]})
    with pytest.raises(HarnessError, match="read"):
        _start(env, platform="darwin")


def test_a_mixed_case_read_rule_is_case_exact_on_linux(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _permissive_user(env, permissions={"deny": [_upper_rule(cache)]})
    assert _start(env, platform="linux") is not None


def test_a_differently_normalized_read_rule_refuses_on_darwin_only(tmp_path):
    env = _fence(tmp_path)
    env["XDG_CACHE_HOME"] = str(tmp_path.resolve() / "caf\u00e9")
    home, lore, cache = _hub_roots(env)
    decomposed = cache.replace("caf\u00e9", "cafe\u0301")
    assert decomposed != cache
    _permissive_user(env, permissions={"deny": [f"Read(/{decomposed}/**)"]})
    with pytest.raises(HarnessError, match="read"):
        _start(env, platform="darwin")
    assert _start(env, platform="linux") is not None


def test_a_mixed_case_read_rule_in_a_managed_file_refuses_on_darwin(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _permissive_user(env)
    _write_managed(tmp_path, {"permissions": {"deny": [_upper_rule(cache)]}}, fence="managed-fence-darwin")
    with pytest.raises(HarnessError, match="read"):
        _start(env, platform="darwin")


def test_a_mixed_case_read_denial_entry_covering_a_root_refuses_on_darwin_only(tmp_path):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _write_user_settings(
        env, _user_sandbox(allowWrite=[".", cache, lore], denyRead=[cache.upper()])
    )
    with pytest.raises(HarnessError, match="read denial"):
        _start(env, platform="darwin")
    assert _start(env, platform="linux") is not None



# -- a ritual cannot write what the operator's own tools later run or read -----


def _add_members(ws: Path, names) -> list[Path]:
    members = []
    for name in names:
        member = ws / name
        (member / "src").mkdir(parents=True)
        (member / "src" / "app.py").write_text("x = 1\n")
        (member / ".git").write_text(f"gitdir: {ws.parent / 'repo.git' / 'worktrees' / name}\n")
        members.append(member)
    return members


def _ritual_denials(env, ws):
    settings = _settings_of(_ritual_start(env, ws))
    return settings["sandbox"]["filesystem"]["denyWrite"], settings["permissions"]["deny"]


@pytest.mark.parametrize("names", [(), ("m1",), ("m1", "m2"), ("alpha", "beta", "gamma")])
def test_ritual_denies_writing_the_manifest_and_every_members_git_pointer_and_config(tmp_path, names):
    env, ws = _ritual_ready(tmp_path)
    members = _add_members(ws, names)
    (ws / "notes").mkdir()
    deny_write, rules = _ritual_denials(env, ws)
    guarded = [ws / "manifest.json"]
    for member in members:
        guarded += [member / ".git", member / ".claude", member / ".mcp.json"]
    for path in guarded:
        assert str(path) in deny_write, path
        assert f"Edit(/{path})" in rules and f"Edit(/{path}/**)" in rules, path
    for member in members:
        for ordinary in (member / "src" / "app.py", member / "README.md", member / "src"):
            assert not _covered_by(deny_write, ordinary), ordinary
    assert not _covered_by(deny_write, ws / "notes" / "x.md")
    assert str(ws / "notes" / ".git") not in deny_write


def test_ritual_guards_a_member_present_when_the_session_starts_not_one_named_in_the_manifest(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    (ws / "manifest.json").write_text(json.dumps({"members": [{"name": "ghost"}]}))
    (member,) = _add_members(ws, ["real"])
    deny_write, _ = _ritual_denials(env, ws)
    assert str(member / ".git") in deny_write
    assert str(ws / "ghost" / ".git") not in deny_write


# the credential folders a session can read and send to an allowed domain
_CREDENTIAL_DIRS = (
    ".ssh", ".aws", ".gnupg", ".config/gh", ".netrc", ".git-credentials", ".kube",
    ".config/gcloud", ".azure", ".docker",
)


def test_ritual_and_setup_sandbox_deny_reading_the_credential_folders_of_the_accounts_home(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    home = Path(env["HOME"]).resolve()
    start = _ritual_start(env, ws)
    settings = _settings_of(start)
    deny_read = settings["sandbox"]["filesystem"]["denyRead"]
    rules = settings["permissions"]["deny"]
    for rel in _CREDENTIAL_DIRS:
        path = home / rel
        assert str(path) in deny_read, rel
        assert f"Read(/{path}/**)" in rules, rel
        assert str(path) in start.setup_sandbox["denied_reads"], rel
    assert str(home / ".npmrc") not in deny_read
    assert not _covered_by(deny_read, home / ".npmrc")


def test_credential_denials_follow_the_accounts_home(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    other = tmp_path.resolve() / "elsewhere" / "operator"
    env["HOME"] = str(other)
    (other / ".claude").mkdir(parents=True)
    _ritual_user(env, ws)
    reads = _sandbox_of(env, ws)["denied_reads"]
    for rel in _CREDENTIAL_DIRS:
        assert str(other / rel) in reads, rel


def test_setup_sandbox_only_still_equals_the_full_ritual_sections_with_the_credential_denials(
    tmp_path, monkeypatch, capsys
):
    env, ws = _ritual_ready(tmp_path)
    assert _ritual_cli(monkeypatch, env, ws) == 0
    full = json.loads(capsys.readouterr().out)["setup_sandbox"]
    assert _setup_only_cli(monkeypatch, env) == 0
    only = json.loads(capsys.readouterr().out)["setup_sandbox"]
    assert only == full
    assert str(Path(env["HOME"]).resolve() / ".ssh") in only["denied_reads"]



def _launched_deny_sets(env, ws):
    settings = _settings_of(_ritual_start(env, ws))
    return settings["sandbox"]["filesystem"]["denyRead"], settings["permissions"].get("deny", [])


@pytest.mark.parametrize(
    "rel",
    [
        "operator/credential",
        "registration/credential",
        "sessions/ws-4242-99/gateway-client.json",
        "db/state.db",
        "db/late.db-wal",
    ],
)
def test_an_outpost_launched_sessions_settings_deny_the_credentials_and_the_database_to_the_sandbox(
    tmp_path, rel
):  # AC83
    env, ws = _ritual_ready(tmp_path)
    denied, _ = _launched_deny_sets(env, ws)
    assert _covered_by(denied, Path(env["OUTPOST_STATE_DIR"]).resolve() / rel)


def test_an_outpost_launched_sessions_settings_do_not_deny_what_it_still_has_to_read(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    denied, _ = _launched_deny_sets(env, ws)
    assert not _covered_by(denied, ws / "read-control.txt")


def test_an_outpost_launched_sessions_settings_deny_the_whole_state_folder_to_the_read_tool_too(tmp_path):  # AC83
    env, ws = _ritual_ready(tmp_path)
    _, rules = _launched_deny_sets(env, ws)
    assert f"Read(/{Path(env['OUTPOST_STATE_DIR']).resolve()}/**)" in rules


# -- a ritual writes records only in its spec's vault (F29) -------------------


def _vault_root_rules(env, vaults):
    lore = Path(env["LORE_STATE_DIR"]).resolve() / "vaults"
    return [lore / v for v in vaults]


@pytest.mark.parametrize(
    "named, denied",
    [("a", ["b", "c"]), ("b", ["a", "c"]), ("c", ["a", "b"])],
)
def test_ritual_start_write_denies_every_vault_but_the_named_one(tmp_path, named, denied):
    env, ws = _ritual_ready(tmp_path)
    _lore_vault_dirs(env, "a", "b", "c")
    settings = _settings_of(_ritual_start(env, ws, vault=named))
    deny_write = settings["sandbox"]["filesystem"]["denyWrite"]
    rules = settings["permissions"]["deny"]
    for vault in _vault_root_rules(env, denied):
        assert str(vault) in deny_write
        assert f"Edit(/{vault})" in rules and f"Edit(/{vault}/**)" in rules
    kept = _vault_root_rules(env, [named])[0]
    assert str(kept) not in deny_write
    assert f"Edit(/{kept}/**)" not in rules
    lore = Path(env["LORE_STATE_DIR"]).resolve()
    assert str(lore) in settings["sandbox"]["filesystem"]["allowWrite"]
    assert str(lore) not in deny_write and str(lore / "vaults") not in deny_write


def test_ritual_start_denies_the_default_vault_when_another_is_named(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _lore_vault_dirs(env, "default", "spec-vault")
    deny_write = _settings_of(_ritual_start(env, ws, vault="spec-vault"))["sandbox"]["filesystem"]["denyWrite"]
    assert str(Path(env["LORE_STATE_DIR"]).resolve() / "vaults" / "default") in deny_write


def test_ritual_start_does_not_deny_a_symlinked_vault_entry_that_resolves_to_the_named_vault(tmp_path):
    env, ws = _ritual_ready(tmp_path)
    _lore_vault_dirs(env, "a", "b")
    vaults = Path(env["LORE_STATE_DIR"]).resolve() / "vaults"
    (vaults / "alias").symlink_to(vaults / "a")
    deny_write = _settings_of(_ritual_start(env, ws, vault="a"))["sandbox"]["filesystem"]["denyWrite"]
    assert str(vaults / "a") not in deny_write
    assert str(vaults / "b") in deny_write


def test_a_second_path_to_the_named_vault_does_not_get_the_named_vault_denied(tmp_path):
    from trailhead.launch_profile import _unnamed_vaults

    env, _ = _ritual_ready(tmp_path)
    _lore_vault_dirs(env, "a", "b")
    state = Path(env["LORE_STATE_DIR"]).resolve()
    (tmp_path / "alias").symlink_to(state)
    named_by_alias = tmp_path / "alias" / "vaults" / "a"
    assert not os.path.samefile(named_by_alias, state / "vaults" / "b")
    assert str(named_by_alias.resolve()) == str(state / "vaults" / "a")
    denied = _unnamed_vaults(named_by_alias, env, "linux")
    assert [p.name for p in denied] == ["b", RITUAL_VAULT]


def _hostile_vault_values(env):
    vaults = Path(env["LORE_STATE_DIR"]).resolve() / "vaults"
    outside = Path(env["LORE_STATE_DIR"]).resolve().parent / "outside-vault"
    outside.mkdir()
    (vaults / "escape").symlink_to(outside)
    (vaults / "alias-of-b").symlink_to(vaults / "b")
    (vaults / "-odd").mkdir()
    (vaults / "nest" / "deep").mkdir(parents=True)
    return ["missing", "escape", "alias-of-b", "..", "-odd", "", "nest/deep", "work/../b"]


@pytest.mark.parametrize("row", range(8))
def test_cli_refuses_a_hostile_ritual_vault_and_writes_no_session_files(tmp_path, monkeypatch, capsys, row):
    env, ws = _ritual_ready(tmp_path)
    _lore_vault_dirs(env, "b")
    value = _hostile_vault_values(env)[row]
    rc = _run_cli(
        monkeypatch, env,
        ["session-start", "--role", RITUAL_ROLE, "--account", "a", "--workspace", str(ws), "--json",
         "--session=rit-1", f"{VAULT_FLAG}={value}"],
    )
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert captured.err.startswith("trailhead: ") and VAULT_FLAG in captured.err
    assert not (Path(env["OUTPOST_STATE_DIR"]) / "sessions" / "rit-1").exists()


def test_cli_refuses_a_ritual_without_a_vault_and_names_the_fix(tmp_path, monkeypatch, capsys):
    env, ws = _ritual_ready(tmp_path)
    rc = _ritual_cli(monkeypatch, env, ws, vault=None)
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == ""
    assert VAULT_FLAG in captured.err and "ritual" in captured.err


def test_cli_ritual_start_with_a_vault_names_the_denied_vaults_in_its_settings(tmp_path, monkeypatch, capsys):
    env, ws = _ritual_ready(tmp_path)
    _lore_vault_dirs(env, "a", "b")
    assert _ritual_cli(monkeypatch, env, ws, vault="a") == 0
    out = json.loads(capsys.readouterr().out)
    settings = _launched_settings(out)
    vaults = Path(env["LORE_STATE_DIR"]).resolve() / "vaults"
    assert str(vaults / "b") in settings["sandbox"]["filesystem"]["denyWrite"]
    assert str(vaults / "a") not in settings["sandbox"]["filesystem"]["denyWrite"]


def test_cli_refuses_a_vault_for_the_hub(tmp_path, monkeypatch, capsys):
    env, _ = _ritual_ready(tmp_path)
    rc = _run_cli(monkeypatch, env, [*_HUB_ARGV, VAULT_FLAG, "a"])
    captured = capsys.readouterr()
    assert rc != 0 and captured.out == "" and VAULT_FLAG in captured.err


def test_the_hub_start_denies_no_vault_directory(tmp_path):
    env = _fence(tmp_path)
    _lore_vault_dirs(env, "a", "b")
    lore = Path(env["LORE_STATE_DIR"]).resolve()
    deny_write = _settings_of(_start(env))["sandbox"]["filesystem"]["denyWrite"]
    for vault in ("a", "b"):
        assert str(lore / "vaults" / vault) not in deny_write


def _srt_run(settings_path: Path, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["srt", "-s", str(settings_path), "-c", command], capture_output=True, text=True, timeout=60
    )


_PROBE_SETTINGS = {
    "network": {"allowedDomains": [], "deniedDomains": []},
    "filesystem": {"denyRead": [], "allowWrite": [], "denyWrite": []},
}


def _scrubbed_tail(stderr: str, home: str) -> str:
    text = stderr.replace(home, "~") if home else stderr
    return " ".join(text.split())[:300]


def _sandbox_start_decision(probe, rendered, home=None):
    """Decide skip / fail / run from two `(returncode, stderr)` start results.

    `probe` ran a trivial command under a minimal known-good settings file, so a
    failure means this host cannot start the sandbox at all (skip). `rendered` ran
    the same trivial command under the ritual's rendered settings: once the probe
    passed, a failure there is the rendered settings' fault (fail).
    """
    home = os.environ.get("HOME", "") if home is None else home
    if probe[0] != 0:
        return "skip", f"the sandbox cannot start on this host (rc={probe[0]}): {_scrubbed_tail(probe[1], home)}"
    if rendered[0] != 0:
        return "fail", (
            f"the sandbox failed to start with the ritual's rendered settings (rc={rendered[0]}): "
            f"{_scrubbed_tail(rendered[1], home)}"
        )
    return "run", ""


def test_a_failed_probe_skips_and_the_reason_carries_its_rc_and_stderr():
    action, reason = _sandbox_start_decision((1, "bwrap: setting up uid map: Permission denied"), (1, "x"))
    assert action == "skip"
    assert "rc=1" in reason and "Permission denied" in reason


def test_a_failed_probe_skips_even_when_the_rendered_settings_start():
    assert _sandbox_start_decision((2, "boom"), (0, ""))[0] == "skip"


def test_the_probe_reason_truncates_long_stderr():
    _, reason = _sandbox_start_decision((1, "e" * 5000), (1, ""))
    assert len(reason) < 600


def test_the_probe_reason_scrubs_the_home_directory():
    _, reason = _sandbox_start_decision((1, "cannot open /home/someone/secret"), (1, ""), home="/home/someone")
    assert "/home/someone" not in reason and "~/secret" in reason


def test_a_passing_probe_with_rendered_settings_that_fail_to_start_fails():
    action, reason = _sandbox_start_decision((0, ""), (1, "invalid settings: denyWrite"))
    assert action == "fail"
    assert "rc=1" in reason and "invalid settings" in reason


def test_a_passing_probe_and_a_starting_render_runs():
    assert _sandbox_start_decision((0, ""), (0, ""))[0] == "run"


@pytest.mark.seam_real
def test_real_sandbox_refuses_a_write_into_a_vault_the_ritual_did_not_name(tmp_path):
    if shutil.which("srt") is None:
        pytest.skip("srt (the sandbox runtime) is not installed")
    env, ws = _ritual_ready(tmp_path)
    _lore_vault_dirs(env, "a", "b")
    fs = _settings_of(_ritual_start(env, ws, vault="a"))["sandbox"]["filesystem"]
    srt_settings = tmp_path / "srt.json"
    srt_settings.write_text(json.dumps({
        "network": {"allowedDomains": [], "deniedDomains": []},
        "filesystem": {"denyRead": [], "allowWrite": fs["allowWrite"], "denyWrite": fs["denyWrite"]},
    }))
    probe_settings = tmp_path / "srt-probe.json"
    probe_settings.write_text(json.dumps(_PROBE_SETTINGS))
    probe = _srt_run(probe_settings, "true")
    rendered = _srt_run(srt_settings, "true")
    action, reason = _sandbox_start_decision((probe.returncode, probe.stderr), (rendered.returncode, rendered.stderr))
    if action == "skip":
        pytest.skip(reason)
    assert action == "run", reason
    vaults = Path(env["LORE_STATE_DIR"]).resolve() / "vaults"
    allowed = _srt_run(srt_settings, f"touch {vaults / 'a' / '.lore.lock'}")
    denied = _srt_run(srt_settings, f"touch {vaults / 'b' / '.lore.lock'}")
    assert allowed.returncode == 0, allowed.stderr
    assert (vaults / "a" / ".lore.lock").exists()
    assert denied.returncode != 0
    assert not (vaults / "b" / ".lore.lock").exists()


def _launched_settings(out: dict) -> dict:
    """The settings a launch hands Claude Code: the session file ``--settings`` names."""
    path = out["argv"][out["argv"].index("--settings") + 1]
    (entry,) = [f for f in out["files"] if f["path"] == path]
    assert entry["mode"] == "0600"
    return json.loads(entry["content"])


@pytest.mark.parametrize("denied_vaults", [2, 200])
def test_a_ritual_launch_command_stays_the_same_size_however_many_vaults_it_denies(
    tmp_path, monkeypatch, capsys, denied_vaults
):
    # tmux refuses a new-session command over 16 KB, and the deny lists grow with the host.
    env, ws = _ritual_ready(tmp_path)
    _lore_vault_dirs(env, *(f"other-{i:03d}" for i in range(denied_vaults)))
    assert _ritual_cli(monkeypatch, env, ws) == 0
    out = json.loads(capsys.readouterr().out)
    vaults = Path(env["LORE_STATE_DIR"]).resolve() / "vaults"
    settings = _launched_settings(out)
    assert str(vaults / f"other-{denied_vaults - 1:03d}") in settings["sandbox"]["filesystem"]["denyWrite"]
    session_dir = Path(env["OUTPOST_STATE_DIR"]).resolve() / "sessions" / "rit-1"
    assert Path(out["argv"][out["argv"].index("--settings") + 1]).parent == session_dir
    assert sum(len(a) + 1 for a in out["argv"]) < 2048


# -- write guards also cover folders and vaults created after launch (F29) ----


def _glob_regex(pattern: str):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out)


def _covered_by_pattern(denied: list[str], path: Path) -> bool:
    """The guard tests' path matcher, extended to the globs a sandbox's denyWrite takes: an
    entry covers the path it matches and everything under it."""
    candidates = [str(path), *(str(p) for p in path.parents)]
    return any(
        (_glob_regex(d).fullmatch(c) if "*" in d else d == c) for d in denied for c in candidates
    )


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_a_ritual_launch_denies_git_and_claude_folders_and_vaults_created_after_it_resolved(tmp_path, platform):
    env, ws = _ritual_ready(tmp_path)
    (before,) = _add_members(ws, ["before"])
    resolved = _ritual_resolved(env, ws, platform=platform)
    lore = Path(env["LORE_STATE_DIR"]).resolve()
    after = ws / "after"
    (after / "sub" / ".git" / "hooks").mkdir(parents=True)
    (after / "sub" / ".claude").mkdir()
    (lore / "vaults" / "fresh" / ".git" / "hooks").mkdir(parents=True)
    (lore / "vaults" / "fresh" / ".claude").mkdir()
    start = ClaudeCodeHarness().session_start(
        resolved, ws, None, interactive=False, account=None, env=env, platform=platform,
        session_dir=Path(env["OUTPOST_STATE_DIR"]).resolve() / "sessions" / "rit-1",
    )
    deny_write = _settings_of(start)["sandbox"]["filesystem"]["denyWrite"]
    for planted in (
        after / "sub" / ".git" / "hooks" / "pre-commit",
        after / "sub" / ".git" / "config",
        after / "sub" / ".claude" / "settings.json",
        ws / "brand-new" / ".claude" / "settings.json",
        lore / "vaults" / "fresh" / ".git" / "hooks" / "pre-commit",
        lore / "vaults" / "fresh" / ".git" / "config",
        lore / "vaults" / "fresh" / ".claude" / "settings.json",
    ):
        assert _covered_by_pattern(deny_write, planted), planted
    cache = Path(resolved.paths[ROLE_CACHE])
    for cached in (cache / "clone" / ".git" / "hooks" / "pre-commit", cache / "clone" / ".claude" / "settings.json"):
        assert not _covered_by_pattern(deny_write, cached), cached
    assert str(before / ".git") in deny_write
    for ordinary in (
        after / "sub" / "app.py",
        after / "sub" / ".git" / "index",
        lore / "vaults" / "fresh" / ".git" / "index",
        lore / "vaults" / "fresh" / ".git" / "objects" / "ab" / "cd",
        lore / "vaults" / "work" / "record.md",
    ):
        assert not _covered_by_pattern(deny_write, ordinary), ordinary


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_a_hub_launch_denies_git_admin_and_claude_folders_created_after_it_resolved(tmp_path, platform):
    env = _fence(tmp_path)
    home, lore, cache = _hub_roots(env)
    _give_user_a_write_list(env)
    resolved = _resolved(env)
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(lore) / "vaults" / "fresh" / ".git" / "hooks").mkdir(parents=True)
    (Path(cache) / "tool" / ".claude").mkdir(parents=True)
    start = ClaudeCodeHarness().session_start(
        resolved, Path(home), None, interactive=True, account=None, env=env, platform=platform,
        session_dir=Path(env["OUTPOST_STATE_DIR"]).resolve() / "sessions" / "s1",
        broker=broker_launch("s1", env=env),
    )
    deny_write = _settings_of(start)["sandbox"]["filesystem"]["denyWrite"]
    for planted in (
        Path(lore) / "vaults" / "fresh" / ".git" / "hooks" / "pre-commit",
        Path(home) / "x" / "y" / "z" / ".git" / "config",
        Path(home) / "x" / ".claude" / "settings.json",
    ):
        assert _covered_by_pattern(deny_write, planted), planted
    for cached in (
        Path(cache) / "tool" / ".claude" / "settings.json",
        Path(cache) / "clone" / ".git" / "hooks" / "pre-commit",
        Path(cache) / "clone" / ".git" / "config",
    ):
        assert not _covered_by_pattern(deny_write, cached), cached
    assert not _covered_by_pattern(deny_write, Path(lore) / "vaults" / "fresh" / ".git" / "index")
