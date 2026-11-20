"""A parent that records criteria it dropped is read correctly by every reader of a parent's coverage.

The parent's body carries a `**Covers:**` line, optionally a `**Partially covers:**` line, and a
`**Narrowed:** dropped AC<n>, … — <reason>` line naming criteria taken off its coverage. Each reader
runs in-process on that body: `ParentCoverage`, the covers and observation gates, a ledger line
reconciled from the parent through `ledger_gate` and `candidate_set`, and the `--diff` append the council
review makes through the in-process lore runner. The two inputs differ in what the coverage lines say about
the dropped criteria: off coverage entirely, and wrongly written as partially covered.
"""

from __future__ import annotations

import difflib
import io
import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import candidate_set  # noqa: E402
import covers_gate  # noqa: E402
import ledger_gate  # noqa: E402
import observation_gate  # noqa: E402
from parent_coverage import ParentCoverage  # noqa: E402

from _lore_runner import LoreRunner  # noqa: E402

VAULT = "v"
SPEC_NAME = "order-export"
PARENT_NAME = "export-the-orders-to-a-csv-file"
NARROWED = "**Narrowed:** dropped AC4, AC5 — the legacy reports can ship later"

SPEC_BODY = """# Order export

## Acceptance Criteria

- **AC1.** The tool writes every order to a CSV file. *Verified by: automated assertion.*

- **AC2.** An order's total is the amount charged. *Verified by: automated assertion.*

- **AC3.** The tool writes only the orders since a date. *Verified by: automated assertion.*

- **AC4.** The tool exports each legacy report the same way. *Verified by: automated assertion.*

- **AC5.** Each legacy report also exports as JSON Lines. *Verified by: automated assertion.*
"""


OBSERVATIONS = [f"- **AC{n}** \u2014 automated-assertion \u2014 tests/test_export.py::test_ac{n}" for n in (1, 2, 3)]


def parent_body(*, partial: str | None, narrowed_first: bool = False) -> str:
    coverage = ["**Covers:** AC1, AC2, AC3"]
    if partial:
        coverage.append(f"**Partially covers:** {partial}")
    middle = [NARROWED, ""] + coverage if narrowed_first else coverage + [NARROWED]
    head = ["**Value claim:** You can export the orders to a CSV file.", "", *middle, ""]
    return "\n".join(head + ["## Criterion observations", "", *OBSERVATIONS, ""])


# (id, partial line written on the parent, tokens the candidate set derives from the reconciled ledger line)
CASES = [
    pytest.param(None, {"covered": "AC1, AC2, AC3", "candidates": "AC4, AC5", "partial": "none"}, id="dropped-off-coverage"),
    pytest.param("AC4", {"covered": "AC1, AC2, AC3", "candidates": "AC4, AC5", "partial": "AC4"}, id="dropped-written-as-partial"),
]


@pytest.fixture
def run_gate(monkeypatch, capsys):
    def run(module, argv, stdin):
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="utf-8"))
        code = module.main(argv)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


def tokens(stdout: str) -> dict[str, str]:
    found = {}
    for line in stdout.splitlines():
        key, _, value = line.partition(":")
        found[key.strip()] = value.strip()
    return found


def ledger_line(coverage: ParentCoverage) -> str:
    covers = f", covers {', '.join(coverage.covers)}" if coverage.covers else ""
    partial = f", partially covers {', '.join(coverage.partial)}" if coverage.partial else ""
    return f"- **Export orders** — an export. (`task/{PARENT_NAME}`, closed 2026-11-20{covers}{partial})"


def spec_with_ledger(coverage: ParentCoverage) -> str:
    return f"{SPEC_BODY}\n## Slices\n\n{ledger_line(coverage)}\n"


@pytest.mark.parametrize("narrowed_first", [False, True], ids=["after-covers", "before-covers"])
@pytest.mark.parametrize("partial, _derived", CASES)
def test_coverage_lines_read_the_same_wherever_the_narrowed_line_sits(partial, _derived, narrowed_first):
    coverage = ParentCoverage(parent_body(partial=partial, narrowed_first=narrowed_first))

    assert coverage.covers == ["AC1", "AC2", "AC3"]
    assert coverage.partial == ([partial] if partial else [])


@pytest.mark.parametrize("partial, _derived", CASES)
def test_the_observation_gate_certifies_a_parent_that_records_what_it_dropped(partial, _derived, run_gate):
    code, _out, err = run_gate(observation_gate, [], parent_body(partial=partial))

    assert code == 0, err


def test_the_observation_gate_still_refuses_a_dropped_criterion_written_back_onto_covers(run_gate):
    body = parent_body(partial=None).replace("**Covers:** AC1, AC2, AC3", "**Covers:** AC1, AC2, AC3, AC4")

    code, _out, err = run_gate(observation_gate, [], body)

    assert code == 1, err
    assert "AC4" in err


@pytest.mark.parametrize("partial, derived", CASES)
def test_dropped_criteria_land_where_the_coverage_line_puts_them(partial, derived, run_gate, tmp_path):
    coverage = ParentCoverage(parent_body(partial=partial))
    spec = spec_with_ledger(coverage)
    parents = tmp_path / "parents.json"
    entry = {"covers": ", ".join(coverage.covers)}
    if coverage.partial:
        entry["partially covers"] = ", ".join(coverage.partial)
    parents.write_text(json.dumps({PARENT_NAME: entry}), encoding="utf-8")

    ledger_code, _out, ledger_err = run_gate(ledger_gate, ["--parent-coverage", str(parents)], spec)
    code, out, err = run_gate(candidate_set, [], spec)

    assert ledger_code == 0, ledger_err
    assert code == 0, err
    got = tokens(out)
    assert {key: got[key] for key in derived} == derived


@pytest.mark.parametrize("partial, _derived", CASES)
def test_the_council_review_append_leaves_the_narrowed_line_in_place(partial, _derived, tmp_path):
    runner = LoreRunner(tmp_path, vaults=(VAULT,))
    body = parent_body(partial=partial)
    for argv, stdin in (
        (["record", "create", "--kind", "spec", "--title", "Order export", "--status", "ready", "--vault", VAULT], SPEC_BODY),
        (["record", "create", "--kind", "task", "--title", "Export the orders to a CSV file", "--status", "in-progress",
          "--related", f"spec={SPEC_NAME}", "--label", "craft/slice-parent", "--vault", VAULT], body),
    ):
        done = runner(argv, stdin)
        assert done.returncode == 0, done.stderr
    appended = body + "\n## Council Review\n\n- *Critical:* none\n"
    diff = "".join(difflib.unified_diff(body.splitlines(True), appended.splitlines(True), "a", "b"))

    done = runner(["record", "update", f"task/{PARENT_NAME}", "--vault", VAULT, "--diff"], diff)
    assert done.returncode == 0, done.stderr
    shown = runner(["record", "show", "--json", "--vault", VAULT, f"task/{PARENT_NAME}"])
    after = json.loads(shown.stdout)["body"]

    assert NARROWED in after.splitlines()
    assert re.search(r"^## Council Review$", after, re.M)
    reread = ParentCoverage(after)
    assert (reread.covers, reread.partial) == (["AC1", "AC2", "AC3"], [partial] if partial else [])
