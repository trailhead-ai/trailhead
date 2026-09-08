"""Cross-derivation contract binding brainstorm's grill step (step 2, "Grill
for Clarity") to the real `edge_confirmations.py` renderer — so the skill's
prose and the renderer's behaviour cannot drift apart.

These tests bind to the real renderer run as a subprocess against real
input, and to real edge-checklist bullet text parsed out of SKILL.md, never
to a copy of either's own wording — mirroring
`test_brainstorm_maturity_contract.py`'s established `_step` idiom.
"""

from __future__ import annotations

import os
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


def test_grill_step_documents_the_invocation_and_the_script_exists_executable():
    grill = _grill_step()
    invocation = re.search(
        r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/(\S+\.py)", grill
    )
    assert invocation, "step 2 must invoke the renderer via the CLAUDE_PLUGIN_ROOT convention"
    script_name = invocation.group(1)
    resolved = SCRIPTS_DIR / script_name
    assert resolved.exists(), f"documented script {script_name!r} does not exist at {resolved}"
    assert os.access(resolved, os.X_OK), f"documented script {script_name!r} is not executable"
    assert resolved == RENDERER, (
        "fixture assumption: step 2 documents edge_confirmations.py specifically"
    )


# ---- the non-zero-exit rule is stated as grilling in full, not skipping ----


def test_grill_step_states_non_zero_exit_grills_all_four_in_full():
    grill = _grill_step()
    clause_match = re.search(r"renderer exits non-zero[^.]*\.", grill, re.IGNORECASE)
    assert clause_match, "step 2 must have a renderer-non-zero-exit clause terminated by '.'"
    clause = clause_match.group(0)
    assert re.search(r"grill\s+all four", clause, re.IGNORECASE), (
        f"the non-zero-exit clause must instruct grilling all four dimensions in "
        f"full: {clause!r}"
    )
    assert not re.search(r"\bskip", clause, re.IGNORECASE), (
        f"the non-zero-exit clause must not instruct skipping: {clause!r}"
    )


# ---- a reopened dimension is grilled as a full branch (never
#      suppressed-only-downgraded, at this surface) ---------------------------


def test_grill_step_states_a_reopened_dimension_is_grilled_as_a_full_branch():
    grill = _grill_step()
    clause_match = re.search(r"operator names in reply[^.]*\.", grill, re.IGNORECASE)
    assert clause_match, "step 2 must have a reopened-dimension clause terminated by '.'"
    clause = clause_match.group(0)
    assert re.search(r"full branch", clause, re.IGNORECASE), (
        f"the reopened-dimension clause must state it is grilled as a full "
        f"branch: {clause!r}"
    )


# ---- the four confirmation defaults, if restated in the skill prose at
#      all, match the renderer's own text; here, the prose points at the
#      renderer's summary line rather than restating the defaults, and that
#      pointer is pinned to the renderer's real output -----------------------


