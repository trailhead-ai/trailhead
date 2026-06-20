"""S6 Slice 4 — the retained session skills (checkpoint / finish / sync) are
rewired against the pull session model and the simplified `lore finish`.

These skills capture via `lore session candidate|referenced`, locate/read the
active note via `lore session-note`, finalize via the harvest-free `lore finish`,
and commit via `lore sync`. None of them may reference a removed/forbidden
command or the retired session vocabulary (`shelved`, the SessionStart hook).

The grep gate (zero forbidden tokens) and the extant-command check below are the
mechanical acceptance criteria for the rewrite — a stale call would otherwise
fail loudly only at use time (spec Observability stance).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "skills"

# The three retained session skills this slice rewrites.
SESSION_SKILLS = ("checkpoint", "finish", "sync")

# Forbidden tokens — removed commands + retired session vocabulary. A match in
# any of the three skills means the prose still drives a dead surface (F3 retired
# `shelved`; F5 retired the SessionStart hook).
FORBIDDEN = (
    "lore new",
    "lore recall",
    "lore patch",
    "lore handoff",
    "lore shelved",
    "lore resume",
    "shelved",
    "SessionStart",
)

# Every `lore <command>` invocation documented in the three skills must resolve
# to one of these extant new-surface commands (sub-actions joined with a space).
EXTANT_COMMANDS = frozenset({
    "session candidate",
    "session referenced",
    "session-note",
    "finish",
    "search",
    "sync",
    "stats",
})

# Matches a documented CLI call: `lore <word>` optionally followed by a second
# word (a sub-action like `session candidate`). Stops at the first flag/pipe.
_LORE_CALL = re.compile(r"\blore\s+([a-z-]+)(?:\s+([a-z-]+))?")

# A fenced ```bash code block — documented CLI invocations live here, so the
# extant-command check looks only inside fences (prose mentions of "the lore
# vault" are not invocations and must not be parsed as one).
_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)


def _skill_text(name: str) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text()


def _fenced_lore_calls(text: str) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    for block in _FENCE.findall(text):
        calls.extend(_LORE_CALL.findall(block))
    return calls


@pytest.mark.parametrize("skill", SESSION_SKILLS)
@pytest.mark.parametrize("token", FORBIDDEN)
def test_skill_has_no_forbidden_token(skill: str, token: str):
    text = _skill_text(skill)
    assert token not in text, (
        f"{skill}/SKILL.md contains forbidden token {token!r} — it drives a "
        "removed command or retired session vocabulary (F3 `shelved` / F5 hook)."
    )


@pytest.mark.parametrize("skill", SESSION_SKILLS)
def test_skill_lore_calls_resolve_to_extant_commands(skill: str):
    text = _skill_text(skill)
    for first, second in _fenced_lore_calls(text):
        two = f"{first} {second}".strip()
        # Prefer the two-word command (e.g. `session candidate`); fall back to
        # the one-word top-level command.
        resolved = two if two in EXTANT_COMMANDS else first
        assert resolved in EXTANT_COMMANDS, (
            f"{skill}/SKILL.md documents `lore {two or first}`, which is not an "
            f"extant new-surface command {sorted(EXTANT_COMMANDS)}."
        )


def test_finish_describes_finalize_only_no_harvest():
    """`finish` body describes finalize-only behavior — no harvest flow.

    The harvest-flow tokens are forbidden (S6 Slice 1 retired the flow). The bare
    word `gotcha` is NOT forbidden — it is a legitimate session-candidate *kind*.
    """
    text = _skill_text("finish").lower()
    for harvest_token in ("harvest", "## harvest candidates", "harvest-pending"):
        assert harvest_token not in text, (
            f"finish/SKILL.md still references the harvest flow ({harvest_token!r}); "
            "`lore finish` is finalize + commit only (S6 Slice 1)."
        )


def test_finish_states_empty_session_path_explicitly():
    """`finish` states the empty-session notice + exit-0 path and relays it."""
    text = _skill_text("finish").lower()
    assert "nothing to finalize" in text, (
        "finish/SKILL.md must state the empty-session notice "
        "('no active session note … nothing to finalize') the user will see."
    )
    assert "exit" in text and "0" in text, (
        "finish/SKILL.md must state that the empty-session path exits 0 "
        "(handled, not a cryptic error)."
    )


def test_checkpoint_stays_active_not_terminal():
    """`checkpoint` keeps status `active` and does not finalize."""
    text = _skill_text("checkpoint").lower()
    assert "active" in text, (
        "checkpoint/SKILL.md must state that status stays `active` (no finalize)."
    )
    # It must not claim to finalize / stamp `ended:` — those belong to `finish`.
    assert "lore finish" not in _skill_text("checkpoint"), (
        "checkpoint must NOT call `lore finish` — it is a mid-session sweep, "
        "status stays `active`."
    )


def test_checkpoint_captures_via_session_candidate():
    """`checkpoint` captures via the new `lore session candidate` surface."""
    text = _skill_text("checkpoint")
    assert "lore session candidate" in text, (
        "checkpoint/SKILL.md must capture missed items via "
        "`lore session candidate` (the new pull-model capture surface)."
    )


def test_checkpoint_description_frames_a_session_sweep():
    """checkpoint's frontmatter must frame it as a session sweep / catch-what-I-missed.

    This disjointness is load-bearing for Slice 5's record-vs-checkpoint trigger
    test — checkpoint is a *review of the whole session*, not a single capture.
    """
    text = _skill_text("checkpoint")
    end = text.find("\n---", 3)
    frontmatter = text[3:end].lower()
    assert "sweep" in frontmatter or "catch what" in frontmatter or "review" in frontmatter, (
        "checkpoint frontmatter must frame it as a session sweep / "
        "'catch what I missed' review, distinct from a single deliberate capture."
    )
