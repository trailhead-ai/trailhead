"""Tests for the maturity calibration-block renderer.

The renderer resolves exactly one maturity level for a spec under review and
emits the calibration block the council dispatch substitutes. It reads the
spec body on stdin and accepts an optional `--agent-instruction-file <path>`.

Resolution order:
  - a `## Maturity` section naming exactly one repository -> that
    repository's level, basis `stamp`
  - a section naming more than one repository -> the highest level among
    them, basis `highest-stamped`
  - a section absent (including empty stdin) -> `maturity_resolve.resolve()`
    against the agent-instruction file, basis `agent-instruction-file`; with
    no such flag, `production`, basis `default`
  - any other stamp violation refuses, carrying the stamp reader's own
    reason-code

Stdout on success (exit 0) is the calibration block: the resolved level and
its basis, plus all five maturity-sensitive concerns each rated at the
severity the resolved level maps to (Critical at production, Important at
early, Minor at prototype).

Exit codes:
  0 -> resolved, block printed
  2 -> fail-closed, reason-code on stderr, no block on stdout
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BARS = REPO_ROOT / "plugins" / "craft" / "scripts" / "maturity_bars.py"

CONCERNS = (
    "backwards compatibility",
    "migration and backfill",
    "rollback and reversibility",
    "production failure visibility",
    "cross-consumer blast radius",
)

SEVERITIES = ("Critical", "Important", "Minor")

SINGLE_REPO_PROTOTYPE = """\
# Some Spec

## Maturity

- lookout: prototype
"""

SINGLE_REPO_EARLY = """\
# Some Spec

## Maturity

- lookout: early
"""

SINGLE_REPO_PRODUCTION = """\
# Some Spec

## Maturity

- lookout: production
"""

NO_MATURITY_SECTION = """\
# Some Spec

Just prose, no stamp at all.
"""

MALFORMED_ENTRY = """\
# Some Spec

## Maturity

- lookout production
"""

INVALID_LEVEL = """\
# Some Spec

## Maturity

- lookout: XTREME_HAXOR_MARKER_9f3a
"""

DUPLICATE_MEMBER = """\
# Some Spec

## Maturity

- lookout: prototype
- lookout: early
"""

DUPLICATE_SECTION = """\
# Some Spec

## Maturity

- lookout: prototype

## Maturity

- trailhead: production
"""

EMPTY_SECTION = """\
# Some Spec

## Maturity

"""

UNRESOLVED_ENUMERATION = """\
# Some Spec

## Maturity

<!-- unresolved-enumeration: cannot enumerate repos touched -->
"""

MALFORMED_LEVEL_WITH_DISTINCTIVE_STRING = """\
# Some Spec

## Maturity

- lookout: XTREME_HAXOR_MARKER_9f3a
"""

AGENT_FILE_PROTOTYPE = """\
# Some Repo

## Project Maturity

prototype
"""

AGENT_FILE_EARLY = """\
# Some Repo

## Project Maturity

early
"""

AGENT_FILE_PRODUCTION = """\
# Some Repo

## Project Maturity

production
"""

AGENT_FILE_NO_DECLARATION = """\
# Some Repo

