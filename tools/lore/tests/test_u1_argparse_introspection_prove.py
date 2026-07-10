"""EPHEMERAL PROVER ARTIFACT — delete once ``test_command_reference.py``'s real
behavioral tests exist. Not shipped test coverage; a one-time check that an
introspection technique works before anything is built on top of it.

Proves: walking ``build_parser()``'s ``argparse.ArgumentParser`` tree via the
``_SubParsersAction``/``_actions`` private-attribute traversal (the same
technique ``dispatch._known_commands`` already uses) reliably reaches the 8
agent-facing leaf parsers at BOTH tree depths (top-level ``search``; nested
``record {create,update,delete,show}`` and ``session {candidate,referenced,show}``),
and that ``required``/``option_strings``/positional ``metavar`` are readable off
each leaf's ``_actions`` well enough to mechanically derive a required-args
synopsis. Also confirms SUPPRESS'd routing flags are present-but-markable.
"""
from __future__ import annotations

import argparse

from lore.cli.dispatch import build_parser


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    """Same technique as ``dispatch._known_commands``: find the one
    ``_SubParsersAction`` among a parser's ``_actions``."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


#  The 8 agent-facing leaves live at two named entry points: ``search`` is a
# leaf in its own right; ``record``/``session`` are groups whose OWN
# ``_SubParsersAction.choices`` hold the real leaves. Every other top-level
# name (init/status/sync/flush/areas/reindex/vault/task) is an operational verb
# and must be excluded — confirmed NOT by "no nested subparsers" (vault/task
# also nest a sub-action) but by curated name, matching the plan's own framing
# ("extended to recurse one level into the record and session subparsers").
_TOP_LEVEL_LEAF = "search"
_EXPAND_GROUPS = ("record", "session")


def _leaves(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """``_known_commands``-style walk, extended one level into ``record``/
    ``session``. Returns a flat map of ``"search"`` / ``"record create"`` /
    ``"session candidate"`` -> leaf parser — exactly the 8 agent-facing leaves,
    given the curated top-level allowlist above.
    """
    top_action = _subparsers_action(parser)
    assert top_action is not None, "expected a top-level _SubParsersAction"

    out: dict[str, argparse.ArgumentParser] = {}
    out[_TOP_LEVEL_LEAF] = top_action.choices[_TOP_LEVEL_LEAF]
    for group_name in _EXPAND_GROUPS:
        group_parser = top_action.choices[group_name]
        nested_action = _subparsers_action(group_parser)
        assert nested_action is not None, f"{group_name!r} has no nested _SubParsersAction"
        for sub_name, sub_leaf in nested_action.choices.items():
            out[f"{group_name} {sub_name}"] = sub_leaf
    return out


def test_traversal_reaches_exactly_the_8_agent_facing_leaves():
    parser = build_parser()
    leaves = _leaves(parser)

    expected = {
        "search",
        "record create", "record update", "record delete", "record show",
        "session candidate", "session referenced", "session show",
    }
    assert set(leaves) == expected, (
        f"traversal surfaced {set(leaves) - expected!r} extra and "
        f"missed {expected - set(leaves)!r}"
    )

    # Explicitly confirm operational verbs never surface as LEAVES of this
    # traversal (they're fine to appear as top-level group names like
    # "record"/"session" themselves — those aren't in `leaves` because they
    # recursed rather than being returned directly).
    operational_verbs = {
        "init", "status", "sync", "flush", "areas", "reindex",
        "vault", "task",
    }
    assert not (operational_verbs & set(leaves))


def test_mixed_depth_both_shapes_reachable():
    """Core U1 case: top-level `search` AND nested `record create` /
    `session candidate` are reachable via the SAME traversal function."""
    parser = build_parser()
    leaves = _leaves(parser)

    # Top-level shape.
    assert "search" in leaves
    assert isinstance(leaves["search"], argparse.ArgumentParser)

    # Nested-under-record shape.
    assert "record create" in leaves
    # Nested-under-session shape.
    assert "session candidate" in leaves


def _action_by_dest(actions, dest):
    for a in actions:
        if a.dest == dest:
            return a
    raise AssertionError(f"no action with dest={dest!r} in {[a.dest for a in actions]}")


def test_search_positional_query_required_with_metavar():
    parser = build_parser()
    leaves = _leaves(parser)
    search_leaf = leaves["search"]

    query_action = _action_by_dest(search_leaf._actions, "query")
    # Positionals: argparse sets `.required = True` for a positional with
    # nargs=None (exactly one value expected) — this is what we need to detect
    # "this positional must be supplied".
    assert query_action.required is True
    assert query_action.option_strings == []  # positional, not a flag
    # No explicit metavar was set on `query` in search.py; argparse defaults an
    # unset positional metavar to the dest name itself.
    assert (query_action.metavar or query_action.dest) == "query"


def test_record_create_required_options_kind_and_title():
    parser = build_parser()
    leaves = _leaves(parser)
    create_leaf = leaves["record create"]

    kind_action = _action_by_dest(create_leaf._actions, "kind")
    title_action = _action_by_dest(create_leaf._actions, "title")

    assert kind_action.required is True
    assert kind_action.option_strings == ["--kind"]
    assert title_action.required is True
    assert title_action.option_strings == ["--title"]

    # And a genuinely-optional flag on the same leaf reads required=False, to
    # confirm the traversal actually distinguishes rather than reporting
    # everything as required.
    status_action = _action_by_dest(create_leaf._actions, "status")
    assert status_action.required is False


def test_record_delete_positional_record_id():
    parser = build_parser()
    leaves = _leaves(parser)
    delete_leaf = leaves["record delete"]

    record_id_action = _action_by_dest(delete_leaf._actions, "record_id")
    assert record_id_action.required is True
    assert record_id_action.option_strings == []
    assert record_id_action.metavar == "RECORD_ID"


def test_session_candidate_required_options_kind_and_phase():
    parser = build_parser()
    leaves = _leaves(parser)
    candidate_leaf = leaves["session candidate"]

    kind_action = _action_by_dest(candidate_leaf._actions, "kind")
    phase_action = _action_by_dest(candidate_leaf._actions, "phase")
    assert kind_action.required is True
    assert kind_action.option_strings == ["--kind"]
    assert phase_action.required is True
    assert phase_action.option_strings == ["--phase"]


def test_record_show_suppressed_routing_flags_present_but_markable():
    """The hidden --repo/--product/--suite/--team flags on `record show` must
    show up in `_actions` (not be silently absent) so a generator can detect
    and skip them via `action.help is argparse.SUPPRESS`."""
    parser = build_parser()
    leaves = _leaves(parser)
    show_leaf = leaves["record show"]

    repo_action = _action_by_dest(show_leaf._actions, "repo")
    assert repo_action.option_strings == ["--repo"]
    assert repo_action.help is argparse.SUPPRESS

    for dest in ("product", "suite", "team"):
        action = _action_by_dest(show_leaf._actions, dest)
        assert action.help is argparse.SUPPRESS

    # And a non-suppressed flag on the same leaf reads a normal help string,
    # confirming the traversal distinguishes SUPPRESS from ordinary help text.
    json_action = _action_by_dest(show_leaf._actions, "json")
    assert json_action.help is not argparse.SUPPRESS
    assert isinstance(json_action.help, str) and json_action.help
