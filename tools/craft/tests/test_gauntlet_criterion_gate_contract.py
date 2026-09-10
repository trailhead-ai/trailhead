"""Contract tests binding `gauntlet/SKILL.md`'s wiring of `criterion_gate.py` to the
gate's actual behavior, and binding `templates/spec.md`'s worked criterion example to
the gate's actual verdict on it.

These are relationship tests, never prose-presence: each one derives the invocation,
the exit-code contract, the anchor position, or the worked example directly out of the
two documents' own text, and then executes the real gate against it. A renamed script,
a moved anchor, a wrong flag, a divergent exit code, or an edit that teaches a shape the
gate refuses fails the corresponding test for the same reason the original defect would
have — the pattern `test_slice_title_quoting_contract.py` already establishes for
`slice/SKILL.md`.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
GAUNTLET_SKILL = CRAFT / "skills" / "gauntlet" / "SKILL.md"
SPEC_TEMPLATE = CRAFT / "templates" / "spec.md"
GATE = CRAFT / "scripts" / "criterion_gate.py"
FIXTURES = Path(__file__).parent / "fixtures"


def _skill_text() -> str:
    return GAUNTLET_SKILL.read_text(encoding="utf-8")


def _template_text() -> str:
    return SPEC_TEMPLATE.read_text(encoding="utf-8")


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _step(name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading."""
    text = _skill_text()
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d+\.", rest)
    return rest[: end.start()] if end else rest


def _normalize(text: str) -> str:
    """Collapse whitespace runs (including a markdown soft line wrap) to a
    single space, so a phrase check does not depend on where prose happens
    to wrap in the source file."""
    return " ".join(text.split())


def _run_gate(spec_body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE)], input=spec_body, capture_output=True, text=True
    )


ALL_CLEAN = _fixture("crit_all_clean.md")
ZERO_IDENTIFIERS = _fixture("crit_zero_identifiers.md")
REFUSED_CODE_LOCATION = _fixture("crit_refused_code_location.md")
DUPLICATE_HEADING = _fixture("crit_duplicate_heading.md")
UNTERMINATED_FENCE = _fixture("crit_unterminated_fence.md")
UNTERMINATED_HTML_COMMENT = _fixture("crit_unterminated_html_comment.md")

_ZERO_CRITERIA_REASON_CODE = "reason-code: zero-criterion-identifiers"
_DUPLICATE_HEADING_REASON_CODE = "reason-code: duplicate-acceptance-criteria-heading"
_UNTERMINATED_REGION_REASON_CODE = "reason-code: unterminated-masked-region"
_EMPTY_STDIN_REASON_CODE = "reason-code: empty-stdin"
_NON_UTF8_STDIN_REASON_CODE = "reason-code: non-utf8-stdin"


# ---------------------------------------------------------------------------
# Item 1 — the documented invocation, executed as written, exits 0/non-zero
# ---------------------------------------------------------------------------


def _documented_invocation() -> str:
    step1 = _step("### 1. Resolve and read the spec")
    m = re.search(r"```sh\n(.+?)\n```", step1, re.DOTALL)
    assert m, (
        "gauntlet/SKILL.md step 1 must show a fenced shell invocation of the "
        "criterion gate"
    )
    command = m.group(1).strip()
    assert "criterion_gate.py" in command, command
    return command


