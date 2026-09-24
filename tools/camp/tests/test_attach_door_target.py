"""Tests for `camp.attach.door_target` — the pure resolution that decides
what `camp attach` is pointed at before the door acts: a resolved
workspace, a picker choice among workspaces, or a refusal. Creates nothing,
attaches nothing, reads no tmux.

Test contract (from
`task/a-slug-a-picker-or-a-refusal-resolves-the-door-s-workspace`):
- Resolution varies with the argument: a slug naming a workspace resolves
  to that workspace; a different slug naming a different workspace
  resolves to that one; a slug naming none yields the fall-through answer
  and not a refusal of its own.
- The same slug in two groups resolves to two different workspaces.
- With no slug and a terminal, the picker's rows are the group's
  workspaces in slug order, each carrying its state; choosing `2` resolves
  to the second row and not the first.
- `camp attach --list --json` still answers with the session pool,
  unchanged — covered separately in `test_attach_cli.py`.
- With no slug and a terminal over an empty group, the answer is the
  stated refusal and the prompt loop never runs.
- With no slug and no terminal, the answer is the missing-slug refusal,
  nothing is read from stdin, and it is the same refusal whether the group
  holds zero workspaces or three.
- Blank, non-numeric, and out-of-range input each re-prompt, and a valid
  choice after any of them still resolves.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.attach.door_target import (  # noqa: E402
    NotAWorkspace,
    ResolvedWorkspace,
    WorkspaceCandidate,
    refusal_message,
    resolve_attach_target,
)
from camp.attach.picker import PoolUnreadable  # noqa: E402
from camp.launch.door import RefusedEmptyGroup, RefusedNoTerminal  # noqa: E402


class _ExplodingCallable:
    """A `workspaces` provider that fails the test if it is ever called."""

    def __call__(self):  # pragma: no cover - must never run
        raise AssertionError("workspaces() was called when it must not be")


class _RefusingStdin(io.StringIO):
    """A stdin stand-in that fails the test if anything ever reads it."""

    def readline(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("resolve_attach_target read stdin without a terminal")


def _candidates(*pairs: tuple[str, str]) -> list[WorkspaceCandidate]:
    """*pairs* of (slug, state_text) -> candidates rooted at a throwaway path."""
    return [
        WorkspaceCandidate(slug=slug, path=Path(f"/ws/{slug}"), state_text=state)
        for slug, state in pairs
    ]


# ---------------------------------------------------------------------------
# A slug given: resolves, resolves differently, or falls through.
# ---------------------------------------------------------------------------


def test_a_slug_naming_a_workspace_resolves_to_it() -> None:
    rows = _candidates(("camp-cli", "running:3"), ("docs-refresh", "none"))

    outcome = resolve_attach_target(
        "camp-cli",
        group_name="trailhead",
        workspaces=lambda: rows,
        isatty=True,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
    )

    assert isinstance(outcome, ResolvedWorkspace)
    assert outcome.slug == "camp-cli"
    assert outcome.group == "trailhead"
    assert outcome.path == Path("/ws/camp-cli")


def test_a_different_slug_resolves_to_a_different_workspace() -> None:
    rows = _candidates(("camp-cli", "running:3"), ("docs-refresh", "none"))

    outcome = resolve_attach_target(
        "docs-refresh",
        group_name="trailhead",
        workspaces=lambda: rows,
        isatty=True,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
    )

    assert isinstance(outcome, ResolvedWorkspace)
    assert outcome.slug == "docs-refresh"
    assert outcome.path == Path("/ws/docs-refresh")


def test_a_slug_naming_no_workspace_falls_through_not_a_refusal() -> None:
    rows = _candidates(("camp-cli", "running:3"))

    outcome = resolve_attach_target(
        "not-a-workspace",
        group_name="trailhead",
        workspaces=lambda: rows,
        isatty=True,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
    )

    assert isinstance(outcome, NotAWorkspace)


def test_same_slug_in_two_groups_resolves_to_two_different_workspaces() -> None:
    group_a_rows = _candidates(("camp-cli", "running:3"))
    group_b_rows = _candidates(("camp-cli", "none"))

    outcome_a = resolve_attach_target(
        "camp-cli",
        group_name="trailhead",
        workspaces=lambda: group_a_rows,
        isatty=True,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
    )
    outcome_b = resolve_attach_target(
        "camp-cli",
        group_name="sibling",
        workspaces=lambda: group_b_rows,
        isatty=True,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
    )

    assert isinstance(outcome_a, ResolvedWorkspace) and isinstance(outcome_b, ResolvedWorkspace)
    assert outcome_a.group == "trailhead"
    assert outcome_b.group == "sibling"
    assert outcome_a != outcome_b


# ---------------------------------------------------------------------------
# No slug, a terminal: the picker.
# ---------------------------------------------------------------------------


def test_no_slug_terminal_picker_rows_are_slug_order_with_state() -> None:
    rows = _candidates(
        ("camp-cli", "running:3"), ("docs-refresh", "none"), ("lookout-spike", "none")
    )
    out = io.StringIO()

    resolve_attach_target(
        None,
        group_name="trailhead",
        workspaces=lambda: rows,
        isatty=True,
        stdin=io.StringIO("1\n"),
        stdout=out,
    )

    rendered = out.getvalue()
    lines = [line for line in rendered.splitlines() if ")" in line]
    assert lines == [
        "  1) camp-cli  running:3",
        "  2) docs-refresh  none",
        "  3) lookout-spike  none",
    ]


def test_no_slug_terminal_choosing_2_resolves_the_second_row_not_the_first() -> None:
    rows = _candidates(
        ("camp-cli", "running:3"), ("docs-refresh", "none"), ("lookout-spike", "none")
    )

    outcome = resolve_attach_target(
        None,
        group_name="trailhead",
        workspaces=lambda: rows,
        isatty=True,
        stdin=io.StringIO("2\n"),
        stdout=io.StringIO(),
    )

    assert isinstance(outcome, ResolvedWorkspace)
    assert outcome.slug == "docs-refresh"


def test_blank_out_of_range_and_non_numeric_each_reprompt_then_valid_choice_resolves() -> None:
    rows = _candidates(("camp-cli", "running:3"), ("docs-refresh", "none"))

    outcome = resolve_attach_target(
        None,
        group_name="trailhead",
        workspaces=lambda: rows,
        isatty=True,
        stdin=io.StringIO("\n99\nnotanumber\n2\n"),
        stdout=io.StringIO(),
    )

    assert isinstance(outcome, ResolvedWorkspace)
    assert outcome.slug == "docs-refresh"


# ---------------------------------------------------------------------------
# No slug, a terminal, an empty group.
# ---------------------------------------------------------------------------


def test_no_slug_terminal_empty_group_refuses_without_prompting() -> None:
    outcome = resolve_attach_target(
        None,
        group_name="trailhead",
        workspaces=lambda: [],
        isatty=True,
        stdin=_RefusingStdin(""),
        stdout=io.StringIO(),
    )

    assert isinstance(outcome, RefusedEmptyGroup)


# ---------------------------------------------------------------------------
# No slug, no terminal.
# ---------------------------------------------------------------------------


def test_no_slug_no_terminal_refuses_without_enumerating() -> None:
    outcome = resolve_attach_target(
        None,
        group_name="trailhead",
        workspaces=_ExplodingCallable(),
        isatty=False,
        stdin=_RefusingStdin(""),
        stdout=io.StringIO(),
    )

    assert isinstance(outcome, RefusedNoTerminal)


def test_no_slug_no_terminal_refuses_the_same_for_a_full_group() -> None:
    """The exploding provider proves enumeration never runs regardless of
    how many workspaces the group actually holds — the refusal is decided
    by the terminal check alone."""
    outcome = resolve_attach_target(
        None,
        group_name="trailhead",
        workspaces=_ExplodingCallable(),
        isatty=False,
        stdin=_RefusingStdin(""),
        stdout=io.StringIO(),
    )

    assert isinstance(outcome, RefusedNoTerminal)


def test_no_slug_no_terminal_exit_of_input_is_never_read() -> None:
    """Reading nothing from stdin is a *different* assertion from getting
    the refusal — this pins the "nothing is read from stdin" half of the
    contract on its own, via a stdin stand-in that fails the test if it is
    ever touched at all."""
    stdin = _RefusingStdin("")

    resolve_attach_target(
        None,
        group_name="trailhead",
        workspaces=_ExplodingCallable(),
        isatty=False,
        stdin=stdin,
        stdout=io.StringIO(),
    )
    # No assertion needed beyond "this call returned" — _RefusingStdin and
    # _ExplodingCallable both raise AssertionError if touched, so reaching
    # this line at all is the proof.


# ---------------------------------------------------------------------------
# EOF mid-picker.
# ---------------------------------------------------------------------------


def test_no_slug_terminal_eof_mid_picker_is_pool_unreadable() -> None:
    rows = _candidates(("camp-cli", "running:3"))

    outcome = resolve_attach_target(
        None,
        group_name="trailhead",
        workspaces=lambda: rows,
        isatty=True,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
    )

    assert isinstance(outcome, PoolUnreadable)


# ---------------------------------------------------------------------------
# Refusal message wording.
# ---------------------------------------------------------------------------


def test_refusal_message_no_terminal_names_the_missing_slug() -> None:
    assert refusal_message(RefusedNoTerminal(), group_name="trailhead") == (
        "camp attach: no workspace named — pass a slug"
    )


def test_refusal_message_empty_group_varies_with_group_name() -> None:
    trailhead_message = refusal_message(RefusedEmptyGroup(), group_name="trailhead")
    sibling_message = refusal_message(RefusedEmptyGroup(), group_name="sibling")

    assert '"trailhead"' in trailhead_message
    assert '"sibling"' in sibling_message
    assert trailhead_message != sibling_message


def test_refusal_message_names_the_given_slug() -> None:
    message = refusal_message(NotAWorkspace(ref="nosuch-zz"), group_name="trailhead")

    assert "nosuch-zz" in message


def test_refusal_message_escapes_a_control_character_in_the_slug() -> None:
    """A slug reaches this refusal as an unvalidated CLI argument, not a
    value camp derived — a raw ESC could otherwise inject a terminal escape
    sequence into what reads as camp's own refusal line."""
    message = refusal_message(NotAWorkspace(ref="evil\x1b[31m"), group_name="trailhead")

    assert "\x1b" not in message
    assert "\\x1b" in message
