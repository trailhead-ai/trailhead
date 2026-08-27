"""Conformance gate over the fork skill document.

`skills/fork/SKILL.md` is prose: nothing at operator time parses it, so a
renamed verb, an invented flag, or a deleted guarantee would ship silently.
This module holds `/camp:fork`'s own gate-anchor checks and applies the two
generic gates shared with concierge — invocation conformance and the
anti-mechanism guard — via `test_skill_conformance_common`. Full
parametrization of every conformance test over every skill in `skills/` is
out of scope for this module; see that module's docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from . import test_skill_conformance_common as skill_common

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_SKILL = _PLUGIN_DIR / "skills" / "fork" / "SKILL.md"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


@pytest.fixture(scope="module")
def skill_text() -> str:
    assert _SKILL.exists(), f"the fork skill document is missing: {_SKILL}"
    return _SKILL.read_text(encoding="utf-8")


# `launch` is the only verb `/camp:fork` documents — it constructs no launch
# of its own, it only calls `camp launch`.
_VERB_HANDLERS: dict[str, tuple[str, str]] = {
    "launch": ("camp/cli/session.py", "_cmd_launch_group_cli"),
}
_GROUP_FLAG_REFUSED_BY: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# 1. Invocation conformance (shared gate)
# ---------------------------------------------------------------------------


def test_document_shows_the_invocation_it_is_checked_against(skill_text: str) -> None:
    """A guard with nothing to check is not a guard."""
    assert len(skill_common.camp_invocations(skill_text)) >= 1


def test_every_mapped_handler_exists() -> None:
    skill_common.every_mapped_handler_exists(_PLUGIN_DIR, _VERB_HANDLERS)


def test_every_documented_verb_is_a_real_camp_verb(skill_text: str) -> None:
    from camp.spine import RESERVED

    assert skill_common.unreal_verbs(skill_text, RESERVED) == []


def test_every_documented_verb_has_a_mapped_handler(skill_text: str) -> None:
    assert skill_common.unmapped_verbs(skill_text, _VERB_HANDLERS) == []


def test_every_documented_flag_is_accepted_by_its_verb(skill_text: str) -> None:
    offenders = skill_common.unaccepted_flags(
        skill_text,
        _PLUGIN_DIR,
        _VERB_HANDLERS,
        group_flag_refused_by=_GROUP_FLAG_REFUSED_BY,
    )
    assert offenders == {}, f"flags no camp verb accepts: {offenders}"


# ---------------------------------------------------------------------------
# 2. Anti-mechanism guard (shared gate)
# ---------------------------------------------------------------------------


def test_document_drives_no_mechanism_of_its_own(skill_text: str) -> None:
    assert skill_common.forbidden_mechanism_hits(skill_text) == []


@pytest.mark.parametrize(
    "violation",
    [
        "Attach with `tmux attach -t <name>`.",
        "Run `claude --resume <uuid>` in the workspace.",
    ],
)
def test_guard_catches_a_real_violation(violation: str) -> None:
    assert skill_common.forbidden_mechanism_hits(violation) != []


# ---------------------------------------------------------------------------
# 3. Fork's own gate-anchor conformance
# ---------------------------------------------------------------------------

REQUIRED_ANCHORS: dict[str, str] = {
    "confirmation-is-authorization": (
        "the confirmation, not the request, is the authorization"
    ),
    "name-verbatim": "the derived name is never reconstructed",
    "world-readable-prompt": "world-readable and non-redactable",
    "check-back": "check back in a moment",
}


def _missing_anchors(text: str) -> list[str]:
    flat = " ".join(text.split())
    return sorted(label for label, anchor in REQUIRED_ANCHORS.items() if anchor not in flat)


def test_every_load_bearing_guarantee_is_present(skill_text: str) -> None:
    assert _missing_anchors(skill_text) == []


def test_anchor_check_survives_rewrapping(skill_text: str) -> None:
    assert _missing_anchors(skill_text.replace("\n", " ")) == []


@pytest.mark.parametrize("label", sorted(REQUIRED_ANCHORS))
def test_anchor_check_reports_a_deleted_guarantee(label: str, skill_text: str) -> None:
    """Deleting a guarantee has to fail — proven for every anchor, one at a
    time."""
    flattened = " ".join(skill_text.split())
    assert _missing_anchors(flattened.replace(REQUIRED_ANCHORS[label], "")) == [label]


def test_document_never_reports_a_phone_divergence(skill_text: str) -> None:
    """The client renders `--name`/`tmux_name` as-is, so the document must
    never claim otherwise: no report of `tmux_name` as a name distinct from
    what the client shows, and no reconciliation pointer for a divergence
    that does not exist."""
    flat = " ".join(skill_text.split())
    assert "camp handle" not in flat
    assert "phone renders" not in flat
    assert "camp sessions --json" not in flat


# ---------------------------------------------------------------------------
# 4. The world-readable warning must live in the playback/confirmation section
# ---------------------------------------------------------------------------


def _playback_section(text: str) -> str:
    """The section headed 'Play the target back before you ask', up to the
    next heading of the same or shallower level."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if "Play the target back" in line), None
    )
    if start is None:
        return ""
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_document_has_a_playback_section(skill_text: str) -> None:
    assert _playback_section(skill_text), "no '## 1. Play the target back' section found"


def test_world_readable_warning_is_in_the_playback_section(skill_text: str) -> None:
    section = " ".join(_playback_section(skill_text).split())
    assert REQUIRED_ANCHORS["world-readable-prompt"] in section


def test_playback_extraction_would_catch_the_warning_living_elsewhere() -> None:
    """Proves the section-scoped check is a real gate: a synthetic document
    that carries the warning ONLY outside the playback section must fail it,
    even though the whole-document anchor check above would pass."""
    fake = (
        "## 1. Play the target back before you ask\n"
        "Name the workspace and confirm.\n"
        "## 2. Report it\n"
        "The prompt is world-readable and non-redactable.\n"
    )
    section = " ".join(_playback_section(fake).split())
    assert REQUIRED_ANCHORS["world-readable-prompt"] not in section


# ---------------------------------------------------------------------------
# Document shape
# ---------------------------------------------------------------------------


def test_document_carries_skill_frontmatter(skill_text: str) -> None:
    lines = skill_text.splitlines()
    assert lines[0] == "---"
    closing = lines.index("---", 1)
    frontmatter = "\n".join(lines[1:closing])
    assert "name: fork" in frontmatter
    assert "description:" in frontmatter


# ---------------------------------------------------------------------------
# 5. The `--prompt` invocation must be shown quoted
# ---------------------------------------------------------------------------


def test_prompted_invocation_shows_the_prompt_value_quoted(skill_text: str) -> None:
    """`$ARGUMENTS` is multi-word by design and `_consume_flag_value` takes
    exactly one token — an unquoted substitution truncates the prompt to its
    first word. The documented invocation must show the quoted form so a
    literal substitution of $ARGUMENTS carries the whole prompt."""
    invocations = [
        invocation
        for invocation in skill_common.camp_invocations(skill_text)
        if "--prompt" in invocation
    ]
    assert invocations, "no documented camp launch --prompt invocation found"
    for invocation in invocations:
        value = invocation[invocation.index("--prompt") + 1]
        assert value.startswith('"') and value.endswith('"'), (
            f"--prompt value {value!r} is not quoted in the documented invocation"
        )
