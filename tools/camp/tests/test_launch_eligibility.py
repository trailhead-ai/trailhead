"""Tests for launch/eligibility.py — the credential-store floor.

Test contract:
- Matching is on fully resolved paths, asserted in BOTH symlink directions.
- The credential-directory deny list wins regardless of any group config; its
  refusal never mentions an allowlist.
- Every one of the pinned deny entries refuses when named exactly and as a
  subdirectory, and matches in the ancestor direction; the list itself is
  pinned against a literal so a silent removal fails the suite.
- Deny entries that do not exist on disk still deny.
- Every `[launch] account` declared by ANY group is denied too — equal, under,
  and ancestor — including to a group that declares no account of its own, which
  is the cross-group case the derivation exists for.
- Derivation is additive only: the hardcoded floor comes through whole and in
  order, and an account equal to, above, or below a floor entry leaves that
  entry denying exactly as before.
- Group configs camp cannot read are a refusal, not a shorter deny list — and a
  readable sibling's `roots` key does not change that answer, since `roots`
  grants nothing.
- Every refusal is a LaunchError, and the gate writes nothing.

HOME always comes from the injected env, so no test reads or touches the
operator's real home directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


#: The credential entries this suite pins, spelled out here rather than imported
#: so that deleting one from the implementation fails the comparison below.
_EXPECTED_DENY_ENTRIES = (
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.azure",
    "~/.kube",
    "~/.docker",
    "~/.config/gcloud",
    "~/.netrc",
    "~/.config/gh",
    "~/.npmrc",
    "~/.pypirc",
    "~/.git-credentials",
    "~/.claude",
    "~/.claude.json",
    "~/Library/Keychains",
    "~/.password-store",
    "~/.local/share/keyrings",
    "~/.config/op",
    "~/.terraform.d",
    "~/.cargo/credentials",
    "~/.gem/credentials",
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A hermetic HOME. Resolved up front so refusal messages are comparable."""
    h = (tmp_path / "home").resolve()
    h.mkdir()
    return h


def _env(home: Path) -> dict[str, str]:
    return {"HOME": str(home)}


def _check(target: Path, home: Path, *, env: dict | None = None) -> None:
    from camp.launch.eligibility import assert_not_a_credential_store

    assert_not_a_credential_store(Path(target).resolve(), env=env or _env(home))


def _refusal(target: Path, home: Path, *, env: dict | None = None) -> str:
    from camp.launch.session import LaunchError

    with pytest.raises(LaunchError) as exc_info:
        _check(target, home, env=env)
    return str(exc_info.value)


_MEMBER_TOML = '[[members]]\nname = "myrepo"\nrepo_root = "/tmp/myrepo"\n'


def _install_group_configs(home: Path, groups: dict[str, str | None]) -> dict[str, str]:
    """Write one group config per entry; return an env pointing camp at them.

    `groups` maps a group name to the account it declares under [launch], or to
    None for a group that declares none.
    """
    groups_dir = home / "camp-config" / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    for name, account in groups.items():
        body = f'[group]\nname = "{name}"\n\n{_MEMBER_TOML}'
        if account is not None:
            body += f'\n[launch]\naccount = "{account}"\n'
        (groups_dir / f"{name}.toml").write_text(body, encoding="utf-8")
    return {"HOME": str(home), "CAMP_CONFIG_DIR": str(home / "camp-config")}


# ---------------------------------------------------------------------------
# The credential-directory deny list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", _EXPECTED_DENY_ENTRIES)
def test_each_deny_entry_refuses_when_named_exactly(home: Path, entry: str) -> None:
    target = home / entry.removeprefix("~/")
    msg = _refusal(target, home)
    assert str(target) in msg


@pytest.mark.parametrize("entry", _EXPECTED_DENY_ENTRIES)
def test_each_deny_entry_refuses_as_a_subdirectory(home: Path, entry: str) -> None:
    denied = home / entry.removeprefix("~/")
    msg = _refusal(denied / "sub", home)
    assert str(denied) in msg


