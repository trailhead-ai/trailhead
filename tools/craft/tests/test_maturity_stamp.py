"""Tests for the maturity stamp reader.

The reader reads a spec body on stdin and reports the per-repository
maturity levels its `## Maturity` section declares, failing closed on every
ambiguity. It imports its primitives from two siblings rather than
re-deriving them: the fenced-block masker and the fail-closed
unique-heading finder come from `covers_gate.py`; the closed level
vocabulary and the offending-value sanitizer come from `maturity_resolve.py`.

Stdout on success (exit 0), one deterministic line, sorted by member name:

    maturity: lookout=prototype, trailhead=production

Exit codes:
  0 → every entry resolved cleanly
  2 → fail-closed, with a `reason-code:` line on stderr naming one of:
      empty-stdin, invalid-utf8-stdin, section-absent, duplicate-section,
      empty-section, unresolved-enumeration, malformed-entry, invalid-level,
      duplicate-member, member-name-too-long
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
STAMP = REPO_ROOT / "plugins" / "craft" / "scripts" / "maturity_stamp.py"

TWO_ENTRIES_REVERSE_ORDER = """\
# Some Spec

## Maturity

- trailhead: production
- lookout: prototype
"""

SINGLE_ENTRY = """\
# Some Spec

## Maturity

- lookout: early
"""

NO_MATURITY_HEADING = """\
# Some Spec

## Other Section

irrelevant content
"""

DUPLICATE_HEADING = """\
# Some Spec

## Maturity

- lookout: prototype

## Other

blah

## Maturity

- lookout: production
"""

HEADING_ONLY_IN_FENCE = """\
# Some Spec

```
## Maturity

- lookout: prototype
```

## Other Section

no real heading here
"""

HEADING_MID_SENTENCE = """\
# Some Spec

Some prose that mentions ## Maturity mid-sentence, not at line start.

- lookout: prototype
"""

INVALID_LEVEL = """\
# Some Spec

## Maturity

- lookout: banana
"""

MALFORMED_ENTRY_NO_COLON = """\
# Some Spec

## Maturity

- lookout prototype
"""

MALFORMED_MEMBER_NAME = """\
# Some Spec

## Maturity

- look/out: prototype
"""

MEMBER_NAME_SINGLE_DOT = """\
# Some Spec

## Maturity

- .: production
"""

MEMBER_NAME_DOUBLE_DOT = """\
# Some Spec

## Maturity

- ..: production
"""

MEMBER_NAME_EMBEDS_DOTS_BUT_IS_NOT_JUST_DOTS = """\
# Some Spec

## Maturity

- ..lookout: production
"""

DUPLICATE_MEMBER = """\
# Some Spec

## Maturity

- lookout: prototype
- lookout: production
"""

CONTROL_CHARS_IN_OFFENDING_LEVEL = (
    "# Some Spec\n\n## Maturity\n\n- lookout: bad\x1bvalue\n"
)

LONG_INVALID_LEVEL = "# Some Spec\n\n## Maturity\n\n- lookout: " + ("gibberish " * 40) + "\n"

TERMINATES_AT_NEXT_HEADING = """\
# Some Spec

## Maturity

- lookout: prototype

## Other Section

- trailhead: production
"""

ZERO_ENTRIES = """\
# Some Spec

## Maturity


## Other Section

irrelevant
"""

UNRESOLVED_ENUMERATION_MARKER = """\
# Some Spec

## Maturity

<!-- unresolved-enumeration: step 1 could not enumerate the repositories this work touches -->

## Other Section

irrelevant
"""

ZERO_ENTRIES_WITH_UNRELATED_COMMENT = """\
# Some Spec

## Maturity

<!-- TODO: fill this in later -->

## Other Section

irrelevant
"""

UNRESOLVED_ENUMERATION_MARKER_MID_SECTION = """\
# Some Spec

## Maturity

Some text before.

<!-- unresolved-enumeration: could not enumerate -->

## Other Section

irrelevant
"""

MEMBER_NAME_WITH_CONTROL_CHARS = (
    "# Some Spec\n\n## Maturity\n\n- look\x1bout: prototype\n"
)

DUPLICATE_SECTION_AND_INVALID_LEVEL = """\
# Some Spec

## Maturity

- lookout: banana

## Other

blah

## Maturity

- lookout: prototype
"""

CASE_VARIANT_MEMBER_NAMES = """\
# Some Spec

## Maturity

- LOOKOUT: production
- lookout: early
- Lookout: prototype
"""

CASE_VARIANT_AGAINST_FIRST_DECLARATION = """\
# Some Spec

## Maturity

- lookout: production
- LOOKOUT: prototype
"""

DISTINCT_NAMES_DIFFER_BY_MORE_THAN_CASE = """\
# Some Spec

## Maturity

