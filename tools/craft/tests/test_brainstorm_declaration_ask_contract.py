"""Brainstorm's framing step (step 1, "Frame") asks the operator for a
maturity level on the absence path, and invokes the writer on an answer that
names one — per
`spec/project-maturity-levels-calibrate-craft-s-standard-of-care` AC4/AC4b.

These tests bind the skill's prose to something executable — the real
`maturity_declare.py` writer, `maturity_resolve.py` reader, and
`maturity_bars.py` renderer, each run as a subprocess against real fixture
agent-instruction bodies — never to a copy of the skill's own wording,
following `test_brainstorm_maturity_contract.py`'s established `_step` idiom.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

# Shared fixture bodies and runners, reused rather than re-derived, so a
# change to either the fixtures or the invocation shape reaches every suite
# that depends on them from one place.
from test_maturity_bars import CONCERNS as BARS_CONCERNS
from test_maturity_bars import _run as _run_bars
from test_maturity_declare import ALREADY_DECLARED_EARLY as VALID_DECLARED_EARLY
from test_maturity_declare import (
    MARKER,
    NO_SECTION_AT_ALL,
    UNCLOSED_FENCE,
)
from test_maturity_declare import _resolver_level_and_reason
from test_maturity_declare import _run as _run_declare
from test_maturity_resolve import INVALID_VALUE_DECLARED

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
BRAINSTORM_SKILL = CRAFT / "skills" / "brainstorm" / "SKILL.md"
SCRIPTS_DIR = CRAFT / "scripts"
DECLARE = SCRIPTS_DIR / "maturity_declare.py"


def _skill_text() -> str:
    return BRAINSTORM_SKILL.read_text(encoding="utf-8")


def _step(name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading —
    mirrors `test_brainstorm_maturity_contract.py`'s own helper."""
    text = _skill_text()
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d+\.", rest)
    return rest[: end.start()] if end else rest


def _ask_clause() -> str:
    """The paragraph introducing the ask, from its bold lead-in through the
    correction-path heading that follows it — scoped so assertions about the
    ask cannot be satisfied by unrelated prose elsewhere in step 1."""
    frame_step = _step("### 1. Frame")
    start = frame_step.index("On the absence path")
    end = frame_step.index("**Correcting a wrong declaration.**")
    return frame_step[start:end]


def _declare_snippet() -> str:
    ask = _ask_clause()
    blocks = re.findall(r"```sh\n(.*?)\n```", ask, re.DOTALL)
    matches = [b for b in blocks if "maturity_declare.py" in b]
    assert matches, f"the ask must carry a fenced maturity_declare.py invocation: {ask!r}"
    assert len(matches) == 1, f"expected exactly one declare invocation block: {matches!r}"
    return matches[0].strip()


def _bars_snippet() -> str:
    ask = _ask_clause()
    blocks = re.findall(r"```sh\n(.*?)\n```", ask, re.DOTALL)
    matches = [b for b in blocks if "maturity_bars.py" in b]
    assert matches, f"the ask must carry a fenced maturity_bars.py invocation: {ask!r}"
    assert len(matches) == 1, f"expected exactly one bars invocation block: {matches!r}"
    return matches[0].strip()


# ---- 1. the documented invocation snippet is executed as written -----------


def _substitute_snippet(snippet: str, target: Path, level: str) -> list[str]:
    substituted = (
        snippet.replace("${CLAUDE_PLUGIN_ROOT}/scripts", str(SCRIPTS_DIR))
        .replace("<repo-root>/CLAUDE.md", str(target))
        .replace("<level>", level)
    )
    return shlex.split(substituted)


def _execute_snippet_and_confirm_declared(argv: list[str], target: Path, level: str) -> None:
    """The check the documented invocation snippet must pass: run it exactly
    as substituted, then confirm the real resolver reads the target back as
    `declared` at `level`. Shared verbatim between the real test below and
    its positive control, so the control genuinely drives this check rather
    than a copy of it."""
    result = subprocess.run(argv, input=b"", capture_output=True)
    assert result.returncode == 0, result.stderr.decode("utf-8")

    got_level, reason = _resolver_level_and_reason(target.read_bytes())
    assert got_level == level
    assert reason == "declared"