@pytest.mark.parametrize("entry", _EXPECTED_DENY_ENTRIES)
def test_each_deny_entry_matches_in_the_ancestor_direction(
    home: Path, entry: str
) -> None:
    """The ancestor leg is what stops a directory rooted at "~" laundering the
    home directory in. For the entries that name a FILE it is the only leg
    that can ever fire, since a window root is always a directory.
    """
    from camp.launch.eligibility import matches_deny_entry

    denied = home / entry.removeprefix("~/")
    assert matches_deny_entry(home, denied)


def test_home_itself_is_refused_as_an_ancestor_of_a_credential_entry(
    home: Path,
) -> None:
    msg = _refusal(home, home)
    assert "credential" in msg


def test_credential_refusal_names_the_credential_rule_not_the_allowlist(
    home: Path,
) -> None:
    """The refusal must never read as something an operator could relax by
    editing a config value."""
    msg = _refusal(home / ".ssh", home)
    assert "credential" in msg
    assert "allowlist" not in msg
    assert "roots" not in msg


def test_credential_deny_applies_under_a_credential_directory(home: Path) -> None:
    msg = _refusal(home / ".ssh" / "keys", home)
    assert "credential" in msg


def test_a_deny_entry_that_does_not_exist_on_disk_still_denies(home: Path) -> None:
    """Resolution is non-strict: a credential directory the operator has not
    created yet is still off limits, so creating it later cannot be a surprise."""
    target = home / ".aws"
    assert not target.exists()
    msg = _refusal(target, home)
    assert "credential" in msg


def test_unrelated_sibling_of_a_denied_entry_is_eligible(home: Path) -> None:
    """~/.config/gcloud and ~/.config/gh are denied; ~/.config itself is denied
    only as their ancestor, and an unrelated sibling under it is not denied."""
    target = home / ".config" / "nvim"
    target.mkdir(parents=True)
    _check(target, home)  # does not raise


# ---------------------------------------------------------------------------
# Failure mode and side effects
# ---------------------------------------------------------------------------


def test_refusal_is_the_launch_engines_error_type(home: Path) -> None:
    """The gate composes with the launch engine: LaunchError is its only failure
    mode, so a refusal here carries the engine's no-process-started guarantee."""
    from camp.launch import eligibility
    from camp.launch.session import LaunchError

    assert eligibility.LaunchError is LaunchError


def test_gate_writes_nothing(home: Path) -> None:
    """Eligibility is a read-only question — it resolves paths and nothing more."""
    before = sorted(str(p) for p in home.rglob("*"))

    _check(home / "elsewhere", home)
    _refusal(home / ".ssh", home)

    assert sorted(str(p) for p in home.rglob("*")) == before


# ---------------------------------------------------------------------------
# Continued — the accounts declared by group configs
# ---------------------------------------------------------------------------


def test_a_declared_account_is_denied(home: Path) -> None:
    """A declared account dir is a credential store."""
    env = _install_group_configs(home, {"levr": "~/.claude-levr"})
    account = home / ".claude-levr"
    account.mkdir()
    msg = _refusal(account, home, env=env)
    assert "credential" in msg


def test_an_ancestor_of_a_declared_account_is_denied(home: Path) -> None:
    """The ancestor leg bites for derived entries too — a directory that
    CONTAINS a declared account hands a rooted window that account's store."""
    env = _install_group_configs(home, {"levr": "~/accounts/levr"})
    (home / "accounts" / "levr").mkdir(parents=True)
    msg = _refusal(home / "accounts", home, env=env)
    assert "credential" in msg


def test_a_directory_under_a_declared_account_is_denied(home: Path) -> None:
    env = _install_group_configs(home, {"levr": "~/.claude-levr"})
    inside = home / ".claude-levr" / "projects"
    inside.mkdir(parents=True)
    msg = _refusal(inside, home, env=env)
    assert "credential" in msg


def test_an_account_declared_by_another_group_is_denied(home: Path) -> None:
    """THE cross-group case. The account belongs to a DIFFERENT group than the
    one whose config is checking. A per-group derivation passes every other
    test here and fails this one, which is the whole finding."""
    env = _install_group_configs(
        home, {"trailhead": None, "levr": "~/.claude-levr"}
    )
    account = home / ".claude-levr"
    account.mkdir()
    msg = _refusal(account, home, env=env)
    assert "credential" in msg


