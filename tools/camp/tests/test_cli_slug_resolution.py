"""Test contract: `camp.cli.dispatch._slug_from_name_or_cwd` — the one slug
resolver every workspace-addressed verb shares.

The resolver takes ALREADY-PARSED values (`name`, `positional`) rather than an
argv list to scan, so each verb declares `--name` in its own parser and this
function only decides precedence. What it decides, in order:

  1. an explicit ``--name`` wins over everything;
  2. failing that, a positional the verb chose to offer;
  3. failing that, the workspace the current directory sits in;
  4. failing that, either a refusal naming the verb, or None when the caller
     declared it can carry on without one.

Every case below varies an input across that ladder, so a reordering or a
dropped rung goes red.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.cli import dispatch  # noqa: E402

_GROUP = {"name": "testgrp", "members": []}


@pytest.fixture()
def unresolvable_cwd(monkeypatch: pytest.MonkeyPatch):
    """Make cwd resolution find nothing, so the rungs above it decide alone."""
    from camp.group import resolve as group_resolve

    monkeypatch.setattr(
        group_resolve, "resolve_from_cwd", lambda *a, **k: (_GROUP, None)
    )
    return monkeypatch


# ---------------------------------------------------------------------------
# Rung 1 and 2 — the explicit answers, and their precedence.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["feat-x", "another-slug"])
def test_explicit_name_is_the_slug(name: str, unresolvable_cwd) -> None:
    """``--name`` resolves to itself — varying it varies the answer."""
    assert dispatch._slug_from_name_or_cwd(_GROUP, verb="status", name=name) == name


@pytest.mark.parametrize("positional", ["feat-y", "yet-another"])
def test_positional_is_the_slug_when_no_name_is_given(
    positional: str, unresolvable_cwd
) -> None:
    assert (
        dispatch._slug_from_name_or_cwd(_GROUP, verb="pwd", positional=positional)
        == positional
    )


def test_name_outranks_a_positional(unresolvable_cwd) -> None:
    """Both supplied: ``--name`` wins. The rung order is the contract, and this
    is the only case that can show it."""
    assert (
        dispatch._slug_from_name_or_cwd(
            _GROUP, verb="pwd", name="from-flag", positional="from-positional"
        )
        == "from-flag"
    )


# ---------------------------------------------------------------------------
# Rung 3 — the working directory, consulted only when nothing was named.
# ---------------------------------------------------------------------------


def test_cwd_supplies_the_slug_when_nothing_was_named(monkeypatch) -> None:
    from camp.group import resolve as group_resolve

    monkeypatch.setattr(
        group_resolve, "resolve_from_cwd", lambda *a, **k: (_GROUP, "from-cwd")
    )
    assert dispatch._slug_from_name_or_cwd(_GROUP, verb="status") == "from-cwd"


def test_cwd_is_not_consulted_when_a_name_was_given(monkeypatch) -> None:
    """The mirror of the case above: an explicit name short-circuits the cwd
    lookup entirely, so a workspace directory cannot override what was typed."""
    from camp.group import resolve as group_resolve

    def _boom(*a, **k):
        raise AssertionError("cwd resolution must not run when --name was given")

    monkeypatch.setattr(group_resolve, "resolve_from_cwd", _boom)
    assert dispatch._slug_from_name_or_cwd(_GROUP, verb="status", name="typed") == "typed"


# ---------------------------------------------------------------------------
# Rung 4 — nothing resolved, and the two ways a caller asked to be told.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["status", "rebase"])
def test_unresolvable_refuses_naming_the_verb(verb: str, unresolvable_cwd, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        dispatch._slug_from_name_or_cwd(_GROUP, verb=verb)
    assert exc.value.code == 1
    assert capsys.readouterr().err == (
        f"camp {verb}: could not determine slug from cwd — "
        "pass --name <slug> or run from inside a workspace directory\n"
    )


def test_unresolvable_returns_none_when_the_caller_allows_it(unresolvable_cwd, capsys) -> None:
    """`camp status`'s fleet view has an answer for "no slug", so it opts out of
    the refusal rather than being forced into one."""
    assert (
        dispatch._slug_from_name_or_cwd(_GROUP, verb="status", allow_none=True) is None
    )
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# Validation still runs on whatever was named.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "context"),
    [({"name": "../escape"}, "--name"), ({"positional": "../escape"}, "argument")],
)
def test_a_dangerous_slug_is_refused_naming_where_it_came_from(
    kwargs: dict, context: str, unresolvable_cwd, capsys
) -> None:
    """Path traversal is rejected before normalization, and the refusal says
    which surface supplied it — the two rungs report differently on purpose."""
    with pytest.raises(SystemExit) as exc:
        dispatch._slug_from_name_or_cwd(_GROUP, verb="pwd", **kwargs)
    assert exc.value.code == 1
    assert f"invalid slug from {context}" in capsys.readouterr().err


def test_cwd_resolution_failure_is_not_a_slug(monkeypatch, capsys) -> None:
    """A `GroupResolutionError` from the cwd lookup means "no slug here", not a
    crash — the verb's own refusal is the answer."""
    from camp.group import resolve as group_resolve

    def _raise(*a, **k):
        raise group_resolve.GroupResolutionError("no group here")

    monkeypatch.setattr(group_resolve, "resolve_from_cwd", _raise)
    assert dispatch._slug_from_name_or_cwd(_GROUP, verb="status", allow_none=True) is None
