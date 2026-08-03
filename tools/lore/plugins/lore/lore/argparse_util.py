"""Argparse-introspection helpers shared by the CLI dispatcher and the config
layer's command-reference generator.

Deliberately dependency-free — no imports of ``lore.cli`` or ``lore.config`` —
so either side can import this module without creating a cycle.
"""
from __future__ import annotations

import argparse


def find_subparsers_action(
    parser: argparse.ArgumentParser,
) -> "argparse._SubParsersAction | None":
    """Return the one ``_SubParsersAction`` among *parser*'s actions, or None."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


# The two-depth shape `_leaf_parsers` covers: `search` is a leaf in its own
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
