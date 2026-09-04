"""`_shared/execute.md`'s wiring of `observation_gate.py` into Phase 6, and the
Phase 5 producer contract that feeds it.

These tests are behavioural, never prose-presence: each one either executes the
invocation the document shows (the pattern `test_slice_title_quoting_contract.py`
establishes — build the real command from the document's own text and run it
through `bash -c`), or derives a set/shape from the document's own heading
structure and compares it against the gate's real behaviour. None of them merely
checks that a phrase appears in the file.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CRAFT = REPO_ROOT / "plugins" / "craft"
EXECUTE_MD = CRAFT / "skills" / "_shared" / "execute.md"
SCRIPTS = CRAFT / "scripts"
GATE = SCRIPTS / "observation_gate.py"
FIXTURES = Path(__file__).parent / "fixtures"

sys.path.insert(0, str(SCRIPTS))
from covers_gate import _COMMONMARK_LINE_RE, _mask_fenced_lines  # noqa: E402


def _doc_text() -> str:
    return EXECUTE_MD.read_text(encoding="utf-8")


def _phase_spans(text: str) -> dict[str, str]:
    """{heading text: body text up to the next `##`/`###` heading}, reading
    only unmasked headings — a `## `/`### ` line inside a fenced code block
    (the grammar examples this document quotes) is illustration, not a real
    section boundary."""
    lines = _COMMONMARK_LINE_RE.split(text)
    masked = _mask_fenced_lines(lines)
    heading_re = re.compile(r"^(#{2,3}) (.+)$")
    headings: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if masked[i]:
            continue
        m = heading_re.match(line)
        if m:
            headings.append((i, m.group(2).strip()))
    spans: dict[str, str] = {}
    for idx, (line_i, name) in enumerate(headings):
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        spans[name] = "\n".join(lines[line_i + 1 : end])
    return spans


def _run_gate(stdin_text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=stdin_text,
        capture_output=True,
        text=True,
    )


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Item 1 — the documented invocation, executed as written
# ---------------------------------------------------------------------------


def _extract_invocation_line(text: str) -> str:
    match = re.search(
        r"lore record show task/<parent-name> --vault <elected-vault> \| "
        r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/observation_gate\.py",
        text,
    )
    assert match, (
        "execute.md must show the documented observation-gate invocation "
        "`lore record show task/<parent-name> --vault <elected-vault> | "
        "${CLAUDE_PLUGIN_ROOT}/scripts/observation_gate.py`"
    )
    return match.group(0)


def _run_documented_invocation(fixture_name: str) -> subprocess.CompletedProcess:
    invocation = _extract_invocation_line(_doc_text())
    invocation = invocation.replace("<parent-name>", "foo").replace(
        "<elected-vault>", "testvault"
    )
    fixture_path = FIXTURES / fixture_name
    script = (
        f"lore() {{ if [ \"$1\" = record ] && [ \"$2\" = show ] && "
        f"[ \"$3\" = task/foo ]; then cat {fixture_path}; fi; }}; " + invocation
    )
    # The gate's shebang is `#!/usr/bin/env python3` — putting the running
    # interpreter's own directory first on PATH means `env python3` resolves
    # to the same 3.11+ interpreter this suite runs under, not whatever
    # `python3` the bare system PATH happens to name. Without this, a
    # 3.11-only construct in the gate would surface only as a SyntaxError in
    # this one test, on a system interpreter this repository does not
    # declare support for.
    interpreter_dir = str(Path(sys.executable).resolve().parent)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={
            "CLAUDE_PLUGIN_ROOT": str(CRAFT),
            "PATH": f"{interpreter_dir}:/usr/bin:/bin",
        },
    )


def test_documented_invocation_exits_zero_on_conforming_parent():
    result = _run_documented_invocation("parent_all_observed.md")
    assert result.returncode == 0, result.stderr


def test_documented_invocation_exits_nonzero_on_parent_missing_observation():
    result = _run_documented_invocation("parent_missing_observation.md")
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# Item 2 — documented exit-code contract agrees with the gate's real exits
# ---------------------------------------------------------------------------


def test_documented_exit_codes_match_real_gate_behaviour():
    text = _doc_text()
    derived = re.search(r"Exit `(\d)` means every covered identifier is derived", text)
    violation = re.search(r"Exit `(\d)` means an integrity violation", text)
    could_not = re.search(r"Exit `(\d)` means the gate could not certify", text)
    assert derived, "execute.md must document exit 0 as 'every covered identifier is derived'"
    assert violation, "execute.md must document exit 1 as an integrity violation"
    assert could_not, "execute.md must document exit 2 as 'could not certify'"

    real_derived = _run_gate(_fixture("parent_all_observed.md")).returncode
    real_violation = _run_gate(_fixture("parent_missing_observation.md")).returncode
    real_could_not = _run_gate("").returncode

    assert int(derived.group(1)) == real_derived
    assert int(violation.group(1)) == real_violation
    assert int(could_not.group(1)) == real_could_not


# ---------------------------------------------------------------------------
# Item 3 — the producer's write mechanism carries --diff, not a bare replace
# ---------------------------------------------------------------------------


def test_producer_write_command_carries_diff_flag():
    phase5 = _phase_spans(_doc_text()).get("Phase 5: Flow-out")
    assert phase5 is not None
    match = re.search(
        r"`(lore record update task/<parent-name> --vault <elected-vault>[^`]*)`",
        phase5,
    )
    assert match, (
        "execute.md's Phase 5 must show the producer's literal write command "
        "as inline code"
    )
    command = match.group(1)
    tokens = command.split()
    assert "--diff" in tokens, (
        f"the documented producer command {command!r} must carry --diff — a "
        "bare-stdin update would replace the whole parent body"
    )


# ---------------------------------------------------------------------------
# Item 4 — the producer contract is anchored at Phase 5 structurally
# ---------------------------------------------------------------------------


def test_producer_contract_anchored_in_phase_5_span():
    spans = _phase_spans(_doc_text())
    phase5 = spans.get("Phase 5: Flow-out")
    assert phase5 is not None, "execute.md must carry a '### Phase 5: Flow-out' heading"
    assert "--diff" in phase5 and "lore record update task/<parent-name>" in phase5, (
        "the producer's write command must live inside the Phase 5 span"
    )
    assert "## Criterion observations" in phase5, (
        "the observation grammar block must live inside the Phase 5 span"
    )


def test_producer_contract_is_not_duplicated_into_another_phase_span():
    """The grammar block's own example lines are its unique fingerprint —
    unlike the section name `## Criterion observations`, which Phase 6's gate
    paragraph legitimately references in prose, the actual worked example
    lines (`- **AC9** — automated-assertion — <evidence pointer>`) define the
    grammar and must exist only where it is authored: Phase 5."""
    spans = _phase_spans(_doc_text())
    marker = "- **AC9** — automated-assertion — <evidence pointer>"
    for name, body in spans.items():
        if name == "Phase 5: Flow-out":
            continue
        assert marker not in body, (
            f"the observation grammar block must be anchored only at Phase 5, "
            f"found it under {name!r} too"
        )


# ---------------------------------------------------------------------------
# Item 5 — two-gate composition: each gate's refusal is independent
# ---------------------------------------------------------------------------


def test_state_coverage_mismatch_does_not_mask_a_clean_observation_set():
    """A body carrying an unrelated `## Enumerated states` section — the
    section state-coverage reads, and would refuse on here — must not change
    the observation gate's own verdict: it reads only its own grammar."""
    result = _run_gate(_fixture("parent_two_gate_composition_state_fails_observation_ok.md"))
    assert result.returncode == 0, result.stderr


