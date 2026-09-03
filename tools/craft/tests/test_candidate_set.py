"""Tests for the candidate-set derivation gate.

The gate reads a spec body on stdin, derives its declared criteria via the
sibling `covers_gate.py`'s `parse_criteria`, derives what the spec's
`## Slices` ledger records as covered, and prints the candidate set (criteria
minus covered) plus whether the coverage union is complete enough to certify
loop termination.

Fixtures are synthetic spec bodies under `tests/fixtures/` — never real vault
records.

Exit-code contract:
  0 → derived cleanly, token block on stdout
  1 → integrity violation: a coverage token names an identifier the spec does
      not declare (`reason-code: undeclared-covered-identifier`)
  2 → fail-closed: empty/non-UTF-8 stdin, no `## Acceptance Criteria` heading,
      zero criterion identifiers (`reason-code: zero-criterion-identifiers`),
      or a malformed coverage token (`reason-code: malformed-coverage-token`)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "craft" / "scripts"
GATE = SCRIPTS_DIR / "candidate_set.py"
FIXTURES = Path(__file__).parent / "fixtures"

TWO_LINES_TWO_COVERED = (FIXTURES / "spec_candidate_two_lines_two_covered.md").read_text(
    encoding="utf-8"
)
FULL_COVERAGE = (FIXTURES / "spec_candidate_full_coverage.md").read_text(encoding="utf-8")
FULL_COVERAGE_WITH_LEGACY_LINE = (
    FIXTURES / "spec_candidate_full_coverage_with_legacy_line.md"
).read_text(encoding="utf-8")
UNION_DISJOINT = (FIXTURES / "spec_candidate_union_disjoint.md").read_text(encoding="utf-8")
EMPTY_SLICES_SECTION = (FIXTURES / "spec_candidate_empty_slices_section.md").read_text(
    encoding="utf-8"
)
NO_SLICES_SECTION = (FIXTURES / "spec_candidate_no_slices_section.md").read_text(
    encoding="utf-8"
)
UNDECLARED_COVERAGE = (FIXTURES / "spec_candidate_undeclared_coverage.md").read_text(
    encoding="utf-8"
)
MALFORMED_TOKEN = (FIXTURES / "spec_candidate_malformed_token.md").read_text(encoding="utf-8")
MISSING_HEADING_SPEC = (FIXTURES / "spec_missing_ac_heading.md").read_text(encoding="utf-8")
ZERO_CRITERIA_SPEC = (FIXTURES / "spec_zero_criteria.md").read_text(encoding="utf-8")
FORGED_FENCE = (FIXTURES / "spec_candidate_forged_slices_in_fence.md").read_text(
    encoding="utf-8"
)
FORGED_LINE_SEPARATOR = (
    FIXTURES / "spec_candidate_forged_slices_via_line_separator.md"
).read_text(encoding="utf-8")
INLINE_MENTION = (FIXTURES / "spec_candidate_inline_slices_mention.md").read_text(
    encoding="utf-8"
)

_UNDECLARED_REASON_CODE = "reason-code: undeclared-covered-identifier"
_MALFORMED_REASON_CODE = "reason-code: malformed-coverage-token"
_ZERO_CRITERIA_REASON_CODE = "reason-code: zero-criterion-identifiers"


def _run(spec_body: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=spec_body,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
    )


def _tokens(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


# ---- 1. two ledger lines, two identifiers covered -------------------------


def test_two_ledger_lines_yield_remaining_seven_candidates():
    r = _run(TWO_LINES_TWO_COVERED)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "AC3, AC4, AC5, AC6, AC7, AC8, AC9"
    assert tokens["complete-eligible"] == "yes"


# ---- 2. full coverage terminates the loop ----------------------------------


def test_full_coverage_yields_no_candidates_and_eligible():
    r = _run(FULL_COVERAGE)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "none"
    assert tokens["complete-eligible"] == "yes"


# ---- 3. full coverage but one legacy line blocks the eligibility claim ----


def test_full_coverage_with_one_legacy_line_is_not_eligible():
    r = _run(FULL_COVERAGE_WITH_LEGACY_LINE)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "none"
    assert tokens["complete-eligible"] == "no"


# ---- 4. coverage is a union across lines, not latest-wins ------------------


def test_coverage_is_the_union_across_disjoint_ledger_lines():
    r = _run(UNION_DISJOINT)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["covered"] == "AC1, AC3, AC2, AC4" or set(
        tokens["covered"].split(", ")
    ) == {"AC1", "AC2", "AC3", "AC4"}
    assert set(tokens["candidates"].split(", ")) == {"AC5", "AC6", "AC7", "AC8", "AC9"}
    assert tokens["complete-eligible"] == "yes"


# ---- 5. no ledger at all, or an empty one, still yields every criterion ----


def test_empty_slices_section_yields_every_criterion_as_candidate():
    r = _run(EMPTY_SLICES_SECTION)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"
    assert tokens["complete-eligible"] == "yes"


def test_no_slices_section_yields_every_criterion_as_candidate():
    r = _run(NO_SLICES_SECTION)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"
    assert tokens["complete-eligible"] == "yes"


# ---- 6. undeclared coverage identifier is an integrity violation ----------


def test_undeclared_coverage_identifier_exits_1_with_reason_code():
    r = _run(UNDECLARED_COVERAGE)
    assert r.returncode == 1, r.stderr + r.stdout
    assert _UNDECLARED_REASON_CODE in r.stderr, r.stderr
    assert "AC99" in r.stderr


# ---- 7. malformed coverage token fails closed ------------------------------


def test_malformed_coverage_token_exits_2_with_reason_code():
    r = _run(MALFORMED_TOKEN)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _MALFORMED_REASON_CODE in r.stderr, r.stderr


# ---- 8. no heading / zero identifiers fail closed --------------------------


def test_missing_acceptance_criteria_heading_exits_2():
    r = _run(MISSING_HEADING_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout


def test_zero_criterion_identifiers_exits_2_with_reason_code():
    r = _run(ZERO_CRITERIA_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE in r.stderr, r.stderr


# ---- 9. empty stdin fails closed -------------------------------------------


def test_empty_stdin_exits_2():
    r = _run("")
    assert r.returncode == 2, r.stderr + r.stdout


# ---- 10. forged-structure class: mandatory, not optional -------------------


def test_forged_slices_ledger_inside_fenced_block_is_invisible():
    """The fake `## Slices` heading and fully-covering line sit inside a
    fenced worked example; the real (empty) ledger below it must be what the
    gate reads, so the fenced forgery must not produce `candidates: none`."""
    r = _run(FORGED_FENCE)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] != "none"
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"


def test_forged_slices_ledger_behind_line_separator_is_invisible():
    """The fake heading and line are hidden behind U+2028 inside one ordinary
    prose paragraph, not a real CommonMark line break. A parser that treats
    U+2028 as a line break would anchor on the fake heading and wrongly
    report the spec fully covered."""
    r = _run(FORGED_LINE_SEPARATOR)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] != "none"
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"


def test_inline_mid_sentence_slices_mention_does_not_anchor():
    """`## Slices` mentioned inline, mid-sentence, is not a heading match at
    line start, so the fake ledger line that follows it must not be read as
    coverage — only the real, empty `## Slices` section at the end counts."""
    r = _run(INLINE_MENTION)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"
    assert tokens["complete-eligible"] == "yes"


# ---- 11. sibling import resolves regardless of the caller's cwd -----------


def test_sibling_import_resolves_from_an_unrelated_cwd(tmp_path):
    r = _run(FULL_COVERAGE, cwd=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ModuleNotFoundError" not in r.stderr


# ---- 13. a symlinked entry point stays sibling-import-safe -----------------


def test_symlinked_entry_point_still_resolves_the_sibling_import(tmp_path):
    link = tmp_path / "candidate_set_link.py"
    link.symlink_to(GATE)
    r = subprocess.run(
        [sys.executable, str(link)],
        input=FULL_COVERAGE,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ModuleNotFoundError" not in r.stderr
