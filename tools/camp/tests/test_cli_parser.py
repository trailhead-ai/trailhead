"""Test contract: `camp.cli.parser.CampParser` — the one argparse subclass every
camp verb parses through.

camp's error hygiene predates argparse here: a refusal is a single
``camp <verb>: <message>`` line on stderr with exit 1, never argparse's
``usage:`` block, never ``prog: error: …``, and never exit 2. `CampParser` is
what reconciles the two, so these tests pin the reconciliation rather than
argparse's own well-tested behavior:

- the refusal WORDING and exit code for the three ways an operator gets an
  invocation wrong (unknown flag, unexpected positional, missing value);
- that an unknown flag is refused at all, which is the behavior camp's
  hand-rolled readers never had;
- that abbreviations are NOT accepted, because argparse's default would
  silently widen every flag camp owns into a set of prefixes.

Each test varies the input across the branch that decides, so it can go red for
a logic change rather than only for a reworded constant.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.cli.parser import CampParser  # noqa: E402


def _launch_parser() -> CampParser:
    """A parser shaped like a real camp verb: one valued flag, one boolean, one
    optional positional. The shape every leaf handler below is migrating to."""
    parser = CampParser(verb="launch")
    parser.add_argument("--group")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("slug", nargs="?")
    return parser


# ---------------------------------------------------------------------------
# The happy path — the parser must actually parse before its refusals matter.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected_group", "expected_slug", "expected_json"),
    [
        ([], None, None, False),
        (["--group", "trailhead"], "trailhead", None, False),
        (["--group=trailhead"], "trailhead", None, False),
        (["myslug"], None, "myslug", False),
        (["--json", "myslug"], None, "myslug", True),
        (["--group", "trailhead", "--json", "myslug"], "trailhead", "myslug", True),
    ],
)
def test_parses_both_flag_spellings_and_positionals(
    argv: list[str], expected_group: str | None, expected_slug: str | None, expected_json: bool
) -> None:
    """Both ``--flag value`` and ``--flag=value`` reach the same attribute, and a
    positional survives alongside them in any order."""
    args = _launch_parser().parse_args(argv)
    assert (args.group, args.slug, args.json) == (expected_group, expected_slug, expected_json)


# ---------------------------------------------------------------------------
# Refusal 1 — an unknown flag. The behavior camp's hand-rolled readers lacked.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--bogus", "--all-groups", "-x"])
def test_unknown_flag_is_refused_naming_that_flag(flag: str, capsys) -> None:
    """An undeclared flag refuses, and the message names the flag the operator
    actually typed — varying the flag varies the message."""
    with pytest.raises(SystemExit) as exc:
        _launch_parser().parse_args([flag])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err == f"camp launch: unknown flag {flag!r}\n"


def test_declared_flag_is_not_refused(capsys) -> None:
    """The mirror of the case above: a flag the parser declares parses silently.

    Without this, `test_unknown_flag_is_refused_naming_that_flag` would still
    pass against a parser that refused everything.
    """
    args = _launch_parser().parse_args(["--json"])
    assert args.json is True
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# Refusal 2 — a positional the verb has no slot for.
# ---------------------------------------------------------------------------


def test_extra_positional_is_refused_as_unexpected_argument(capsys) -> None:
    """A second positional has no slot, and refuses as an argument rather than as
    a flag — the two refusals are worded differently on purpose."""
    with pytest.raises(SystemExit) as exc:
        _launch_parser().parse_args(["one", "two"])
    assert exc.value.code == 1
    assert capsys.readouterr().err == "camp launch: unexpected argument 'two'\n"


# ---------------------------------------------------------------------------
# Refusal 3 — a valued flag with nothing after it.
# ---------------------------------------------------------------------------


def test_valued_flag_without_a_value_is_refused(capsys) -> None:
    """``--group`` as the final token refuses, naming the flag that wanted a value."""
    with pytest.raises(SystemExit) as exc:
        _launch_parser().parse_args(["--group"])
    assert exc.value.code == 1
    assert capsys.readouterr().err == "camp launch: --group requires a value\n"


def test_valued_flag_swallows_no_following_flag(capsys) -> None:
    """``--group --json`` must refuse rather than take ``--json`` as the group name.

    argparse already declines to consume a following option as a value; this pins
    that camp's wording for it is the missing-value refusal, not a stray
    unknown-flag report about ``--json``.
    """
    with pytest.raises(SystemExit) as exc:
        _launch_parser().parse_args(["--group", "--json"])
    assert exc.value.code == 1
    assert capsys.readouterr().err == "camp launch: --group requires a value\n"


# ---------------------------------------------------------------------------
# Abbreviations stay off.
# ---------------------------------------------------------------------------


def test_flag_abbreviation_is_not_accepted(capsys) -> None:
    """``--gro`` is an unknown flag, not a shorthand for ``--group``.

    argparse's default (`allow_abbrev=True`) would accept it, silently widening
    every camp flag into all of its unambiguous prefixes — a surface camp never
    declared and could not later narrow without breaking whoever found it.
    """
    with pytest.raises(SystemExit) as exc:
        _launch_parser().parse_args(["--gro", "trailhead"])
    assert exc.value.code == 1
    assert capsys.readouterr().err == "camp launch: unknown flag '--gro'\n"


# ---------------------------------------------------------------------------
# The refusal shape itself — no usage block, no argparse prefix, no traceback.
# ---------------------------------------------------------------------------


def test_refusal_is_one_camp_line_with_no_usage_block(capsys) -> None:
    """argparse's default refusal prints a ``usage:`` block and ``prog: error:``.

    camp prints exactly one line. This is the seam's whole reason to exist, so it
    is pinned directly against the alternative it replaces.
    """
    with pytest.raises(SystemExit):
        _launch_parser().parse_args(["--bogus"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" not in captured.err
    assert "error:" not in captured.err
    assert len(captured.err.splitlines()) == 1


def test_verb_names_the_refusal(capsys) -> None:
    """The prefix tracks the verb the parser was built for — varying the verb
    varies every refusal it emits."""
    parser = CampParser(verb="sessions")
    parser.add_argument("--json", action="store_true")
    with pytest.raises(SystemExit):
        parser.parse_args(["--bogus"])
    assert capsys.readouterr().err == "camp sessions: unknown flag '--bogus'\n"


# ---------------------------------------------------------------------------
# A declared metavar sharpens the missing-value refusal.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metavar", "expected"),
    [
        ("NAME=PATH", "camp group: --member requires a NAME=PATH value\n"),
        ("REF", "camp group: --member requires a REF value\n"),
        (None, "camp group: --member requires a value\n"),
    ],
)
def test_missing_value_refusal_names_the_declared_metavar(
    metavar: str | None, expected: str, capsys
) -> None:
    """A flag whose value has a shape worth naming declares it as a metavar, and
    the missing-value refusal quotes that shape back.

    Without a metavar the refusal stays generic rather than falling back to
    argparse's derived placeholder (``MEMBER``), which reads as a variable name
    the operator never typed.
    """
    parser = CampParser(verb="group")
    parser.add_argument("--member", action="append", metavar=metavar)
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--member"])
    assert exc.value.code == 1
    assert capsys.readouterr().err == expected


# ---------------------------------------------------------------------------
# group_verb_parser — the flags the router forwards but the leaf does not own.
# ---------------------------------------------------------------------------


def test_group_verb_parser_tolerates_the_forwarded_group_flag() -> None:
    """`main()` resolves `--group` and then forwards argv WHOLE to the leaf, so a
    leaf that did not declare it would refuse the router's own flag.

    The value is parsed and available, but leaves ignore it: the group they act
    on is the resolved object the router hands them, never a name re-read here.
    """
    from camp.cli.parser import group_verb_parser

    parser = group_verb_parser("list")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(["--group", "trailhead", "--json"])
    assert (args.group, args.json) == ("trailhead", True)


def test_group_verb_parser_refuses_dry_run_unless_the_verb_declares_it() -> None:
    """`--dry-run` is forwarded to every verb but only means something on the ones
    that change state, so a read-only verb refuses it rather than ignoring it.

    This is the pair that shows the flag is opt-in: same argv, two parsers, two
    answers.
    """
    from camp.cli.parser import group_verb_parser

    stateful = group_verb_parser("remove", dry_run=True)
    assert stateful.parse_args(["--dry-run"]).dry_run is True

    readonly = group_verb_parser("list")
    with pytest.raises(SystemExit) as exc:
        readonly.parse_args(["--dry-run"])
    assert exc.value.code == 1


def test_group_verb_parser_still_refuses_an_unknown_flag(capsys) -> None:
    """Tolerating the router's flags widens the surface by exactly those flags —
    everything else is still refused."""
    from camp.cli.parser import group_verb_parser

    with pytest.raises(SystemExit):
        group_verb_parser("list").parse_args(["--bogus"])
    assert capsys.readouterr().err == "camp list: unknown flag '--bogus'\n"


# ---------------------------------------------------------------------------
# The two surfaces camp takes back from argparse: -h, and which surplus token
# gets named.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_flags_are_not_claimed_by_a_verb_parser(flag: str, capsys) -> None:
    """``-h``/``--help`` belong to camp's own top-level help, which is a grouped
    menu rather than an argparse dump.

    Stock argparse would claim both, print its own usage, and exit 0 — silently
    shadowing the help camp actually wrote. A verb parser must instead treat
    them as flags it does not declare, which is what reaches camp's help.
    """
    with pytest.raises(SystemExit) as exc:
        _launch_parser().parse_args([flag])
    assert exc.value.code == 1
    assert capsys.readouterr().err == f"camp launch: unknown flag {flag!r}\n"


@pytest.mark.parametrize(
    ("argv", "named"),
    [
        (["--alpha", "--beta"], "--alpha"),
        (["--beta", "--alpha"], "--beta"),
        (["--zeta", "extra", "--yota"], "--zeta"),
    ],
)
def test_the_first_surplus_token_is_the_one_reported(
    argv: list[str], named: str, capsys
) -> None:
    """Several tokens can be surplus at once; the refusal names the FIRST.

    It is the one the operator has to fix before anything downstream can be
    diagnosed, and reordering the same tokens moves which one is named — so the
    choice is positional, not a property of the token itself.
    """
    with pytest.raises(SystemExit):
        _launch_parser().parse_args(argv)
    assert capsys.readouterr().err == f"camp launch: unknown flag {named!r}\n"