def test_documented_declare_snippet_executed_as_written_writes_the_answered_level(
    tmp_path,
):
    snippet = _declare_snippet()

    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")

    argv = _substitute_snippet(snippet, target, "early")
    # Bare invocation via the script's own shebang, exactly as documented —
    # never rewritten as `python3 maturity_declare.py`, which would leave the
    # mode bit untested.
    assert argv[0] == str(DECLARE), (
        f"the snippet's first token must be the writer itself: {argv!r}"
    )

    _execute_snippet_and_confirm_declared(argv, target, "early")


def test_documented_declare_snippet_handles_a_repo_root_containing_a_space(tmp_path):
    """The snippet substitutes a real absolute path for `<repo-root>`, and a
    real repo-root can contain a space — an unquoted snippet breaks the
    invocation as written (shlex splits the path in two)."""
    snippet = _declare_snippet()

    spacey_root = tmp_path / "repo with space"
    spacey_root.mkdir()
    target = spacey_root / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")

    argv = _substitute_snippet(snippet, target, "early")
    assert argv[0] == str(DECLARE)
    assert argv[1] == str(target), (
        f"the repo-root path must survive as a single argv token: {argv!r}"
    )

    _execute_snippet_and_confirm_declared(argv, target, "early")


def test_documented_declare_snippet_wrong_is_caught_by_the_check_above(tmp_path):
    """Positive control for the check above: drives the exact same
    execute-then-confirm-declared check (`_execute_snippet_and_confirm_declared`,
    not a copy of it) against a documented-but-wrong snippet naming the
    resolver instead of the writer, and observes it actually go red — the
    resolver ignores argv and reads stdin, so with no stdin content the
    round-trip check's own `returncode == 0` assertion is what fails."""
    wrong_snippet = (
        "${CLAUDE_PLUGIN_ROOT}/scripts/maturity_resolve.py "
        "<repo-root>/CLAUDE.md <level>"
    )
    target = tmp_path / "CLAUDE.md"
    target.write_text(NO_SECTION_AT_ALL, encoding="utf-8")

    argv = _substitute_snippet(wrong_snippet, target, "early")
    assert argv[0] != str(DECLARE), (
        "fixture assumption: the wrong snippet must name a different script"
    )

    with pytest.raises(AssertionError, match="returncode"):
        _execute_snippet_and_confirm_declared(argv, target, "early")


# ---- 2. the snippet carries no hardcoded level and no hardcoded repository -


def test_declare_snippet_carries_only_placeholders_no_hardcoded_level_or_repo():
    snippet = _declare_snippet()
    for level_word in ("prototype", "early", "production"):
        assert not re.search(rf"\b{level_word}\b", snippet), (
            f"the snippet must never name a real level literally — found "
            f"{level_word!r} in {snippet!r}, which would declare the wrong "
            f"thing for whatever repository it is pointed at"
        )
    assert "<level>" in snippet, f"the snippet must carry a <level> placeholder: {snippet!r}"
    assert "<repo-root>" in snippet, (
        f"the snippet must carry a <repo-root> placeholder, not a real path: {snippet!r}"
    )


def test_ask_states_the_answer_is_normalized_before_it_reaches_a_command_line():
    """The operator's answer is free text, but the command line only ever
    sees one of the three closed-vocabulary words — the ask must say so."""
    ask = _ask_clause()
    normalize_match = re.search(r"normalized[^.]*\.", ask, re.IGNORECASE)
    assert normalize_match, (
        f"the ask must state the answer is normalized before reaching a "
        f"command line: {ask!r}"
    )
    clause = normalize_match.group(0)
    assert re.search(r"one of the three closed-vocabulary words", clause), (
        f"the normalization clause must name the closed vocabulary: {clause!r}"
    )
    assert re.search(r"command line", clause, re.IGNORECASE), (
        f"the normalization clause must say where the normalized word ends "
        f"up: {clause!r}"
    )


# ---- 3. the ask fires only on the absence path ------------------------------


def test_declared_level_fixture_does_not_resolve_as_section_absent():
    _, reason = _resolver_level_and_reason(VALID_DECLARED_EARLY.encode("utf-8"))
    assert reason != "section-absent"
    assert reason == "declared"


def test_invalid_value_fixture_does_not_resolve_as_section_absent():
    _, reason = _resolver_level_and_reason(INVALID_VALUE_DECLARED.encode("utf-8"))
    assert reason != "section-absent"
    assert reason == "invalid-value"


