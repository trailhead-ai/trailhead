"""Cross-derivation contract binding planning's Define Tasks step (step 7) —
in both `skills/plan/SKILL.md` (the interactive planning ritual) and
`agents/planner.md` (the dispatched planner subagent's own decomposition
step) — to the real `migration_bar.py` renderer, so neither document's prose
can drift from the renderer's actual behaviour, or from each other.

These tests bind to the real renderer run as a subprocess against real
fixture spec bodies, and to real step-7 prose parsed out of each document,
never to a copy of either's own wording — mirroring
`test_brainstorm_edge_confirmation_contract.py`'s established `_step` idiom.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
PLAN_SKILL = CRAFT / "skills" / "plan" / "SKILL.md"
PLANNER_AGENT = CRAFT / "agents" / "planner.md"
SCRIPTS_DIR = CRAFT / "scripts"
RENDERER = SCRIPTS_DIR / "migration_bar.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import migration_bar  # noqa: E402

PROTOTYPE_FIXTURE = b"## Maturity\n\n- trailhead: prototype\n"
PRODUCTION_FIXTURE = b"## Maturity\n\n- trailhead: production\n"
REFUSING_FIXTURE = b"## Maturity\n\n- trailhead: prototype\n- lookout: production\n"

_DOC_PATHS = [PLAN_SKILL, PLANNER_AGENT]
_DOC_IDS = ["skill", "planner"]
_doc_params = pytest.mark.parametrize("doc_path", _DOC_PATHS, ids=_DOC_IDS)


def _doc_text(doc_path: Path) -> str:
    return doc_path.read_text(encoding="utf-8")


def _step(doc_path: Path, name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading."""
    text = _doc_text(doc_path)
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d", rest)
    return rest[: end.start()] if end else rest


def _define_tasks_step(doc_path: Path) -> str:
    return _step(doc_path, "### 7. Define Tasks")


def _normalized_define_tasks_step(doc_path: Path) -> str:
    """Step 7's body with hard-wrap whitespace collapsed, so a phrase that
    happens to straddle a wrapped line is still matched by a single-space
    literal pattern."""
    return " ".join(_define_tasks_step(doc_path).split())


def _extracted_invocation_script(doc_path: Path) -> Path:
    step = _define_tasks_step(doc_path)
    match = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/(\S+\.py)", step)
    assert match, "step 7 must invoke a renderer via the CLAUDE_PLUGIN_ROOT convention"
    return SCRIPTS_DIR / match.group(1)


def _run_extracted_renderer(doc_path: Path, stdin_bytes: bytes, args: list[str] | None = None):
    script = _extracted_invocation_script(doc_path)
    return subprocess.run(
        [sys.executable, str(script), *(args or [])],
        input=stdin_bytes,
        capture_output=True,
    )


# ---- the invocation is documented, names the migration renderer
#      specifically, and that renderer actually runs through its own
#      shebang — the way step 7's documented command invokes it, rather
#      than via `sys.executable` ----------------------------------------


@_doc_params
def test_step7_documents_invocation_of_migration_bar_runnable_via_its_own_shebang(doc_path):
    resolved = _extracted_invocation_script(doc_path)
    assert resolved == RENDERER, "fixture assumption: step 7 documents migration_bar.py specifically"

    result = subprocess.run([str(resolved)], input=PROTOTYPE_FIXTURE, capture_output=True)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert result.stdout.decode("utf-8").splitlines()[0].startswith(
        "migration-bar: (level: prototype) "
    ), "bare invocation via the documented script's own shebang must produce a real block"


# ---- the invocation runs against real fixture spec bodies, not a
#      pattern-matched copy of either document's own wording -----------------


