"""End-to-end proof of the slice's actual claim: a level resolved from a
repository's agent-instruction file (`maturity_resolve.py`) reaches a spec's
markdown (via the real `templates/spec.md` `## Maturity` section) and is read
back out of that markdown alone (`maturity_stamp.py`), by a reader holding
nothing else.

Every fixture agent-instruction body is imported from `test_maturity_resolve`
— the resolver's own producer fixtures — rather than re-typed here, and every
stamped spec body is rendered through `test_brainstorm_maturity_contract`'s
own template-rendering helper, so this file never invents a wire shape of its
own for either seam. All three scripts run as real subprocesses.

Inertness (a `## Maturity` section not perturbing `candidate_set.py` or
`covers_gate.py`, with and without a `## Slices` ledger) is NOT retested here:
`test_brainstorm_maturity_contract.py`'s
`test_template_placement_is_inert_to_candidate_set_with_ledger` /
`_without_ledger` (lines 443-454) already assert byte-identical
`candidate_set.py` stdout between a stamped body and its unstamped twin, in
both ledger shapes, and `test_template_placement_is_inert_to_covers_gate_with_ledger`
/ `_without_ledger` (lines 457-470) already assert identical `covers_gate.py`
exit codes (and stdout) for the same `--covers` list between a stamped body
and its unstamped twin, in both ledger shapes. Those are exactly this task's
two inertness contract items — re-asserting them here would be a
near-duplicate of already-landed coverage, not new proof.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from test_brainstorm_maturity_contract import (
    STAMP,
    _render_template_with_maturity_entries,
)
from test_maturity_resolve import (
    EARLY_DECLARED,
    NO_SECTION_AT_ALL,
    PRODUCTION_DECLARED,
    PROTOTYPE_DECLARED,
)

REPO_ROOT = Path(__file__).parent.parent
RESOLVER = REPO_ROOT / "plugins" / "craft" / "scripts" / "maturity_resolve.py"


def _resolve(body: str) -> str:
    """Run the real resolver as a subprocess and return the resolved level."""
    result = subprocess.run(
        [sys.executable, str(RESOLVER)],
        input=body.encode("utf-8"),
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8")
    lines = result.stdout.decode("utf-8").splitlines()
    level_line = next(line for line in lines if line.startswith("level: "))
    return level_line.removeprefix("level: ")


def _stamp(body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(STAMP)],
        input=body.encode("utf-8"),
        capture_output=True,
    )


# ---- round trip: a declared level survives resolve -> stamp -> read -------


def test_declared_prototype_round_trips_through_resolve_stamp_and_read():
    level = _resolve(PROTOTYPE_DECLARED)
    assert level == "prototype", "fixture assumption: PROTOTYPE_DECLARED resolves to prototype"

    body = _render_template_with_maturity_entries({"trailhead": level})
    result = _stamp(body)

    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert result.stdout.decode("utf-8") == "maturity: trailhead=prototype\n"


# ---- round trip: a defaulted level is stamped as a level, not an absence --


def test_defaulted_production_round_trips_through_resolve_stamp_and_read():
    level = _resolve(NO_SECTION_AT_ALL)
    assert level == "production", (
        "fixture assumption: NO_SECTION_AT_ALL defaults to production"
    )

    body = _render_template_with_maturity_entries({"lookout": level})
    result = _stamp(body)

    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert result.stdout.decode("utf-8") == "maturity: lookout=production\n"


# ---- round trip: three repositories, three distinct resolved levels -------


def test_three_repository_stamp_reads_back_each_member_at_its_own_level():
    levels = {
        "trailhead": _resolve(PROTOTYPE_DECLARED),
        "lookout": _resolve(EARLY_DECLARED),
        "outpost": _resolve(PRODUCTION_DECLARED),
    }
    assert levels == {
        "trailhead": "prototype",
        "lookout": "early",
        "outpost": "production",
    }, "fixture assumption: the three resolver fixtures declare three distinct levels"

    body = _render_template_with_maturity_entries(levels)
    result = _stamp(body)

    assert result.returncode == 0, result.stderr.decode("utf-8")
    stamped = result.stdout.decode("utf-8")
    assert stamped == "maturity: lookout=early, outpost=production, trailhead=prototype\n", (
        "each member must read back at its own resolved level, sorted by name — "
        "none of them collapsed to the highest (production)"
    )