def test_ask_triggers_scoped_to_absence_and_section_absent_only():
    frame_step = _step("### 1. Frame")
    trigger_match = re.search(
        r"On the absence path[^.]*\.", frame_step
    )
    assert trigger_match, (
        f"step 1 must have an 'On the absence path' trigger clause "
        f"terminated by '.': {frame_step!r}"
    )
    trigger = trigger_match.group(0)
    assert "section-absent" in trigger
    assert re.search(r"no agent-instruction file", trigger, re.IGNORECASE)
    assert "invalid-value" not in trigger, (
        f"the ask's trigger clause must not fire on invalid-value, which is "
        f"a bad declaration, not an absent one: {trigger!r}"
    )
    assert "declared" not in trigger.split("section-absent")[0], (
        f"the ask's trigger clause must not fire on an ordinary declared "
        f"reason: {trigger!r}"
    )


# ---- 4. the decline path writes nothing -------------------------------------


def test_decline_clause_states_writing_nothing():
    ask = _ask_clause()
    decline_match = re.search(r"declined or absent answer[^.]*\.", ask, re.IGNORECASE)
    assert decline_match, f"the ask must have a decline clause: {ask!r}"
    assert re.search(r"writes? nothing", decline_match.group(0), re.IGNORECASE), (
        f"the decline clause must state that nothing is written: "
        f"{decline_match.group(0)!r}"
    )


def test_decline_branch_documents_no_write_invocation():
    """AC4b requires a declined answer to write nothing, so the documented
    decline branch must not carry a writer invocation. Asserted against the
    live skill text: everything from the decline sentence to the end of the
    ask is the branch an agent follows on a decline, and a `maturity_declare`
    invocation appearing there would direct the write AC4b forbids."""
    ask = _ask_clause()
    decline_start = ask.index("On a declined or absent answer")
    decline_branch = ask[decline_start:]
    assert "maturity_declare" not in decline_branch, (
        "the decline branch must direct no write, but it names the writer: "
        f"{decline_branch!r}"
    )


# ---- 5. the writer-refusal path is pinned, not just promised ---------------


def test_writer_refusal_is_driven_with_the_positive_control_fixture(tmp_path):
    """Reuses the preceding task's positive-control fixture (a file ending
    inside an unclosed code fence) to drive a real writer refusal."""
    target = tmp_path / "CLAUDE.md"
    target.write_text(UNCLOSED_FENCE, encoding="utf-8")
    original = target.read_bytes()

    result = _run_declare(str(target), "prototype")

    assert result.returncode == 2
    stderr = result.stderr.decode("utf-8")
    assert "reason-code: self-check-failed" in stderr
    assert MARKER not in stderr
    assert target.read_bytes() == original


def test_refusal_clause_instructs_reporting_and_continuing_at_production():
    ask = _ask_clause()
    refusal_match = re.search(r"If the writer refuses[^.]*\.", ask)
    assert refusal_match, f"the ask must have a writer-refusal clause: {ask!r}"
    clause = refusal_match.group(0)
    assert re.search(r"report", clause, re.IGNORECASE)
    assert re.search(r"continue", clause, re.IGNORECASE)
    assert "production" in clause


# ---- 6. the five concerns and severity are pinned against the real renderer


def test_maturity_bars_default_output_names_five_concerns_at_production_severity():
    result = _run_bars(b"")
    assert result.returncode == 0, result.stderr.decode("utf-8")
    stdout = result.stdout.decode("utf-8")
    assert "maturity: production (basis: default)" in stdout
    for concern in BARS_CONCERNS:
        assert f"- {concern}: Critical" in stdout, (
            f"the default (production) render must map {concern!r} to "
            f"Critical: {stdout!r}"
        )
    assert len(BARS_CONCERNS) == 5, "fixture assumption: exactly five concerns"


def test_ask_instructs_invoking_the_renderer_for_the_recommended_level():
    ask = _ask_clause()
    assert "maturity_bars.py" in ask, (
        f"the ask must instruct invoking the real renderer to show what the "
        f"recommendation governs: {ask!r}"
    )
    match = re.search(r"```sh\n(.*maturity_bars\.py.*)\n```", ask, re.DOTALL)
    assert match, f"the renderer invocation must be fenced: {ask!r}"


