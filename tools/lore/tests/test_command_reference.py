"""Tests for the generated agent-facing command reference.

``lore.config.command_reference.build_reference`` walks an injected
``argparse.ArgumentParser`` (never the CLI module itself — the parser is
supplied by the caller) and renders a compact reference for the small set of
commands agents actually invoke directly: ``search``, the four ``record``
actions, and the three ``session`` actions. Everything else (``init``,
``status``, ``sync``, ``flush``, ``areas``, ``reindex``, ``vault``, ``task``)
is an operational surface an agent is not expected to drive directly and must
never appear.

These tests exercise the traversal and rendering against the REAL parser
(``lore.cli.dispatch.build_parser``) rather than a hand-built stand-in, so a
change to any covered subcommand's flags is caught here.
"""
from __future__ import annotations

import argparse

import pytest
from lore.argparse_util import find_subparsers_action
from lore.cli.dispatch import build_parser
from lore.config import command_reference as cr


# ---------------------------------------------------------------------------
# Traversal: reaches exactly the 8 covered leaves, at both tree depths.
# ---------------------------------------------------------------------------

def test_traversal_reaches_exactly_the_8_covered_leaves():
    parser = build_parser()
    leaves = cr._leaf_parsers(parser)

    expected = {
        "search",
        "record create", "record update", "record delete", "record show",
        "session candidate", "session referenced", "session show",
    }
    assert set(leaves) == expected

    operational_verbs = {"init", "status", "sync", "flush", "areas", "reindex", "vault", "task"}
    assert not (operational_verbs & set(leaves))


def test_mixed_depth_both_shapes_are_reachable():
    parser = build_parser()
    leaves = cr._leaf_parsers(parser)

    # Top-level shape: `search` is a direct leaf of the root parser.
    assert isinstance(leaves["search"], argparse.ArgumentParser)
    # Nested shape: `record`/`session` leaves live one level under a group.
    assert isinstance(leaves["record create"], argparse.ArgumentParser)
    assert isinstance(leaves["session candidate"], argparse.ArgumentParser)


# ---------------------------------------------------------------------------
# Surface-growth guard: an uncurated new leaf under record/session must be
# caught here rather than silently missing from the rendered reference.
# ---------------------------------------------------------------------------

def test_no_uncurated_leaves_exist_under_record_or_session():
    parser = build_parser()
    top_action = find_subparsers_action(parser)

    curated = {
        "record": {"create", "update", "delete", "show"},
        "session": {"candidate", "referenced", "show"},
    }
    for group, known in curated.items():
        group_parser = top_action.choices[group]
        nested_action = find_subparsers_action(group_parser)
        actual = set(nested_action.choices)
        assert actual == known, (
            f"'{group}' subcommands changed: {actual - known!r} are uncurated — "
            f"add them to lore.config.command_reference before this can pass"
        )


# ---------------------------------------------------------------------------
# Required-args derivation matches introspection exactly.
# ---------------------------------------------------------------------------

def test_required_args_search_positional_query():
    parser = build_parser()
    leaf = cr._leaf_parsers(parser)["search"]
    assert cr._required_args(leaf) == ["<query>"]


def test_required_args_record_create_kind_and_title():
    parser = build_parser()
    leaf = cr._leaf_parsers(parser)["record create"]
    assert cr._required_args(leaf) == ["--kind KIND", "--title TITLE"]


def test_required_args_record_delete_positional_record_id():
    parser = build_parser()
    leaf = cr._leaf_parsers(parser)["record delete"]
    assert cr._required_args(leaf) == ["<RECORD_ID>"]


def test_required_args_session_candidate_kind_and_phase():
    parser = build_parser()
    leaf = cr._leaf_parsers(parser)["session candidate"]
    assert cr._required_args(leaf) == ["--kind KIND", "--phase PHASE"]


def test_required_args_session_show_has_none():
    parser = build_parser()
    leaf = cr._leaf_parsers(parser)["session show"]
    assert cr._required_args(leaf) == []


