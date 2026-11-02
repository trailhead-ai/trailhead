"""The two guards of the `ritual-output-wording` eval's counting extension that a script other than the wording
grader decides.

G6 is the slice statement's covered-criteria value, drafted on a slice covering seven criteria of the count spec: the
value must pass `covers_gate.py` over the count spec and must name exactly the seven.

G7 is the ledger line a slice's reconcile appends for a done parent carrying a `**Covers:**` of those seven. A line is
graded by the two scripts that read it: placed under `## Slices` of the count spec, `ledger_gate.py
--parent-coverage` must exit 0 and `candidate_set.py` must report all seven `covered:`. Both are needed, because the
ledger gate passes a line whose covers token was dropped and the candidate set then offers the finished criteria again.

The helpers here are what the eval's later tasks call to grade the G6 and G7 captures. The committed captures are
registered in the two tables below; a capture in `runs/` that is not registered fails.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

CRAFT_ROOT = Path(__file__).parent.parent
SCRIPTS = CRAFT_ROOT / "plugins" / "craft" / "scripts"
CASE = CRAFT_ROOT / "plugins" / "craft" / "evals" / "ritual-output-wording"
COUNT_SPEC = CASE / "fixtures" / "count-spec.md"
RUNS = CASE / "runs"

sys.path.insert(0, str(SCRIPTS))
import covers_gate  # noqa: E402

SEVEN = ["AC5", "AC6", "AC7", "AC8", "AC9", "AC10", "AC11"]
SLICE_ID = "the-year-end-slice"
COVERS_VALUE = ", ".join(SEVEN)
LEDGER_LINE = (
    "- **The year-end slice** — the trustees get the year's attendance, wall length and refunds as three tables "
    f"they can download. (`task/{SLICE_ID}`, closed 2026-10-30, covers {COVERS_VALUE})"
)


@dataclass(frozen=True)
class LedgerVerdict:
    passes: bool
    gate_returncode: int
    gate_reason_code: str | None
    covered: tuple[str, ...]


def last_nonblank_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _run(script: str, args: list[str], stdin: str):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


def grade_ledger_line(capture_text: str, workdir: Path) -> LedgerVerdict:
    """Place the capture's last non-blank line under `## Slices` of the count spec and run both readers.

    The parent the line is checked against carries `**Covers:**` of the seven. The line passes when the ledger gate
    exits 0 and the candidate-set gate reports all seven as `covered:`.
    """
    spec = COUNT_SPEC.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + last_nonblank_line(capture_text) + "\n"
    parents = workdir / "parents.json"
    parents.write_text(json.dumps({SLICE_ID: {"covers": COVERS_VALUE}}), encoding="utf-8")
    gate = _run("ledger_gate.py", ["--parent-coverage", str(parents)], spec)
    reason = re.search(r"reason-code: (\S+)$", gate.stderr, re.MULTILINE)
    candidates = _run("candidate_set.py", [], spec)
    covered_token = re.search(r"^covered: (.*)$", candidates.stdout, re.MULTILINE)
    covered = ()
    if candidates.returncode == 0 and covered_token and covered_token.group(1) != "none":
        covered = tuple(part.strip() for part in covered_token.group(1).split(","))
    passes = gate.returncode == 0 and set(covered) == set(SEVEN)
    return LedgerVerdict(passes, gate.returncode, reason.group(1) if reason else None, covered)


def covers_value_names_the_seven(capture_text: str) -> bool:
    """G6: the capture's last non-blank line, as the `**Covers:**` value, passes `covers_gate.py` over the count
    spec and names exactly the seven criteria."""
    value = last_nonblank_line(capture_text).strip()
    value = re.sub(r"^\*\*Covers:\*\*\s*", "", value)
    gate = _run("covers_gate.py", ["--covers", value], COUNT_SPEC.read_text(encoding="utf-8"))
    if gate.returncode != 0:
        return False
    return set(covers_gate.parse_covers(value)) == set(SEVEN)


# --- G7: the ledger line -----------------------------------------------------------------------


def test_the_exact_line_passes_both_readers(tmp_path):
    verdict = grade_ledger_line(LEDGER_LINE, tmp_path)
    assert verdict.passes
    assert verdict.gate_returncode == 0
    assert set(verdict.covered) == set(SEVEN)


def test_only_the_last_nonblank_line_is_the_line_to_append(tmp_path):
    reply = f"Here is the line to append:\n\n{LEDGER_LINE}\n\n\n"
    assert grade_ledger_line(reply, tmp_path).passes


def test_a_line_reworded_as_counts_fails_the_ledger_gate(tmp_path):
    line = LEDGER_LINE.replace(f"covers {COVERS_VALUE}", "covers seven criteria across two sections")
    verdict = grade_ledger_line(line, tmp_path)
    assert not verdict.passes
    assert verdict.gate_returncode == 2
    assert verdict.gate_reason_code == "malformed-coverage-token"


def test_a_line_reworded_as_a_range_fails_the_ledger_gate(tmp_path):
    line = LEDGER_LINE.replace(f"covers {COVERS_VALUE}", "covers AC5-AC11")
    verdict = grade_ledger_line(line, tmp_path)
    assert not verdict.passes
    assert verdict.gate_returncode == 2
    assert verdict.gate_reason_code == "malformed-coverage-token"


def test_a_line_short_of_one_criterion_fails_the_ledger_gate(tmp_path):
    line = LEDGER_LINE.replace(f"covers {COVERS_VALUE}", "covers " + ", ".join(SEVEN[:-1]))
    verdict = grade_ledger_line(line, tmp_path)
    assert not verdict.passes
    assert verdict.gate_returncode == 1
    assert verdict.gate_reason_code == "coverage-contradicts-parent"


def test_a_line_with_no_covers_token_passes_the_ledger_gate_and_fails_the_candidate_set(tmp_path):
    line = LEDGER_LINE.replace(f", covers {COVERS_VALUE}", "")
    verdict = grade_ledger_line(line, tmp_path)
    assert verdict.gate_returncode == 0
    assert verdict.covered == ()
    assert not verdict.passes


def test_a_line_naming_a_slice_with_no_parent_fails_though_the_candidate_set_reads_all_seven(tmp_path):
    line = LEDGER_LINE.replace(f"task/{SLICE_ID}", "task/some-other-slice")
    verdict = grade_ledger_line(line, tmp_path)
    assert set(verdict.covered) == set(SEVEN)
    assert verdict.gate_returncode == 1
    assert verdict.gate_reason_code == "orphaned-ledger-entry"
    assert not verdict.passes


# --- G6: the covered-criteria value -------------------------------------------------------------


def test_a_value_naming_the_seven_passes_the_covers_check():
    assert covers_value_names_the_seven(COVERS_VALUE)


def test_a_value_with_its_label_is_read_as_the_value():
    assert covers_value_names_the_seven(f"Statement above.\n\n**Covers:** {COVERS_VALUE}\n")


def test_a_value_naming_six_is_accepted_by_the_gate_but_fails_the_check():
    six = ", ".join(SEVEN[:-1])
    assert _run("covers_gate.py", ["--covers", six], COUNT_SPEC.read_text(encoding="utf-8")).returncode == 0
    assert not covers_value_names_the_seven(six)


def test_a_value_repeating_an_identifier_fails_the_check():
    assert not covers_value_names_the_seven(COVERS_VALUE + ", AC5")


def test_a_value_naming_an_identifier_the_spec_does_not_declare_fails_the_check():
    assert not covers_value_names_the_seven(COVERS_VALUE + ", AC13")


# --- the committed pilot captures ---------------------------------------------------------------

# G7 captures by stem: True when the ledger line passes both readers. G6 captures by stem: True when the value passes
# the covers check. A capture in runs/ with the prefix and no entry here fails the registration test.
G7_CAPTURES: dict[str, bool] = {"count-g7-proposal-treated-1": True}
G6_CAPTURES: dict[str, bool] = {"count-g6-proposal-treated-1": True}


def _captures(site: str) -> list[str]:
    return sorted(
        p.stem
        for p in RUNS.glob(f"count-{site}-*.txt")
        if re.fullmatch(rf"count-{site}-.+-\d+\.txt", p.name) and not p.name.endswith(".stderr.txt")
    )


def test_every_g7_capture_in_runs_is_registered():
    assert _captures("g7") == sorted(G7_CAPTURES)


def test_every_g6_capture_in_runs_is_registered():
    assert _captures("g6") == sorted(G6_CAPTURES)


@pytest.mark.parametrize("stem", sorted(G7_CAPTURES))
def test_a_g7_capture_grades_to_its_registered_verdict(stem, tmp_path):
    text = (RUNS / f"{stem}.txt").read_text(encoding="utf-8")
    assert grade_ledger_line(text, tmp_path).passes is G7_CAPTURES[stem]


@pytest.mark.parametrize("stem", sorted(G6_CAPTURES))
def test_a_g6_capture_grades_to_its_registered_verdict(stem):
    text = (RUNS / f"{stem}.txt").read_text(encoding="utf-8")
    assert covers_value_names_the_seven(text) is G6_CAPTURES[stem]
