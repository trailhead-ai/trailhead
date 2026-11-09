"""Tests for trailhead/launch_profile.py — the harness-neutral hub launch profile
and its role path resolver. Every test injects env/platform with tmp roots, so
nothing reaches the live install.
"""

from pathlib import Path

import pytest

from trailhead.launch_profile import (
    HUB_PROFILE,
    InvalidAccountError,
    LaunchProfile,
    UnknownRoleError,
    hub_config_path,
    hub_home,
    mirrors_path,
    resolve_profile,
    validate_account,
)
from trailhead.paths import PathResolutionError


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg" / "config"),
        "XDG_STATE_HOME": str(tmp_path / "xdg" / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg" / "cache"),
    }
    env.update(overrides)
    return env


def test_hub_profile_resolves_every_role_under_the_injected_roots(tmp_path):
    env = _env(tmp_path)
    r = resolve_profile(HUB_PROFILE, "a", env=env, platform="linux")
    assert r.paths["hub-home"] == tmp_path / "xdg/state/ranger/hubs/a"
    assert r.paths["lore-state"] == tmp_path / "xdg/state/lore"
    assert r.paths["cache"] == tmp_path / "xdg/cache/ranger/mirrors/a"
    assert r.paths["token-store"] == tmp_path / "xdg/state/outpost/operator"
    assert all(p.is_absolute() for p in r.paths.values())


def test_hub_profile_writes_are_an_allow_list_of_roles_and_token_store_is_read_denied(tmp_path):
    r = resolve_profile(HUB_PROFILE, "a", env=_env(tmp_path), platform="linux")
    assert set(r.writable) == {r.paths["hub-home"], r.paths["lore-state"], r.paths["cache"]}
    assert r.read_denied == (r.paths["token-store"],)


def test_changing_one_override_moves_exactly_the_roles_it_governs(tmp_path):
    base = resolve_profile(HUB_PROFILE, "a", env=_env(tmp_path), platform="linux").paths
    moved_env = _env(tmp_path, RANGER_STATE_DIR=str(tmp_path / "elsewhere"))
    moved = resolve_profile(HUB_PROFILE, "a", env=moved_env, platform="linux").paths
    assert moved["hub-home"] == tmp_path / "elsewhere/hubs/a"
    for role in ("lore-state", "cache", "token-store"):
        assert moved[role] == base[role]

    lore_env = _env(tmp_path, LORE_STATE_DIR=str(tmp_path / "lorestate"))
    lore = resolve_profile(HUB_PROFILE, "a", env=lore_env, platform="linux").paths
    assert lore["lore-state"] == tmp_path / "lorestate"
    for role in ("hub-home", "cache", "token-store"):
        assert lore[role] == base[role]

    cache_env = _env(tmp_path, RANGER_CACHE_DIR=str(tmp_path / "c"))
    cache = resolve_profile(HUB_PROFILE, "a", env=cache_env, platform="linux").paths
    assert cache["cache"] == tmp_path / "c/mirrors/a"
    for role in ("hub-home", "lore-state", "token-store"):
        assert cache[role] == base[role]

    tok_env = _env(tmp_path, OUTPOST_STATE_DIR=str(tmp_path / "op"))
    tok = resolve_profile(HUB_PROFILE, "a", env=tok_env, platform="linux").paths
    assert tok["token-store"] == tmp_path / "op/operator"
    for role in ("hub-home", "lore-state", "cache"):
        assert tok[role] == base[role]


def test_darwin_and_linux_resolve_to_xdg_never_library(tmp_path):
    env = {"HOME": str(tmp_path / "home")}
    darwin = resolve_profile(HUB_PROFILE, "a", env=env, platform="darwin").paths
    linux = resolve_profile(HUB_PROFILE, "a", env=env, platform="linux").paths
    assert darwin == linux
    home = tmp_path / "home"
    assert darwin["hub-home"] == home / ".local/state/ranger/hubs/a"
    assert darwin["cache"] == home / ".cache/ranger/mirrors/a"
    assert all("Library" not in str(p) for p in darwin.values())


def test_two_accounts_resolve_to_disjoint_locations(tmp_path):
    env = _env(tmp_path)
    a = resolve_profile(HUB_PROFILE, "a", env=env, platform="linux").paths
    b = resolve_profile(HUB_PROFILE, "b", env=env, platform="linux").paths
    for role in ("hub-home", "cache"):
        assert a[role] != b[role]
        assert a[role] not in b[role].parents and b[role] not in a[role].parents
    assert hub_config_path("a", env=env, platform="linux") != hub_config_path(
        "b", env=env, platform="linux"
    )


def test_ranger_location_helpers(tmp_path):
    env = _env(tmp_path)
    assert hub_config_path("acme", env=env, platform="linux") == (
        tmp_path / "xdg/config/ranger/hubs/acme.json"
    )
    assert hub_home("acme", env=env, platform="linux") == tmp_path / "xdg/state/ranger/hubs/acme"
    assert mirrors_path("acme", env=env, platform="linux") == (
        tmp_path / "xdg/cache/ranger/mirrors/acme"
    )


@pytest.mark.parametrize("bad", ["", "..", "a/b", "-a", "A", "a_b", "a b", ".", "a\n", "a/../b"])
def test_invalid_account_refused_before_any_path_is_built(tmp_path, bad):
    # An env that would itself fail path resolution proves validation runs first.
    env = {"XDG_STATE_HOME": "relative"}
    with pytest.raises(InvalidAccountError):
        resolve_profile(HUB_PROFILE, bad, env=env, platform="linux")
    with pytest.raises(InvalidAccountError):
        hub_config_path(bad, env=env, platform="linux")
    with pytest.raises(InvalidAccountError):
        hub_home(bad, env=env, platform="linux")
    with pytest.raises(InvalidAccountError):
        mirrors_path(bad, env=env, platform="linux")


@pytest.mark.parametrize("good", ["a", "a1", "acme-corp", "0x", "a-"])
def test_valid_account_passes_validation(good):
    assert validate_account(good) == good


def test_unknown_role_refused_with_the_role_named():
    with pytest.raises(UnknownRoleError, match="scratch-disk"):
        LaunchProfile(writable_roles=("hub-home", "scratch-disk"))
    with pytest.raises(UnknownRoleError, match="mystery"):
        LaunchProfile(read_denied_roles=("mystery",))


def test_relative_override_refused(tmp_path):
    env = _env(tmp_path, RANGER_STATE_DIR="relative/dir")
    with pytest.raises(PathResolutionError):
        resolve_profile(HUB_PROFILE, "a", env=env, platform="linux")


def test_resolver_creates_nothing(tmp_path):
    env = _env(tmp_path)
    resolve_profile(HUB_PROFILE, "a", env=env, platform="linux")
    hub_config_path("a", env=env, platform="linux")
    assert list(tmp_path.iterdir()) == []
