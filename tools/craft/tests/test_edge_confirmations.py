"""Tests for the prototype edge-confirmation renderer.

The renderer reads a bare `## Maturity` block on stdin (the same `- <member
name>: <level>` grammar `maturity_stamp.py` already parses) and emits
brainstorm's edge-checklist confirmation block. When every entry resolves
`prototype`, it suppresses the four maturity-sensitive dimensions of that
checklist — Reversibility, Migration / backfill, Failure visibility, Blast
radius — into one confirmation line apiece, each naming the dimension and the
concrete default it assumes. When any entry resolves otherwise, it states
that no dimension is suppressed and every branch opens normally.

Stdout on success (exit 0), the block, exactly once.

Exit codes:
  0 → block printed
  2 → fail-closed, `reason-code:` on stderr, nothing on stdout:
      empty-stdin, invalid-utf8-stdin, or any reason-code
      `maturity_stamp.py`'s own `parse_entries` raises (malformed-entry,
      invalid-level, section-absent, empty-section, duplicate-member, ...).
      The offending text a `StampError` may carry is never echoed on either
      stream.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "plugins" / "craft" / "scripts" / "edge_confirmations.py"

ALL_PROTOTYPE_SINGLE = "## Maturity\n\n- trailhead: prototype\n"
ALL_PROTOTYPE_TWO = "## Maturity\n\n- trailhead: prototype\n- lookout: prototype\n"
MIXED_PROTOTYPE_EARLY = "## Maturity\n\n- trailhead: prototype\n- lookout: early\n"
SINGLE_PRODUCTION = "## Maturity\n\n- trailhead: production\n"

MALFORMED_ENTRY = "## Maturity\n\n- trailhead prototype\n"
INVALID_LEVEL = "## Maturity\n\n- trailhead: banana\n"

_DIMENSIONS = (
    "Reversibility",
    "Migration / backfill",
    "Failure visibility",
    "Blast radius",
)


def _run(stdin_bytes: bytes) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_bytes,
        capture_output=True,
    )


def _run_bare(stdin_bytes: bytes) -> subprocess.CompletedProcess:
    """Invoke the script the way brainstorm's documentation will — bare, by
    its own path, relying on the shebang and the executable bit — rather
    than through `sys.executable`."""
    return subprocess.run(
        [str(SCRIPT)],
        input=stdin_bytes,
        capture_output=True,
    )


def _confirmation_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("- ")]


# ---- suppression: all entries prototype -----------------------------------


def test_all_prototype_single_entry_emits_exactly_four_confirmation_lines():
    result = _run(ALL_PROTOTYPE_SINGLE.encode("utf-8"))
    assert result.returncode == 0
    lines = _confirmation_lines(result.stdout.decode("utf-8"))
    assert len(lines) == 4


def test_all_prototype_two_entries_still_suppresses():
    result = _run(ALL_PROTOTYPE_TWO.encode("utf-8"))
    assert result.returncode == 0
    lines = _confirmation_lines(result.stdout.decode("utf-8"))
    assert len(lines) == 4


def test_each_confirmation_line_names_its_dimension():
    result = _run(ALL_PROTOTYPE_SINGLE.encode("utf-8"))
    stdout = result.stdout.decode("utf-8")
    for dimension in _DIMENSIONS:
        assert dimension in stdout, f"expected dimension {dimension!r} in output"


def test_confirmation_lines_state_the_concrete_default_not_a_bare_label():
    """Each confirmation line states the concrete assumption it defaults to,
    not a bare 'defaulted' label, so the operator can judge whether to
    reopen the dimension without re-deriving what the default meant. Every
    one of the four dimensions is pinned independently — a default that
    regresses to a bare label on any one of them must fail this test."""
    result = _run(ALL_PROTOTYPE_SINGLE.encode("utf-8"))
    stdout = result.stdout.decode("utf-8")
    assert (
        "Reversibility: assumed acceptable to flag-day; prototype state is disposable, "
        "so no rollback path is needed." in stdout
    )
    assert (
        "Migration / backfill: assumed none; prototype state is disposable, so there is "
        "nothing to migrate." in stdout
    )
    assert (
        "Failure visibility: assumed operator-only; the only consumer is the operator "
        "running this themselves." in stdout
    )
    assert (
        "Blast radius: assumed confined to the operator; there are no other consumers "
        "to affect." in stdout
    )
    assert "defaulted" not in stdout.lower()


# ---- no suppression: any entry not prototype -------------------------------


def test_mixed_prototype_and_early_emits_zero_confirmation_lines():
    result = _run(MIXED_PROTOTYPE_EARLY.encode("utf-8"))
    assert result.returncode == 0
    lines = _confirmation_lines(result.stdout.decode("utf-8"))
    assert len(lines) == 0


def test_single_production_entry_emits_zero_confirmation_lines():
    result = _run(SINGLE_PRODUCTION.encode("utf-8"))
    assert result.returncode == 0
    lines = _confirmation_lines(result.stdout.decode("utf-8"))
    assert len(lines) == 0


def test_mixed_prototype_and_early_states_no_dimension_suppressed():
    result = _run(MIXED_PROTOTYPE_EARLY.encode("utf-8"))
    stdout = result.stdout.decode("utf-8")
    assert "no dimension suppressed" in stdout.lower()


def test_single_production_states_every_branch_opens_normally():
    result = _run(SINGLE_PRODUCTION.encode("utf-8"))
    stdout = result.stdout.decode("utf-8")
    assert "opens normally" in stdout.lower()


# ---- fail-closed: empty / undecodable stdin --------------------------------


def test_empty_stdin_exits_two_with_reason_code_and_nothing_on_stdout():
    result = _run(b"")
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"reason-code: empty-stdin" in result.stderr


def test_non_utf8_stdin_exits_two_with_invalid_utf8_stdin_reason_code():
    result = _run(b"\xff\xfe not valid utf-8")
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"reason-code: invalid-utf8-stdin" in result.stderr


# ---- fail-closed: malformed maturity block, never echoing offending text ---


def test_malformed_entry_exits_two_naming_maturity_stamps_own_reason_code():
    result = _run(MALFORMED_ENTRY.encode("utf-8"))
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"reason-code: malformed-entry" in result.stderr


def test_malformed_entry_never_echoes_offending_text_on_either_stream():
    result = _run(MALFORMED_ENTRY.encode("utf-8"))
    combined = result.stdout + result.stderr
    assert b"trailhead prototype" not in combined


def test_invalid_level_exits_two_naming_maturity_stamps_own_reason_code():
    result = _run(INVALID_LEVEL.encode("utf-8"))
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"reason-code: invalid-level" in result.stderr


def test_invalid_level_never_echoes_offending_text_on_either_stream():
    result = _run(INVALID_LEVEL.encode("utf-8"))
    combined = result.stdout + result.stderr
    assert b"banana" not in combined


def test_all_prototype_predicate_does_not_vacuously_suppress_an_empty_mapping():
    """The suppression predicate must be locally safe: an empty mapping must
    not read as 'every entry is prototype'. Exercises the predicate directly
    rather than through `parse_entries`'s `empty-section` guard, so the
    decision does not depend on that upstream invariant to stay safe."""
    sys.path.insert(0, str(SCRIPT.parent))
    from edge_confirmations import _all_prototype

    assert _all_prototype({}) is False


def test_render_error_is_structurally_unable_to_carry_offending_text():
    """The never-echo property lives in the error type's inability to hold
    the value at all, not merely in the print sites' restraint — a mutation
    aimed only at the print site would miss a future call path that reaches
    stdout/stderr some other way."""
    sys.path.insert(0, str(SCRIPT.parent))
    from edge_confirmations import RenderError

    err = RenderError("malformed-entry")
    public_attrs = {a for a in vars(err) if not a.startswith("_")}
    assert public_attrs == {"reason_code"}


# ---- exit 0 prints the block exactly once -----------------------------------


def test_success_prints_the_maturity_summary_line_exactly_once():
    result = _run(ALL_PROTOTYPE_SINGLE.encode("utf-8"))
    stdout = result.stdout.decode("utf-8")
    assert stdout.count("maturity:") == 1


# ---- bare invocation, the way brainstorm's documentation will run it -------


def test_bare_invocation_by_path_runs_and_emits_the_block():
    result = _run_bare(ALL_PROTOTYPE_SINGLE.encode("utf-8"))
    assert result.returncode == 0
    lines = _confirmation_lines(result.stdout.decode("utf-8"))
    assert len(lines) == 4
