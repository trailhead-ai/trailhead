"""Tests for the migration-suppression directive renderer.

The renderer reads a spec body on stdin plus a `--target-repo` naming the
plan's target repository, resolves that repository's stamped maturity level
by importing `maturity_stamp.parse_entries` (the stamp grammar) and
`maturity_bars.resolve_level` (the absent-stamp fallback chain) — never
re-deriving either — and reports whether migration and backfill tasks are
suppressed for a plan targeting that repository.

`--target-repo` is optional when the stamp names exactly one repository (it
resolves to that sole entry) and required once the stamp names more than
one. A `--target-repo` absent from a stamp that names other repositories
refuses rather than falling back to a level.

Stdout on success (exit 0), a directive block, exactly once:
  - basis `prototype` -> suppression block naming the level, the basis, and
    the condition that reopens migration/backfill work.
  - any other level -> a block stating nothing is suppressed.

Exit codes:
  0 -> resolved, block printed.
  2 -> fail-closed, `reason-code:` on stderr, nothing on stdout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "craft" / "scripts"
SCRIPT = SCRIPTS_DIR / "migration_bar.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import maturity_bars  # noqa: E402

SINGLE_PROTOTYPE = "## Maturity\n\n- trailhead: prototype\n"
SINGLE_EARLY = "## Maturity\n\n- trailhead: early\n"
SINGLE_PRODUCTION = "## Maturity\n\n- trailhead: production\n"

MULTI_TARGET_PROTOTYPE = (
    "## Maturity\n\n- trailhead: prototype\n- lookout: production\n"
)
MULTI_TARGET_PRODUCTION = (
    "## Maturity\n\n- trailhead: production\n- lookout: prototype\n"
)

NO_MATURITY_SECTION = "# Some Spec\n\n## Acceptance Criteria\n\n- one\n"

MALFORMED_LEVEL_MARKER = "ZZZ-DISTINCTIVE-MARKER-ZZZ"
MALFORMED_LEVEL_STAMP = f"## Maturity\n\n- trailhead: {MALFORMED_LEVEL_MARKER}\n"


def _run(stdin_text: str, args: list[str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(args or [])],
        input=stdin_text.encode("utf-8"),
        capture_output=True,
    )


def _stdout(result: subprocess.CompletedProcess) -> str:
    return result.stdout.decode("utf-8")


def _stderr(result: subprocess.CompletedProcess) -> str:
    return result.stderr.decode("utf-8")


# ---- suppression: single-entry stamp resolves to prototype -----------------


def test_single_prototype_stamp_suppresses_and_names_level_and_basis():
    result = _run(SINGLE_PROTOTYPE)
    assert result.returncode == 0, _stderr(result)
    out = _stdout(result)
    assert "prototype" in out
    assert "(basis: stamp)" in out
    assert "suppressed" in out


# ---- non-suppression: single-entry stamp at early / production -------------


def test_single_early_stamp_does_not_suppress():
    result = _run(SINGLE_EARLY)
    assert result.returncode == 0, _stderr(result)
    out = _stdout(result)
    assert "not-suppressed" in out


def test_single_production_stamp_does_not_suppress():
    result = _run(SINGLE_PRODUCTION)
    assert result.returncode == 0, _stderr(result)
    out = _stdout(result)
    assert "not-suppressed" in out


# ---- multi-repository stamp: reads the TARGET repository, never highest ----


def test_multi_repo_target_prototype_sibling_production_suppresses():
    result = _run(MULTI_TARGET_PROTOTYPE, ["--target-repo", "trailhead"])
    assert result.returncode == 0, _stderr(result)
    out = _stdout(result)
    assert "prototype" in out
    assert "suppressed" in out
    assert "not-suppressed" not in out


def test_multi_repo_target_production_sibling_prototype_does_not_suppress():
    result = _run(MULTI_TARGET_PRODUCTION, ["--target-repo", "trailhead"])
    assert result.returncode == 0, _stderr(result)
    out = _stdout(result)
    assert "not-suppressed" in out


# ---- absent stamp falls back through the real resolver end to end ---------


def test_absent_maturity_section_falls_back_to_agent_instruction_file(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(
        "## Project Maturity\n\nprototype\n", encoding="utf-8"
    )
    result = _run(
        NO_MATURITY_SECTION, ["--agent-instruction-file", str(agent_file)]
    )
    assert result.returncode == 0, _stderr(result)
    out = _stdout(result)
    assert "prototype" in out
    assert "(basis: agent-instruction-file)" in out
    assert "suppressed" in out
    assert "not-suppressed" not in out


def test_absent_maturity_section_no_agent_instruction_file_defaults_production_no_suppression():
    result = _run(NO_MATURITY_SECTION)
    assert result.returncode == 0, _stderr(result)
    out = _stdout(result)
    assert "production" in out
    assert "(basis: default)" in out
    assert "not-suppressed" in out


# ---- target-repo absent from a multi-entry stamp: refuses ------------------


def test_target_repo_absent_from_multi_entry_stamp_refuses():
    result = _run(MULTI_TARGET_PROTOTYPE, ["--target-repo", "does-not-exist"])
    assert result.returncode != 0
    assert _stdout(result) == ""
    err = _stderr(result)
    assert "reason-code: target-repo-absent" in err


def test_multi_entry_stamp_without_target_repo_refuses():
    result = _run(MULTI_TARGET_PROTOTYPE)
    assert result.returncode != 0
    assert _stdout(result) == ""
    err = _stderr(result)
    assert "reason-code: target-repo-required" in err


# ---- every refusal: nothing on stdout, stable reason-code on stderr --------


def test_malformed_entry_refusal_prints_nothing_on_stdout_and_a_reason_code():
    result = _run("## Maturity\n\n- trailhead prototype\n")
    assert result.returncode != 0
    assert _stdout(result) == ""
    assert "reason-code:" in _stderr(result)


# ---- a refusal never echoes the offending stamp value (mutation-checked) --


def test_invalid_level_refusal_never_echoes_the_offending_value():
    result = _run(MALFORMED_LEVEL_STAMP)
    assert result.returncode != 0
    assert MALFORMED_LEVEL_MARKER not in _stdout(result)
    assert MALFORMED_LEVEL_MARKER not in _stderr(result)


# ---- the suppression block names the concern via maturity_bars._CONCERNS --


def test_suppression_block_names_the_canonical_migration_concern_phrase():
    concern_phrase = maturity_bars._CONCERNS[1]
    assert concern_phrase == "migration and backfill"
    result = _run(SINGLE_PROTOTYPE)
    assert result.returncode == 0, _stderr(result)
    assert concern_phrase in _stdout(result)
