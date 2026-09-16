"""Test contract: `camp group`'s argument parsing.

The contract pinned here is the one `camp group --help` states — three modes
selected by flag, a repeatable `--member NAME=PATH`, a defaulted
`--branch-pattern`, three booleans, and exactly one positional group name — plus
the refusals for each way of getting it wrong.

These tests are deliberately about the PARSE only: `_parse_init_args` returns the
settings dict and touches no filesystem, so every case here is a pure
argv-in/settings-out assertion. What the modes then DO with those settings is
covered by the group-config and dispatch suites.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.cli.group import _parse_init_args  # noqa: E402

_DEFAULT_BRANCH_PATTERN = "worktree-{slug}"


# ---------------------------------------------------------------------------
# The group name, and the defaults everything else falls back to.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["trailhead", "levr", "a-b_c.d"])
def test_group_name_is_the_lone_positional(name: str) -> None:
    """The positional is the group name, whatever it is — and with no other flag
    every setting takes its documented default."""
    parsed = _parse_init_args([name])
    assert parsed == {
        "group_name": name,
        "members": [],
        "branch_pattern": _DEFAULT_BRANCH_PATTERN,
        "force": False,
        "allow_missing": False,
        "scaffold": False,
    }


def test_group_name_may_follow_its_flags() -> None:
    """Flags before the positional parse identically to flags after it — the
    operator's ordering is not part of the contract."""
    before = _parse_init_args(["--force", "--scaffold", "grp"])
    after = _parse_init_args(["grp", "--force", "--scaffold"])
    assert before == after
    assert (before["group_name"], before["force"], before["scaffold"]) == ("grp", True, True)


# ---------------------------------------------------------------------------
# --member: repeatable, split on the FIRST '=' only.
# ---------------------------------------------------------------------------


def test_members_accumulate_in_order() -> None:
    """`--member` is repeatable and order-preserving — the members land in the
    order the operator declared them, which is the order camp provisions them."""
    parsed = _parse_init_args(
        ["grp", "--member", "api=/srv/api", "--member", "web=/srv/web"]
    )
    assert parsed["members"] == [
        {"name": "api", "repo_root": "/srv/api"},
        {"name": "web", "repo_root": "/srv/web"},
    ]


@pytest.mark.parametrize("spelling", ["--member=api=/srv/api", "--member"])
def test_member_accepts_both_flag_spellings(spelling: str) -> None:
    """`--member NAME=PATH` and `--member=NAME=PATH` reach the same member."""
    argv = ["grp", spelling] if "=" in spelling else ["grp", spelling, "api=/srv/api"]
    assert _parse_init_args(argv)["members"] == [{"name": "api", "repo_root": "/srv/api"}]


def test_member_path_may_itself_contain_equals() -> None:
    """The split is on the FIRST '=' only, so a path carrying one survives whole.

    This is the case a naive `split("=")` gets wrong, and the reason the contract
    names the first separator explicitly.
    """
    parsed = _parse_init_args(["grp", "--member", "api=/srv/a=b/c"])
    assert parsed["members"] == [{"name": "api", "repo_root": "/srv/a=b/c"}]


# ---------------------------------------------------------------------------
# --branch-pattern: defaulted, overridable, both spellings.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["grp"], _DEFAULT_BRANCH_PATTERN),
        (["grp", "--branch-pattern", "wt/{slug}"], "wt/{slug}"),
        (["grp", "--branch-pattern=feature/{slug}"], "feature/{slug}"),
    ],
)
def test_branch_pattern_defaults_and_overrides(argv: list[str], expected: str) -> None:
    """Absent, the documented default; present in either spelling, the operator's."""
    assert _parse_init_args(argv)["branch_pattern"] == expected


# ---------------------------------------------------------------------------
# The three booleans are independent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--force", "--allow-missing", "--scaffold"])
def test_each_boolean_sets_only_its_own_setting(flag: str) -> None:
    """Passing one boolean must not move either of the others."""
    parsed = _parse_init_args(["grp", flag])
    key = flag.lstrip("-").replace("-", "_")
    assert parsed[key] is True
    assert [k for k in ("force", "allow_missing", "scaffold") if parsed[k]] == [key]


# ---------------------------------------------------------------------------
# Refusals.
# ---------------------------------------------------------------------------


def test_missing_group_name_is_refused(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        _parse_init_args(["--force"])
    assert exc.value.code == 1
    assert capsys.readouterr().err == "camp group: a group name is required\n"


def test_second_positional_is_refused(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        _parse_init_args(["grp", "extra"])
    assert exc.value.code == 1
    assert capsys.readouterr().err == "camp group: unexpected argument 'extra'\n"


@pytest.mark.parametrize("flag", ["--bogus", "--forc", "-z"])
def test_unknown_flag_is_refused_naming_it(flag: str, capsys) -> None:
    """Including `--forc`: a near-miss of a real flag is unknown, not an
    abbreviation of `--force`."""
    with pytest.raises(SystemExit) as exc:
        _parse_init_args(["grp", flag])
    assert exc.value.code == 1
    assert capsys.readouterr().err == f"camp group: unknown flag {flag!r}\n"


def test_member_without_a_value_is_refused(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        _parse_init_args(["grp", "--member"])
    assert exc.value.code == 1
    assert capsys.readouterr().err == "camp group: --member requires a NAME=PATH value\n"


def test_branch_pattern_without_a_value_is_refused(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        _parse_init_args(["grp", "--branch-pattern"])
    assert exc.value.code == 1
    assert capsys.readouterr().err == "camp group: --branch-pattern requires a value\n"


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        ("noequals", "expected NAME=PATH"),
        ("=/srv/api", "member NAME must not be empty"),
        ("api=", "member PATH must not be empty"),
    ],
)
def test_malformed_member_is_refused_naming_the_reason(
    spec: str, reason: str, capsys
) -> None:
    """Each way a member spec can be malformed reports its own reason — varying
    the spec varies the message, so no one branch can absorb the others."""
    with pytest.raises(SystemExit) as exc:
        _parse_init_args(["grp", "--member", spec])
    assert exc.value.code == 1
    assert capsys.readouterr().err == f"camp group: malformed --member {spec!r} — {reason}\n"