def test_skill_quotes_the_renderers_own_summary_line_verbatim():
    result = _run_renderer(ALL_PROTOTYPE_SINGLE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    summary_line = result.stdout.decode("utf-8").splitlines()[0]

    grill = _grill_step()
    assert summary_line in grill, (
        f"step 2 must quote the renderer's own summary line verbatim rather than "
        f"restating a paraphrase of it: {summary_line!r} not found in step 2"
    )


# ---- composition seam: the framing step's own worked example, stripped of
#      its annotation, actually parses ---------------------------------------


def test_framing_step_worked_example_is_present_and_annotated():
    """Fixture ground truth: step 1's own body documents a worked example
    carrying a parenthetical annotation the renderer's grammar cannot accept
    bare. Scoped to step 1's own body — not the whole file — so that this
    fixture assumption cannot be satisfied by step 2 merely re-quoting the
    same string."""
    frame = _frame_step()
    assert FRAMING_STEP_WORKED_EXAMPLE_ANNOTATED in frame, (
        "fixture assumption: step 1 documents this annotated worked example verbatim"
    )


def test_grill_step_instructs_stripping_the_annotation_before_composing():
    grill = _grill_step()
    assert re.search(r"stripping\s+any\s+parenthetical\s+annotation", grill), (
        "step 2 must instruct stripping the parenthetical annotation before "
        f"composing the bare block: {grill!r}"
    )


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


def test_grill_step_states_surfacing_the_renderers_reason_code_on_non_zero_exit():
    grill = _grill_step()
    clause_match = re.search(
        r"On a non-zero\s*\nexit[^.]*\.|On a non-zero exit[^.]*\.",
        grill,
        re.IGNORECASE,
    )
    assert clause_match, "step 2 must have an 'on a non-zero exit' clause terminated by '.'"
    clause = clause_match.group(0)
    assert re.search(r"reason-code", clause), (
        f"the on-a-non-zero-exit clause must instruct stating the renderer's "
        f"reason-code to the operator: {clause!r}"
    )
    assert re.search(r"state", clause, re.IGNORECASE), (
        f"the on-a-non-zero-exit clause must instruct stating (not suppressing) "
        f"the reason-code: {clause!r}"
    )


def test_grill_step_distinguishes_the_reason_code_from_untrusted_repo_content():
    grill = _grill_step()
    clause_match = re.search(
        r"reason-code:?`?\s*to the operator:[^.]*\.", grill, re.IGNORECASE
    )
    assert clause_match, (
        "step 2 must have a clause distinguishing the reason-code's own "
        "provenance, terminated by '.'"
    )
    clause = clause_match.group(0)
    assert re.search(r"authored by this script", clause, re.IGNORECASE), (
        f"the clause must state the reason-code is authored by this script, not "
        f"repository content: {clause!r}"
    )
    assert re.search(r"not\s+read\s+from\s+repository\s+content", clause, re.IGNORECASE), (
        f"the clause must contrast against repository-authored content: {clause!r}"
    )


# ---- the dimension-set rule: the first four edge-checklist bullets stay
#      grilled in full at every level, prototype included -------------------


def test_grill_step_states_the_first_four_bullets_stay_grilled_at_every_level():
    grill = _grill_step()
    clause_match = re.search(
        r"the first four \([^)]*\)[^.]*grilled in full at every level[^.]*\.",
        grill,
        re.IGNORECASE,
    )
    assert clause_match, (
        "step 2 must have a 'the first four (...) ... grilled in full at every "
        "level' clause terminated by '.'"
    )
    clause = " ".join(clause_match.group(0).split())
    assert re.search(r"\bprototype\b", clause, re.IGNORECASE), (
        f"the clause must name `prototype` as included in every level: {clause!r}"
    )
    for bullet_label in ("Boundaries", "Failure modes", "Hidden assumptions", "Scope"):
        assert bullet_label in clause, (
            f"the clause must name the first-four bullet label {bullet_label!r} "
            f"as staying grilled in full: {clause!r}"
        )


# ---- exchange seam: the confirmations reach the operator as ONE exchange
#      spanning all four dimensions, not one prompt per dimension (positive
#      assertion, never an absence assertion) --------------------------------


def test_grill_step_states_confirmations_are_put_as_one_exchange_spanning_all_four():
    grill = _grill_step()
    clause_match = re.search(
        r"one exchange[^.]*\.", grill, re.IGNORECASE
    )
    assert clause_match, "step 2 must have a 'one exchange' clause terminated by '.'"
    clause = clause_match.group(0)
    assert re.search(r"single\s+reopen\s+instruction", clause, re.IGNORECASE), (
        f"the one-exchange clause must describe a single reopen instruction "
        f"spanning all four dimensions: {clause!r}"
    )
    assert re.search(r"all four dimensions together", clause, re.IGNORECASE), (
        f"the one-exchange clause must span all four dimensions together, not "
        f"one prompt per dimension: {clause!r}"
    )