Nothing declared here.
"""

_LEVEL_ORDER = ("prototype", "early", "production")
_SEVERITY_BY_LEVEL = {
    "production": "Critical",
    "early": "Important",
    "prototype": "Minor",
}


def _run(stdin_bytes: bytes, args: list[str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BARS), *(args or [])],
        input=stdin_bytes,
        capture_output=True,
    )


def _stdout(result: subprocess.CompletedProcess) -> str:
    return result.stdout.decode("utf-8")


def _stderr(result: subprocess.CompletedProcess) -> str:
    return result.stderr.decode("utf-8")


# ---- per-level severity rendering ----------------------------------------


def test_production_level_rates_every_concern_critical():
    result = _run(SINGLE_REPO_PRODUCTION.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    for concern in CONCERNS:
        assert f"{concern}: Critical" in out


def test_early_level_rates_every_concern_important():
    result = _run(SINGLE_REPO_EARLY.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    for concern in CONCERNS:
        assert f"{concern}: Important" in out


def test_prototype_level_rates_every_concern_minor():
    result = _run(SINGLE_REPO_PROTOTYPE.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    for concern in CONCERNS:
        assert f"{concern}: Minor" in out


def test_all_five_concerns_appear_at_every_level():
    for fixture in (SINGLE_REPO_PROTOTYPE, SINGLE_REPO_EARLY, SINGLE_REPO_PRODUCTION):
        out = _stdout(_run(fixture.encode("utf-8")))
        for concern in CONCERNS:
            assert concern in out, f"{concern!r} missing from block for {fixture!r}"


def test_severity_vocabulary_is_exactly_three_words_no_fourth_tier():
    for fixture in (SINGLE_REPO_PROTOTYPE, SINGLE_REPO_EARLY, SINGLE_REPO_PRODUCTION):
        out = _stdout(_run(fixture.encode("utf-8")))
        found = {word for word in ("Critical", "Important", "Minor", "Major", "Severe", "Blocker") if word in out}
        assert found <= set(SEVERITIES)
        # every concern line's severity is one of the three
        for concern in CONCERNS:
            for line in out.splitlines():
                if line.strip().startswith(f"- {concern}:"):
                    severity = line.split(":", 1)[1].strip()
                    assert severity in SEVERITIES


# ---- basis: stamp ----------------------------------------------------------


def test_single_repository_stamp_resolves_to_that_level_basis_stamp():
    result = _run(SINGLE_REPO_EARLY.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    assert "early" in out
    assert "(basis: stamp)" in out


def test_single_repository_stamp_wins_over_conflicting_agent_instruction_file(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(AGENT_FILE_PRODUCTION, encoding="utf-8")
    result = _run(
        SINGLE_REPO_PROTOTYPE.encode("utf-8"),
        ["--agent-instruction-file", str(agent_file)],
    )
    assert result.returncode == 0
    out = _stdout(result)
    assert "prototype" in out
    assert "(basis: stamp)" in out
    for concern in CONCERNS:
        assert f"{concern}: Minor" in out


# ---- basis: highest-stamped -------------------------------------------------


import itertools  # noqa: E402


def _two_repo_stamp(level_a: str, level_b: str) -> str:
    return (
        "# Some Spec\n\n## Maturity\n\n"
        f"- repo-a: {level_a}\n"
        f"- repo-b: {level_b}\n"
    )


import pytest  # noqa: E402


@pytest.mark.parametrize("level_a,level_b", list(itertools.permutations(_LEVEL_ORDER, 2)))
def test_two_repo_stamp_resolves_to_higher_level_basis_highest_stamped(level_a, level_b):
    result = _run(_two_repo_stamp(level_a, level_b).encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    higher = level_a if _LEVEL_ORDER.index(level_a) > _LEVEL_ORDER.index(level_b) else level_b
    assert "highest-stamped" in out
    for concern in CONCERNS:
        assert f"{concern}: {_SEVERITY_BY_LEVEL[higher]}" in out


# ---- basis: agent-instruction-file / default -------------------------------


def test_absent_section_with_agent_instruction_file_resolves_its_declared_level(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(AGENT_FILE_EARLY, encoding="utf-8")
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", str(agent_file)],
    )
    assert result.returncode == 0
    out = _stdout(result)
    assert "early" in out
    assert "agent-instruction-file" in out


def test_absent_section_with_no_flag_resolves_production_basis_default():
    result = _run(NO_MATURITY_SECTION.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    assert "maturity: production (basis: default)" in out
    for concern in CONCERNS:
        assert f"{concern}: Critical" in out


def test_empty_stdin_resolves_identically_to_absent_section_exit_zero():
    result = _run(b"")
    assert result.returncode == 0
    out = _stdout(result)
    assert "maturity: production (basis: default)" in out


def test_empty_stdin_with_agent_instruction_file_uses_it(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(AGENT_FILE_PROTOTYPE, encoding="utf-8")
    result = _run(b"", ["--agent-instruction-file", str(agent_file)])
    assert result.returncode == 0
    out = _stdout(result)
    assert "prototype" in out
    assert "agent-instruction-file" in out


def test_absent_section_agent_file_declares_nothing_resolves_production(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(AGENT_FILE_NO_DECLARATION, encoding="utf-8")
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", str(agent_file)],
    )
    assert result.returncode == 0
    out = _stdout(result)
    assert "maturity: production (basis: agent-instruction-file)" in out


def test_absent_section_agent_file_out_of_vocabulary_resolves_production(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(
        "# Some Repo\n\n## Project Maturity\n\nbanana\n", encoding="utf-8"
    )
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", str(agent_file)],
    )
    assert result.returncode == 0
    out = _stdout(result)
    assert "maturity: production (basis: agent-instruction-file)" in out


# ---- unreadable agent-instruction-file -------------------------------------


def test_missing_agent_instruction_file_exits_nonzero_with_distinct_reason_code():
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", "/does/not/exist/CLAUDE.md"],
    )
    assert result.returncode != 0
    err = _stderr(result)
    assert "reason-code: agent-instruction-file-unreadable" in err


def test_directory_as_agent_instruction_file_exits_nonzero_same_reason_code(tmp_path):
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", str(tmp_path)],
    )
    assert result.returncode != 0
    err = _stderr(result)
    assert "reason-code: agent-instruction-file-unreadable" in err


def test_unreadable_agent_instruction_file_exits_nonzero_same_reason_code(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(AGENT_FILE_PRODUCTION, encoding="utf-8")
    agent_file.chmod(0o000)
    try:
        result = _run(
            NO_MATURITY_SECTION.encode("utf-8"),
            ["--agent-instruction-file", str(agent_file)],
        )
        assert result.returncode != 0
        err = _stderr(result)
        assert "reason-code: agent-instruction-file-unreadable" in err
    finally:
        agent_file.chmod(0o644)


def test_unreadable_agent_instruction_file_raises_no_traceback(tmp_path):
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", "/does/not/exist/CLAUDE.md"],
    )
    assert "Traceback" not in _stderr(result)


# ---- remaining stamp violations pass through their own reason-code --------


@pytest.mark.parametrize(
    "fixture,reason_code",
    [
        (MALFORMED_ENTRY, "malformed-entry"),
        (INVALID_LEVEL, "invalid-level"),
        (DUPLICATE_MEMBER, "duplicate-member"),
        (DUPLICATE_SECTION, "duplicate-section"),
        (EMPTY_SECTION, "empty-section"),
        (UNRESOLVED_ENUMERATION, "unresolved-enumeration"),
    ],
)
def test_each_remaining_stamp_violation_exits_nonzero_with_stamp_readers_reason_code(fixture, reason_code):
    result = _run(fixture.encode("utf-8"))
    assert result.returncode != 0
    err = _stderr(result)
    assert f"reason-code: {reason_code}" in err


# ---- no offending value ever echoed ----------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [MALFORMED_LEVEL_WITH_DISTINCTIVE_STRING],
)
def test_no_refusal_writes_offending_value_to_stdout_or_stderr(fixture):
    result = _run(fixture.encode("utf-8"))
    assert result.returncode != 0
    assert "XTREME_HAXOR_MARKER_9f3a" not in _stdout(result)
    assert "XTREME_HAXOR_MARKER_9f3a" not in _stderr(result)


# ---- no block on non-zero exit ---------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [
        MALFORMED_ENTRY,
        INVALID_LEVEL,
        DUPLICATE_MEMBER,
        DUPLICATE_SECTION,
        EMPTY_SECTION,
        UNRESOLVED_ENUMERATION,
    ],
)
def test_nonzero_exit_never_writes_a_calibration_block_to_stdout(fixture):
    result = _run(fixture.encode("utf-8"))
    assert result.returncode != 0
    out = _stdout(result)
    for concern in CONCERNS:
        assert concern not in out
    assert out == ""


# ---- resolved level and basis are surfaced ---------------------------------


def test_block_states_resolved_level_and_basis():
    result = _run(SINGLE_REPO_EARLY.encode("utf-8"))
    out = _stdout(result)
    assert "early" in out
    assert "(basis: stamp)" in out


# ---- undecodable stdin fails closed ----------------------------------------


def test_non_utf8_stdin_refuses_with_the_stamp_readers_own_reason_code():
    """Undecodable stdin is unreadable input, and unreadable input fails
    closed — it is never silently read as an absent `## Maturity` section.
    The reason-code is the one `maturity_stamp.py` already raises for this
    input rather than a second vocabulary."""
    result = _run(b"\xff\xfe\x00garbage")
    assert result.returncode != 0
    assert "reason-code: invalid-utf8-stdin" in _stderr(result)


def test_non_utf8_stdin_refuses_even_with_an_agent_instruction_file():
    """The fallback to the agent-instruction file is for a spec that carries
    no section — not for one whose bytes could not be read at all."""
    result = _run(
        b"\xff\xfe\x00garbage",
        ["--agent-instruction-file", str(BARS)],
    )
    assert result.returncode != 0
    assert "reason-code: invalid-utf8-stdin" in _stderr(result)


def test_non_utf8_stdin_writes_no_calibration_block_to_stdout():
    result = _run(b"\xff\xfe\x00garbage")
    assert result.returncode != 0
    assert _stdout(result) == ""


def test_unreadable_agent_instruction_file_writes_no_block_to_stdout(tmp_path):
    """Completes the "at any reason-code" property for the seventh
    reason-code — the one this renderer owns rather than forwards."""
    directory = tmp_path / "a-directory"
    directory.mkdir()
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", str(directory)],
    )
    assert result.returncode != 0
    assert _stdout(result) == ""
