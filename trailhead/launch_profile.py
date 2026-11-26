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

``workspace`` is the one caller-supplied role: ``resolve_profile`` validates the path the
caller names (see :func:`validate_workspace`) instead of deriving it from the account.

``read_denied_roles`` may name *set* roles, which resolve to every path of a kind that
belongs to some other account: their hub homes, mirrors, group vaults and harness
configuration directories. The other accounts are the other configured hubs and the camp
groups whose launch account differs from this account's own; a vault that one of this
account's own groups also binds is never denied.

``credential-dirs`` is a set role that resolves ``CREDENTIAL_DIRS`` against the home of the
environment given; both the session's read denials and its setup steps' sandbox use it.
``workspace-guards`` is a set role derived from the workspace: its ``manifest.json`` and, for
each direct subdirectory of it that holds a ``.git`` entry (a member's worktree), that
member's ``MEMBER_GUARDED_NAMES``. It is read from disk when the profile resolves, so a
member created later is not named.

``unnamed-vaults`` is a set role derived from the one vault the caller names: every directory
under the lore state's ``vaults`` root except that vault (and any entry that resolves to it).
It is read from disk when the profile resolves.

``readable_roles`` names directories the session may read although they lie outside its
write roots; the harness grants read access and no write there.

All resolution goes through ``trailhead.paths`` and creates nothing.
"""

from __future__ import annotations

import os
import re
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from trailhead.paths import cache_dir, cache_home, config_dir, state_dir

ROLE_HUB_HOME = "hub-home"
ROLE_LORE_STATE = "lore-state"
ROLE_CACHE = "cache"
ROLE_TOKEN_STORE = "token-store"
ROLE_WORKSPACE = "workspace"
ROLE_PLUGIN_TREE = "plugin-tree"
ROLE_OTHER_HUB_HOMES = "other-hub-homes"
ROLE_OTHER_MIRRORS = "other-mirrors"
ROLE_OTHER_VAULTS = "other-vaults"
ROLE_OTHER_ACCOUNT_CONFIGS = "other-account-configs"
ROLE_CREDENTIAL_DIRS = "credential-dirs"
ROLE_WORKSPACE_GUARDS = "workspace-guards"
ROLE_UNNAMED_VAULTS = "unnamed-vaults"

#: Credential stores under the account's home that a session may neither read nor send to an
#: allowed domain, named relative to the home. ``~/.npmrc`` is deliberately absent: installs
#: from a private registry authenticate with it, so setup steps must still read it.
CREDENTIAL_DIRS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".config/gh",
    ".netrc",
    ".git-credentials",
    ".kube",
    ".config/gcloud",
    ".azure",
    ".docker",
)

#: What a member directory holds that the operator's own tools later act on: the git pointer
#: (whose target's config can run commands unsandboxed), harness settings and MCP servers.
MEMBER_GUARDED_NAMES = (".git", ".claude", ".mcp.json")

_ACCOUNT_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

BROKER_SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
BROKER_SERVER = "outpost"
BROKER_SCOPE = "ranger"
BROKER_MCP_PATH = "/broker/mcp"
DRIVE_TOOL = "drive_spec"
BROKER_TOOLS = ("timeline_since", DRIVE_TOOL)
RITUAL_ROLE = "ritual"
SESSIONS_SOCKET = "outpost-sessions"
RITUAL_SESSION_PATTERN = "ritual-<hex>"
TASK_WRAPPER_ENV = "CAMP_TASK_WRAPPER"
TASK_WRAPPER_PLACEHOLDERS = ("{worktree}", "{member}")
SETUP_SANDBOX_KEYS = ("allowed_domains", "denied_domains", "denied_reads")
DRIVE_GROUPS_OUTPUT_KEYS = ("groups",)
_BROKER_SESSION_ID_RE = re.compile(BROKER_SESSION_ID_PATTERN)
VAULT_NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_VAULT_NAME_RE = re.compile(VAULT_NAME_PATTERN)


class LaunchProfileError(ValueError):
    """Base class for launch-profile errors."""


class InvalidAccountError(LaunchProfileError):
    """The account short name does not match ^[a-z0-9][a-z0-9-]*$."""


class UnknownRoleError(LaunchProfileError):
    """A profile names a directory role the resolver does not know."""


class WorkspaceError(LaunchProfileError):
    """The workspace path is missing, outside camp's worktrees root, or not a camp workspace."""


class VaultError(LaunchProfileError):
    """The vault name is missing, malformed, or not a vault directory directly under the vaults root."""


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
    return outpost_state(env=env, platform=platform) / "operator"


def outpost_state(*, env: dict[str, str] | None = None, platform: str | None = None) -> Path:
    """Outpost's whole state directory: the database, the operator ``credential`` and
    every session's broker credentials."""
    return state_dir("outpost", env=env, platform=platform)


def validate_broker_session(session_id: str) -> str:
    if not _BROKER_SESSION_ID_RE.fullmatch(session_id):
        raise InvalidBrokerSessionError(
            f"broker session id {session_id!r} is not a valid session id; "
            f"it must match {BROKER_SESSION_ID_PATTERN}"
        )
    return session_id


def _broker_session_dir(
    session_id: str, *, env: dict[str, str] | None, platform: str | None
) -> Path:
    validate_broker_session(session_id)
    return outpost_state(env=env, platform=platform) / "sessions" / session_id


def broker_credential_path(
    session_id: str, *, env: dict[str, str] | None = None, platform: str | None = None
) -> Path:
    """Where Outpost writes a hub session's broker client credentials."""
    return _broker_session_dir(session_id, env=env, platform=platform) / "broker-client.json"


def broker_mcp_config_path(
    session_id: str, *, env: dict[str, str] | None = None, platform: str | None = None
) -> Path:
    """Where Outpost writes a hub session's MCP config, beside its credentials."""
    return _broker_session_dir(session_id, env=env, platform=platform) / "mcp-config.json"


@dataclass(frozen=True)
class BrokerLaunch:
    """What a harness needs to wire one session to the Outpost broker.

    ``header_command`` is the argv of a program that prints the Authorization header as a
    JSON object; ``config_path`` is where the rendered MCP config is written, a location
    the session can neither read nor write.
    """

    server: str
    url: str
    tools: tuple[str, ...]
    credential_path: str
    config_path: str
    header_command: tuple[str, ...]

    @property
    def header_shell_command(self) -> str:
        return shlex.join(self.header_command)


def broker_launch(
    session_id: str,
    *,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    trailhead_bin: str | None = None,
    port: int | None = None,
) -> BrokerLaunch:
    """The broker wiring for *session_id*; the trailhead executable and the port are injectable."""
    from trailhead import outpost_lifecycle
    from trailhead.pathint import repo_root

    credential = str(broker_credential_path(session_id, env=env, platform=platform))
    binary = trailhead_bin if trailhead_bin is not None else str(repo_root() / "bin" / "trailhead")
    number = port if port is not None else outpost_lifecycle.DAEMON_PORT
    return BrokerLaunch(
        server=BROKER_SERVER,
        url=f"http://{outpost_lifecycle.DAEMON_HOST}:{number}{BROKER_MCP_PATH}",
        tools=BROKER_TOOLS,
        credential_path=credential,
        config_path=str(broker_mcp_config_path(session_id, env=env, platform=platform)),
        header_command=(binary, "broker-header", "--credential-file", credential),
    )


def plugin_tree(*, env: dict[str, str] | None = None, platform: str | None = None) -> Path:
    """The parent of every harness's composed plugin tree (``<root>/<harness name>``)."""
    return state_dir("trailhead", env=env, platform=platform) / "composed"


def validate_workspace(
    workspace: str | os.PathLike[str],
    *,
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """The real path of a camp workspace, or :class:`WorkspaceError`.

    Camp keeps a workspace at ``<camp state>/<group>/worktrees/<slug>`` with a
    ``manifest.json`` in it. Symlinks are resolved before the check, so a link under the
    root that points elsewhere is refused and a link that points at another workspace is
    that workspace. A symlink loop resolves to a path that is not a directory.
    """
    root = Path(os.path.realpath(state_dir("camp", env=env, platform=platform)))
    real = Path(os.path.realpath(workspace))
    if root != real and root not in real.parents:
        raise WorkspaceError(
            f"workspace {workspace} resolves to {real}, which is outside camp's worktrees root {root}"
        )
    parts = real.relative_to(root).parts
    if len(parts) != 3 or parts[1] != "worktrees":
        raise WorkspaceError(
            f"workspace {workspace} is not a camp workspace (expected {root}/<group>/worktrees/<slug>)"
        )
    if not real.is_dir():
        raise WorkspaceError(
            f"workspace {workspace} is not an existing directory; create it with `camp new` first"
        )
    if not (real / "manifest.json").is_file():
        raise WorkspaceError(
            f"workspace {workspace} has no camp manifest ({real / 'manifest.json'}); refusing"
        )
    return real


def _vaults_root(env, platform) -> Path:
    return state_dir("lore", env=env, platform=platform) / "vaults"


def validate_vault(
    vault: str,
    *,
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """The real path of the named lore vault, or :class:`VaultError`.

    The name must be a plain directory name (``VAULT_NAME_PATTERN``), and the directory
    must exist directly under the lore state's ``vaults`` root once symlinks are resolved:
    a link that points out of the root, or at another vault, is refused.
    """
    if not _VAULT_NAME_RE.fullmatch(vault):
        raise VaultError(
            f"--vault {vault!r} is not a vault name; it must match {VAULT_NAME_PATTERN} "
            "(see `lore vault ls` for the names)"
        )
    root = _vaults_root(env, platform)
    real = Path(os.path.realpath(root / vault))
    if real != Path(os.path.realpath(root)) / vault:
        raise VaultError(
            f"--vault {vault!r} resolves to {real}, which is not a vault directly under {root}; "
            "name a vault from `lore vault ls`"
        )
    if not real.is_dir():
        raise VaultError(f"--vault {vault!r} is not an existing vault under {root}; name one from `lore vault ls`")
    return real


def _unnamed_vaults(vault: Path, env, platform) -> tuple[Path, ...]:
    root = _vaults_root(env, platform)
    if not root.is_dir():
        return ()
    return tuple(p for p in sorted(root.iterdir()) if p.is_dir() and not os.path.samefile(p, vault))


def _expand_home(value: str, env: dict[str, str] | None) -> str:
    home = (env if env is not None else os.environ).get("HOME", "")
    return home + value[1:] if value == "~" or value.startswith("~/") else value


def _other_hub_accounts(account: str, env, platform) -> list[str]:
    hubs = hub_config_path(account, env=env, platform=platform).parent
    if not hubs.is_dir():
        return []
    found = (re.fullmatch(r"([a-z0-9][a-z0-9-]*)\.json", p.name) for p in hubs.iterdir())
    return sorted(m.group(1) for m in found if m and m.group(1) != account)


def _groups(env, platform) -> list[dict]:
    """Every camp group file that parses; one that does not declares nothing."""
    groups = config_dir("camp", env=env, platform=platform) / "groups"
    parsed: list[dict] = []
    if groups.is_dir():
        for toml_file in sorted(groups.glob("*.toml")):
            try:
                parsed.append(tomllib.loads(toml_file.read_text()))
            except (OSError, tomllib.TOMLDecodeError):
                continue
    return parsed


def _group_launch_account(group: dict, env) -> str | None:
    launch = group.get("launch")
    value = launch.get("account") if isinstance(launch, dict) else None
    return _expand_home(value, env) if isinstance(value, str) else None


def _group_vaults(group: dict, env, platform) -> list[Path]:
    paths: list[Path] = []
    lore = state_dir("lore", env=env, platform=platform)
    for key in ("lore_scopes", "shared_vaults"):
        entries = group.get(key)
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                paths.append(lore / "vaults" / entry["name"])
                if isinstance(entry.get("root"), str):
                    paths.append(Path(_expand_home(entry["root"], env)))
    return paths


def _own_launch_account(account: str, env, platform) -> str | None:
    from trailhead.ranger.config import DEFAULT_LAUNCH_ACCOUNT, read_hub_config

    launch = read_hub_config(account, env=env, platform=platform).launch_account
    return None if launch == DEFAULT_LAUNCH_ACCOUNT else _expand_home(launch, env)


def _other_groups(account: str, env, platform) -> tuple[list[dict], list[dict]]:
    """``(own groups, other groups)``: those on this account's launch account and the rest."""
    own_launch = _own_launch_account(account, env, platform)
    groups = _groups(env, platform)
    own = [g for g in groups if _group_launch_account(g, env) == own_launch]
    return own, [g for g in groups if g not in own]


def _other_vaults(account: str, env, platform) -> tuple[Path, ...]:
    own, other = _other_groups(account, env, platform)
    kept = {p for g in own for p in _group_vaults(g, env, platform)}
    denied = dict.fromkeys(p for g in other for p in _group_vaults(g, env, platform) if p not in kept)
    return tuple(denied)


def _other_account_configs(account: str, env, platform) -> tuple[Path, ...]:
    """Launch-account directories other than this account's own. The default account has no
    path a harness-neutral resolver can name, so it is never listed."""
    own = _own_launch_account(account, env, platform)
    _, other = _other_groups(account, env, platform)
    from trailhead.ranger.config import DEFAULT_LAUNCH_ACCOUNT, read_hub_config

    declared = [_group_launch_account(g, env) for g in other]
    for hub in _other_hub_accounts(account, env, platform):
        launch = read_hub_config(hub, env=env, platform=platform).launch_account
        declared.append(None if launch == DEFAULT_LAUNCH_ACCOUNT else _expand_home(launch, env))
    return tuple(dict.fromkeys(Path(a) for a in declared if a and a != own))


def _credential_dirs(account: str, env, platform) -> tuple[Path, ...]:
    home = (env if env is not None else os.environ).get("HOME")
    root = Path(home) if home else Path.home()
    return tuple(root / rel for rel in CREDENTIAL_DIRS)


def _workspace_guards(workspace: Path) -> tuple[Path, ...]:
    guarded = [workspace / "manifest.json"]
    for member in sorted(p for p in workspace.iterdir() if p.is_dir() and os.path.lexists(p / ".git")):
        guarded.extend(member / name for name in MEMBER_GUARDED_NAMES)
    return tuple(guarded)


_ROLE_SET_RESOLVERS = {
    ROLE_CREDENTIAL_DIRS: _credential_dirs,
    ROLE_OTHER_HUB_HOMES: lambda a, e, p: tuple(
        hub_home(b, env=e, platform=p) for b in _other_hub_accounts(a, e, p)
    ),
    ROLE_OTHER_MIRRORS: lambda a, e, p: tuple(
        mirrors_path(b, env=e, platform=p) for b in _other_hub_accounts(a, e, p)
    ),
    ROLE_OTHER_VAULTS: _other_vaults,
    ROLE_OTHER_ACCOUNT_CONFIGS: _other_account_configs,
}

_ROLE_RESOLVERS = {
    ROLE_PLUGIN_TREE: lambda a, e, p: plugin_tree(env=e, platform=p),
    ROLE_HUB_HOME: lambda a, e, p: hub_home(a, env=e, platform=p),
    ROLE_LORE_STATE: lambda a, e, p: state_dir("lore", env=e, platform=p),
    ROLE_CACHE: lambda a, e, p: cache_home(env=e, platform=p),
    ROLE_TOKEN_STORE: lambda a, e, p: outpost_state(env=e, platform=p),
}


@dataclass(frozen=True)
class LaunchProfile:
    writable_roles: tuple[str, ...] = ()
    git_guarded_roles: tuple[str, ...] = ()
    read_denied_roles: tuple[str, ...] = ()
    write_denied_roles: tuple[str, ...] = ()
    readable_roles: tuple[str, ...] = ()
    network_allowed_hosts: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    # Plugin skills the session may not invoke, by their qualified names (``plugin:skill``).
    denied_skills: tuple[str, ...] = ()
    never_prompt: bool = True
    model: str | None = None
    effort: str | None = None

    def __post_init__(self) -> None:
        known = (*_ROLE_RESOLVERS, *_ROLE_SET_RESOLVERS, ROLE_WORKSPACE, ROLE_WORKSPACE_GUARDS, ROLE_UNNAMED_VAULTS)
        for role in (
            *self.writable_roles,
            *self.git_guarded_roles,
            *self.read_denied_roles,
            *self.write_denied_roles,
            *self.readable_roles,
        ):
            if role not in known:
                raise UnknownRoleError(f"unknown directory role {role!r} in launch profile")


@dataclass(frozen=True)
class ResolvedProfile:
    profile: LaunchProfile
    account: str
    paths: dict[str, Path]
    path_sets: dict[str, tuple[Path, ...]] = field(default_factory=dict)

    @property
    def writable(self) -> tuple[Path, ...]:
        return tuple(self.paths[r] for r in self.profile.writable_roles)

    @property
    def readable(self) -> tuple[Path, ...]:
        return tuple(self.paths[r] for r in self.profile.readable_roles)

    def _paths_of(self, roles: tuple[str, ...]) -> tuple[Path, ...]:
        found: list[Path] = []
        for role in roles:
            found.extend(self.path_sets[role] if role in self.path_sets else (self.paths[role],))
        return tuple(found)

    @property
    def read_denied(self) -> tuple[Path, ...]:
        return self._paths_of(self.profile.read_denied_roles)

    @property
    def write_denied(self) -> tuple[Path, ...]:
        return self._paths_of(self.profile.write_denied_roles)


HUB_PROFILE = LaunchProfile(
    writable_roles=(ROLE_HUB_HOME, ROLE_LORE_STATE, ROLE_CACHE),
    git_guarded_roles=(ROLE_LORE_STATE,),
    read_denied_roles=(ROLE_TOKEN_STORE,),
    network_allowed_hosts=(),
    mcp_servers=(BROKER_SERVER,),
    # The hub drives a spec through the broker's drive tool, which runs the slice loop in its
    # own workspace session; craft's driver would run that loop inside the hub instead.
    denied_skills=("craft:drive",),
    never_prompt=True,
    model=None,
    effort="medium",
)


RITUAL_PROFILE = LaunchProfile(
    writable_roles=(ROLE_WORKSPACE, ROLE_LORE_STATE, ROLE_CACHE),
    git_guarded_roles=(ROLE_LORE_STATE,),
    read_denied_roles=(
        ROLE_TOKEN_STORE,
        ROLE_OTHER_HUB_HOMES,
        ROLE_OTHER_MIRRORS,
        ROLE_OTHER_VAULTS,
        ROLE_OTHER_ACCOUNT_CONFIGS,
        ROLE_CREDENTIAL_DIRS,
    ),
    write_denied_roles=(ROLE_OTHER_MIRRORS, ROLE_OTHER_VAULTS, ROLE_WORKSPACE_GUARDS, ROLE_UNNAMED_VAULTS),
    readable_roles=(ROLE_PLUGIN_TREE,),
    network_allowed_hosts=(),
    mcp_servers=(),
    never_prompt=True,
    model=None,
    effort=None,
)


def resolve_profile(
    profile: LaunchProfile,
    account: str,
    *,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    workspace: str | os.PathLike[str] | None = None,
    without_workspace: bool = False,
    vault: str | None = None,
) -> ResolvedProfile:
    """Resolve *profile*'s roles for *account*.

    ``without_workspace`` resolves a workspace-taking profile with no workspace, for a
    caller that reads only what does not depend on one (the ``workspace`` role is then
    absent from the result's paths); it is refused alongside a *workspace*.

    A profile that write-denies ``unnamed-vaults`` needs the one *vault* its session may write
    (see :func:`validate_vault`); a profile that does not takes none.
    """
    validate_account(account)
    roles = dict.fromkeys(
        (
            *profile.writable_roles,
            *profile.git_guarded_roles,
            *profile.read_denied_roles,
            *profile.write_denied_roles,
            *profile.readable_roles,
        )
    )
    wants_workspace = ROLE_WORKSPACE in roles or ROLE_WORKSPACE_GUARDS in roles
    if without_workspace and workspace is not None:
        raise WorkspaceError("without_workspace and a workspace were both given")
    if wants_workspace and workspace is None and not without_workspace:
        raise WorkspaceError("a ritual session needs a workspace; none was given")
    if workspace is not None and not wants_workspace:
        raise WorkspaceError("this profile takes no workspace")
    wants_vault = ROLE_UNNAMED_VAULTS in roles
    if wants_vault and vault is None and not without_workspace:
        raise VaultError("a ritual session needs a vault; pass --vault <name> (see `lore vault ls`)")
    if vault is not None and not wants_vault:
        raise VaultError("this profile takes no vault")
    if vault is not None and without_workspace:
        raise VaultError("without_workspace and a vault were both given")
    paths: dict[str, Path] = {}
    path_sets: dict[str, tuple[Path, ...]] = {}
    for role in roles:
        if role == ROLE_UNNAMED_VAULTS:
            if not without_workspace:
                path_sets[role] = _unnamed_vaults(validate_vault(vault, env=env, platform=platform), env, platform)
        elif role == ROLE_WORKSPACE:
            if without_workspace:
                continue
            paths[role] = validate_workspace(workspace, env=env, platform=platform)
        elif role == ROLE_WORKSPACE_GUARDS:
            if without_workspace:
                continue
            path_sets[role] = _workspace_guards(validate_workspace(workspace, env=env, platform=platform))
        elif role in _ROLE_SET_RESOLVERS:
            path_sets[role] = _ROLE_SET_RESOLVERS[role](account, env, platform)
        else:
            paths[role] = _ROLE_RESOLVERS[role](account, env, platform)
    return ResolvedProfile(profile=profile, account=account, paths=paths, path_sets=path_sets)
