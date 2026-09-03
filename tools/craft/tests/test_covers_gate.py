"""Tests for the covers gate.

The gate certifies a drafted `--covers` identifier list against a spec's
declared `## Acceptance Criteria` before a slice parent record is written.
The spec body arrives on stdin; the drafted list is a `--covers` argument.

Fixtures are synthetic spec bodies under `tests/fixtures/` — never real vault
records.

Exit-code contract (matches `leak_gate.py`):
  0 → clean (every covered identifier is a real criterion, grammar valid)
  1 → violation (unknown identifier, bad grammar, duplicate — prints a
      `reason:` line)
  2 → error / fail-closed (empty stdin, no `## Acceptance Criteria` heading,
      missing `--covers` argument)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "covers_gate.py"
FIXTURES = Path(__file__).parent / "fixtures"

NINE_CRITERIA_SPEC = (FIXTURES / "spec_ac1_to_ac9.md").read_text(encoding="utf-8")
MISSING_HEADING_SPEC = (FIXTURES / "spec_missing_ac_heading.md").read_text(encoding="utf-8")
INDENTED_HEADING_IN_FENCE_SPEC = (
    FIXTURES / "spec_indented_heading_in_fence.md"
).read_text(encoding="utf-8")
ZERO_CRITERIA_SPEC = (FIXTURES / "spec_zero_criteria.md").read_text(encoding="utf-8")


def _run(covers: str | None, spec_body: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE)]
    if covers is not None:
        cmd += ["--covers", covers]
    return subprocess.run(cmd, input=spec_body, capture_output=True, text=True)


# ---- clean path --------------------------------------------------------


def test_known_identifier_against_nine_criterion_spec_exits_0():
    r = _run("AC2", NINE_CRITERIA_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


# ---- unknown identifier -------------------------------------------------


def test_unknown_identifier_exits_1_and_names_it():
    r = _run("AC12", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC12" in r.stderr


# ---- anti-heuristic grammar cases ---------------------------------------


def test_covers_value_with_prose_alongside_identifier_exits_1():
    r = _run("AC2 also covers login", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


def test_covers_value_with_free_text_and_no_identifier_exits_1():
    r = _run("this covers the login flow", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


def test_duplicate_identifier_exits_1():
    r = _run("AC2, AC2", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


def test_empty_covers_value_exits_1():
    r = _run("", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


def test_missing_covers_argument_exits_2():
    r = _run(None, NINE_CRITERIA_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout


# ---- whitespace grammar ---------------------------------------------------


def test_no_space_after_comma_parses_and_exits_0():
    r = _run("AC1,AC2", NINE_CRITERIA_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_single_space_after_comma_parses_and_exits_0():
    r = _run("AC1, AC2", NINE_CRITERIA_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_trailing_comma_exits_1():
    r = _run("AC1,AC2,", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


# ---- stdin / spec fail-closed --------------------------------------------


def test_empty_stdin_exits_2_not_1_or_0():
    r = _run("AC2", "")
    assert r.returncode == 2, r.stderr + r.stdout


def test_missing_acceptance_criteria_section_exits_2():
    r = _run("AC2", MISSING_HEADING_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout


def test_inline_prose_mention_of_heading_does_not_satisfy_the_anchor():
    """`spec_missing_ac_heading.md` mentions `## Acceptance Criteria` inline,
    mid-paragraph — a loose containment check would wrongly treat that as the
    real heading. The gate must anchor on the heading at line start."""
    assert "## Acceptance Criteria" in MISSING_HEADING_SPEC
    r = _run("AC2", MISSING_HEADING_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout


# ---- spec parser: sub-headings and nested bullets ------------------------


def test_parser_ignores_grouping_subheading_and_nested_bullet():
    """The fixture spec has a `###` grouping sub-heading and a nested
    sub-bullet under AC4 — neither should be countable as a criterion, so a
    `--covers` value naming only real top-level identifiers (AC1-AC9) must
    still exit 0, proving the parser didn't choke on or miscount them."""
    r = _run("AC1,AC2,AC3,AC4,AC5,AC6,AC7,AC8,AC9", NINE_CRITERIA_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_parser_rejects_a_nested_bullet_as_a_bare_criterion():
    """The nested sub-bullet under AC4 forms no criterion of its own, so no
    identifier for it exists — asserting an out-of-range identifier like
    AC10 (there is no tenth criterion) exits 1."""
    r = _run("AC10", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


# ---- heading anchor: line-start only, never a loosely-indented match -----


def test_indented_heading_inside_a_fenced_example_is_not_the_anchor():
    """The fixture's worked example contains an indented `## Acceptance
    Criteria` heading inside a fenced code block, above the real section. A
    parser that anchors on `line.strip()` would treat that fake heading as
    the section start and never reach the real AC1/AC2 below it. The real
    identifiers must still certify."""
    r = _run("AC1,AC2", INDENTED_HEADING_IN_FENCE_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


# ---- zero-identifier spec: legacy shape fails closed with its own reason --


def test_zero_criterion_identifiers_exits_2_with_a_distinct_reason():
    """A spec whose `## Acceptance Criteria` heading is present but which
    declares no `**ACn.**` identifiers (predates the convention) must fail
    closed at exit 2 — never exit 1's 'unknown criterion identifier(s)',
    which misdiagnoses a legacy spec as a drafting error — and its reason
    line must name that the spec declares no criterion identifiers, so a
    caller can distinguish this case from every other exit-2 reason."""
    r = _run("AC1", ZERO_CRITERIA_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "no criterion identifiers" in r.stderr, r.stderr


# ---- non-UTF-8 stdin fails closed, never a traceback ----------------------


def test_non_utf8_stdin_exits_2_not_a_traceback():
    cmd = [sys.executable, str(GATE), "--covers", "AC1"]
    r = subprocess.run(cmd, input=b"\xff\xfe not valid utf-8", capture_output=True)
    assert r.returncode == 2, r.stderr + r.stdout
    assert b"Traceback" not in r.stderr


# ---- --covers grammar: no trailing newline admitted ------------------------


def test_covers_value_with_trailing_newline_fails_grammar_not_membership():
    """`_COVERS_RE`'s trailing `$` (without MULTILINE) matches before a
    trailing newline, so `--covers "AC1\\n"` was wrongly accepted by the
    grammar and only then refused as an unknown identifier — misnaming the
    cause. It must be refused by the grammar check itself."""
    r = _run("AC1\n", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "not a valid identifier list" in r.stderr, r.stderr
    assert "unknown criterion identifier" not in r.stderr, r.stderr
