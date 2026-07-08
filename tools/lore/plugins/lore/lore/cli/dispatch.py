"""The `lore` CLI — the single entry point for reading and writing the vault.

Capture, search, session bookkeeping, status guarding, and vault scaffolding all
live behind subcommands here. Run ``lore --help`` (or ``lore <cmd> --help``) for
the authoritative, self-describing subcommand list — this docstring deliberately
does not restate it, so the two can't drift.
"""
from __future__ import annotations

import argparse
import sys

from . import areas, flush, init, record, search, session, sync, task, vault


# Table mapping removed/renamed commands to their replacements.
# The ``recall`` command was retired and its call sites rewired to
# ``search``; this entry redirects an agent that still types the old command to
# its replacement so the retired ``recall`` subcommand resolves to a clear
# non-zero error with a "did you mean 'lore search'?" hint, never a silent no-op.
_DISPATCH_HINTS: dict[str, str] = {
    "recall": "search",
    "set-status": "record update --status",
}


def _unknown_command_hint(command: str) -> str:
    """Return a 'did you mean …?' hint for an unrecognized command, or an empty string."""
    if command in _DISPATCH_HINTS:
        replacement = _DISPATCH_HINTS[command]
        return f"did you mean 'lore {replacement}'?"
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lore", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    # Registration order determines the ``--help`` subcommand listing; it mirrors
    # the historical single-file ``build_parser`` (init, status, sync, flush,
    # areas, reindex, search, record, task, vault, session).
    init.add_init_subparsers(sub)
    sync.add_sync_subparser(sub)
    flush.add_flush_subparser(sub)
    areas.add_areas_subparsers(sub)
    search.add_search_subparser(sub)
    record.add_record_subparser(sub)
    task.add_task_subparser(sub)
    vault.add_vault_subparser(sub)
    session.add_session_subparser(sub)

    return parser


def _known_commands(parser: argparse.ArgumentParser) -> frozenset[str]:
    """Return the set of registered top-level subcommand names.

    Introspects the parser's ``_SubParsersAction`` so the unknown-command hint
    can tell a genuinely unrecognized command from a *valid* command
    that merely failed on a sub-argument (e.g. ``record create`` missing
    ``--kind``) — the latter must NOT be mislabelled "unknown command".
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return frozenset(action.choices)
    return frozenset()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # Intercept argparse's SystemExit so we can emit the ``did you mean …?``
    # hint before re-raising. argparse calls sys.exit(2) on an
    # unrecognized command, so we catch that here.
    raw = argv if argv is not None else sys.argv[1:]
    try:
        args = parser.parse_args(list(raw))
    except SystemExit as exc:
        cmd = raw[0] if raw else ""
        # Only treat this as an unknown command when the first token is not a
        # flag and not a registered subcommand. A valid command that fails on a
        # sub-argument re-raises argparse's own error untouched (do
        # not swallow / mislabel legitimate argparse errors).
        if exc.code == 2 and cmd and not cmd.startswith("-") and cmd not in _known_commands(parser):
            hint = _unknown_command_hint(cmd)
            if hint:
                print(f"lore: unknown command {cmd!r}. {hint}", file=sys.stderr)
            else:
                # For any unrecognized command, print a generic prompt so agents
                # can distinguish a typo from a real error.
                print(
                    f"lore: unknown command {cmd!r}. "
                    f"Run 'lore --help' for a list of subcommands.",
                    file=sys.stderr,
                )
        raise
    return args.func(args)