# ---------------------------------------------------------------------------
# Curated extras: every flag named in _LEAF_SPECS must exist on its leaf's
# live parser — a bogus curated flag is a bug, not a silent omission.
# ---------------------------------------------------------------------------

_ALL_CURATED_EXTRAS = [
    (name, flag)
    for name, spec in cr._LEAF_SPECS.items()
    for flag in spec.extras
]


@pytest.mark.parametrize("leaf_name,flag", _ALL_CURATED_EXTRAS)
def test_curated_extra_flag_exists_on_its_leaf(leaf_name, flag):
    parser = build_parser()
    leaf = cr._leaf_parsers(parser)[leaf_name]
    all_flags = {opt for action in leaf._actions for opt in action.option_strings}
    assert flag in all_flags


def test_bogus_curated_extra_flag_is_rejected():
    leaf_parser = argparse.ArgumentParser()
    leaf_parser.add_argument("--real", action="store_true")
    with pytest.raises(ValueError):
        cr._extra_action_descriptor(leaf_parser, "--not-a-real-flag")


# ---------------------------------------------------------------------------
# Compactness: no --unset-* mirror flags or other routing noise in extras.
# ---------------------------------------------------------------------------

def test_no_unset_mirror_flags_in_curated_extras():
    for spec in cr._LEAF_SPECS.values():
        for flag in spec.extras:
            assert not flag.startswith("--unset-")


def test_no_scope_routing_flags_in_curated_extras():
    scope_flags = {"--repo", "--product", "--suite", "--team"}
    for spec in cr._LEAF_SPECS.values():
        assert not (scope_flags & set(spec.extras))


# ---------------------------------------------------------------------------
# Stdin-body hints: session candidate, record create, record update all read
# their body from stdin as a convention rather than an argparse argument.
# ---------------------------------------------------------------------------

def test_stdin_hint_present_for_session_candidate():
    assert cr._LEAF_SPECS["session candidate"].stdin_hint is not None


def test_stdin_hint_present_for_record_create():
    assert cr._LEAF_SPECS["record create"].stdin_hint is not None


def test_stdin_hint_present_for_record_update():
    assert cr._LEAF_SPECS["record update"].stdin_hint is not None


def test_record_update_stdin_hint_enumerates_its_three_body_modes():
    hint = cr._LEAF_SPECS["record update"].stdin_hint
    assert "full-replace" in hint or "default" in hint
    assert "--diff" in hint
    assert "metadata-only" in hint


def test_rendered_output_carries_all_three_stdin_hints():
    parser = build_parser()
    text = cr.build_reference(parser)
    # One rendered block per stdin-bearing leaf; each carries its own hint text.
    assert text.count("stdin:") == 3


# ---------------------------------------------------------------------------
# SUPPRESS'd actions never leak into the rendered block.
# ---------------------------------------------------------------------------

def test_suppressed_routing_flags_excluded_from_rendered_output():
    parser = build_parser()
    text = cr.build_reference(parser)
    assert "--repo" not in text
    assert "--product" not in text
    assert "--suite" not in text
    assert "--team" not in text


# ---------------------------------------------------------------------------
# Block header carries the explicit --help fallback line.
# ---------------------------------------------------------------------------

def test_header_includes_help_fallback_line():
    parser = build_parser()
    text = cr.build_reference(parser)
    assert "lore <verb> --help" in text


# ---------------------------------------------------------------------------
# Determinism: repeated renders are byte-identical.
# ---------------------------------------------------------------------------

def test_determinism_same_parser_instance():
    parser = build_parser()
    assert cr.build_reference(parser) == cr.build_reference(parser)


def test_determinism_fresh_parser_instances():
    assert cr.build_reference(build_parser()) == cr.build_reference(build_parser())


# ---------------------------------------------------------------------------
# End-to-end sanity: every covered leaf renders its full command name.
# ---------------------------------------------------------------------------

def test_every_leaf_command_name_appears_in_output():
    parser = build_parser()
    text = cr.build_reference(parser)
    for name in (
        "search", "record create", "record update", "record delete", "record show",
        "session candidate", "session referenced", "session show",
    ):
        assert f"lore {name}" in text
