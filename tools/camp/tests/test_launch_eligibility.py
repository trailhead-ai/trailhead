"""Tests for launch/eligibility.py — the one gate that fences directory rooting.

Test contract:
- No configured roots → refusal naming the missing allowlist and the group.
- Equal-to-a-root and under-a-root are eligible; the root's PARENT is not.
- Matching is on fully resolved paths, asserted in BOTH symlink directions.
- The credential-directory deny list is checked after the allowlist and wins
  regardless of configuration; its refusal never mentions the allowlist.
- Every one of the pinned deny entries refuses when named exactly and as a
  subdirectory, and matches in the ancestor direction; the list itself is
  pinned against a literal so a silent removal fails the suite.
- Deny entries that do not exist on disk still deny.
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


def _group(roots: list[str] | None = None, *, name: str = "testgroup") -> dict:
    """A loaded-group-config shaped dict. `roots=None` omits [launch] entirely."""
    cfg: dict = {"group": {"name": name}, "members": []}
    if roots is not None:
        cfg["launch"] = {"roots": list(roots)}
    return cfg


def _check(target: Path, group: dict, home: Path) -> Path:
    from camp.launch.eligibility import assert_launch_eligible

    return assert_launch_eligible(target, group=group, env=_env(home))


def _refusal(target: Path, group: dict, home: Path) -> str:
    from camp.launch.session import LaunchError

    with pytest.raises(LaunchError) as exc_info:
        _check(target, group, home)
    return str(exc_info.value)


# ---------------------------------------------------------------------------
# Gate 1 — nothing configured
# ---------------------------------------------------------------------------


def test_no_launch_block_refuses_naming_allowlist_and_group(home: Path) -> None:
    """A group with no [launch] block has no eligible directory at all."""
    msg = _refusal(home / "anywhere", _group(None, name="mygroup"), home)
    assert "allowlist" in msg
    assert "launch" in msg and "roots" in msg
    assert "mygroup" in msg


def test_launch_block_without_roots_refuses(home: Path) -> None:
    """A [launch] block that configures no roots is the same refusal — an empty
    block must not read as a permissive one."""
    cfg = _group()
    cfg["launch"] = {}
    msg = _refusal(home / "anywhere", cfg, home)
    assert "allowlist" in msg


# ---------------------------------------------------------------------------
# Gate 2 — the allowlist
# ---------------------------------------------------------------------------


def test_target_exactly_at_a_root_is_eligible(home: Path) -> None:
    root = home / "code"
    root.mkdir()
    assert _check(root, _group([str(root)]), home) == root


def test_target_deep_under_a_root_is_eligible(home: Path) -> None:
    root = home / "code"
    deep = root / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert _check(deep, _group([str(root)]), home) == deep


def test_target_that_is_the_roots_parent_is_refused(home: Path) -> None:
    """Eligibility is equal-or-under, never ancestor-of: allowlisting a
    subdirectory must not allowlist everything above it."""
    root = home / "code" / "inner"
    root.mkdir(parents=True)
    msg = _refusal(root.parent, _group([str(root)]), home)
    assert "allowlist" in msg


def test_target_outside_every_root_refuses_naming_the_allowlist(home: Path) -> None:
    root = home / "code"
    root.mkdir()
    other = home / "elsewhere"
    other.mkdir()
    msg = _refusal(other, _group([str(root)]), home)
    assert "allowlist" in msg
    assert str(root) in msg


def test_roots_entries_expand_tilde_from_the_injected_home(home: Path) -> None:
    """'~' in a roots entry resolves against the injected HOME, not the real one."""
    root = home / "code"
    root.mkdir()
    assert _check(root, _group(["~/code"]), home) == root


def test_one_of_several_roots_matching_is_enough(home: Path) -> None:
    first = home / "one"
    second = home / "two"
    first.mkdir()
    second.mkdir()
    assert _check(second, _group([str(first), str(second)]), home) == second


# ---------------------------------------------------------------------------
# Gate 2 — resolution, in both symlink directions
# ---------------------------------------------------------------------------


def test_symlink_outside_the_allowlist_pointing_in_is_eligible(home: Path) -> None:
    root = home / "code"
    inner = root / "project"
    inner.mkdir(parents=True)
    outside = home / "elsewhere"
    outside.mkdir()
    link = outside / "link"
    link.symlink_to(inner)

    assert _check(link, _group([str(root)]), home) == inner


def test_symlink_inside_the_allowlist_pointing_out_is_refused(home: Path) -> None:
    root = home / "code"
    root.mkdir()
    outside = home / "elsewhere"
    outside.mkdir()
    link = root / "escape"
    link.symlink_to(outside)

    msg = _refusal(link, _group([str(root)]), home)
    assert "allowlist" in msg
    assert str(outside) in msg


def test_a_root_entry_is_resolved_too(home: Path) -> None:
    """A roots entry that is itself a symlink fences its resolved target."""
    real = home / "real"
    real.mkdir()
    alias = home / "alias"
    alias.symlink_to(real)

    assert _check(real / "sub", _group([str(alias)]), home) == real / "sub"


# ---------------------------------------------------------------------------
# Gate 3 — the credential-directory deny list
# ---------------------------------------------------------------------------


def test_deny_entries_match_the_pinned_list_exactly(home: Path) -> None:
    """The list is fixed. Adding or removing an entry is a deliberate change to
    a security boundary, so it has to break this comparison first."""
    from camp.launch.eligibility import CREDENTIAL_DENY_ENTRIES

    assert tuple(CREDENTIAL_DENY_ENTRIES) == _EXPECTED_DENY_ENTRIES


@pytest.mark.parametrize("entry", _EXPECTED_DENY_ENTRIES)
def test_each_deny_entry_refuses_when_named_exactly(home: Path, entry: str) -> None:
    target = home / entry.removeprefix("~/")
    msg = _refusal(target, _group(["~"]), home)
    assert str(target) in msg


@pytest.mark.parametrize("entry", _EXPECTED_DENY_ENTRIES)
def test_each_deny_entry_refuses_as_a_subdirectory(home: Path, entry: str) -> None:
    denied = home / entry.removeprefix("~/")
    msg = _refusal(denied / "sub", _group(["~"]), home)
    assert str(denied) in msg


@pytest.mark.parametrize("entry", _EXPECTED_DENY_ENTRIES)
def test_each_deny_entry_matches_in_the_ancestor_direction(
    home: Path, entry: str
) -> None:
    """The ancestor leg is what stops roots = ["~"] laundering the home
    directory in. For the entries that name a FILE it is the only leg that can
    ever fire, since a launch root is always a directory.
    """
    from camp.launch.eligibility import matches_deny_entry

    denied = home / entry.removeprefix("~/")
    assert matches_deny_entry(home, denied)


def test_home_itself_is_refused_as_an_ancestor_of_a_credential_entry(
    home: Path,
) -> None:
    """roots = ["~"] passes the allowlist and is then denied outright."""
    msg = _refusal(home, _group(["~"]), home)
    assert "credential" in msg


def test_credential_refusal_names_the_credential_rule_not_the_allowlist(
    home: Path,
) -> None:
    """The deny wins regardless of configuration, so its refusal must never read
    as something the operator could relax by editing the allowlist."""
    msg = _refusal(home / ".ssh", _group(["~"]), home)
    assert "credential" in msg
    assert "allowlist" not in msg
    assert "roots" not in msg


def test_credential_deny_applies_under_a_credential_directory(home: Path) -> None:
    msg = _refusal(home / ".ssh" / "keys", _group(["~"]), home)
    assert "credential" in msg


def test_a_deny_entry_that_does_not_exist_on_disk_still_denies(home: Path) -> None:
    """Resolution is non-strict: a credential directory the operator has not
    created yet is still off limits, so creating it later cannot be a surprise."""
    target = home / ".aws"
    assert not target.exists()
    msg = _refusal(target, _group(["~"]), home)
    assert "credential" in msg


def test_unrelated_sibling_of_a_denied_entry_is_eligible(home: Path) -> None:
    """~/.config/gcloud and ~/.config/gh are denied; ~/.config itself is denied
    only as their ancestor, and an unrelated sibling under it is not denied."""
    target = home / ".config" / "nvim"
    target.mkdir(parents=True)
    assert _check(target, _group(["~"]), home) == target


def test_deny_beats_an_allowlist_that_names_the_credential_directory(
    home: Path,
) -> None:
    """Allowlisting a credential directory outright does not make it eligible."""
    ssh = home / ".ssh"
    ssh.mkdir()
    msg = _refusal(ssh, _group([str(ssh)]), home)
    assert "credential" in msg


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
    root = home / "code"
    root.mkdir()
    before = sorted(str(p) for p in home.rglob("*"))

    _check(root, _group([str(root)]), home)
    _refusal(home / "elsewhere", _group([str(root)]), home)
    _refusal(home / ".ssh", _group(["~"]), home)
    _refusal(home / "anywhere", _group(None), home)

    assert sorted(str(p) for p in home.rglob("*")) == before