def _run_documented_invocation(spec_body: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Build and execute the exact invocation step 1 documents, with `lore` stubbed
    to emit `spec_body` on stdout — the pattern `test_slice_title_quoting_contract.py`
    establishes for `slice/SKILL.md`'s own documented invocation.
    """
    spec_file = tmp_path / "spec_body.md"
    spec_file.write_text(spec_body, encoding="utf-8")
    command = _documented_invocation().replace("<spec-id>", "spec-x")

    script = (
        f"lore() {{ cat {shlex.quote(str(spec_file))}; }}; "
        f"CLAUDE_PLUGIN_ROOT={shlex.quote(str(CRAFT))}; "
        f"{command}"
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_documented_invocation_exits_0_against_a_conforming_spec(tmp_path):
    r = _run_documented_invocation(ALL_CLEAN, tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout


def test_documented_invocation_exits_nonzero_against_a_refused_criterion(tmp_path):
    r = _run_documented_invocation(REFUSED_CODE_LOCATION, tmp_path)
    assert r.returncode != 0, r.stdout


# ---------------------------------------------------------------------------
# Item 2 — the document's stated exit-code contract agrees with the gate's
# ---------------------------------------------------------------------------


def _documented_exit0_code() -> int:
    step1 = _step("### 1. Resolve and read the spec")
    m = re.search(r"Exit (\d+) certifies", step1)
    assert m, (
        "gauntlet/SKILL.md step 1 must state, as 'Exit N certifies', which "
        "exit code certifies a clean spec"
    )
    return int(m.group(1))


def _documented_exit1_code() -> int:
    step1 = _normalize(_step("### 1. Resolve and read the spec"))
    m = re.search(r"exit (\d+) \(integrity violation", step1)
    assert m, (
        "gauntlet/SKILL.md step 1 must state, as 'exit N (integrity "
        "violation', which exit code is the integrity-violation refusal"
    )
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Item 3 — the gate is anchored before the pass-dispatch step, by document order
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Item 4 — the template's worked criterion example certifies
# ---------------------------------------------------------------------------


def _worked_conformant_criterion() -> str:
    text = _template_text()
    m = re.search(
        r"Conformant \(names the role\): \"(- \*\*AC\d+\.\*\* .+?)\"", text, re.DOTALL
    )
    assert m, (
        "templates/spec.md must show a worked conformant criterion example, "
        "quoted as a literal `- **ACn.** ...` bullet"
    )
    return m.group(1)


def test_templates_worked_conformant_criterion_certifies():
    bullet = _worked_conformant_criterion()
    spec_body = f"## Acceptance Criteria\n\n{bullet}\n"
    r = _run_gate(spec_body)
    assert r.returncode == 0, r.stderr + r.stdout


def test_templates_worked_refused_criterion_is_actually_refused():
    """The template's paired 'before' example is not itself taught — but it must
    be a real refusal, or the pairing teaches a false contrast."""
    text = _template_text()
    m = re.search(
        r"Refused \(names the endpoint\): \"(- \*\*AC\d+\.\*\* .+?)\"", text, re.DOTALL
    )
    assert m, "templates/spec.md must show a worked refused criterion example"
    spec_body = f"## Acceptance Criteria\n\n{m.group(1)}\n"
    r = _run_gate(spec_body)
    assert r.returncode == 1, r.stderr + r.stdout


# ---------------------------------------------------------------------------
# Item 4b — the verification method bar's own worked pair proves the trailer
# is required unconditionally, even on a criterion an automated assertion
# already verifies. If the guidance ever regresses to exempting that case,
# either this pair stops existing (failing the `assert m` below) or an
# editor who keeps the pair internally consistent with a reverted rule turns
# the "Conformant" bullet into one the real gate refuses (failing the
# certifies test below) — both are ways this catches the regression.
# ---------------------------------------------------------------------------


def _worked_verification_refused_criterion() -> str:
    text = _template_text()
    m = re.search(
        r"Refused \(omits the trailer\): \"(- \*\*AC\d+\.\*\*.+?)\"", text, re.DOTALL
    )
    assert m, (
        "templates/spec.md must show a worked example of a criterion refused "
        "for omitting the verification trailer"
    )
    return m.group(1)


def _worked_verification_conformant_criterion() -> str:
    text = _template_text()
    m = re.search(
        r"Conformant \(states the method\): \"(- \*\*AC\d+\.\*\*.+?)\"", text, re.DOTALL
    )
    assert m, (
        "templates/spec.md must show a worked example of a criterion made "
        "conformant by stating the verification method"
    )
    return m.group(1)


def test_templates_verification_bar_worked_refused_criterion_is_actually_refused():
    """The 'omits the trailer' example must be a real refusal specifically for
    lacking a trailer — proving the bar applies even though the criterion is
    otherwise fine — or the pairing teaches a false contrast."""
    bullet = _worked_verification_refused_criterion()
    spec_body = f"## Acceptance Criteria\n\n{bullet}\n"
    r = _run_gate(spec_body)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "carries no verification trailer" in r.stderr


def test_templates_verification_bar_worked_conformant_criterion_certifies():
    bullet = _worked_verification_conformant_criterion()
    spec_body = f"## Acceptance Criteria\n\n{bullet}\n"
    r = _run_gate(spec_body)
    assert r.returncode == 0, r.stderr + r.stdout


# ---------------------------------------------------------------------------
# Item 5 — the legacy carve-out composes with the refusal, both directions —
# both directions derived from the document's own wiring text, not hardcoded
# in the test, so a wiring edit that widened or lost the discrimination fails.
# ---------------------------------------------------------------------------


def _documented_carveout_code() -> str:
    step1 = _normalize(_step("### 1. Resolve and read the spec"))
    m = re.search(
        r"\*\*Exit 2 with `(reason-code: [\w-]+)` is the one non-zero exit "
        r"that proceeds\*\*",
        step1,
    )
    assert m, (
        "gauntlet/SKILL.md step 1 must name, in one bolded sentence, which "
        "single reason-code is the carve-out that proceeds"
    )
    return m.group(1)


def _documented_blocking_codes() -> set[str]:
    step1 = _step("### 1. Resolve and read the spec")
    idx = step1.index("Every other non-zero exit refuses the run")
    tail = step1[idx:]
    codes = set(re.findall(r"reason-code: [\w-]+", tail))
    assert codes, (
        "gauntlet/SKILL.md step 1 must name, in the refusal paragraph, the "
        "reason-codes that block dispatch"
    )
    return codes


def test_zero_identifier_spec_reaches_dispatch_via_the_carveout():
    carveout = _documented_carveout_code()
    r = _run_gate(ZERO_IDENTIFIERS)
    assert r.returncode == 2
    assert carveout in r.stderr


def test_refused_criterion_spec_does_not_reach_dispatch():
    carveout = _documented_carveout_code()
    r = _run_gate(REFUSED_CODE_LOCATION)
    assert r.returncode == 1
    assert carveout not in r.stderr


# ---------------------------------------------------------------------------
# Item 6 — every non-carve-out exit-2 reason blocks dispatch, one test each.
# "Wiring coded as exit 2 proceeds" would pass item 5 (the carve-out case
# alone) but fail here, because each of these is a *different* exit-2 reason
# that the carve-out's own reason-code test does not exercise.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Item 7 — the unapplied-bars report is pinned to the carve-out path
# ---------------------------------------------------------------------------


def _carveout_clause() -> str:
    """The paragraph in step 1 that declares the carve-out reason-code — scoped
    to that declaring clause, not the whole document, per
    `lesson/scope-a-declaration-test-to-the-declaring-clause-not-to-the-whole-file`.
    """
    step1 = _step("### 1. Resolve and read the spec")
    idx = step1.index(_ZERO_CRITERIA_REASON_CODE)
    # the clause runs from the start of its paragraph to the next blank line
    para_start = step1.rfind("\n\n", 0, idx) + 2
    para_end = step1.find("\n\n", idx)
    if para_end == -1:
        para_end = len(step1)
    return step1[para_start:para_end]
