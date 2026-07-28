"""Generates a compact invocation reference for the small set of ``lore``
subcommands an agent is expected to drive directly.

``build_reference(parser)`` takes an already-built ``argparse.ArgumentParser``
(the CLI's own ``lore.cli.dispatch.build_parser()`` output) and renders one
block per covered leaf: the exact command name, its mechanically-derived
required arguments (never hand-maintained — read straight off the parser so
it can't drift), a purpose one-liner, a thin curated list of common optional
flags, an optional one-clause hint disambiguating two easily-confused curated
flags, and — where the command reads its payload from stdin rather than an
argparse argument — a stdin hint.

This module never imports the CLI package itself. The parser is always
supplied by the caller, both so the function is trivially unit-testable
against a real or fabricated parser, and so this config-layer module never
creates an import cycle back into ``lore.cli``.

Coverage is deliberately narrow: ``search`` (a direct top-level command) plus
the ``record`` and ``session`` command groups, expanded one level to their
``create``/``update``/``delete``/``show`` and ``candidate``/``referenced``/
``show`` actions respectively. Every other top-level command (``init``,
``status``, ``sync``, ``flush``, ``areas``, ``reindex``, ``vault``, ``task``)
is an operational surface, not something an agent is expected to invoke
directly, and is intentionally excluded — including the ``vault``/``task``
groups, which *also* nest their own subcommands and would otherwise leak in
under a naive "recurse into any group with nested subcommands" walk.

Visual convention: a required argument renders bare (a positional as
``<NAME>``, a required flag as ``--flag VALUE``); a curated optional flag
renders bracketed (``[--flag VALUE]``). This is the one marker used
throughout, so a reader can tell required from optional at a glance without
re-deriving it per leaf.

``argparse.SUPPRESS``-marked actions (hidden routing flags on a couple of the
``record`` leaves) are excluded everywhere: from required-args derivation and
from the curated extras, so a hidden flag can never leak into the rendered
block even if a future edit mistakenly marks it required.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from ..argparse_util import find_subparsers_action

# The two-depth shape this generator covers: `search` is a leaf in its own
# right; `record`/`session` are groups whose own nested subparsers hold the
# real leaves. Curated by name (not derived structurally) because `vault` and
# `task` also nest their own subparsers and must NOT be swept in.
_TOP_LEVEL_LEAF = "search"
_EXPAND_GROUPS = ("record", "session")


def _leaf_parsers(parser: argparse.ArgumentParser) -> "dict[str, argparse.ArgumentParser]":
    """Return ``{"search": ..., "record create": ..., ...}`` for the covered leaves.

    Raises ``ValueError`` if the expected top-level or nested subparsers
    structure is missing — a caller must never receive a silently-partial map.
    """
    top_action = find_subparsers_action(parser)
    if top_action is None:
        raise ValueError("parser has no top-level subparsers action")
    if _TOP_LEVEL_LEAF not in top_action.choices:
        raise ValueError(f"parser has no {_TOP_LEVEL_LEAF!r} top-level command")

    leaves: dict[str, argparse.ArgumentParser] = {
        _TOP_LEVEL_LEAF: top_action.choices[_TOP_LEVEL_LEAF],
    }
    for group_name in _EXPAND_GROUPS:
        if group_name not in top_action.choices:
            raise ValueError(f"parser has no {group_name!r} top-level command")
        group_parser = top_action.choices[group_name]
        nested_action = find_subparsers_action(group_parser)
        if nested_action is None:
            raise ValueError(f"{group_name!r} subparser has no nested subparsers action")
        for sub_name, sub_leaf in nested_action.choices.items():
            leaves[f"{group_name} {sub_name}"] = sub_leaf
    return leaves


def _is_hidden(action: argparse.Action) -> bool:
    return action.help is argparse.SUPPRESS


def _flag_descriptor(flag: str, action: argparse.Action) -> str:
    """Render a bare (unbracketed) ``--flag`` or ``--flag VALUE``.

    ``--flag`` alone when the action takes no value (``nargs == 0``), else
    ``--flag VALUE`` using its metavar, falling back to its dest upper-cased.
    """
    if action.nargs == 0:
        return flag
    token = (action.metavar or action.dest).upper()
    return f"{flag} {token}"


def _required_action_descriptor(action: argparse.Action) -> str:
    """Render one required action as a bare (unbracketed) token.

    A positional (no ``option_strings``) becomes ``<TOKEN>`` using its metavar,
    falling back to its dest — not every positional sets an explicit metavar.
    A required flag renders via ``_flag_descriptor``.
    """
    if not action.option_strings:
        token = action.metavar or action.dest
        return f"<{token}>"
    return _flag_descriptor(action.option_strings[0], action)


def _required_args(leaf_parser: argparse.ArgumentParser) -> "list[str]":
    """Return the rendered required-argument descriptors for *leaf_parser*.

    Mechanically derived from ``required``/``option_strings``/``metavar`` —
    never hand-maintained — so it can't silently drift from the parser it
    describes. Hidden (``SUPPRESS``) actions are skipped defensively, even
    though none of the currently-hidden flags are required.
    """
    descriptors: list[str] = []
    for action in leaf_parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if _is_hidden(action):
            continue
        if not getattr(action, "required", False):
            continue
        descriptors.append(_required_action_descriptor(action))
    return descriptors


def _extra_action_descriptor(leaf_parser: argparse.ArgumentParser, flag: str) -> str:
    """Render one curated optional flag as ``[--flag]`` or ``[--flag VALUE]``.

    Looks the flag up on the live parser rather than trusting the curated
    string alone, so a curated flag that no longer exists on the leaf raises
    here instead of silently rendering a stale, unusable hint.
    """
    for action in leaf_parser._actions:
        if flag in action.option_strings:
            return f"[{_flag_descriptor(flag, action)}]"
    raise ValueError(f"curated extra flag {flag!r} does not exist on this leaf's parser")


@dataclass(frozen=True)
class _LeafSpec:
    """The curated (hand-authored) layer for one covered leaf.

    ``purpose`` is a one-line description of what the command does.
    ``extras`` is a thin allowlist of the common optional flags worth
    surfacing (existence-validated against the live parser, never a source of
    routing/mirror noise like ``--unset-*`` or the scope-routing flags).
    ``flags_hint`` is set only where two curated flags are easy to confuse and
    need a one-clause disambiguation beyond their bracketed rendering.
    ``stdin_hint`` is set only for the leaves that read their payload from
    stdin as a convention rather than an argparse argument.
    """

    purpose: str
    extras: "tuple[str, ...]" = field(default_factory=tuple)
    flags_hint: "str | None" = None
    stdin_hint: "str | None" = None


#: Display order matches insertion order — dicts preserve it, so there is no
#: separate order list to keep in sync by hand.
_LEAF_SPECS: "dict[str, _LeafSpec]" = {
    "search": _LeafSpec(
        purpose="Query the vault via the KQL-subset search facade.",
        extras=("--json", "--limit"),
    ),
    "record create": _LeafSpec(
        purpose="Create a new vault record.",
        extras=("--status", "--keyword", "--label", "--related"),
        flags_hint="--related links to another record; --label is a free attribute",
        stdin_hint=(
            "the record body, read verbatim — a leading '---' is never parsed "
            "as frontmatter"
        ),
    ),
    "record update": _LeafSpec(
        purpose="Update an existing vault record (a scope flag auto-moves it).",
        extras=("--status", "--title", "--diff", "--related"),
        stdin_hint=(
            "full-replace body (default), a unified diff applied to the "
            "existing body with --diff, or omit stdin entirely for a "
            "metadata-only update (flags only, body left unchanged)"
        ),
    ),
    "record delete": _LeafSpec(
        purpose="Delete a vault record (body + sidecar + index row).",
    ),
    "record show": _LeafSpec(
        purpose="Read a record's body (and sidecar with --json).",
        extras=("--json",),
    ),
    "session candidate": _LeafSpec(
        purpose="Log a record-candidate for the current session (lazy-creates the session).",
        stdin_hint=(
            "the candidate body — no piped stdin silently logs an empty-body "
            "candidate, with no warning"
        ),
    ),
    "session referenced": _LeafSpec(
        purpose="Log that a record was used this session (a no-op if the session doesn't exist).",
        extras=("--session-id", "--worktree"),
    ),
    "session show": _LeafSpec(
        purpose="Read this worktree's current session record.",
        extras=("--json", "--session-id", "--worktree"),
    ),
}


_HEADER = (
    "## Lore command reference (generated)\n"
    "\n"
    "Required arguments for the commands below are mechanically derived from "
    "the CLI itself; for other flags run `lore <verb> --help`.\n"
)


def build_reference(parser: argparse.ArgumentParser) -> str:
    """Render the full command reference block for *parser*.

    Pure and deterministic: calling this twice on the same parser (or on two
    fresh ``build_parser()`` instances) produces byte-identical output, since
    nothing here reads clock, environment, or filesystem state.
    """
    leaves = _leaf_parsers(parser)

    blocks = [_HEADER]
    for name, spec in _LEAF_SPECS.items():
        leaf_parser = leaves[name]

        required = _required_args(leaf_parser)
        invocation = " ".join([f"lore {name}", *required]).rstrip()

        lines = [invocation, f"    {spec.purpose}"]
        if spec.extras:
            rendered_extras = " ".join(
                _extra_action_descriptor(leaf_parser, flag) for flag in spec.extras
            )
            lines.append(f"    optional: {rendered_extras}")
        if spec.flags_hint:
            lines.append(f"    flags: {spec.flags_hint}")
        if spec.stdin_hint:
            lines.append(f"    stdin: {spec.stdin_hint}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks) + "\n"
