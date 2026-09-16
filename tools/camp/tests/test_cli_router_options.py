"""Test contract: the router-level option readers in `camp.cli.dispatch`.

`main()` must know three things BEFORE it classifies a verb or resolves a group:
has the group axis been widened (``--all-groups``/``-g``), has the machine axis
been widened (``--all-hosts``/``-a``), and has one machine been named
(``--host <name>``). It must learn them without disturbing the verb's own flags,
which it forwards whole to the handler it eventually picks.

`read_router_options` is that reader. Two properties matter and are pinned here:

- it CONSUMES the options it owns and passes everything else through untouched,
  in order, so a leaf still sees its own argv;
- it distinguishes ``--host`` ABSENT from ``--host`` present-but-valueless,
  because `main()` refuses those two differently — a verb that has no use for
  ``--host`` must be told so even when the flag also has no value.

`read_group_option` is the separate, NON-consuming read of ``--group``.
`main()` needs to see that flag to refuse it alongside a widening option, while
still forwarding it to the handler — `camp launch --host h --group g` relays the
group name to the far side, so consuming it here would strip the value the
remote invocation is built from.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.cli.dispatch import read_group_option, read_router_options  # noqa: E402


# ---------------------------------------------------------------------------
# The widening axes, including the bundled spellings.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "all_groups", "all_hosts"),
    [
        ([], False, False),
        (["--all-groups"], True, False),
        (["-g"], True, False),
        (["--all-hosts"], False, True),
        (["-a"], False, True),
        (["-a", "-g"], True, True),
        (["-ag"], True, True),
        (["-ga"], True, True),
        (["--all-groups", "--all-hosts"], True, True),
    ],
)
def test_each_widening_axis_is_read_from_every_spelling(
    argv: list[str], all_groups: bool, all_hosts: bool
) -> None:
    """Long, short, and bundled short spellings all reach the same two answers.

    The bundled cases are the ones worth having: a single ``-ag`` token names
    both axes, and reading only one of them would silently narrow the answer.
    """
    parsed, _rest = read_router_options("list", argv)
    assert (parsed.all_groups, parsed.all_hosts) == (all_groups, all_hosts)


def test_a_bundle_that_is_not_wholly_camps_own_flags_is_left_alone() -> None:
    """``-la`` is not camp's ``-l`` plus camp's ``-a`` — camp has no ``-l``.

    It must pass through WHOLE, so the verb it was aimed at refuses it as the
    unknown flag it is, rather than the router silently harvesting an ``-a``
    out of the middle of somebody else's token.
    """
    parsed, rest = read_router_options("list", ["-la", "--json"])
    assert (parsed.all_groups, parsed.all_hosts) == (False, False)
    assert rest == ["-la", "--json"]


# ---------------------------------------------------------------------------
# --host: absent, named, and present-but-valueless are three different answers.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], None),
        (["--host", "andromeda"], "andromeda"),
        (["--host=andromeda"], "andromeda"),
        (["--host"], ""),
        (["--host="], ""),
    ],
)
def test_host_distinguishes_absent_from_valueless(argv: list[str], expected) -> None:
    """None means "not asked for"; "" means "asked for, with no name given".

    `main()` needs both: a verb ``--host`` does not apply to is refused for THAT
    reason even when the value is also missing, and the missing value is only
    reported for a verb where a value would have been accepted.
    """
    parsed, _rest = read_router_options("list", argv)
    assert parsed.host == expected


def test_host_never_swallows_a_following_flag_as_its_name() -> None:
    """``--host --group g`` names no host — a host name never starts with a dash.

    Reading ``--group`` as the host name would both invent a host and silence the
    collision refusal that the ``--group`` was supposed to trigger.
    """
    parsed, _rest = read_router_options("list", ["--host", "--group", "g"])
    assert parsed.host == ""
    assert read_group_option(["--host", "--group", "g"]) == "g"


# ---------------------------------------------------------------------------
# Everything else passes through, in order.
# ---------------------------------------------------------------------------


def test_a_verbs_own_flags_pass_through_in_order() -> None:
    """The router owns four options; every other token reaches the handler
    untouched and in the order it was typed."""
    parsed, rest = read_router_options(
        "sessions", ["-g", "--dir", "/some/path", "--json", "myslug"]
    )
    assert parsed.all_groups is True
    assert rest == ["--dir", "/some/path", "--json", "myslug"]


def test_group_is_left_in_the_rest_for_the_handler() -> None:
    """``--group`` is READ by the router but not consumed.

    `camp launch --host h --group g` forwards the group name across to the far
    side; a router that consumed it here would leave the handler with nothing to
    forward. This is the one router-visible option that must survive the read.
    """
    _parsed, rest = read_router_options("launch", ["--host", "h", "--group", "g"])
    assert rest == ["--group", "g"]


# ---------------------------------------------------------------------------
# read_group_option — presence, value, and the valueless case.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], None),
        (["--json"], None),
        (["--group", "trailhead"], "trailhead"),
        (["--group=trailhead"], "trailhead"),
        (["--group"], ""),
    ],
)
def test_group_option_reads_presence_and_value(argv: list[str], expected) -> None:
    """A valueless ``--group`` still reads as PRESENT ("") rather than absent
    (None), so a collision refusal fires on it instead of the invocation
    silently dispatching as though no group had been named."""
    assert read_group_option(argv) == expected


def test_group_option_does_not_mutate_its_input() -> None:
    """The caller goes on to forward this exact list to a handler."""
    argv = ["--group", "trailhead", "--json"]
    read_group_option(argv)
    assert argv == ["--group", "trailhead", "--json"]