- lookout: production
- Trailhead: early
"""

MARKER_INSIDE_FENCED_CODE_BLOCK = """\
# Some Spec

## Maturity

```
<!-- unresolved-enumeration: this is code, not a real marker -->
```

## Other Section

irrelevant
"""

INDENTED_UNRESOLVED_ENUMERATION_MARKER = """\
# Some Spec

## Maturity

  <!-- unresolved-enumeration: indented but still the real marker -->

## Other Section

irrelevant
"""

MARKER_ALONGSIDE_VALID_ENTRIES = """\
# Some Spec

## Maturity

- lookout: prototype
<!-- unresolved-enumeration: leftover note, entries exist so this is not the empty case -->
- trailhead: production
"""


def _run(stdin_bytes: bytes) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(STAMP)],
        input=stdin_bytes,
        capture_output=True,
    )


def _lines(result: subprocess.CompletedProcess) -> list[str]:
    return result.stdout.decode("utf-8").splitlines()


def _err_lines(result: subprocess.CompletedProcess) -> list[str]:
    return result.stderr.decode("utf-8").splitlines()


# ---- success paths --------------------------------------------------------


def test_two_entries_sorted_by_member_name_not_source_order():
    result = _run(TWO_ENTRIES_REVERSE_ORDER.encode("utf-8"))
    assert result.returncode == 0
    assert _lines(result) == ["maturity: lookout=prototype, trailhead=production"]


def test_single_entry_section_exits_zero():
    result = _run(SINGLE_ENTRY.encode("utf-8"))
    assert result.returncode == 0
    assert _lines(result) == ["maturity: lookout=early"]


# ---- fail-closed on stdin that cannot be resolved at all ------------------


def test_empty_stdin_exits_two_with_empty_stdin_reason_code():
    result = _run(b"")
    assert result.returncode == 2
    assert not any(line.startswith("maturity:") for line in _lines(result))
    assert "reason-code: empty-stdin" in result.stderr.decode("utf-8")


def test_non_utf8_stdin_exits_two_with_invalid_utf8_stdin_reason_code():
    result = _run(b"\xff\xfe not valid utf-8")
    assert result.returncode == 2
    assert not any(line.startswith("maturity:") for line in _lines(result))
    assert "reason-code: invalid-utf8-stdin" in result.stderr.decode("utf-8")


# ---- section presence / uniqueness ----------------------------------------


def test_no_maturity_heading_exits_two_with_section_absent():
    result = _run(NO_MATURITY_HEADING.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: section-absent" in result.stderr.decode("utf-8")


def test_two_unmasked_maturity_headings_exits_two_with_duplicate_section():
    result = _run(DUPLICATE_HEADING.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: duplicate-section" in result.stderr.decode("utf-8")


def test_heading_only_inside_fenced_block_does_not_satisfy_heading_search():
    result = _run(HEADING_ONLY_IN_FENCE.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: section-absent" in result.stderr.decode("utf-8")


def test_heading_mid_sentence_not_at_line_start_does_not_satisfy_it():
    result = _run(HEADING_MID_SENTENCE.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: section-absent" in result.stderr.decode("utf-8")


# ---- entry grammar ---------------------------------------------------------


def test_level_outside_vocabulary_exits_two_with_invalid_level_and_names_it():
    result = _run(INVALID_LEVEL.encode("utf-8"))
    assert result.returncode == 2
    err = _err_lines(result)
    assert "reason-code: invalid-level" in "\n".join(err)
    assert any("banana" in line for line in err)


def test_entry_not_matching_bullet_grammar_at_all_is_malformed_entry():
    result = _run(MALFORMED_ENTRY_NO_COLON.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: malformed-entry" in result.stderr.decode("utf-8")


def test_member_name_outside_safe_shape_is_rejected_as_malformed_entry():
    result = _run(MALFORMED_MEMBER_NAME.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: malformed-entry" in result.stderr.decode("utf-8")


def test_member_name_single_dot_is_rejected_as_malformed_entry():
    """A bare `.` as the whole member name would later be treated as a path
    segment by an AC7 attribution consumer; reject it here rather than let it
    read back as a legitimate repository key."""
    result = _run(MEMBER_NAME_SINGLE_DOT.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: malformed-entry" in result.stderr.decode("utf-8")


def test_member_name_double_dot_is_rejected_as_malformed_entry():
    """A bare `..` as the whole member name is the same path-segment hazard as
    `.`, one directory level up."""
    result = _run(MEMBER_NAME_DOUBLE_DOT.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: malformed-entry" in result.stderr.decode("utf-8")


def test_member_name_containing_dots_but_not_only_dots_still_accepted():
    """The rejection is scoped to the whole name being exactly `.` or `..` —
    a name that merely contains dots (a legitimate shape under the existing
    `[A-Za-z0-9._-]+` grammar) must still resolve cleanly."""
    result = _run(MEMBER_NAME_EMBEDS_DOTS_BUT_IS_NOT_JUST_DOTS.encode("utf-8"))
    assert result.returncode == 0
    assert _lines(result) == ["maturity: ..lookout=production"]


def test_same_member_named_twice_exits_two_with_duplicate_member_regardless_of_agreement():
    result = _run(DUPLICATE_MEMBER.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: duplicate-member" in result.stderr.decode("utf-8")


# ---- member-name length bound ----------------------------------------------


def test_member_name_at_the_length_bound_still_resolves():
    name = "a" * 100
    spec = f"# Some Spec\n\n## Maturity\n\n- {name}: production\n"
    result = _run(spec.encode("utf-8"))
    assert result.returncode == 0
    assert _lines(result) == [f"maturity: {name}=production"]


def test_member_name_one_character_over_the_length_bound_refuses():
    name = "a" * 101
    spec = f"# Some Spec\n\n## Maturity\n\n- {name}: production\n"
    result = _run(spec.encode("utf-8"))
    assert result.returncode == 2
    err = result.stderr.decode("utf-8")
    assert "reason-code: member-name-too-long" in err


def test_member_name_over_the_length_bound_never_echoes_the_offending_value():
    name = "a" * 101
    spec = f"# Some Spec\n\n## Maturity\n\n- {name}: production\n"
    result = _run(spec.encode("utf-8"))
    assert result.returncode == 2
    err = result.stderr.decode("utf-8")
    assert name not in err
    assert "offending-value:" not in err


# ---- sanitization of offending text ---------------------------------------


def test_offending_level_with_control_characters_is_stripped_before_stderr():
    result = _run(CONTROL_CHARS_IN_OFFENDING_LEVEL.encode("utf-8"))
    assert result.returncode == 2
    err = result.stderr.decode("utf-8")
    assert "\x1b" not in err
    assert any("badvalue" in line for line in _err_lines(result))


def test_offending_level_beyond_length_bound_is_truncated_with_marker():
    result = _run(LONG_INVALID_LEVEL.encode("utf-8"))
    assert result.returncode == 2
    err = result.stderr.decode("utf-8")
    offending = [line for line in err.splitlines() if "offending-value:" in line]
    assert offending, "invalid-level rejection must report an offending-value line"
    assert "…(truncated)" in offending[0]
    assert len(offending[0]) < len(LONG_INVALID_LEVEL)


def test_member_name_with_control_characters_is_sanitized_on_rejection_path():
    result = _run(MEMBER_NAME_WITH_CONTROL_CHARS.encode("utf-8"))
    assert result.returncode == 2
    err = result.stderr.decode("utf-8")
    assert "\x1b" not in err
    assert "reason-code: malformed-entry" in err
    assert "offending-value: lookout" in err


# ---- section boundary ------------------------------------------------------


def test_section_terminates_at_next_top_level_heading():
    result = _run(TERMINATES_AT_NEXT_HEADING.encode("utf-8"))
    assert result.returncode == 0
    assert _lines(result) == ["maturity: lookout=prototype"]


# ---- zero entries is its own outcome ---------------------------------------


def test_heading_present_with_zero_entries_is_its_own_distinct_reason_code():
    result = _run(ZERO_ENTRIES.encode("utf-8"))
    assert result.returncode == 2
    err = _err_lines(result)
    reason_code_lines = [line for line in err if "reason-code:" in line]
    assert len(reason_code_lines) == 1
    assert reason_code_lines[0].endswith("reason-code: empty-section")


# ---- the unresolved-enumeration marker is machine-distinguishable ---------


def test_unresolved_enumeration_marker_exits_two_with_its_own_reason_code():
    result = _run(UNRESOLVED_ENUMERATION_MARKER.encode("utf-8"))
    assert result.returncode == 2
    assert not any(line.startswith("maturity:") for line in _lines(result))
    err = _err_lines(result)
    reason_code_lines = [line for line in err if "reason-code:" in line]
    assert len(reason_code_lines) == 1
    assert reason_code_lines[0].endswith("reason-code: unresolved-enumeration")


def test_zero_entries_with_an_unrelated_comment_is_still_plain_empty_section():
    """An HTML comment that is not the exact unresolved-enumeration marker must
    not be mistaken for it — the marker is a specific string, not any comment
    at all under the heading."""
    result = _run(ZERO_ENTRIES_WITH_UNRELATED_COMMENT.encode("utf-8"))
    assert result.returncode == 2
    err = _err_lines(result)
    reason_code_lines = [line for line in err if "reason-code:" in line]
    assert len(reason_code_lines) == 1
    assert reason_code_lines[0].endswith("reason-code: empty-section")


def test_stray_prose_before_the_marker_is_still_malformed_entry():
    """The marker only settles what an otherwise-empty section means; it does
    not let a non-bullet line elsewhere in the section skip the ordinary
    entry grammar check."""
    result = _run(UNRESOLVED_ENUMERATION_MARKER_MID_SECTION.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: malformed-entry" in result.stderr.decode("utf-8")


# ---- double-defect determinism ---------------------------------------------


def test_duplicate_section_and_invalid_level_reports_one_deterministic_reason_code():
    result = _run(DUPLICATE_SECTION_AND_INVALID_LEVEL.encode("utf-8"))
    assert result.returncode == 2
    err = _err_lines(result)
    reason_code_lines = [line for line in err if "reason-code:" in line]
    assert len(reason_code_lines) == 1
    assert reason_code_lines[0].endswith("reason-code: duplicate-section")


# ---- module docstring cross-reference --------------------------------------


def test_module_docstring_cross_references_the_open_prompt_injection_channel_task():
    text = STAMP.read_text(encoding="utf-8")
    assert "task/the-offending-value-echo-is-an-unclosed-prompt-injection-channel" in text


_TEN_REASON_CODES = (
    "empty-stdin",
    "invalid-utf8-stdin",
    "section-absent",
    "duplicate-section",
    "empty-section",
    "unresolved-enumeration",
    "malformed-entry",
    "invalid-level",
    "duplicate-member",
    "member-name-too-long",
)


def test_this_test_module_docstring_lists_all_ten_reason_codes():
    """This test module's own contract docstring (top of file) must stay in
    sync with the reader's actual reason-code vocabulary — a stale list here
    misdescribes the contract this file's tests actually pin."""
    with open(__file__, "r", encoding="utf-8") as f:
        module_docstring = f.read().split('"""')[1]
    for code in _TEN_REASON_CODES:
        assert code in module_docstring, (
            f"this test module's docstring must list reason-code {code!r}: "
            f"{module_docstring!r}"
        )


