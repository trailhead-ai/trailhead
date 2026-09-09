"""Cross-derivation contract binding planning's Define Tasks step (step 7) to
the real `migration_bar.py` renderer — so the skill's prose and the
renderer's behaviour cannot drift apart.

These tests bind to the real renderer run as a subprocess against real
fixture spec bodies, and to real step-7 prose parsed out of SKILL.md, never
to a copy of either's own wording — mirroring
`test_brainstorm_edge_confirmation_contract.py`'s established `_step` idiom.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
PLAN_SKILL = CRAFT / "skills" / "plan" / "SKILL.md"
SCRIPTS_DIR = CRAFT / "scripts"
RENDERER = SCRIPTS_DIR / "migration_bar.py"

PROTOTYPE_FIXTURE = b"## Maturity\n\n- trailhead: prototype\n"
PRODUCTION_FIXTURE = b"## Maturity\n\n- trailhead: production\n"


def _skill_text() -> str:
    return PLAN_SKILL.read_text(encoding="utf-8")


def _step(name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading."""
    text = _skill_text()
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d", rest)
    return rest[: end.start()] if end else rest


def _define_tasks_step() -> str:
    return _step("### 7. Define Tasks")


def _normalized_define_tasks_step() -> str:
    """Step 7's body with hard-wrap whitespace collapsed, so a phrase that
    happens to straddle a wrapped line is still matched by a single-space
    literal pattern."""
    return " ".join(_define_tasks_step().split())


def _extracted_invocation_script() -> Path:
    step = _define_tasks_step()
    match = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/(\S+\.py)", step)
    assert match, "step 7 must invoke a renderer via the CLAUDE_PLUGIN_ROOT convention"
    return SCRIPTS_DIR / match.group(1)


def _run_extracted_renderer(stdin_bytes: bytes, args: list[str] | None = None):
    script = _extracted_invocation_script()
    return subprocess.run(
        [sys.executable, str(script), *(args or [])],
        input=stdin_bytes,
        capture_output=True,
    )


# ---- the invocation is documented, and the script it names exists,
#      executable, and is the migration renderer specifically ---------------


def test_step7_documents_invocation_via_claude_plugin_root_convention_and_script_exists_executable():
    resolved = _extracted_invocation_script()
    assert resolved.exists(), f"documented script does not exist at {resolved}"
    assert os.access(resolved, os.X_OK), f"documented script {resolved} is not executable"
    assert resolved == RENDERER, "fixture assumption: step 7 documents migration_bar.py specifically"


# ---- the invocation runs against real fixture spec bodies, not a
#      pattern-matched copy of the skill's own wording -----------------------


