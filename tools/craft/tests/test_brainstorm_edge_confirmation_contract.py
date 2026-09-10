"""Cross-derivation contract binding brainstorm's grill step (step 2, "Grill
for Clarity") to the real `edge_confirmations.py` renderer — so the skill's
prose and the renderer's behaviour cannot drift apart.

These tests bind to the real renderer run as a subprocess against real
input, and to real edge-checklist bullet text parsed out of SKILL.md, never
to a copy of either's own wording — mirroring
`test_brainstorm_maturity_contract.py`'s established `_step` idiom.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
BRAINSTORM_SKILL = CRAFT / "skills" / "brainstorm" / "SKILL.md"
SCRIPTS_DIR = CRAFT / "scripts"
RENDERER = SCRIPTS_DIR / "edge_confirmations.py"

ALL_PROTOTYPE_SINGLE = b"## Maturity\n\n- trailhead: prototype\n"

# The exact worked example the "Frame" step documents for its own annotated
# output, quoted literally as
# `lookout: production (no declaration — defaults to production)`. The
# grill step must instruct stripping the annotation before this reaches the
# renderer, and that stripped form must actually parse.
FRAMING_STEP_WORKED_EXAMPLE_ANNOTATED = (
    "lookout: production (no declaration — defaults to production)"
)


def _skill_text() -> str:
    return BRAINSTORM_SKILL.read_text(encoding="utf-8")


def _step(name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading."""
    text = _skill_text()
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d+\.", rest)
    return rest[: end.start()] if end else rest


def _grill_step() -> str:
    return _step("### 2. Grill for Clarity")


def _frame_step() -> str:
    return _step("### 1. Frame")


def _run_renderer(stdin_bytes: bytes) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RENDERER)],
        input=stdin_bytes,
        capture_output=True,
    )


def _edge_checklist_bullet_labels() -> list[str]:
    """The bold bullet labels of step 2's edge checklist ("Then poke at the
    edges" through the maturity-sensitivity paragraph), parsed from the
    document itself."""
    grill = _grill_step()
    edges_start = grill.index("**Then poke at the edges.**")
    edges_end = grill.index("**The last four bullets above")
    edges_block = grill[edges_start:edges_end]
    return re.findall(r"^- \*\*([^:*]+):\*\*", edges_block, re.MULTILINE)


def _renderer_dimension_names(stdout: str) -> list[str]:
    return [
        line[2 : line.index(":", 2)]
        for line in stdout.splitlines()
        if line.startswith("- ")
    ]


# ---- every renderer dimension name is a literal edge-checklist bullet label
#      in SKILL.md ------------------------------------------------------------


def test_every_renderer_dimension_corresponds_to_a_literal_edge_checklist_bullet_label():
    result = _run_renderer(ALL_PROTOTYPE_SINGLE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    dimension_names = _renderer_dimension_names(result.stdout.decode("utf-8"))
    assert dimension_names, "fixture assumption: the renderer emits confirmation lines"

    bullet_labels = _edge_checklist_bullet_labels()
    for dimension in dimension_names:
        assert dimension in bullet_labels, (
            f"renderer dimension {dimension!r} has no literal edge-checklist bullet "
            f"label in SKILL.md; labels found: {bullet_labels!r}"
        )


# ---- the invocation is documented, and the script it names exists and is
#      executable -------------------------------------------------------------


# ---- the non-zero-exit rule is stated as grilling in full, not skipping ----


# ---- a reopened dimension is grilled as a full branch (never
#      suppressed-only-downgraded, at this surface) ---------------------------


# ---- the four confirmation defaults, if restated in the skill prose at
#      all, match the renderer's own text; here, the prose points at the
#      renderer's summary line rather than restating the defaults, and that
#      pointer is pinned to the renderer's real output -----------------------


# ---- composition seam: the framing step's own worked example, stripped of
#      its annotation, actually parses ---------------------------------------


def test_stripped_framing_step_worked_example_parses_and_resolves_through_the_renderer():
    """The exact worked example step 1 documents, with its annotation
    stripped exactly the way step 2 instructs (composing the bare
    `<member>: <level>` line), actually parses through the real renderer."""
    bare_level = FRAMING_STEP_WORKED_EXAMPLE_ANNOTATED.split(" (", 1)[0]
    assert bare_level == "lookout: production"
    stdin = f"## Maturity\n\n- {bare_level}\n".encode("utf-8")
    result = _run_renderer(stdin)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert "no dimension suppressed" in result.stdout.decode("utf-8").lower()


# ---- failure seam: the non-zero exit states the renderer's own reason-code
#      to the operator, and distinguishes it from repo-authored content -----


# ---- the dimension-set rule: the first four edge-checklist bullets stay
#      grilled in full at every level, prototype included -------------------


# ---- exchange seam: the confirmations reach the operator as ONE exchange
#      spanning all four dimensions, not one prompt per dimension (positive
#      assertion, never an absence assertion) --------------------------------
