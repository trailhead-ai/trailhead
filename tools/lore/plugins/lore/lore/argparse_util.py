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