def test_a_cross_group_account_refusal_names_only_the_credential_rule(
    home: Path,
) -> None:
    """A derived entry refuses on the same terms as a hardcoded one: an operator
    must never read a credential refusal as something a config value could fix."""
    env = _install_group_configs(
        home, {"trailhead": None, "levr": "~/.claude-levr"}
    )
    account = home / ".claude-levr"
    account.mkdir()
    msg = _refusal(account, home, env=env)
    assert "credential" in msg
    assert "allowlist" not in msg
    assert "roots" not in msg


def test_an_unrelated_directory_stays_eligible_when_accounts_are_declared(
    home: Path,
) -> None:
    """Derivation adds entries; it does not make everything ineligible."""
    env = _install_group_configs(
        home, {"trailhead": None, "levr": "~/.claude-levr"}
    )
    target = home / "code" / "project"
    target.mkdir(parents=True)
    _check(target, home, env=env)  # does not raise


def test_a_declared_account_reached_by_symlink_is_denied_where_it_resolves(
    home: Path,
) -> None:
    """Both sides are fully resolved, so an account declared through a symlink
    denies the directory it actually points at."""
    real = home / "real-account"
    real.mkdir()
    (home / "linked-account").symlink_to(real)
    env = _install_group_configs(home, {"levr": "~/linked-account"})
    msg = _refusal(real, home, env=env)
    assert "credential" in msg


@pytest.mark.parametrize("account", ["~/.claude", "~", "~/.claude/nested"])
def test_a_declared_account_cannot_shadow_a_hardcoded_entry(
    home: Path, account: str
) -> None:
    """An account equal to, an ancestor of, or a child of a hardcoded entry
    leaves that entry denying exactly as it did before."""
    from camp.launch.eligibility import CREDENTIAL_DENY_ENTRIES, credential_deny_entries

    env = _install_group_configs(home, {"levr": account})
    assert set(CREDENTIAL_DENY_ENTRIES) <= set(credential_deny_entries(env=env))
    assert "credential" in _refusal(home / ".ssh", home, env=env)
    assert "credential" in _refusal(home / ".claude", home, env=env)


def test_a_relative_account_contributes_no_entry(home: Path) -> None:
    """A cwd-relative account names no fixed location — deriving from it would
    make the boundary move with the directory camp is invoked from, and no
    harness can honor it in the first place."""
    from camp.launch.eligibility import credential_deny_entries

    env = _install_group_configs(home, {"levr": "relative-account"})
    assert not any("relative-account" in entry for entry in credential_deny_entries(env=env))


def test_group_configs_that_cannot_be_read_refuse_the_launch(home: Path) -> None:
    """Fail closed: camp that cannot enumerate the declared accounts cannot know
    the boundary, and it refuses as a LaunchError like every other refusal."""
    env = _install_group_configs(home, {"levr": "~/.claude-levr"})
    (home / "camp-config" / "groups" / "broken.toml").write_text("not = [toml", encoding="utf-8")
    target = home / "code"
    target.mkdir()
    msg = _refusal(target, home, env=env)
    assert "group config" in msg


