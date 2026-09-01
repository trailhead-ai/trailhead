"""The slice loop's two remaining unwired handoffs — review's exit, gauntlet's entry.

The slice loop pipeline is `brainstorm -> gauntlet -> (slice -> plan -> execute ->
review)* -> distill` (already pinned verbatim in distill/SKILL.md by
test_distill_contract.py). Two boundaries still pointed at the pre-loop pipeline:

  - **review/SKILL.md's closing handoff** unconditionally told the operator to run
    distill once a reviewed diff merged. Under the loop, one review closes one
    slice, not the spec — the handoff must re-enter `/craft:slice` for the next
    pass, and reach for distill only once that loop reports the spec closed out.
  - **gauntlet/SKILL.md's draft -> ready handoff** pointed the operator at
    `/craft:plan spec/<id>` — the topic-rooted path, which creates its own parent
    and advances the spec on its own, bypassing the loop entirely. It is the
    loop's only wired entry point, so it must hand off to `/craft:slice spec/<id>`
    instead.

Also pins README.md's high-level pipeline description and its gauntlet-to-plan
sentence, both still describing the pre-loop shape.

Every phrase pinned below is verified whole-file-unique before being asserted.
"""

from __future__ import annotations

import re
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
REVIEW = CRAFT / "skills" / "review" / "SKILL.md"
GAUNTLET = CRAFT / "skills" / "gauntlet" / "SKILL.md"
README = Path(__file__).parent.parent / "README.md"

# Same regex test_gauntlet_contract.py polices every craft prose file with, restated
# as a literal pattern so this file fails independently of that one's existence.
_SPEC_ADVANCE_RE = re.compile(r"<spec-id>\s+--status\s+(\w+)")


def _pin(path: Path, phrase: str, reason: str) -> None:
    text = path.read_text()
    assert text.count(phrase) == 1, (
        f"pinned phrase must be unique in {path.name} (found {text.count(phrase)}): "
        f"{phrase!r}"
    )
    assert any(phrase in line for line in text.splitlines()), reason


# --- review/SKILL.md's closing handoff re-enters the loop, not distill directly ---


def test_review_handoff_reenters_slice_for_the_next_pass():
    _pin(
        REVIEW,
        "Run `/craft:slice spec/streaming-export`",
        "review/SKILL.md's closing handoff must send the operator back into "
        "`/craft:slice` for the spec's next pass, fully formed with a real spec "
        "id — a per-slice review closes one slice, not the whole spec.",
    )


def test_review_handoff_names_distill_only_once_the_loop_closes_out():
    _pin(
        REVIEW,
        "the slice loop reports",
        "review/SKILL.md must condition its distill handoff on the slice loop "
        "reporting the spec closed out — distill is not the unconditional next "
        "step from every review anymore.",
    )


def test_review_handoff_still_hands_off_to_distill_fully_formed():
    text = REVIEW.read_text()
    assert "/craft:distill spec/" in text, (
        "review/SKILL.md must still carry a fully-formed `/craft:distill spec/...` "
        "handoff — reachable once the loop closes out, not removed."
    )
    idx = text.index("/craft:distill spec/")
    tail = text[idx + len("/craft:distill spec/") : idx + len("/craft:distill spec/") + 1]
    assert tail != "<", (
        "review/SKILL.md's distill handoff must stay fully formed — a real-looking "
        "spec name, never a bracketed `<placeholder>`."
    )


def test_review_handoff_still_never_advances_the_spec_itself():
    text = REVIEW.read_text()
    advances = _SPEC_ADVANCE_RE.findall(text)
    assert not advances, (
        f"review/SKILL.md's handoff text must never advance a spec's status "
        f"itself ({advances}) — that edge belongs to distill/SKILL.md alone."
    )


# --- gauntlet/SKILL.md's draft -> ready handoff enters the loop, not plan directly ---


def test_gauntlet_handoff_enters_the_loop_via_slice():
    _pin(
        GAUNTLET,
        "/craft:slice spec/streaming-export",
        "gauntlet/SKILL.md's draft -> ready handoff must send the operator to "
        "`/craft:slice spec/<id>` — the loop's entry point — not to "
        "`/craft:plan spec/<id>`, which creates its own parent and strands the "
        "spec outside the loop.",
    )


def test_gauntlet_handoff_no_longer_names_plan_as_the_direct_next_step():
    text = GAUNTLET.read_text()
    assert "let the user invoke\n`/craft:plan`" not in text, (
        "gauntlet/SKILL.md must no longer hand the operator directly to "
        "`/craft:plan` after a spec advances to `ready` — that bypasses the "
        "slice loop's only wired entry point."
    )


# --- README.md reflects the loop, not the pre-loop plan -> execute -> review shape ---


def test_readme_describes_the_slice_loop():
    _pin(
        README,
        "(slice → plan → execute → review)*",
        "README.md must describe craft's development loop with the same "
        "bracketed-loop notation the pipeline string uses elsewhere "
        "(brainstorm → gauntlet → (slice → plan → execute → review)* → "
        "distill), not the flat pre-loop 'plan → execute → review'.",
    )


def test_readme_no_longer_describes_the_flat_pre_loop_pipeline():
    text = README.read_text()
    assert "**plan → execute → review**" not in text, (
        "README.md must not still describe the pipeline as the flat "
        "'plan → execute → review' — that predates the slice loop."
    )


def test_readme_gauntlet_hands_off_to_slice_not_plan():
    _pin(
        README,
        "before `/craft:slice` starts the build loop",
        "README.md's planning-skills paragraph must describe the gauntlet "
        "handing off into `/craft:slice` — the loop's entry point — not "
        "directly into `/craft:plan`, which creates its own parent and "
        "strands the spec outside the loop.",
    )
