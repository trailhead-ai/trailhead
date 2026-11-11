"""A token-less `## Slices` ledger line earns coverage credit when a `## Follow-ups`
entry is marked `done by task/<that line's own task id>`.

`fixtures/candidate_set_at_base/` holds a craft release that gives no such credit:
the gate and the sibling modules it imports, all four from one origin/main tree, so
the compatibility test can show how an older craft reads a ledger the credit-aware
loop writes (source fingerprints, from
`git hash-object`: candidate_set c1cfb375e5d4f12fb8085c8655f4ea447365ce17,
covers_gate 56cedf163b3b442d258cd4419918c767c5c59c7d, waiting
6e899536071c2b7ed86bd8dd9bd1fe388009cb4d, markdown_section
2b359c18d66bc14abdcb9f91ee903bb5bcdf4324). The compatibility test runs that
committed copy as a subprocess; it never reads git history.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
GATE = TESTS.parent / "plugins" / "craft" / "scripts" / "candidate_set.py"
BASE_GATE = TESTS / "fixtures" / "candidate_set_at_base" / "candidate_set.py"

CRITERIA = "## Acceptance Criteria\n\n- **AC1.** First.\n- **AC2.** Second.\n\n"


def _ledger(*lines: str) -> str:
    return "## Slices\n\n" + "\n".join(lines) + "\n\n"


def _entry(n: int, status: str) -> str:
    return (
        f"- **F{n}.** Title {n}\n"
        f"  - status: {status}\n"
        "  - what: something\n"
        "  - where: `a/b.py`\n"
        "  - found: while building\n"
    )


def _followups(*entries: str) -> str:
    return "## Follow-ups\n\n" + "".join(entries)


LEGACY = "- **Legacy** — claim. (`task/legacy`, closed 2026-01-01)"
COVERING = "- **Cover** — claim. (`task/cover`, closed 2026-01-02, covers AC1)"
PARTIAL = "- **Part** — claim. (`task/part`, closed 2026-01-02, partially covers AC2)"


def _run(gate: Path, body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(gate)], input=body, capture_output=True, text=True
    )


def _eligible(body: str) -> str:
    r = _run(GATE, body)
    assert r.returncode == 0, r.stderr
    return dict(ln.split(": ", 1) for ln in r.stdout.splitlines())["complete-eligible"]


def _body(ledger_lines, *followups) -> str:
    return CRITERIA + _ledger(*ledger_lines) + (_followups(*followups) if followups else "")


# A done-by entry on a token-less line's own task earns credit.
def test_done_by_own_task_credits_tokenless_line():
    body = _body([COVERING, "- **Ent** — claim. (`task/ent`, closed 2026-01-03)"],
                 _entry(1, "done by task/ent"))
    assert _eligible(body) == "yes"


def test_tokenless_line_without_done_by_stays_ineligible():
    body = _body([COVERING, "- **Ent** — claim. (`task/ent`, closed 2026-01-03)"],
                 _entry(1, "open"))
    assert _eligible(body) == "no"


def test_legacy_line_without_followups_section_stays_ineligible():
    assert _eligible(_body([COVERING, LEGACY])) == "no"


def test_done_by_task_no_ledger_line_names_earns_nothing():
    body = _body([COVERING, LEGACY], _entry(1, "done by task/z"))
    assert _eligible(body) == "no"


@pytest.mark.parametrize(
    "status", ["folded into task/legacy", "dropped: task/legacy"]
)
def test_folded_or_dropped_entry_naming_the_task_earns_nothing(status):
    body = _body([COVERING, LEGACY], _entry(1, status))
    assert _eligible(body) == "no"


def test_done_by_does_not_credit_a_line_outside_the_four_field_shape():
    odd = "- **Odd** — claim. (`task/odd`, closed 2026-01-03, with a stray note)"
    body = _body([COVERING, odd], _entry(1, "done by task/odd"))
    assert _eligible(body) == "no"


def test_credit_is_per_line_own_task_only():
    body = _body(
        [COVERING, LEGACY, "- **Ent** — claim. (`task/ent`, closed 2026-01-03)"],
        _entry(1, "done by task/ent"),
    )
    assert _eligible(body) == "no"


def test_one_done_by_entry_credits_its_line_among_several_entries():
    body = _body(
        [COVERING, LEGACY, "- **Ent** — claim. (`task/ent`, closed 2026-01-03)"],
        _entry(1, "dropped: nowhere: not needed"),
        _entry(2, "done by task/ent"),
        _entry(3, "done by task/legacy"),
    )
    assert _eligible(body) == "yes"


def test_token_carrying_lines_output_is_unchanged_by_done_by_entries():
    base = _body([COVERING, PARTIAL])
    with_entries = _body([COVERING, PARTIAL], _entry(1, "done by task/cover"),
                         _entry(2, "done by task/part"))
    assert _run(GATE, with_entries).stdout == _run(GATE, base).stdout


def test_malformed_followups_section_refuses_rather_than_reading_a_ledger_without_credit():
    body = _body([COVERING, "- **Ent** — claim. (`task/ent`, closed 2026-01-03)"],
                 _entry(1, "done by task/ent"), "- not an entry\n")
    r = _run(GATE, body)
    assert r.returncode == 2
    assert r.stdout == ""
    assert "reason-code: malformed-followups-section" in r.stderr


# Compatibility: with no `## Follow-ups` section the output matches the base gate
# byte for byte, on shapes the credit touches (token-less, covered, partial, empty).
@pytest.mark.parametrize(
    "ledger",
    [[COVERING, LEGACY], [COVERING, PARTIAL], [COVERING], [LEGACY], []],
)
def test_no_followups_section_output_matches_base_gate(ledger):
    body = _body(ledger)
    new, old = _run(GATE, body), _run(BASE_GATE, body)
    entry_lines = ("entries-open: none\n", "entries-folded: none\n")
    assert all(line in new.stdout for line in entry_lines)
    without_entry_lines = new.stdout.replace(entry_lines[0], "").replace(entry_lines[1], "")
    assert (new.returncode, without_entry_lines, new.stderr) == (old.returncode, old.stdout, old.stderr)


# Compatibility: the pre-credit gate, run on the spec body this slice loop produces
# (a token-less four-field line with a done-by entry beside criteria-covering lines),
# still exits 0, stays not-eligible, and parses the covering lines' coverage.
@pytest.mark.parametrize(
    "criteria_lines, covered, partial",
    [
        ([COVERING], "AC1", "none"),
        ([COVERING, PARTIAL], "AC1", "AC2"),
    ],
)
def test_base_gate_still_parses_coverage_on_new_shape_ledger(criteria_lines, covered, partial):
    body = _body(
        [*criteria_lines, "- **Ent** — claim. (`task/ent`, closed 2026-01-03)"],
        _entry(1, "done by task/ent"),
    )
    r = _run(BASE_GATE, body)
    out = dict(ln.split(": ", 1) for ln in r.stdout.splitlines())
    assert r.returncode == 0, r.stderr
    assert out["complete-eligible"] == "no"
    assert out["covered"] == covered
    assert out["partial"] == partial