def test_clean_enumerated_states_does_not_mask_a_missing_observation():
    """A body with no `## Enumerated states` section at all (state-coverage
    would pass trivially) still gets refused on its own missing observation."""
    result = _run_gate(_fixture("parent_two_gate_composition_state_ok_observation_missing.md"))
    assert result.returncode == 1
    assert "AC9" in result.stderr


# ---------------------------------------------------------------------------
# Item 6 — refusal names identifiers; completion report carries a matching clause
# ---------------------------------------------------------------------------


def test_refusal_names_the_specific_missing_identifier():
    result = _run_gate(_fixture("parent_missing_observation.md"))
    assert result.returncode == 1
    assert "AC9" in result.stderr


def test_completion_report_worked_example_carries_a_criterion_observations_clause_matching_state_coverage_shape():
    text = _doc_text()
    example = re.search(r"^> (simplify:.*)$", text, re.MULTILINE)
    assert example, "execute.md must carry the Phase 6 worked completion-report example"
    line = example.group(1)
    state_m = re.search(r"state-coverage: parent (\d+), doc (\d+), missing (\d+)", line)
    obs_m = re.search(r"criterion-observations: covered (\d+), observed (\d+), missing (\d+)", line)
    assert state_m, "worked example must retain the state-coverage clause's 3-field shape"
    assert obs_m, (
        "worked example must carry a criterion-observations clause naming covered, "
        "observed, and missing counts — the same shape state-coverage already uses"
    )
