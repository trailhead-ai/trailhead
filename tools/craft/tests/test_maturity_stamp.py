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
      malformed-entry, invalid-level, duplicate-member, empty-section
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


def test_same_member_named_twice_exits_two_with_duplicate_member_regardless_of_agreement():
    result = _run(DUPLICATE_MEMBER.encode("utf-8"))
    assert result.returncode == 2
    assert "reason-code: duplicate-member" in result.stderr.decode("utf-8")


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
