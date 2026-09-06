"""Tests for the maturity resolver.

The resolver reads a repository's agent-instruction file on stdin and
resolves its declared project-maturity level — the closed vocabulary
`prototype` / `early` / `production` — defaulting to `production` when the
declaration is absent or out of vocabulary.

Stdout token vocabulary (exit 0, one resolution per invocation):

    level: prototype|early|production
    reason: declared|section-absent|invalid-value
    offending-value: <sanitized text>   (present only when reason is invalid-value)

Exit codes:
  0 → resolved (declared, defaulted-from-absent, or defaulted-from-invalid)
  2 → fail-closed (empty or non-UTF-8 stdin) — never resolves to any level
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RESOLVER = REPO_ROOT / "plugins" / "craft" / "scripts" / "maturity_resolve.py"

PROTOTYPE_DECLARED = """\
# Some Repo

## Project Maturity

prototype

## Other Section

irrelevant content
"""

EARLY_DECLARED = """\
# Some Repo

## Project Maturity

early
"""

PRODUCTION_DECLARED = """\
# Some Repo

## Project Maturity

production
"""

NO_SECTION_AT_ALL = """\
# Some Repo

## Other Section

Nothing about maturity here.
"""

INVALID_VALUE_DECLARED = """\
# Some Repo

## Project Maturity

banana
"""

CONTROL_CHARS_IN_INVALID_VALUE = (
    "# Some Repo\n\n## Project Maturity\n\nbad\x07value\x1b\n"
)

CASE_INSENSITIVE_WITH_PROSE = """\
# Some Repo

## Project Maturity

We are running at an EARLY stage of development right now, per the last review.
"""

PROSE_MENTIONS_WORD_BUT_NO_SECTION = """\
# Some Repo

## Other Section

We shipped an early version once, but that isn't a declaration.
"""

PRODUCTION_WORD_OUTSIDE_SECTION_NO_SECTION_AT_ALL = """\
# Some Repo

## Dependency Posture

This service runs in production today, per ops.
"""

PRODUCTION_DECLARED_WITH_SURROUNDING_PROSE = """\
# Some Repo

## Project Maturity

This repository is at the Production level of maturity, per the last review.
"""


def _run(stdin_bytes: bytes) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RESOLVER)],
        input=stdin_bytes,
        capture_output=True,
    )


def _lines(result: subprocess.CompletedProcess) -> list[str]:
    return result.stdout.decode("utf-8").splitlines()


# ---- resolves each vocabulary word --------------------------------------


def test_prototype_declared_resolves_to_prototype():
    result = _run(PROTOTYPE_DECLARED.encode("utf-8"))
    assert result.returncode == 0
    assert "level: prototype" in _lines(result)
    assert "reason: declared" in _lines(result)


def test_early_declared_resolves_to_early():
    result = _run(EARLY_DECLARED.encode("utf-8"))
    assert result.returncode == 0
    assert "level: early" in _lines(result)
    assert "reason: declared" in _lines(result)


def test_production_declared_resolves_to_production():
    result = _run(PRODUCTION_DECLARED.encode("utf-8"))
    assert result.returncode == 0
    assert "level: production" in _lines(result)
    assert "reason: declared" in _lines(result)


# ---- section absence vs. invalid value are distinct reasons --------------


def test_no_project_maturity_section_resolves_to_production_with_section_absent_reason():
    result = _run(NO_SECTION_AT_ALL.encode("utf-8"))
    assert result.returncode == 0
    assert "level: production" in _lines(result)
    assert "reason: section-absent" in _lines(result)


def test_out_of_vocabulary_value_resolves_to_production_with_invalid_value_reason():
    result = _run(INVALID_VALUE_DECLARED.encode("utf-8"))
    assert result.returncode == 0
    assert "level: production" in _lines(result)
    assert "reason: invalid-value" in _lines(result)
    assert "offending-value: banana" in _lines(result)


def test_section_absent_and_invalid_value_never_collapse_to_the_same_reason_token():
    absent = _lines(_run(NO_SECTION_AT_ALL.encode("utf-8")))
    invalid = _lines(_run(INVALID_VALUE_DECLARED.encode("utf-8")))
    assert "reason: section-absent" in absent
    assert "reason: invalid-value" in invalid
    assert "reason: section-absent" not in invalid
    assert "reason: invalid-value" not in absent


def test_offending_value_is_stripped_of_control_characters():
    result = _run(CONTROL_CHARS_IN_INVALID_VALUE.encode("utf-8"))
    assert result.returncode == 0
    assert "reason: invalid-value" in _lines(result)
    assert "offending-value: badvalue" in _lines(result)


# ---- case-insensitivity and prose tolerance ------------------------------


def test_declaration_matching_is_case_insensitive_and_tolerates_surrounding_prose():
    result = _run(CASE_INSENSITIVE_WITH_PROSE.encode("utf-8"))
    assert result.returncode == 0
    assert "level: early" in _lines(result)
    assert "reason: declared" in _lines(result)


def test_prose_mentioning_a_level_word_without_declaring_it_does_not_match():
    result = _run(PROSE_MENTIONS_WORD_BUT_NO_SECTION.encode("utf-8"))
    assert result.returncode == 0
    assert "level: production" in _lines(result)
    assert "reason: section-absent" in _lines(result)


def test_conjunction_only_a_declared_section_matches_not_the_word_alone():
    """Neither rule alone accepts exactly this pair: a fixture where
    `production` appears in prose OUTSIDE any `## Project Maturity` section
    must resolve via the absence path (reason: section-absent), while a
    fixture where the same word is DECLARED inside the section, with
    surrounding prose, must resolve via the declared path (reason:
    declared). A resolver that scans the whole document for the word would
    report `declared` for the first fixture too; a resolver that only checks
    "does a Project Maturity section exist" would not distinguish either."""
    outside_section = _lines(_run(PRODUCTION_WORD_OUTSIDE_SECTION_NO_SECTION_AT_ALL.encode("utf-8")))
    assert "level: production" in outside_section
    assert "reason: section-absent" in outside_section
    assert "reason: declared" not in outside_section

    inside_section = _lines(_run(PRODUCTION_DECLARED_WITH_SURROUNDING_PROSE.encode("utf-8")))
    assert "level: production" in inside_section
    assert "reason: declared" in inside_section
    assert "reason: section-absent" not in inside_section


# ---- fail-closed on stdin that cannot be resolved at all -----------------


def test_empty_stdin_fails_closed_with_nonzero_exit():
    result = _run(b"")
    assert result.returncode != 0
    assert b"level:" not in result.stdout
    assert b"reason-code: empty-stdin" in result.stderr


def test_non_utf8_stdin_fails_closed_with_nonzero_exit():
    result = _run(b"\xff\xfe not valid utf-8")
    assert result.returncode != 0
    assert b"level:" not in result.stdout
    assert b"reason-code: invalid-utf8-stdin" in result.stderr


# ---- stdlib-only import ---------------------------------------------------


def test_script_imports_nothing_outside_the_standard_library():
    import ast

    tree = ast.parse(RESOLVER.read_text(encoding="utf-8"))
    stdlib_modules = {"__future__", "re", "sys"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                assert top_level in stdlib_modules, f"non-stdlib import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            top_level = node.module.split(".")[0]
            assert top_level in stdlib_modules, f"non-stdlib import: {node.module}"
