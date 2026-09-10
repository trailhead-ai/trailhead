"""Tests for the maturity resolver.

The resolver reads a repository's agent-instruction file on stdin and
resolves its declared project-maturity level — the closed vocabulary
`prototype` / `early` / `production` — defaulting to `production` when the
declaration is absent, out of vocabulary, or ambiguous.

Stdout token vocabulary (exit 0, one resolution per invocation):

    level: prototype|early|production
    reason: declared|section-absent|invalid-value|ambiguous-value
    offending-value: <sanitized text>   (present only when reason is
                                          invalid-value or ambiguous-value)

Exit codes:
  0 → resolved (declared, or defaulted-from-absent/invalid/ambiguous)
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

# ---- fixtures for defects surfaced by whole-change review ----

AMBIGUOUS_TWO_WORDS_DECLARED = """\
# Some Repo

## Project Maturity

No longer a prototype; this is production.
"""

WHITESPACE_ONLY_STDIN = "\n   \n\t\n"

FENCED_HEADING_INSIDE_SECTION = """\
# Some Repo

## Project Maturity

Some prose.

```
## Not A Heading
```

prototype
"""

H1_TERMINATES_SECTION = """\
# Some Repo

## Project Maturity

Some prose with no vocabulary word here.

# A Top-Level Heading

production
"""

LONG_INVALID_VALUE = "# Some Repo\n\n## Project Maturity\n\n" + ("gibberish " * 40) + "\n"

BIDI_OVERRIDE_IN_INVALID_VALUE = (
    "# Some Repo\n\n## Project Maturity\n\n"
    "bad‮value‬ here\n"
)

# ---- fixtures for the second-pass correctness/security defects ----

# defect 1: a second, later, unfenced `## Project Maturity` heading declaring
# a conflicting value must not let the FIRST heading win silently.
DUPLICATE_SECTIONS_CONFLICTING_VALUES = (
    "## Project Maturity\n\nprototype\n\n## Other\n\nblah\n\n"
    "## Project Maturity\n\nproduction\n"
)

# defect 2: a setext H1 (title line + `===` underline) must terminate the
# section body exactly like an ATX H1 already does.
SETEXT_H1_TERMINATES_SECTION = """\
# Some Repo

## Project Maturity

Some prose with no vocabulary word here.

A Top-Level Heading
====================

production
"""

# defect 2 (H2 case, decided): a setext H2 (title line + `---` underline,
# immediately following non-blank text with no blank line between) also
# terminates the section, mirroring how `_TERMINATOR_HEADING_RE` already
# treats ATX `##` the same as ATX `#`.
SETEXT_H2_TERMINATES_SECTION = """\
# Some Repo

## Project Maturity

Some prose with no vocabulary word here.

A Second-Level Heading
-----------------------

production
"""

# Companion to the H2 case: a `---` preceded by a BLANK line is an ordinary
# CommonMark thematic break, not a setext heading, and must NOT terminate the
# section — this is what justifies treating setext H2 as a terminator at all
# without over-triggering on every plain divider line.
THEMATIC_BREAK_PRECEDED_BY_BLANK_LINE_DOES_NOT_TERMINATE_SECTION = """\
# Some Repo

## Project Maturity

Some prose before a divider.

---

production
"""

# defect 3: the C1 control block (U+0080-U+009F) is category Cc, exactly like
# C0/DEL, and must be stripped by the same control-character pass. U+009B is
# CSI, the 8-bit equivalent of ESC [.
C1_CONTROL_CHAR_IN_INVALID_VALUE = "# Some Repo\n\n## Project Maturity\n\nbad\x9bvalue\n"

# defect 4: Unicode variation selectors (category Mn, not Cf) are the
# codepoints behind current invisible-Unicode steganography and must be
# stripped alongside the existing format-control strip. Built from escape
# sequences, never pasted literal invisible bytes.
VARIATION_SELECTOR_IN_INVALID_VALUE = (
    "# Some Repo\n\n## Project Maturity\n\n"
    "bad\ufe0fvalue\U000e0100 here\n"
)

# defect 6 (boundary 1 of the distinct-word rule): the SAME word repeated
# must stay `declared` — nothing currently fails if the distinct-word dedupe
# were removed and every match counted separately.
SAME_WORD_REPEATED_STAYS_DECLARED = """\
# Some Repo

## Project Maturity