# ---- case-fold collisions on member names are rejected, never silently folded --


def test_case_variant_member_names_are_rejected_as_duplicate_member():
    """LOOKOUT / lookout / Lookout would resolve to one path segment on a
    case-insensitive filesystem (macOS APFS default, Windows) once an AC7
    attribution consumer treats the member name as a path segment — reject the
    collision here rather than silently writing three distinct entries that
    later collapse into one, whichever the consumer happens to read last."""
    result = _run(CASE_VARIANT_MEMBER_NAMES.encode("utf-8"))
    assert result.returncode == 2
    assert not any(line.startswith("maturity:") for line in _lines(result))
    assert "reason-code: duplicate-member" in result.stderr.decode("utf-8")


def test_case_variant_against_a_single_earlier_declaration_is_also_rejected():
    result = _run(CASE_VARIANT_AGAINST_FIRST_DECLARATION.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: duplicate-member" in result.stderr.decode("utf-8")


def test_names_differing_by_more_than_case_still_resolve_cleanly():
    """The collision check is scoped to case-only collisions — two genuinely
    distinct member names must still resolve, so the fix cannot be a blanket
    case-fold-everything that would reject legitimate distinct repositories."""
    result = _run(DISTINCT_NAMES_DIFFER_BY_MORE_THAN_CASE.encode("utf-8"))
    assert result.returncode == 0
    assert _lines(result) == ["maturity: Trailhead=early, lookout=production"]


# ---- the unresolved-enumeration marker scan is fence-aware and not column-anchored


def test_marker_inside_fenced_code_block_is_not_read_as_a_genuine_declaration():
    """A marker-shaped string typed inside a fenced code block under the
    heading is code content, not a real declaration — the section still has
    zero real entries and no genuine marker, so it is plain `empty-section`,
    matching what an unfenced twin with no marker at all would report."""
    result = _run(MARKER_INSIDE_FENCED_CODE_BLOCK.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: empty-section" in result.stderr.decode("utf-8")


def test_indented_marker_is_still_recognized_as_the_real_marker():
    """The marker regex must not be column-anchored — an indented marker is
    still the genuine unresolved-enumeration statement, not a degraded
    empty-section."""
    result = _run(INDENTED_UNRESOLVED_ENUMERATION_MARKER.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: unresolved-enumeration" in result.stderr.decode("utf-8")


def test_marker_alongside_valid_entries_is_ignored_and_exits_zero():
    """The marker only settles what an otherwise-empty section means. A
    section with real entries plus a leftover marker comment is not the
    empty case at all — the entries win and the comment is inert."""
    result = _run(MARKER_ALONGSIDE_VALID_ENTRIES.encode("utf-8"))
    assert result.returncode == 0
    assert _lines(result) == ["maturity: lookout=prototype, trailhead=production"]