@_doc_params
def test_invocation_extracted_from_step7_against_prototype_fixture_produces_suppression_block(doc_path):
    result = _run_extracted_renderer(doc_path, PROTOTYPE_FIXTURE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    out = result.stdout.decode("utf-8")
    assert out.splitlines()[0].startswith("migration-bar: (level: prototype) ")
    assert "— suppressed: migration and backfill" in out


@_doc_params
def test_invocation_extracted_from_step7_against_production_fixture_produces_no_suppression_block(doc_path):
    result = _run_extracted_renderer(doc_path, PRODUCTION_FIXTURE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    out = result.stdout.decode("utf-8")
    assert out.splitlines()[0].startswith("migration-bar: (level: production) ")
    assert "— not-suppressed: migration and backfill" in out


# ---- position: the instruction lives inside step 7, ahead of step 8 -------


@_doc_params
def test_migration_bar_instruction_lives_inside_step_7_and_precedes_step_8(doc_path):
    text = _doc_text(doc_path)
    step7_start = text.index("### 7. Define Tasks")
    step8_start = text.index("### 8. Write the Plan")
    assert step7_start < step8_start

    # Derived from the renderer's own file on disk, not retyped, so a renamed
    # renderer (or a documented invocation that drifts from it) is caught here
    # rather than only by the shebang-execution test above.
    renderer_name = Path(migration_bar.__file__).name
    invocation_marker = f"${{CLAUDE_PLUGIN_ROOT}}/scripts/{renderer_name}"
    invocation_index = text.index(invocation_marker, step7_start)
    assert step7_start < invocation_index < step8_start, (
        "the migration-bar invocation must be documented inside step 7, "
        "ahead of step 8"
    )

    # The position check alone inspects text; run the documented invocation
    # too, so this test exercises the renderer it pins rather than only
    # grepping prose for where its name appears.
    result = _run_extracted_renderer(doc_path, PROTOTYPE_FIXTURE)
    assert result.returncode == 0, result.stderr.decode("utf-8")


def _concern_word(doc_path: Path, fixture: bytes, state: str) -> str:
    """The first word of the concern phrase the renderer's own block names
    after `state`, captured from an actual run rather than retyped."""
    result = _run_extracted_renderer(doc_path, fixture)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    first_line = result.stdout.decode("utf-8").splitlines()[0]
    match = re.search(rf"{state}: (.+)$", first_line)
    assert match, (
        f"renderer block must state a {state} concern phrase: {first_line!r}"
    )
    return match.group(1).split()[0]


def _reopening_condition_phrase(stdout: str) -> str:
    """The renderer's own reopening-condition phrase, captured from its
    actual suppression-block text rather than retyped."""
    match = re.search(r"acceptance criteria require (.+?) —", stdout)
    assert match, (
        f"renderer suppression block must state a reopening condition after "
        f"'acceptance criteria require': {stdout!r}"
    )
    return match.group(1)


# ---- safe direction: non-zero exit decomposes migration work normally -----


@_doc_params
def test_step7_names_safe_direction_for_non_zero_exit(doc_path):
    step7 = _normalized_define_tasks_step(doc_path)
    clause_match = re.search(r"[Oo]n a non-zero exit[^.]*\.", step7)
    assert clause_match, "step 7 must have an 'on a non-zero exit' clause terminated by '.'"
    clause = " ".join(clause_match.group(0).split())

    refusal = _run_extracted_renderer(doc_path, REFUSING_FIXTURE)
    assert refusal.returncode != 0
    reason_marker_match = re.search(r"reason-code:", refusal.stderr.decode("utf-8"))
    assert reason_marker_match, (
        f"renderer refusal must emit a reason-code marker: {refusal.stderr!r}"
    )
    reason_marker = reason_marker_match.group(0)

    concern_word = _concern_word(doc_path, PROTOTYPE_FIXTURE, "suppressed")

    assert re.search(rf"decompose {re.escape(concern_word)}.*normally", clause, re.IGNORECASE), (
        f"the non-zero-exit clause must decompose {concern_word} work normally: {clause!r}"
    )
    assert reason_marker in clause, (
        f"the non-zero-exit clause must state the renderer's own "
        f"{reason_marker!r} marker: {clause!r}"
    )


# ---- safe direction: any level other than prototype decomposes normally ---


@_doc_params
def test_step7_names_safe_direction_for_non_prototype_level(doc_path):
    step7 = _normalized_define_tasks_step(doc_path)
    prototype_level = migration_bar._PROTOTYPE_LEVEL
    clause_match = re.search(
        rf"resolved level other than `{re.escape(prototype_level)}`[^.]*\.", step7
    )
    assert clause_match, (
        f"step 7 must have a 'resolved level other than `{prototype_level}`' "
        f"clause terminated by '.'"
    )
    clause = " ".join(clause_match.group(0).split())

    concern_word = _concern_word(doc_path, PRODUCTION_FIXTURE, "not-suppressed")

    assert re.search(rf"decompose {re.escape(concern_word)}.*normally", clause, re.IGNORECASE), (
        f"the non-prototype-level clause must decompose {concern_word} work "
        f"normally: {clause!r}"
    )


# ---- acceptance-criteria carve-out: reopens migration work, quoting the
#      renderer's own reopening-condition phrase -----------------------------
#
# Whether the clause also requires *naming* which criterion reopened the
# task, and *keeping* the task once reopened, has no renderer-side
# counterpart to derive it from: the renderer's suppression block never
# mentions naming or keeping anything — that half is pure planner judgment.
# A prose-presence search for those phrases would be exactly the retyped,
# undeliverable check `CLAUDE.md`'s "never write a test whose subject is the
# text of a prose document" rule forbids, so it is not asserted here. Per
# the plan's own council review (Critical: "the eval is the only artifact
# that can show a planning session actually honours the block"), that half
# is measured behaviorally by the `prototype-plan-carries-no-migration` eval
# under `plugins/craft/evals/`, not by this code-vs-document check.


@_doc_params
def test_step7_names_acceptance_criteria_carveout(doc_path):
    result = _run_extracted_renderer(doc_path, PROTOTYPE_FIXTURE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    key_phrase = _reopening_condition_phrase(result.stdout.decode("utf-8"))

    step7 = _normalized_define_tasks_step(doc_path)
    clause_match = re.search(
        rf"unless an acceptance criterion requires {re.escape(key_phrase)}[^.]*\.",
        step7,
    )
    assert clause_match, (
        f"step 7 must have an acceptance-criteria carve-out clause requiring "
        f"{key_phrase!r}, terminated by '.'"
    )


# ---- durable trace: the fields step 7 requires writing into Given Axioms
#      are derived from the renderer's own emitted block, not retyped -------


@_doc_params
def test_durable_trace_fields_are_derived_from_the_renderers_own_emitted_block(doc_path):
    result = _run_extracted_renderer(doc_path, PROTOTYPE_FIXTURE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    first_line = result.stdout.decode("utf-8").splitlines()[0]

    block_re = re.compile(
        r"^migration-bar: \(level: (?P<level>\S+)\) \(basis: (?P<basis>\S+)\) "
        r"\(target-repo: (?P<target_repo>\S+)\) — "
        r"(?P<state>suppressed|not-suppressed): (?P<concern>.+)$"
    )
    match = block_re.match(first_line)
    assert match, (
        f"renderer's first block line no longer carries a distinguishable "
        f"level, basis, target-repo, and state: {first_line!r}"
    )

    step7 = _normalized_define_tasks_step(doc_path)
    given_axioms_clause_match = re.search(
        r"write the decision into the plan's `Given Axioms`[^.]*\.",
        step7,
    )
    assert given_axioms_clause_match, (
        "step 7 must have a durable-trace clause naming the Given Axioms "
        "write, terminated by '.'"
    )
    clause = " ".join(given_axioms_clause_match.group(0).split())

    # Field-label cues come from block_re's own named groups — not a
    # hand-typed tuple — so a renamed renderer field (the group name here
    # tracks the label the renderer's own header format bakes in) cannot
    # leave this check asserting a token the renderer no longer emits.
    label_groups = [
        name for name in block_re.groupindex if name not in ("state", "concern")
    ]
    label_cues = [name.replace("_", "-") for name in label_groups]
    for cue in [*label_cues, match.group("state")]:
        assert f"`{cue}`" in clause, (
            f"the durable-trace clause must require writing `{cue}`: {clause!r}"
        )


@_doc_params
def test_carveout_condition_is_derived_from_the_renderers_own_reopening_text(doc_path):
    result = _run_extracted_renderer(doc_path, PROTOTYPE_FIXTURE)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    key_phrase = _reopening_condition_phrase(result.stdout.decode("utf-8"))

    step7 = _normalized_define_tasks_step(doc_path)
    assert key_phrase in step7, (
        f"step 7's acceptance-criteria carve-out must quote the renderer's "
        f"own reopening condition phrase {key_phrase!r}, not a paraphrase: "
        f"{step7!r}"
    )
