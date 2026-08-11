"""Direct coverage for lore.argparse_util._leaf_parsers.

command_reference.py (the previous sole consumer besides the test suite) was
deleted; this file keeps _leaf_parsers under direct test against the real CLI
parser plus a synthetic-parser check of its ValueError branches.
"""
from __future__ import annotations

import argparse

import pytest

from lore.argparse_util import _leaf_parsers
from lore.cli.dispatch import build_parser


def test_leaf_parsers_covers_real_cli_leaves():
    parser = build_parser()
    leaves = _leaf_parsers(parser)

    assert set(leaves) == {
        "search",
        "record create",
        "record delete",
        "record rename",
        "record show",
        "record update",
        "session candidate",
        "session referenced",
        "session show",
    }
    assert all(isinstance(leaf, argparse.ArgumentParser) for leaf in leaves.values())


def test_leaf_parsers_raises_when_top_level_leaf_missing():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    subparsers.add_parser("record")
    subparsers.add_parser("session")

    with pytest.raises(ValueError, match="search"):
        _leaf_parsers(parser)


def test_leaf_parsers_raises_when_expand_group_missing():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    subparsers.add_parser("search")
    record_parser = subparsers.add_parser("record")
    record_parser.add_subparsers()
    # "session" group is missing entirely.

    with pytest.raises(ValueError, match="session"):
        _leaf_parsers(parser)


def test_leaf_parsers_raises_when_group_has_no_nested_subparsers():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    subparsers.add_parser("search")
    record_parser = subparsers.add_parser("record")
    record_parser.add_subparsers()
    subparsers.add_parser("session")
    # session parser has no nested subparsers action of its own.

    with pytest.raises(ValueError, match="session.*nested subparsers"):
        _leaf_parsers(parser)