def test_invocation_extracted_from_step7_against_prototype_fixture_produces_suppression_block():
    result = _run_extracted_renderer(PROTOTYPE_FIXTURE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    out = result.stdout.decode("utf-8")
    assert out.splitlines()[0].startswith("migration-bar: prototype ")
    assert "— suppressed: migration and backfill" in out


def test_invocation_extracted_from_step7_against_production_fixture_produces_no_suppression_block():
    result = _run_extracted_renderer(PRODUCTION_FIXTURE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    out = result.stdout.decode("utf-8")
    assert out.splitlines()[0].startswith("migration-bar: production ")
    assert "— not-suppressed: migration and backfill" in out


# ---- position: the instruction lives inside step 7, ahead of step 8 -------


def test_migration_bar_instruction_lives_inside_step_7_and_precedes_step_8():
    text = _skill_text()
    step7_start = text.index("### 7. Define Tasks")
    step8_start = text.index("### 8. Write the Plan")
    assert step7_start < step8_start

    step7 = _define_tasks_step()
    invocation_index = step7.index("${CLAUDE_PLUGIN_ROOT}/scripts/migration_bar.py")

    full_invocation_index = text.index(
        "${CLAUDE_PLUGIN_ROOT}/scripts/migration_bar.py", step7_start
    )
    assert step7_start < full_invocation_index < step8_start, (
        "the migration-bar invocation must be documented inside step 7, "
        "ahead of step 8"
    )
    assert invocation_index >= 0


# ---- safe direction: non-zero exit decomposes migration work normally -----


def test_step7_names_safe_direction_for_non_zero_exit():
    step7 = _normalized_define_tasks_step()
    clause_match = re.search(r"[Oo]n a non-zero exit[^.]*\.", step7)
    assert clause_match, "step 7 must have an 'on a non-zero exit' clause terminated by '.'"
    clause = " ".join(clause_match.group(0).split())
    assert re.search(r"decompose migration.*normally", clause, re.IGNORECASE), (
        f"the non-zero-exit clause must decompose migration work normally: {clause!r}"
    )
    assert re.search(r"reason-code", clause), (
        f"the non-zero-exit clause must state the renderer's own reason-code: {clause!r}"
    )


# ---- safe direction: any level other than prototype decomposes normally ---


def test_step7_names_safe_direction_for_non_prototype_level():
    step7 = _normalized_define_tasks_step()
    clause_match = re.search(
        r"resolved level other than `prototype`[^.]*\.", step7
    )
    assert clause_match, (
        "step 7 must have a 'resolved level other than `prototype`' clause "
        "terminated by '.'"
    )
    clause = " ".join(clause_match.group(0).split())
    assert re.search(r"decompose migration.*normally", clause, re.IGNORECASE), (
        f"the non-prototype-level clause must decompose migration work "
        f"normally: {clause!r}"
    )


# ---- acceptance-criteria carve-out: reopens migration work, and requires
#      naming the criterion when exercised -----------------------------------


def test_step7_names_acceptance_criteria_carveout_and_requires_naming_criterion():
    step7 = _normalized_define_tasks_step()
    clause_match = re.search(
        r"unless an acceptance criterion requires preserving existing state[^.]*\.",
        step7,
    )
    assert clause_match, (
        "step 7 must have an acceptance-criteria carve-out clause terminated "
        "by '.'"
    )
    clause = " ".join(clause_match.group(0).split())
    assert re.search(r"name which criterion", clause, re.IGNORECASE), (
        f"the carve-out clause must require naming which criterion reopened "
        f"the task: {clause!r}"
    )
    assert re.search(r"keep the task", clause, re.IGNORECASE), (
        f"the carve-out clause must keep the task when the carve-out fires: "
        f"{clause!r}"
    )


# ---- durable trace: the fields step 7 requires writing into Given Axioms
#      are derived from the renderer's own emitted block, not retyped -------


def test_durable_trace_fields_are_derived_from_the_renderers_own_emitted_block():
    result = _run_extracted_renderer(PROTOTYPE_FIXTURE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    first_line = result.stdout.decode("utf-8").splitlines()[0]

    block_re = re.compile(
        r"^migration-bar: (?P<level>\S+) \(basis: (?P<basis>\S+)\) — "
        r"(?P<state>suppressed|not-suppressed): (?P<concern>.+)$"
    )
    match = block_re.match(first_line)
    assert match, (
        f"renderer's first block line no longer carries a distinguishable "
        f"level and basis: {first_line!r}"
    )

    step7 = _normalized_define_tasks_step()
    given_axioms_clause_match = re.search(
        r"write the decision into the plan's `Given Axioms`[^.]*\.",
        step7,
    )
    assert given_axioms_clause_match, (
        "step 7 must have a durable-trace clause naming the Given Axioms "
        "write, terminated by '.'"
    )
    clause = " ".join(given_axioms_clause_match.group(0).split())
    for cue in ("target repository", "resolved level", "the basis", "suppressed"):
        assert cue in clause, (
            f"the durable-trace clause must require writing {cue!r}: {clause!r}"
        )


def test_carveout_condition_is_derived_from_the_renderers_own_reopening_text():
    result = _run_extracted_renderer(PROTOTYPE_FIXTURE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    stdout = result.stdout.decode("utf-8")
    reopen_match = re.search(r"[Rr]eopens only if (.+?)\.", stdout)
    assert reopen_match, f"renderer suppression block must state a reopening condition: {stdout!r}"
    key_phrase = "preserving existing state"
    assert key_phrase in reopen_match.group(1), (
        f"fixture assumption: renderer condition mentions {key_phrase!r}: "
        f"{reopen_match.group(1)!r}"
    )

    step7 = _normalized_define_tasks_step()
    assert key_phrase in step7, (
        f"step 7's acceptance-criteria carve-out must quote the renderer's "
        f"own reopening condition phrase {key_phrase!r}, not a paraphrase: "
        f"{step7!r}"
    )
