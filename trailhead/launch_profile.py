"""Harness-neutral launch profile for a role, and its path resolver.

A profile declares what a launched session may touch, naming every directory
by *role* rather than by path. ``resolve_profile`` turns a profile plus an
account short name into absolute basedir paths for a host; each harness
implementation renders the resolved profile into its own settings.

Write rules are an allow-list of writable roles only. A harness whose
user-level write list merges with the session's narrows by deriving denials
itself; the profile carries no deny-write list.

All resolution goes through ``trailhead.paths`` and creates nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from trailhead.paths import cache_dir, cache_home, config_dir, state_dir

ROLE_HUB_HOME = "hub-home"
ROLE_LORE_STATE = "lore-state"
ROLE_CACHE = "cache"
ROLE_TOKEN_STORE = "token-store"

_ACCOUNT_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


class LaunchProfileError(ValueError):
    """Base class for launch-profile errors."""


class InvalidAccountError(LaunchProfileError):
    """The account short name does not match ^[a-z0-9][a-z0-9-]*$."""


class UnknownRoleError(LaunchProfileError):
    """A profile names a directory role the resolver does not know."""


def validate_account(account: str) -> str:
    if not _ACCOUNT_RE.fullmatch(account):
        raise InvalidAccountError(
            f"account {account!r} is not a valid short name; it must match ^[a-z0-9][a-z0-9-]*$"
        )
    return account


def hub_config_path(
    account: str, *, env: dict[str, str] | None = None, platform: str | None = None
) -> Path:
    validate_account(account)
    return config_dir("ranger", env=env, platform=platform) / "hubs" / f"{account}.json"


def hub_home(
    account: str, *, env: dict[str, str] | None = None, platform: str | None = None
) -> Path:
    validate_account(account)
    return state_dir("ranger", env=env, platform=platform) / "hubs" / account


def mirrors_path(
    account: str, *, env: dict[str, str] | None = None, platform: str | None = None
) -> Path:
    validate_account(account)
    return cache_dir("ranger", env=env, platform=platform) / "mirrors" / account


def _lore_state(account: str, env: dict[str, str] | None, platform: str | None) -> Path:
    return state_dir("lore", env=env, platform=platform)


def _token_store(account: str, env: dict[str, str] | None, platform: str | None) -> Path:
    return state_dir("outpost", env=env, platform=platform) / "operator"


_ROLE_RESOLVERS = {
    ROLE_HUB_HOME: lambda a, e, p: hub_home(a, env=e, platform=p),
    ROLE_LORE_STATE: _lore_state,
    ROLE_CACHE: lambda a, e, p: cache_home(env=e, platform=p),
    ROLE_TOKEN_STORE: _token_store,
}


@dataclass(frozen=True)
class LaunchProfile:
    writable_roles: tuple[str, ...] = ()
    read_denied_roles: tuple[str, ...] = ()
    network_allowed_hosts: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    never_prompt: bool = True
    model: str | None = None
    effort: str | None = None

    def __post_init__(self) -> None:
        for role in (*self.writable_roles, *self.read_denied_roles):
            if role not in _ROLE_RESOLVERS:
                raise UnknownRoleError(f"unknown directory role {role!r} in launch profile")


@dataclass(frozen=True)
class ResolvedProfile:
    profile: LaunchProfile
    account: str
    paths: dict[str, Path]

    @property
    def writable(self) -> tuple[Path, ...]:
        return tuple(self.paths[r] for r in self.profile.writable_roles)

    @property
    def read_denied(self) -> tuple[Path, ...]:
        return tuple(self.paths[r] for r in self.profile.read_denied_roles)


HUB_PROFILE = LaunchProfile(
    writable_roles=(ROLE_HUB_HOME, ROLE_LORE_STATE, ROLE_CACHE),
    read_denied_roles=(ROLE_TOKEN_STORE,),
    network_allowed_hosts=(),
    mcp_servers=(),
    never_prompt=True,
    model=None,
    effort="medium",
)


def resolve_profile(
    profile: LaunchProfile,
    account: str,
    *,
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> ResolvedProfile:
    validate_account(account)
    roles = dict.fromkeys((*profile.writable_roles, *profile.read_denied_roles))
    paths = {role: _ROLE_RESOLVERS[role](account, env, platform) for role in roles}
    return ResolvedProfile(profile=profile, account=account, paths=paths)
