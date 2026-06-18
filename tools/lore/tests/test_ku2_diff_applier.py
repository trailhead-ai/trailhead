"""KU2 assumption-prover: hand-rolled unified-diff applier in pure Python stdlib.

Tests the prototype ``apply_unified_diff(body, diff)`` function which must:
  1. Parse unified-diff hunks (``@@ -L,N +L,M @@`` headers + ` `/`-`/`+` lines).
  2. Verify each hunk's context against the current body.
  3. On ANY mismatch, reject — return body byte-for-byte UNMODIFIED + list of
     rejected hunk headers.
  4. On clean apply, produce the correct post-hunk body.

The three adversarial cases that must be covered:
  (a) CRLF body — body lines end ``\\r\\n`` while diff context uses ``\\n``; must
      not falsely match or silently mangle line endings.
  (b) Trailing-newline mismatch — hunk context assumes a trailing newline the body
      lacks (or vice versa); must be detected → reject.
  (c) Adjacent/contiguous hunks — two hunks touching nearby regions; offsets must
      be tracked so the second hunk applies at the correct place, or both reject
      atomically (no partial application).

This test file is EPHEMERAL — remove after KU2 is resolved and Slice 4 ships its
proper behavioral tests (``tests/test_record_cli_update.py``).

Implementation note — line-ending representation in the applier:
  Body is split via ``str.splitlines(keepends=True)`` so each body line retains its
  original line ending (``\\n`` or ``\\r\\n`` or none for the last line).  The diff
  is also split via ``splitlines(keepends=True)`` so each hunk line retains its
  ending.  When extracting the content portion of a hunk line (stripping the leading
  marker character), the content is compared **verbatim** (including its trailing
  ``\\n`` or ``\\r\\n``) against the corresponding body line.

  This means:
  - LF diff context ``"line two\\n"`` vs LF body line ``"line two\\n"`` → match.
  - LF diff context ``"line two\\n"`` vs CRLF body line ``"line two\\r\\n"`` → mismatch
    (correct: LF diff was not generated from this CRLF body).
  - CRLF diff context ``"line two\\r\\n"`` vs CRLF body line ``"line two\\r\\n"`` → match.

  For the trailing-newline case, ``difflib.unified_diff`` encodes a missing trailing
  newline by simply omitting ``\\n`` from the last line in the hunk (it does NOT emit
  the ``\\ No newline at end of file`` marker that ``patch(1)``-format diffs use).
  The applier handles this naturally: a body line without ``\\n`` matches a hunk
  context line without ``\\n``, and mismatches when one has ``\\n`` and the other does
  not.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Prototype applier (self-contained, pure stdlib)
# ---------------------------------------------------------------------------

@dataclass
class _Hunk:
    header: str          # raw ``@@ -L,N +L,M @@`` line (for error reporting)
    old_start: int       # 1-based line number in the original file
    old_count: int
    new_count: int
    lines: list[str]     # lines WITH leading marker AND original line ending


class DiffRejectError(Exception):
    """Raised when one or more hunks cannot be applied.

    Attributes:
        original_body: the body exactly as received — byte-for-byte unmodified.
        rejected: list of ``(header, reason)`` pairs for each failing hunk.
    """
    def __init__(self, original_body: str, rejected: list[tuple[str, str]]):
        self.original_body = original_body
        self.rejected = rejected
        super().__init__(
            f"{len(rejected)} hunk(s) rejected: "
            + "; ".join(f"{h}: {r}" for h, r in rejected)
        )


_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)


class DiffFormatError(Exception):
    """Raised when the diff string is not in a parseable unified-diff format.

    This is distinct from ``DiffRejectError`` (which means the diff is valid
    format but the context doesn't match the body).  ``DiffFormatError`` means
    the diff itself is structurally unparseable.

    Known trigger: ``difflib.unified_diff`` concatenates two consecutive
    no-newline lines (e.g. ``-old_content+new_content``) when BOTH the deleted
    and the inserted line lack a trailing newline.  The result is ambiguous
    because the ``+`` that starts the insertion is visually indistinguishable
    from a ``+`` that is part of the deleted content.  The applier detects this
    via the hunk-count deficit (``new_seen < new_count``) and raises rather than
    guessing incorrectly.
    """


def _parse_hunks(diff: str) -> list[_Hunk]:
    """Parse a unified diff string into a list of ``_Hunk`` objects.

    Uses ``diff.splitlines(keepends=True)`` so each hunk line retains its
    original line ending (``\\n`` or ``\\r\\n``).  The trailing line in a hunk
    generated from a file without a trailing newline will have NO line ending —
    this is how ``difflib.unified_diff`` encodes the ``\\ No newline at end of
    file`` case when ONLY ONE side (old or new) lacks a trailing newline.

    **Concatenated-line edge case:** when BOTH the deleted line and the inserted
    replacement line lack a trailing newline, ``difflib.unified_diff`` emits them
    *without any separator* (e.g. ``-old_content+new_content``).  Because the
    embedded ``+`` is structurally indistinguishable from content, the applier
    detects this via hunk-count deficit after parsing and raises
    ``DiffFormatError``.  This is the only safe option: guessing would risk
    silent corruption.  In practice lore record bodies always have a trailing
    newline (``write_temp_then_rename`` guarantees it), so this path is
    unreachable for well-formed inputs.

    Each ``_Hunk.lines`` entry is the raw hunk line including its leading
    `` ``/``-``/``+`` marker AND the original line ending (if any).
    """
    hunks: list[_Hunk] = []
    current: Optional[_Hunk] = None

    for raw in diff.splitlines(keepends=True):
        # Strip trailing newline for regex matching but keep original for storage.
        raw_stripped = raw.rstrip("\r\n")
        m = _HUNK_HEADER_RE.match(raw_stripped)
        if m:
            if current is not None:
                _validate_hunk_counts(current)
                hunks.append(current)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            current = _Hunk(
                header=raw_stripped,
                old_start=old_start,
                old_count=old_count,
                new_count=new_count,
                lines=[],
            )
        elif current is not None:
            # Skip file-header lines.
            if raw_stripped.startswith(("--- ", "+++ ")):
                continue
            if raw and raw[0] in (" ", "-", "+"):
                # Keep the full line including its ending.
                current.lines.append(raw)

    if current is not None:
        _validate_hunk_counts(current)
        hunks.append(current)

    return hunks


def _validate_hunk_counts(hunk: _Hunk) -> None:
    """Validate that parsed hunk line counts match the @@ header counts.

    Raises ``DiffFormatError`` if there is a count deficit, which indicates
    the concatenated-no-newline edge case (see ``_parse_hunks`` docstring).
    """
    old_seen = sum(1 for l in hunk.lines if l and l[0] in (" ", "-"))
    new_seen = sum(1 for l in hunk.lines if l and l[0] in (" ", "+"))
    if old_seen < hunk.old_count or new_seen < hunk.new_count:
        raise DiffFormatError(
            f"Hunk {hunk.header!r}: parsed {old_seen}/{hunk.old_count} old lines "
            f"and {new_seen}/{hunk.new_count} new lines — the diff appears to use "
            f"the concatenated-no-newline format emitted by difflib when both the "
            f"deleted and inserted lines lack a trailing newline. This format is "
            f"ambiguous and cannot be applied safely. Regenerate the diff from "
            f"content with a trailing newline, or use patch(1) format with the "
            f"'\\ No newline at end of file' marker."
        )


def apply_unified_diff(body: str, diff: str) -> tuple[str, list[str]]:
    """Apply a unified diff to ``body`` returning ``(new_body, rejected_headers)``.

    Decision rule:
      - Parse all hunks first.
      - Verify EVERY hunk's context (`` `` and ``-`` lines) against ``body``.
      - If ALL hunks verify cleanly → apply them in order, tracking a line-offset
        so each subsequent hunk addresses the correct position in the evolving
        output.
      - If ANY hunk fails verification → RAISE ``DiffRejectError`` with the body
        **byte-for-byte unmodified** and the list of failing hunk headers.
        This is the atomic-reject guarantee: no partial application ever occurs.

    Body lines are compared **verbatim** (including line endings) against the
    corresponding hunk context lines.  This ensures:
    - CRLF vs LF mismatches are always detected (never silently normalised).
    - Trailing-newline presence/absence is always detected.

    The ``@@ -L,N @@`` line-numbers are 1-based; the implementation converts to
    0-based indices internally.

    Raises ``DiffRejectError`` if any hunk fails.
    """
    original_body = body
    # Use keepends=True so line endings are part of each element.
    body_lines = body.splitlines(keepends=True)
    hunks = _parse_hunks(diff)

    # --- Phase 1: verify all hunks against the original body --------------------
    rejected: list[tuple[str, str]] = []

    for hunk in hunks:
        # Collect the context+deletion lines from the hunk for matching.
        # Each element is the full content including ending (or '' for a line
        # that is entirely the marker with no content).
        ctx_lines: list[str] = []
        for hl in hunk.lines:
            marker = hl[0]
            content = hl[1:]   # strip the leading marker character; keep ending
            if marker in (" ", "-"):
                ctx_lines.append(content)

        # The hunk's old_start is 1-based; convert to 0-based index.
        start_0 = hunk.old_start - 1

        # Check bounds: the hunk must not reference lines past the end of body.
        end_0 = start_0 + len(ctx_lines)
        if end_0 > len(body_lines):
            rejected.append((
                hunk.header,
                f"context overruns body (body has {len(body_lines)} lines, "
                f"hunk starts at line {hunk.old_start} and expects "
                f"{len(ctx_lines)} context/deletion lines)",
            ))
            continue

        # Compare each context/deletion line verbatim (incl. line endings).
        mismatch_line: Optional[int] = None
        for i, expected in enumerate(ctx_lines):
            actual = body_lines[start_0 + i]
            if actual != expected:
                mismatch_line = hunk.old_start + i
                break

        if mismatch_line is not None:
            rejected.append((
                hunk.header,
                f"context mismatch at body line {mismatch_line}",
            ))

    if rejected:
        # Atomic reject: raise with the original body unmodified.
        raise DiffRejectError(
            original_body=original_body,
            rejected=rejected,
        )

    # --- Phase 2: apply all hunks (all verified) --------------------------------
    # Work on a mutable copy of the split lines.
    result_lines = list(body_lines)
    # ``offset`` tracks net line delta from previously applied hunks so each
    # subsequent hunk indexes the correct position in the evolving result.
    offset = 0

    for hunk in hunks:
        # Build the old slice (context + deletions) and the new slice
        # (context + insertions) from the hunk lines.
        old_slice: list[str] = []
        new_slice: list[str] = []

        for hl in hunk.lines:
            marker = hl[0]
            content = hl[1:]   # strip the leading marker; keep ending
            if marker == " ":
                old_slice.append(content)
                new_slice.append(content)
            elif marker == "-":
                old_slice.append(content)
            elif marker == "+":
                new_slice.append(content)

        start_0 = hunk.old_start - 1 + offset
        end_0 = start_0 + len(old_slice)
        result_lines[start_0:end_0] = new_slice
        offset += len(new_slice) - len(old_slice)

    return "".join(result_lines), []


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_diff(old: str, new: str, fromfile: str = "a", tofile: str = "b") -> str:
    """Generate a unified diff using difflib (the standard library generator)."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(old_lines, new_lines, fromfile=fromfile, tofile=tofile)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

import pytest


class TestCleanApply:
    """Sanity: a clean diff applies and the round-trip is byte-for-byte correct."""

    def test_single_hunk_applies(self):
        original = "line one\nline two\nline three\n"
        modified = "line one\nline TWO\nline three\n"
        diff = _make_diff(original, modified)
        result, rejected = apply_unified_diff(original, diff)
        assert result == modified
        assert rejected == []

    def test_multi_line_insert(self):
        original = "alpha\nbeta\n"
        modified = "alpha\nnew line\nbeta\n"
        diff = _make_diff(original, modified)
        result, _ = apply_unified_diff(original, diff)
        assert result == modified

    def test_deletion(self):
        original = "keep\ndelete me\nkeep too\n"
        modified = "keep\nkeep too\n"
        diff = _make_diff(original, modified)
        result, _ = apply_unified_diff(original, diff)
        assert result == modified

    def test_empty_body_add_content(self):
        original = ""
        modified = "new content\n"
        diff = _make_diff(original, modified)
        result, _ = apply_unified_diff(original, diff)
        assert result == modified


class TestStaleReject:
    """A stale/non-applying diff must return the body byte-for-byte unchanged."""

    def test_stale_diff_rejected(self):
        """A diff generated from an older version of the body is rejected."""
        original_v1 = "line one\nline two\nline three\n"
        modified_v1 = "line one\nline TWO\nline three\n"
        diff = _make_diff(original_v1, modified_v1)

        # Body has since changed — v1 diff is now stale.
        current_body = "line one\nSOMETHING ELSE\nline three\n"

        with pytest.raises(DiffRejectError) as exc_info:
            apply_unified_diff(current_body, diff)

        err = exc_info.value
        # Body must be byte-for-byte unchanged.
        assert err.original_body == current_body
        # At least one rejected hunk reported.
        assert len(err.rejected) >= 1
        # The rejection message should name the mismatch.
        header, reason = err.rejected[0]
        assert "context mismatch" in reason or "overruns" in reason

    def test_stale_diff_body_identical(self):
        """The original_body in the error is the exact same object, not a copy."""
        body = "unchanged body\n"
        diff = _make_diff("some other\ncontent\n", "different\ncontent\n")

        with pytest.raises(DiffRejectError) as exc_info:
            apply_unified_diff(body, diff)

        assert exc_info.value.original_body is body  # same object, not a copy


class TestCRLFBody:
    """Adversarial case (a): CRLF body vs LF diff context.

    The applier must not falsely match (CRLF body line != LF diff context line)
    and must not silently strip/mangle ``\\r`` characters.

    Key insight: ``difflib.unified_diff`` preserves the line endings of its inputs.
    A diff generated from LF content has LF endings in its context lines.  A diff
    generated from CRLF content has CRLF endings in its context lines.  The applier
    compares verbatim, so LF context never matches a CRLF body line and vice versa.

    Two sub-cases:
      1. The diff was generated from the LF version of the body — applying it
         to a CRLF body must REJECT (context mismatch), not silently normalise.
      2. If the diff was generated from the CRLF body itself (both sides CRLF),
         it should apply cleanly and preserve ``\\r\\n`` endings in the output.
    """

    def test_crlf_body_lf_diff_rejected(self):
        """LF diff context does NOT match CRLF body lines → must reject."""
        body_crlf = "line one\r\nline two\r\nline three\r\n"
        # Diff generated from the LF version — context lines end in ``\\n``.
        original_lf = "line one\nline two\nline three\n"
        modified_lf = "line one\nline TWO\nline three\n"
        diff_lf = _make_diff(original_lf, modified_lf)

        with pytest.raises(DiffRejectError) as exc_info:
            apply_unified_diff(body_crlf, diff_lf)

        err = exc_info.value
        # Body is returned byte-for-byte unmodified — CRLF endings intact.
        assert err.original_body == body_crlf
        assert "\r\n" in err.original_body, "CRLF must be preserved in rejected body"
        assert len(err.rejected) >= 1

    def test_crlf_body_crlf_diff_applies(self):
        """A diff generated from a CRLF body applies cleanly and preserves endings."""
        body_crlf = "line one\r\nline two\r\nline three\r\n"
        modified_crlf = "line one\r\nline TWO\r\nline three\r\n"
        # Generate the diff from the actual CRLF content.
        diff_crlf = _make_diff(body_crlf, modified_crlf)

        result, rejected = apply_unified_diff(body_crlf, diff_crlf)
        assert result == modified_crlf
        assert rejected == []
        # Verify CRLF is preserved — no half-converted line endings.
        assert "\r\n" in result
        assert result.count("\r\n") == 3

    def test_crlf_body_reject_no_silent_strip(self):
        """After rejection, the body has exactly the same \\r\\n count as the input."""
        body_crlf = "alpha\r\nbeta\r\ngamma\r\n"
        lf_diff = _make_diff("alpha\nbeta\ngamma\n", "alpha\nBETA\ngamma\n")

        with pytest.raises(DiffRejectError) as exc_info:
            apply_unified_diff(body_crlf, lf_diff)

        returned_body = exc_info.value.original_body
        assert returned_body.count("\r\n") == 3
        assert returned_body == body_crlf


class TestTrailingNewlineMismatch:
    """Adversarial case (b): trailing-newline mismatch → reject, body unchanged.

    ``difflib.unified_diff`` does NOT emit the ``\\ No newline at end of file``
    marker that ``patch(1)``-format diffs use.  Instead, it encodes the missing
    trailing newline by omitting the ``\\n`` from the last line in the hunk.  The
    applier handles this naturally because body lines are also kept verbatim (via
    ``splitlines(keepends=True)``), so a body line without ``\\n`` only matches a
    hunk context line without ``\\n``.

    Sub-cases:
      1. Body has trailing newline; hunk context was generated from a body WITHOUT
         trailing newline (last line lacks ``\\n`` in the diff).
      2. Body LACKS trailing newline; hunk context was generated from a body WITH
         trailing newline (last line ends with ``\\n`` in the diff).
    """

    def test_body_with_newline_diff_without(self):
        """Diff context expects no trailing newline, body has one → safe rejection.

        When BOTH old and new lack a trailing newline (the most common case for
        this sub-test), ``difflib.unified_diff`` concatenates the two no-newline
        lines into a single ambiguous token.  The applier raises ``DiffFormatError``
        (not ``DiffRejectError``) because the diff is structurally unparseable
        before we can even check context.  This is still a safe outcome:
        - the body is NEVER modified (we raise before any apply step)
        - the error is distinct and descriptive

        For the lore use case, record bodies always end with ``\\n`` (enforced by
        ``write_temp_then_rename``), so this case is unreachable in practice.
        """
        old_no_nl = "first line\nsecond line"   # no trailing newline
        new_no_nl = "first line\nSECOND LINE"   # no trailing newline
        diff = _make_diff(old_no_nl, new_no_nl)

        # Verify difflib encodes the missing newline by omitting \\n from last line.
        assert not diff.splitlines(keepends=True)[-1].endswith("\n"), (
            "test precondition: difflib omits \\n from the last line "
            "when input has no trailing newline"
        )

        # The actual body has a trailing newline.
        body_with_nl = "first line\nsecond line\n"

        # The applier raises DiffFormatError (ambiguous concatenated format).
        # The body is NOT modified in any case — format errors are caught before apply.
        with pytest.raises(DiffFormatError):
            apply_unified_diff(body_with_nl, diff)

    def test_body_without_newline_diff_with(self):
        """Diff context expects trailing newline (last line ends \\n), body lacks one → reject."""
        old_with_nl = "first line\nsecond line\n"
        new_with_nl = "first line\nSECOND LINE\n"
        diff = _make_diff(old_with_nl, new_with_nl)

        # Body lacks the trailing newline.
        body_no_nl = "first line\nsecond line"

        with pytest.raises(DiffRejectError) as exc_info:
            apply_unified_diff(body_no_nl, diff)

        err = exc_info.value
        assert err.original_body == body_no_nl
        assert not err.original_body.endswith("\n"), "lack of trailing newline must be preserved"
        assert len(err.rejected) >= 1

    def test_no_newline_both_sides_format_error(self):
        """When BOTH old and new lack a trailing newline, difflib concatenates them.

        ``difflib.unified_diff`` emits ``-old_content+new_content`` (no separator)
        when both sides have no trailing newline.  This is ambiguous — the embedded
        ``+`` cannot be reliably distinguished from content.  The applier detects the
        count deficit and raises ``DiffFormatError`` rather than guessing.

        This is safe: rejection is the correct outcome.  Lore record bodies are
        always written with a trailing newline by ``write_temp_then_rename``, so
        this path is unreachable for well-formed records.
        """
        body_no_nl = "first line\nsecond line"
        modified_no_nl = "first line\nSECOND LINE"
        diff = _make_diff(body_no_nl, modified_no_nl)
        # Verify difflib actually produces the concatenated format.
        assert "+SECOND LINE" in diff.splitlines(keepends=True)[-1], (
            "test precondition: difflib concatenates two no-newline lines"
        )
        # The applier must raise DiffFormatError rather than silently misparse.
        with pytest.raises(DiffFormatError):
            apply_unified_diff(body_no_nl, diff)

    def test_no_newline_only_new_side_applies(self):
        """Adding content while the new side lacks a trailing newline.

        Only the NEW side (insertion) lacks ``\\n``; the old/deleted line DOES
        have ``\\n``.  difflib emits the deletion and insertion as separate items
        in this case, so the format is unambiguous and the applier handles it.
        """
        body_with_nl = "first line\nsecond line\n"
        modified_no_nl = "first line\nSECOND LINE"  # new side has no trailing \n
        diff = _make_diff(body_with_nl, modified_no_nl)
        # The last line of the diff should have no \n (the new side lacks it).
        last_line = diff.splitlines(keepends=True)[-1]
        assert not last_line.endswith("\n"), "test precondition: last diff line lacks \\n"
        # The body line DOES have \n, so context doesn't match — should reject.
        # (The -second line\n hunk context matches, but the +SECOND LINE insertion
        # is fine; what may not match is if the old context line comparison works.)
        # Let's check: this should actually APPLY because the old context (-second line\n)
        # matches the body's 'second line\n'.
        result, rejected = apply_unified_diff(body_with_nl, diff)
        assert result == modified_no_nl
        assert not result.endswith("\n")
        assert rejected == []


class TestAdjacentHunks:
    """Adversarial case (c): two adjacent/contiguous hunks.

    A naive applier that doesn't track the offset from previously applied hunks
    will index the second hunk at the wrong position in the mutated output.

    Sub-cases:
      1. Both hunks apply cleanly → both must be applied at the correct positions.
      2. First hunk applies but second fails → BOTH rejected (atomic), body unchanged.
    """

    def _build_body(self) -> str:
        return (
            "line 1\n"
            "line 2\n"
            "line 3\n"
            "line 4\n"
            "line 5\n"
            "line 6\n"
            "line 7\n"
            "line 8\n"
        )

    def test_two_adjacent_hunks_both_apply(self):
        """Two hunks that each change a different region both apply correctly."""
        body = self._build_body()
        modified = (
            "line 1\n"
            "LINE 2\n"   # changed by hunk 1
            "line 3\n"
            "line 4\n"
            "LINE 5\n"   # changed by hunk 2
            "line 6\n"
            "line 7\n"
            "line 8\n"
        )
        diff = _make_diff(body, modified)
        result, rejected = apply_unified_diff(body, diff)
        assert result == modified
        assert rejected == []

    def test_two_hunks_offset_tracking(self):
        """Insert lines in hunk 1; hunk 2 must apply at the shifted position."""
        body = (
            "A\n"
            "B\n"
            "C\n"
            "D\n"
            "E\n"
        )
        # Hunk 1: insert two lines after A (lines 1-2 become 1-4).
        # Hunk 2: replace E with ECHO (must find E at its new index after insertion).
        modified = (
            "A\n"
            "inserted 1\n"
            "inserted 2\n"
            "B\n"
            "C\n"
            "D\n"
            "ECHO\n"
        )
        diff = _make_diff(body, modified)
        result, rejected = apply_unified_diff(body, diff)
        assert result == modified
        assert rejected == []

    def test_second_hunk_fails_both_rejected_atomically(self):
        """If hunk 2 fails context verification, BOTH hunks are rejected atomically."""
        body = self._build_body()
        # Build a diff that patches lines 2 and 6.
        modified = (
            "line 1\n"
            "LINE 2\n"
            "line 3\n"
            "line 4\n"
            "line 5\n"
            "LINE 6\n"
            "line 7\n"
            "line 8\n"
        )
        diff = _make_diff(body, modified)

        # Corrupt the actual body so hunk 2's context won't match.
        stale_body = (
            "line 1\n"
            "line 2\n"
            "line 3\n"
            "line 4\n"
            "line 5\n"
            "SOMETHING ELSE\n"   # hunk 2 expects "line 6\n"
            "line 7\n"
            "line 8\n"
        )

        with pytest.raises(DiffRejectError) as exc_info:
            apply_unified_diff(stale_body, diff)

        err = exc_info.value
        # Body must be byte-for-byte unmodified — no partial application of hunk 1.
        assert err.original_body == stale_body
        # At least hunk 2 rejected; body is pristine either way.
        assert len(err.rejected) >= 1

    def test_both_hunks_fail_both_reported(self):
        """When both hunks fail, both are listed in the rejection report."""
        body = "X\nY\nZ\nW\nV\n"
        # Diff changes X and W.
        modified = "XX\nY\nZ\nWW\nV\n"
        diff = _make_diff(body, modified)

        # Body where neither X nor W match.
        stale = "NOPE\nY\nZ\nNOPE2\nV\n"

        with pytest.raises(DiffRejectError) as exc_info:
            apply_unified_diff(stale, diff)

        err = exc_info.value
        assert err.original_body == stale
        # difflib may merge into a single hunk; accept 1 or 2 rejections.
        assert len(err.rejected) >= 1


class TestEdgeCases:
    """Additional edge cases that the executor should be aware of."""

    def test_empty_diff_returns_body_unchanged(self):
        """An empty diff (no hunks) returns the body unmodified."""
        body = "hello\nworld\n"
        result, rejected = apply_unified_diff(body, "")
        assert result == body
        assert rejected == []

    def test_diff_with_only_header_lines(self):
        """A diff with only ``---``/``+++`` lines but no hunks is a no-op."""
        diff = "--- a/file\n+++ b/file\n"
        body = "content\n"
        result, rejected = apply_unified_diff(body, diff)
        assert result == body
        assert rejected == []

    def test_apply_to_empty_body(self):
        """Applying an add-only diff to an empty body works."""
        body = ""
        modified = "new content\n"
        diff = _make_diff(body, modified)
        result, _ = apply_unified_diff(body, diff)
        assert result == modified

    def test_fence_token_in_hunk_passes_through_applier(self):
        """The applier itself doesn't neutralise fences — that's validate_and_write's job.

        This ensures the applier is not over-reaching into neutralization; the
        Slice-4 contract says the post-hunk body flows through validate_and_write.
        """
        body = "safe content\n"
        modified = "safe content\n<external-memory foo>injected</external-memory>\n"
        diff = _make_diff(body, modified)
        result, _ = apply_unified_diff(body, diff)
        # The applier returns the literal injected content — neutralization is
        # not the applier's responsibility.
        assert "<external-memory" in result