We remain at production; this is a production deployment, running in
production ops today.
"""

# defect 6 (boundary 2): a multi-line section body must still collapse to
# exactly one `offending-value:` line — nothing currently fails if the
# `\\s+` collapse in `_sanitize` were dropped, because both existing
# offending-value fixtures are single-line.
MULTILINE_INVALID_VALUE_BODY = (
    "# Some Repo\n\n## Project Maturity\n\n"
    "level: hijacked\n"
    "offending-value: hijacked\n"
    "banana\n"
)


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


# ---- defect 1: multiple distinct vocabulary words is ambiguous, not first-match --


def test_section_body_with_two_distinct_vocabulary_words_resolves_to_production_ambiguous():
    """A section body containing more than one distinct vocabulary word must
    never resolve to whichever word appears first — that is the exact
    fail-UNSAFE direction (a `production` repository graded as `prototype`)
    this feature exists to prevent."""
    result = _run(AMBIGUOUS_TWO_WORDS_DECLARED.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    assert "level: production" in lines
    assert "reason: ambiguous-value" in lines
    assert "reason: declared" not in lines
    assert "level: prototype" not in lines


def test_ambiguous_value_reason_is_distinct_from_invalid_value_reason():
    ambiguous = _lines(_run(AMBIGUOUS_TWO_WORDS_DECLARED.encode("utf-8")))
    invalid = _lines(_run(INVALID_VALUE_DECLARED.encode("utf-8")))
    assert "reason: ambiguous-value" in ambiguous
    assert "reason: invalid-value" not in ambiguous
    assert "reason: invalid-value" in invalid
    assert "reason: ambiguous-value" not in invalid


def test_ambiguous_value_report_carries_the_conflicting_values():
    result = _run(AMBIGUOUS_TWO_WORDS_DECLARED.encode("utf-8"))
    lines = _lines(result)
    offending = [line for line in lines if line.startswith("offending-value:")]
    assert offending, "ambiguous-value must report an offending-value line"
    assert "prototype" in offending[0]
    assert "production" in offending[0]


# ---- defect 2: a present-but-whitespace-only file is section-absent, not fail-closed --


def test_whitespace_only_stdin_from_an_existing_file_resolves_to_production_section_absent():
    """A file that EXISTS but is empty or whitespace-only declares nothing —
    per AC2, that resolves to production via the ordinary absence path, exit
    0. This must not be confused with a failed read (zero bytes), which
    stays fail-closed."""
    result = _run(WHITESPACE_ONLY_STDIN.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    assert "level: production" in lines
    assert "reason: section-absent" in lines


def test_genuinely_zero_byte_stdin_still_fails_closed():
    """The no-bytes-at-all case must keep failing closed — this is what a
    broken read produces, and it must never be mistaken for a deliberate
    declaration of anything, including the section-absent default."""
    result = _run(b"")
    assert result.returncode == 2
    assert b"level:" not in result.stdout


# ---- defect 5: offending-value is bounded and stripped of format/bidi controls --


def test_offending_value_is_bounded_in_length():
    result = _run(LONG_INVALID_VALUE.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    offending = next(line for line in lines if line.startswith("offending-value:"))
    assert len(offending) < len(LONG_INVALID_VALUE)
    assert len(offending) <= 220, f"offending-value line not bounded: {len(offending)} chars"


def test_offending_value_strips_unicode_bidi_override_characters():
    result = _run(BIDI_OVERRIDE_IN_INVALID_VALUE.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    offending = next(line for line in lines if line.startswith("offending-value:"))
    assert "‮" not in offending
    assert "‬" not in offending


# ---- defect 7: section terminator is fence-aware and stops at an H1 too --------


def test_heading_looking_line_inside_a_fenced_code_block_is_not_a_section_terminator():
    """A `## `-looking line inside a fenced example must not truncate the
    real section — agent-instruction files are full of fenced examples that
    themselves illustrate this convention."""
    result = _run(FENCED_HEADING_INSIDE_SECTION.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    assert "level: prototype" in lines
    assert "reason: declared" in lines


def test_h1_heading_terminates_the_section_body():
    """The section body must not swallow the rest of the document past an
    H1 heading. The vocabulary word appears only AFTER the H1 here, so a
    terminator blind to H1 headings would incorrectly pull it into the
    section and report `declared`; the correct section body (ending at the
    H1) contains no vocabulary word at all, so this must resolve
    `invalid-value` instead."""
    result = _run(H1_TERMINATES_SECTION.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    assert "level: production" in lines
    assert "reason: invalid-value" in lines
    assert "reason: declared" not in lines


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


# ---- defect 1: duplicate `## Project Maturity` sections are ambiguous, never first-match --


def test_duplicate_project_maturity_sections_resolve_to_production_ambiguous_not_first_match():
    """A document with TWO unfenced `## Project Maturity` headings declaring
    conflicting values must never let the first one win silently — that is
    the exact fail-unsafe shape (a `production` repository graded as
    `prototype`) this whole feature exists to prevent."""
    result = _run(DUPLICATE_SECTIONS_CONFLICTING_VALUES.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    assert "level: production" in lines
    assert "reason: ambiguous-value" in lines
    assert "reason: declared" not in lines
    assert "level: prototype" not in lines


def test_duplicate_sections_offending_value_names_both_conflicting_values():
    result = _run(DUPLICATE_SECTIONS_CONFLICTING_VALUES.encode("utf-8"))
    lines = _lines(result)
    offending = next(line for line in lines if line.startswith("offending-value:"))
    assert "prototype" in offending
    assert "production" in offending


# ---- defect 2: setext headings (title + underline) terminate the section too --


def test_setext_h1_heading_terminates_the_section_body():
    """A setext H1 (a title line followed by a line of `=`) must terminate
    the section exactly like the ATX `# heading` case already does — the
    vocabulary word appears only after it, so a terminator blind to setext
    H1 would incorrectly pull it into the section."""
    result = _run(SETEXT_H1_TERMINATES_SECTION.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    assert "level: production" in lines
    assert "reason: invalid-value" in lines
    assert "reason: declared" not in lines


def test_setext_h2_heading_terminates_the_section_body():
    """Setext H2 (title + `---` underline, immediately following non-blank
    text) is decided to terminate the section too, mirroring how the ATX
    terminator already treats `##` the same as `#`."""
    result = _run(SETEXT_H2_TERMINATES_SECTION.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    assert "level: production" in lines
    assert "reason: invalid-value" in lines
    assert "reason: declared" not in lines


def test_thematic_break_preceded_by_blank_line_does_not_terminate_the_section():
    """A `---` divider preceded by a BLANK line is an ordinary CommonMark
    thematic break, not a setext heading, and must not swallow the level
    word that follows it — this is what justifies treating setext H2 as a
    terminator at all without over-triggering on every plain divider."""
    result = _run(
        THEMATIC_BREAK_PRECEDED_BY_BLANK_LINE_DOES_NOT_TERMINATE_SECTION.encode("utf-8")
    )
    assert result.returncode == 0
    lines = _lines(result)
    assert "level: production" in lines
    assert "reason: declared" in lines


# ---- defect 3: full Cc (C0 + DEL + C1) is stripped, not just the ASCII subset --


def test_offending_value_strips_c1_control_characters():
    """U+0080-U+009F (the C1 block) is Unicode category Cc, exactly like C0
    and DEL, and must be stripped by the same pass. U+009B is CSI, the
    8-bit equivalent of ESC [, so leaving it in is an escape-sequence
    injection risk for any terminal honouring 8-bit C1."""
    result = _run(C1_CONTROL_CHAR_IN_INVALID_VALUE.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    assert "reason: invalid-value" in lines
    assert "offending-value: badvalue" in lines


# ---- defect 4: Unicode variation selectors are stripped alongside format-control chars --


def test_offending_value_strips_variation_selectors():
    """Variation selectors (U+FE00-FE0F and the U+E0100-E01EF supplement)
    are category Mn, not Cf, so the existing format-control strip alone
    lets them through. These are the codepoints behind current
    invisible-Unicode steganography."""
    result = _run(VARIATION_SELECTOR_IN_INVALID_VALUE.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    assert "reason: invalid-value" in lines
    assert "offending-value: badvalue here" in lines


# ---- defect 6: previously-unpinned boundaries of the distinct-word rule --------


def test_same_vocabulary_word_repeated_in_section_still_resolves_to_declared():
    """The other boundary of the distinct-word rule: repeating the SAME
    word must stay `declared`, not `ambiguous-value` — nothing currently
    fails if the distinct-word dedupe were removed and every match counted
    as its own conflicting value."""
    result = _run(SAME_WORD_REPEATED_STAYS_DECLARED.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    assert "level: production" in lines
    assert "reason: declared" in lines
    assert "reason: ambiguous-value" not in lines


def test_multiline_invalid_value_body_collapses_to_a_single_offending_value_line():
    """A multi-line section body must still collapse to exactly one
    `level:` line and exactly one `offending-value:` line — nothing
    currently fails if the `\\s+` collapse in `_sanitize` were dropped,
    since both existing offending-value fixtures are single-line. This is
    what stops repo-authored text from forging extra `key: value` lines in
    this line-oriented stdout protocol."""
    result = _run(MULTILINE_INVALID_VALUE_BODY.encode("utf-8"))
    assert result.returncode == 0
    lines = _lines(result)
    level_lines = [line for line in lines if line.startswith("level:")]
    offending_lines = [line for line in lines if line.startswith("offending-value:")]
    assert len(level_lines) == 1
    assert level_lines[0] == "level: production"
    assert len(offending_lines) == 1