def test_ask_shows_all_three_levels_consequences_via_the_level_flag():
    """AC4/AC4b's Delivers requires the ask to state what the CHOSEN level
    does — but the operator is choosing among three words, so seeing only
    the recommended level's block leaves the other two invisible at the
    moment of choosing. The ask must run the renderer once per vocabulary
    word via its `--level` flag, never re-listing the mapping itself."""
    block = _bars_snippet()
    for level in ("prototype", "early", "production"):
        assert re.search(rf"--level[= ]{level}\b", block), (
            f"the ask must invoke maturity_bars.py --level {level!r}: {block!r}"
        )


def test_ask_does_not_carry_a_second_copy_of_the_concern_mapping():
    """The ask must show the mapping by running the renderer, never by
    re-listing it — a second copy drifts from the renderer the moment either
    changes."""
    ask = _ask_clause()
    for concern in BARS_CONCERNS:
        assert concern not in ask, (
            f"the ask must not hardcode {concern!r} — it must come from "
            f"running maturity_bars.py: {ask!r}"
        )


# ---- 7. the documented correction path works when followed -----------------


def _apply_documented_correction(declared_body: str, old_level: str, new_level: str) -> str:
    """Implements the correction-path recipe the ask's prose describes:
    replace the vocabulary word the rationale sentence names with the
    correct one."""
    assert old_level in declared_body
    return declared_body.replace(old_level, new_level)


def test_correction_path_recipe_resolves_to_the_new_level():
    corrected = _apply_documented_correction(VALID_DECLARED_EARLY, "early", "production")
    level, reason = _resolver_level_and_reason(corrected.encode("utf-8"))
    assert level == "production"
    assert reason == "declared"


def test_correction_path_trap_naming_the_old_level_too_is_ambiguous():
    """The trap the ask's prose must warn about: a corrected rationale that
    still names the old level (explaining the change) makes the section
    ambiguous and resolves back to production, not forward to the new
    level."""
    trap_body = """\
# Some Repo

## Project Maturity

No longer early — this repository is now at the production level.
"""
    level, reason = _resolver_level_and_reason(trap_body.encode("utf-8"))
    assert reason == "ambiguous-value"
    assert level == "production"


def test_correction_path_clause_names_the_trap():
    frame_step = _step("### 1. Frame")
    start = frame_step.index("**Correcting a wrong declaration.**")
    correction = frame_step[start:]
    assert re.search(r"trap", correction, re.IGNORECASE)
    assert re.search(r"ambiguous", correction, re.IGNORECASE)
    assert "production" in correction


# ---- 8. the recommendation and the vocabulary are pinned against the real
#         resolver's closed vocabulary --------------------------------------


def test_ask_recommends_production_and_names_exactly_the_closed_vocabulary():
    ask = _ask_clause()
    recommend_match = re.search(r"Recommend `production`\.", ask)
    assert recommend_match, f"the ask must recommend production explicitly: {ask!r}"

    sys.path.insert(0, str(SCRIPTS_DIR))
    from maturity_resolve import LEVELS  # noqa: PLC0415

    for level in LEVELS:
        assert re.search(rf"`{level}`", ask), (
            f"the ask must name the closed-vocabulary word {level!r}: {ask!r}"
        )
    assert len(LEVELS) == 3, "fixture assumption: the closed vocabulary has exactly three words"
    for off_vocab in ("spike", "throwaway", "mature", "stable", "beta"):
        assert off_vocab not in ask.lower(), (
            f"the ask must name only the closed vocabulary, found "
            f"off-vocabulary word {off_vocab!r}"
        )


# ---- 9. the ask fires at most once per repository per session --------------


def test_ask_states_it_fires_once_per_repository():
    ask = _ask_clause()
    lead_sentence_match = re.search(r"^On the absence path[^.]*\.", ask)
    assert lead_sentence_match, f"the ask must have a lead sentence terminated by '.': {ask!r}"
    lead_sentence = lead_sentence_match.group(0)
    assert re.search(r"\bonce\b", lead_sentence, re.IGNORECASE), (
        f"the ask's lead sentence must state it fires once per repository: "
        f"{lead_sentence!r}"
    )


# ---- 10. the prose-wrap gate at column 100 stays clean ----------------------


def test_wrap_gate_stays_clean_on_the_edited_skill():
    wrap_gate = SCRIPTS_DIR / "wrap_gate.py"
    result = subprocess.run(
        [sys.executable, str(wrap_gate), str(BRAINSTORM_SKILL)],
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"wrap gate must stay clean on brainstorm/SKILL.md: "
        f"{result.stdout.decode('utf-8')}{result.stderr.decode('utf-8')}"
    )
