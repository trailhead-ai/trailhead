"""The `lore` CLI — the single entry point for reading and writing the vault.

Capture, search, session bookkeeping, status guarding, and vault scaffolding all
live behind subcommands here. Run ``lore --help`` (or ``lore <cmd> --help``) for
the authoritative, self-describing subcommand list — this docstring deliberately
does not restate it, so the two can't drift.
"""
from __future__ import annotations

import argparse
import difflib
import sys

from . import areas, flush, init, pipeline, record, resolve, search, session, sync, task, vault
from ..argparse_util import find_subparsers_action


# Table mapping removed/renamed commands to their replacements.
# The ``recall`` command was retired and its call sites rewired to
# ``search``; this entry redirects an agent that still types the old command to
# its replacement so the retired ``recall`` subcommand resolves to a clear
# non-zero error with a "did you mean 'lore search'?" hint, never a silent no-op.
_DISPATCH_HINTS: dict[str, str] = {
    "recall": "search",
    "set-status": "record update --status",
}


# Nested synonym redirects, keyed by (parent prog, typed token). String
# distance cannot reach these — ``tree``/``graph`` share almost no characters,
# and ``list``/``ls`` scores 0.67, below the cutoff and indistinguishable from
# ``logs``/``ls`` by any generic rule — so, like the top-level rename table,
# they are named explicitly.
_ACTION_HINTS: dict[tuple[str, str], str] = {
    ("lore task", "tree"): "graph",
    ("lore vault", "list"): "ls",
}

# Minimum similarity for a nearest-match suggestion. A suggestion that fires on
# a string nothing resembles is worse than silence, and difflib's ratio is
# generous with words that merely share a prefix: ``remove``/``resolve`` scores
# 0.77, ``start``/``status`` 0.73, ``remove``/``rename`` 0.67. The bar sits
# above that whole band so only a near-identical token suggests anything.
_SUGGESTION_CUTOFF = 0.8


def _choice_names(action: "argparse._SubParsersAction") -> list[str]:
    """Return *action*'s subcommand names, or [] when its choices aren't enumerable."""
    try:
        return list(action.choices)
    except TypeError:
        # ``lore resolve`` accepts any token via a custom choices object.
        return []


def _nearest_choice(token: str, choices: list[str]) -> str:
    """Return the closest of *choices* to *token*, or an empty string."""
    matches = difflib.get_close_matches(token, choices, n=1, cutoff=_SUGGESTION_CUTOFF)
    return matches[0] if matches else ""


def _unknown_command_hint(command: str, choices: list[str]) -> str:
    """Return a 'did you mean …?' hint for an unrecognized top-level command.

    The rename table wins over string distance: ``recall``→``search`` and
    ``set-status``→``record update --status`` are renames no edit distance finds.
    """
    if command in _DISPATCH_HINTS:
        return f"did you mean 'lore {_DISPATCH_HINTS[command]}'?"
    nearest = _nearest_choice(command, choices)
    return f"did you mean '{nearest}'?" if nearest else ""


def _unknown_action_hint(prog: str, action_name: str, choices: list[str]) -> str:
    """Return a 'did you mean …?' hint for an unrecognized nested action."""
    replacement = _ACTION_HINTS.get((prog, action_name)) or _nearest_choice(action_name, choices)
    return f"did you mean '{replacement}'?" if replacement else ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lore", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    # Registration order determines the ``--help`` subcommand listing; it mirrors
    # the historical single-file ``build_parser`` (init, status, sync, flush,
    # resolve, areas, reindex, search, record, task, vault, session).
    init.add_init_subparsers(sub)
    sync.add_sync_subparser(sub)
    flush.add_flush_subparser(sub)
    resolve.add_resolve_subparser(sub)
    areas.add_areas_subparsers(sub)
    search.add_search_subparser(sub)
    record.add_record_subparser(sub)
    task.add_task_subparser(sub)
    pipeline.add_pipeline_subparser(sub)
    vault.add_vault_subparser(sub)
    session.add_session_subparser(sub)

    return parser


def _find_unrecognized_token(
    parser: argparse.ArgumentParser, raw: list[str]
) -> "tuple[str, str, list[str]] | None":
    """Locate the first subcommand token *raw* fails to resolve against *parser*.

    Returns ``(parent prog, token, sibling choices)``, or None when every
    subcommand token resolves. Walking the whole subparser chain — rather than
    looking only at ``raw[0]`` — is what lets the hint reach nested verbs, which
    are where mistyped commands actually land. Returning None for a resolvable
    chain is what keeps a *valid* command that merely failed on a sub-argument
    (``record create`` without ``--kind``) from being labelled unrecognized.
    """
    current = parser
    prog = parser.prog
    for token in raw:
        if token.startswith("-"):
            # An option ends the subcommand chain; the rest belongs to *current*.
            return None
        action = find_subparsers_action(current)
        if action is None:
            return None
        choices = _choice_names(action)
        if not choices:
            # An open-ended choices object (``lore resolve <vault>``) accepts
            # any token, so nothing here is unrecognized.
            return None
        if token in choices:
            current = action.choices[token]
            prog = f"{prog} {token}"
            continue
        return (prog, token, choices)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # Intercept argparse's SystemExit so we can emit the ``did you mean …?``
    # hint before re-raising. argparse calls sys.exit(2) on an
    # unrecognized command, so we catch that here.
    raw = argv if argv is not None else sys.argv[1:]
    try:
        args = parser.parse_args(list(raw))
    except SystemExit as exc:
        unrecognized = _find_unrecognized_token(parser, list(raw)) if exc.code == 2 else None
        if unrecognized is not None:
            prog, token, choices = unrecognized
            if prog == parser.prog:
                hint = _unknown_command_hint(token, choices)
                label = f"{prog}: unknown command {token!r}."
            else:
                hint = _unknown_action_hint(prog, token, choices)
                label = f"{prog}: unrecognized action {token!r}."
            # Without a hint, point at the help for the level the token missed
            # at — the root dump is the largest help in the CLI, and a nested
            # miss only ever needed its own subcommand's list.
            if hint:
                tail = hint
            elif prog == parser.prog:
                tail = "Run 'lore --help' for a list of subcommands."
            else:
                tail = f"Run '{prog} --help' for a list of actions."
            print(f"{label} {tail}", file=sys.stderr)
        raise
    return args.func(args)
