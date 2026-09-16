"""The one ``argparse.ArgumentParser`` subclass every camp verb parses through.

camp's error hygiene is older than its use of argparse and does not match it. A
camp refusal is a single line — ``camp <verb>: <message>`` on stderr — and exits
1. argparse's is a ``usage:`` block followed by ``<prog>: error: <message>``,
and exits 2. `CampParser` reconciles the two so a verb gains argparse's
tokenizing without any caller seeing argparse's voice.

Three things it changes about stock argparse, each load-bearing:

``allow_abbrev`` is off.
    argparse accepts any unambiguous prefix of a long option by default, so
    declaring ``--group`` would silently also declare ``--gro``, ``--gr`` and
    ``--g``. That is a surface camp never chose, cannot document, and could not
    narrow later without breaking whoever found it. A flag is spelled the one
    way it is declared.

``add_help`` is off.
    ``-h``/``--help`` belong to camp's own top-level help, which is a grouped
    menu rather than an argparse dump. A verb parser that quietly claimed ``-h``
    would shadow it.

Surplus tokens are refused one at a time, by kind.
    argparse reports every leftover in one ``unrecognized arguments:`` line and
    does not distinguish a flag it does not know from a positional it has no
    slot for. camp words those differently — ``unknown flag`` names something
    the verb does not accept, ``unexpected argument`` names something it accepts
    too few of — so the first surplus token is classified here and reported
    alone. Reporting the first is deliberate: it is the one the operator has to
    fix before anything downstream can be diagnosed.

Messages argparse produces that camp has no wording for pass through verbatim
under the ``camp <verb>:`` prefix, so a parser feature adopted later degrades to
a legible line rather than to argparse's voice.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import NoReturn

#: argparse's "this option wanted a value and did not get one" message, in the
#: two shapes it takes (``expected one argument`` for a plain valued flag,
#: ``expected N arguments`` for an ``nargs=N`` one). The option is rendered by
#: argparse as every spelling joined with "/" (``-g/--group``); camp names the
#: long form, which is the one its messages and docs use.
_EXPECTED_ARGUMENTS = re.compile(
    r"^argument (?P<option>[^:]+): expected (?:one|\d+) arguments?$"
)


class CampParser(argparse.ArgumentParser):
    """An ``ArgumentParser`` that refuses in camp's voice.

    *verb* is the command as the operator types it (``launch``, ``transfer
    probe``) and becomes the prefix on every refusal this parser emits. It is
    the only required argument; everything else is stock argparse.
    """

    def __init__(self, verb: str, **kwargs) -> None:
        kwargs.setdefault("prog", f"camp {verb}")
        kwargs.setdefault("add_help", False)
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(**kwargs)
        self.verb = verb

    # -- refusal -----------------------------------------------------------

    def die(self, message: str) -> NoReturn:
        """Emit ``camp <verb>: <message>`` on stderr and exit 1.

        The single exit point for every refusal this parser makes, so a verb's
        own validation can refuse in the identical shape by calling it rather
        than reproducing the prefix.
        """
        print(f"camp {self.verb}: {message}", file=sys.stderr)
        sys.exit(1)

    def error(self, message: str) -> NoReturn:
        """Translate argparse's refusal into camp's, then exit.

        Overriding this is what suppresses the ``usage:`` block and the
        ``prog: error:`` prefix — neither is ever rendered, so neither can leak
        argparse's formatting (or, on 3.14, its colour) into camp's output.
        """
        self.die(self._camp_message(message))

    def _camp_message(self, message: str) -> str:
        """Return *message* in camp's wording, or unchanged if camp has none."""
        expected = _EXPECTED_ARGUMENTS.match(message)
        if expected:
            option = expected.group("option").split("/")[-1]
            shape = self._declared_metavar(option)
            return f"{option} requires a {shape} value" if shape else f"{option} requires a value"
        return message

    def _declared_metavar(self, option: str) -> str | None:
        """Return the metavar *option* was declared with, or None if it had none.

        Only an explicitly declared metavar counts. argparse derives a
        placeholder from the destination when none is given (``--member`` →
        ``MEMBER``), which names a variable the operator never typed and reads
        as noise in a refusal; the generic wording is better than that.
        """
        for action in self._actions:
            if option in action.option_strings:
                return action.metavar if isinstance(action.metavar, str) else None
        return None

    def _refuse_surplus(self, token: str) -> NoReturn:
        """Refuse the first token the parse had no home for, classified by kind."""
        if token.startswith("-"):
            self.die(f"unknown flag {token!r}")
        self.die(f"unexpected argument {token!r}")

    # -- parsing -----------------------------------------------------------

    def parse_args(self, args=None, namespace=None):  # type: ignore[override]
        """Parse *args*, refusing the first surplus token in camp's wording.

        Stock ``parse_args`` funnels every leftover into one
        ``unrecognized arguments:`` line, which loses both the distinction
        between a flag and a positional and the order they appeared in. Going
        through ``parse_known_args`` keeps both.
        """
        parsed, surplus = self.parse_known_args(args, namespace)
        if surplus:
            self._refuse_surplus(surplus[0])
        return parsed


def group_verb_parser(verb: str, *, dry_run: bool = False) -> CampParser:
    """A `CampParser` for a verb reached through the group-aware router.

    The router in ``cli/dispatch.py`` resolves ``--group`` to a config object
    before dispatch, but forwards the invocation's argv WHOLE to the handler it
    chose. A leaf that declared only its own flags would therefore refuse the
    router's, so every group-aware verb declares ``--group`` here — parsed,
    available, and ignored. The group a handler acts on is the resolved object
    it was handed, never a name re-read from argv, because re-reading it is how
    the two could come to disagree.

    ``--dry-run`` rides along the same way but is opt-in: the router forwards it
    to every verb while it only means anything on the ones that change state.
    A verb that passes *dry_run* declares it and honours it; every other verb
    refuses it, which is the honest answer for a flag that would otherwise be
    silently ignored.
    """
    parser = CampParser(verb=verb)
    parser.add_argument("--group")
    if dry_run:
        parser.add_argument("--dry-run", action="store_true")
    return parser