def test_a_readable_siblings_roots_does_not_change_the_deny_list_answer(
    home: Path,
) -> None:
    """`roots` grants nothing, so a readable sibling group config carrying it
    alongside a declared account must derive the exact same deny list as the
    same sibling with no `roots` at all.

    This assurance is fully derivative of `_parse_launch` never storing
    `roots` in the parsed config (`group/config.py`'s own contract): by the
    time `_declared_account_entries` reads a config, a `roots`-carrying and a
    `roots`-free sibling are byte-for-byte identical `launch` dicts, since
    `_declared_account_entries` reads only `.get("account")`. A mutation that
    makes `_parse_launch` store `roots` again does not turn this test red —
    confirmed directly — because eligibility.py has no code path that reads
    the key at all; there is nothing here for `roots` to leak into. See
    `test_group_configs_that_cannot_be_read_refuse_the_launch` for this
    contract item's other half (the unreadable-sibling case), which does
    fail closed."""
    from camp.launch.eligibility import credential_deny_entries

    with_roots_dir = home / "camp-config-a" / "groups"
    with_roots_dir.mkdir(parents=True)
    (with_roots_dir / "levr.toml").write_text(
        f'[group]\nname = "levr"\n\n{_MEMBER_TOML}\n'
        '[launch]\naccount = "~/.claude-levr"\nroots = ["~/code"]\n',
        encoding="utf-8",
    )
    without_roots_dir = home / "camp-config-b" / "groups"
    without_roots_dir.mkdir(parents=True)
    (without_roots_dir / "levr.toml").write_text(
        f'[group]\nname = "levr"\n\n{_MEMBER_TOML}\n'
        '[launch]\naccount = "~/.claude-levr"\n',
        encoding="utf-8",
    )

    env_with = {"HOME": str(home), "CAMP_CONFIG_DIR": str(home / "camp-config-a")}
    env_without = {"HOME": str(home), "CAMP_CONFIG_DIR": str(home / "camp-config-b")}

    assert credential_deny_entries(env=env_with) == credential_deny_entries(env=env_without)


#: A TOML escape for an embedded NUL. Written as an escape because a raw control
#: byte is not legal inside a basic string, and decoded by tomllib into the real
#: character — so the parser downstream sees exactly what an operator's typo, or
#: a hostile config, would put there.
_NUL_ESCAPE = "\\u0000"


def test_another_groups_malformed_account_refuses_rather_than_raising(
    home: Path,
) -> None:
    """THE cross-group blast radius. The deny list pools the accounts of EVERY
    group, so one group's unresolvable value is reached while deriving the
    boundary for a check that has nothing to do with it. Resolving it raises
    ValueError, which escapes as a raw traceback and takes every
    directory-rooted check, for every group, down with it. Fail CLOSED: the same
    refusal an unreadable config gets."""
    from camp.launch.session import LaunchError

    env = _install_group_configs(
        home, {"levr": f"~/accounts/{_NUL_ESCAPE}levr", "trailhead": None}
    )
    target = home / "code"
    target.mkdir()

    with pytest.raises(LaunchError):
        _check(target, home, env=env)


def test_a_groups_own_malformed_account_refuses_rather_than_raising(
    home: Path,
) -> None:
    """The own-group case: the group whose value is malformed is the same one
    being checked against."""
    env = _install_group_configs(home, {"levr": f"/accounts/{_NUL_ESCAPE}levr"})
    target = home / "code"
    target.mkdir()

    from camp.launch.session import LaunchError

    with pytest.raises(LaunchError):
        _check(target, home, env=env)


def test_a_deny_entry_that_cannot_be_resolved_refuses_the_launch(
    home: Path, monkeypatch
) -> None:
    """Fail closed at the resolve step itself, not only where the entries are
    read. An entry the filesystem refuses to resolve is an entry camp cannot rule
    out, and answering with a shorter deny list would turn it into a widened
    boundary."""
    from camp.launch import eligibility
    from camp.launch.session import LaunchError

    monkeypatch.setattr(
        eligibility,
        "credential_deny_entries",
        lambda *, env: ("/accounts/\x00levr",),
    )
    target = home / "code"
    target.mkdir()

    with pytest.raises(LaunchError):
        _check(target, home)


def test_a_groups_directory_that_was_never_created_yields_the_floor(home: Path) -> None:
    """No groups directory means no group declares an account, so there is
    nothing to derive and nothing to miss — the hardcoded floor answers alone,
    and it still refuses."""
    from camp.launch.eligibility import CREDENTIAL_DENY_ENTRIES, credential_deny_entries

    config_dir = home / "camp-config"
    config_dir.mkdir()
    assert not (config_dir / "groups").exists()
    env = {"HOME": str(home), "CAMP_CONFIG_DIR": str(config_dir)}

    assert credential_deny_entries(env=env) == CREDENTIAL_DENY_ENTRIES
    assert "credential" in _refusal(home / ".claude", home, env=env)
