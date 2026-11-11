"""Harness-neutral launch profile for a role, and its path resolver.

A profile declares what a launched session may touch, naming every directory
by *role* rather than by path. ``resolve_profile`` turns a profile plus an
account short name into absolute basedir paths for a host; each harness
implementation renders the resolved profile into its own settings.

Write rules are an allow-list of writable roles only. A harness whose
user-level write list merges with the session's narrows by deriving denials
itself; the profile carries no deny-write list.

``git_guarded_roles`` names writable roles that hold git repositories whose
administrative files (``config``, ``hooks``, ``info``) the session must never
write: a harness's own protection of them covers only the session's working
directory, and a planted filter, hook or ``core.*`` setting would later run
unsandboxed in whatever tool next touches the repository. The hub guards the
lore state root, where every vault lives as a git working tree. A guarded role
that is not writable adds nothing, since a harness only denies what it would
otherwise allow.

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

BROKER_SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
BROKER_SERVER = "outpost"
BROKER_SCOPE = "ranger"
_BROKER_SESSION_ID_RE = re.compile(BROKER_SESSION_ID_PATTERN)


class LaunchProfileError(ValueError):
    """Base class for launch-profile errors."""


class InvalidAccountError(LaunchProfileError):
    """The account short name does not match ^[a-z0-9][a-z0-9-]*$."""


class UnknownRoleError(LaunchProfileError):
    """A profile names a directory role the resolver does not know."""


class InvalidBrokerSessionError(LaunchProfileError):
    """The broker session id does not match the shared contract's session-id shape."""


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


def operator_token_store(
    *, env: dict[str, str] | None = None, platform: str | None = None
) -> Path:
    """Outpost's operator directory, which holds the operator ``credential`` file."""
    return state_dir("outpost", env=env, platform=platform) / "operator"


def validate_broker_session(session_id: str) -> str:
    if not _BROKER_SESSION_ID_RE.fullmatch(session_id):
        raise InvalidBrokerSessionError(
            f"broker session id {session_id!r} is not a valid session id; "
            f"it must match {BROKER_SESSION_ID_PATTERN}"
        )
    return session_id


def broker_credential_path(
    session_id: str, *, env: dict[str, str] | None = None, platform: str | None = None
) -> Path:
    """Where Outpost writes a hub session's broker client credentials."""
    validate_broker_session(session_id)
    return state_dir("outpost", env=env, platform=platform) / "sessions" / session_id / "broker-client.json"


_ROLE_RESOLVERS = {
    ROLE_HUB_HOME: lambda a, e, p: hub_home(a, env=e, platform=p),
    ROLE_LORE_STATE: lambda a, e, p: state_dir("lore", env=e, platform=p),
    ROLE_CACHE: lambda a, e, p: cache_home(env=e, platform=p),
    ROLE_TOKEN_STORE: lambda a, e, p: operator_token_store(env=e, platform=p),
}


@dataclass(frozen=True)
class LaunchProfile:
    writable_roles: tuple[str, ...] = ()
    git_guarded_roles: tuple[str, ...] = ()
    read_denied_roles: tuple[str, ...] = ()
    network_allowed_hosts: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    never_prompt: bool = True
    model: str | None = None
    effort: str | None = None

    def __post_init__(self) -> None:
        for role in (*self.writable_roles, *self.git_guarded_roles, *self.read_denied_roles):
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
    git_guarded_roles=(ROLE_LORE_STATE,),
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
    roles = dict.fromkeys(
        (*profile.writable_roles, *profile.git_guarded_roles, *profile.read_denied_roles)
    )
    paths = {role: _ROLE_RESOLVERS[role](account, env, platform) for role in roles}
    return ResolvedProfile(profile=profile, account=account, paths=paths)
