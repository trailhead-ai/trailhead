"""EPHEMERAL assumption-prover test — delete after Slice 1a is committed.

Proves that a compressed harvest-candidates stub's documented emission format
produces output that the real hook (harvest-candidates.py) parses into the
correct typed entries.

Plan: /brain/plans/2026-06/2026-06-12-trailhead-shared-scaffolding-dedup.md
Unknown: "Harvest stub minimum-viable content" (Known Unknowns section)
Blocks: Slice 1a → 1b

Clean up: remove this entire file after Slice 1a's implementer adds proper tests.
File to delete: tools/lore/tests/test_harvest_stub_proof.py (lines 1-end)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the ACTUAL hook regexes — not copies, the real ones.
# ---------------------------------------------------------------------------

HOOK_PATH = (
    Path(__file__).resolve().parent.parent
    / "plugins" / "lore" / "hooks" / "harvest-candidates.py"
)

# Load the hook module directly so we get SECTION_RE and ENTRY_RE verbatim.
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("harvest_candidates_hook", HOOK_PATH)
_hook = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_hook)

SECTION_RE: re.Pattern = _hook.SECTION_RE
ENTRY_RE: re.Pattern = _hook.ENTRY_RE


def _parse_entries(text: str) -> list[tuple[str, str]]:
    """Drive text through the hook's two-step parse; return [(type, body), ...]."""
    match = SECTION_RE.search(text)
    if not match:
        return []
    entries = []
    for raw_line in match.group(1).splitlines():
        m = ENTRY_RE.match(raw_line)
        if m:
            entries.append((m.group(1), m.group(2).strip()))
    return entries


# ---------------------------------------------------------------------------
# The candidate compressed stub (verbatim — implementer uses this text in 1a).
#
# Structure: heading (stub section title) + blank + one instruction line that
# explicitly names the `## Harvest candidates` heading to emit + the six typed
# prefixes + suffix rule + blank + role-specific sentence (verbatim from
# researcher.md:86).
#
# Key invariant proved here: the AGENT emits the heading + dashes directly
# (no prose between them), because the instruction line says
# "append a `## Harvest candidates` block ... with `- <type>: <body>` entry lines".
# The stub SECTION TITLE in the .md file is irrelevant to the hook parser; only
# the agent's OUTPUT is parsed.
# ---------------------------------------------------------------------------

CANDIDATE_STUB = """\
## Harvest candidates (end-of-message)

If your work surfaced anything durable, append a `## Harvest candidates` block as the LAST section of your message, with `- <type>: <body>` entry lines using: `lesson:` `dead-end:` `deferred:` `follow-up:` `decision:` `gotcha:`. Omit entirely if nothing qualifies; empty headers are noise.

For a researcher specifically, the highest-value emissions are **lessons** (durable invariants you discovered) and **gotchas** (surprising subsystem behavior worth patching into a subsystem profile). Skip dead-ends — those belong to troubleshooters/implementers who actually tried things."""


class TestCandidateStubFormat:
    """Positive: agent output following the compressed stub's documented format parses cleanly."""

    def _representative_output(self, entries_block: str) -> str:
        """Wrap an entries block in a realistic researcher final-message."""
        return (
            "**TL;DR**: The harvest hook anchors on a suffix block.\n\n"
            "## What I found\n\n"
            "The hook at `harvest-candidates.py:29-35` requires `## Harvest candidates`\n"
            "to be the message suffix, with `- <type>: <body>` lines immediately after.\n\n"
            "## Sources\n\n"
            "- `tools/lore/plugins/lore/hooks/harvest-candidates.py`\n\n"
            + entries_block
        )

    def test_all_six_types_captured(self):
        """All six valid typed prefixes parse into the correct (type, body) tuples."""
        msg = self._representative_output(
            "## Harvest candidates\n\n"
            "- lesson: the hook requires the block to be the message suffix\n"
            "- dead-end: tried inserting prose after the heading — SECTION_RE fails\n"
            "- deferred: revisit when Slice 1b rolls out to remaining 6 agents\n"
            "- follow-up: monitor whether compressed-stub agents emit malformed blocks\n"
            "- decision: prose instruction must precede the heading not follow it\n"
            "- gotcha: heading with extra text breaks SECTION_RE matching\n"
        )
        entries = _parse_entries(msg)
        assert len(entries) == 6, f"expected 6 entries, got {len(entries)}: {entries}"
        types_found = {e[0] for e in entries}
        assert types_found == {"lesson", "dead-end", "deferred", "follow-up", "decision", "gotcha"}

    def test_follow_up_type_captured(self):
        """Specifically proves `follow-up:` (the renamed type from `radar:`) parses."""
        msg = self._representative_output(
            "## Harvest candidates\n\n"
            "- follow-up: watch whether lore agents emit bad blocks post-stub-compress\n"
        )
        entries = _parse_entries(msg)
        assert entries == [("follow-up", "watch whether lore agents emit bad blocks post-stub-compress")]

    def test_suffix_anchor_holds_with_trailing_newline(self):
        """A single trailing newline after the last entry is accepted by \\s*\\Z."""
        msg = (
            "Report body.\n\n"
            "## Harvest candidates\n\n"
            "- lesson: trailing newline is fine\n"
            "- gotcha: the hook accepts one blank line at the very end\n"
        )
        entries = _parse_entries(msg)
        assert len(entries) == 2

    def test_multiple_blank_lines_before_first_entry(self):
        """\\n+ between heading and first dash accepts multiple blank lines."""
        msg = (
            "Report.\n\n"
            "## Harvest candidates\n\n\n\n"
            "- lesson: multiple blank lines before first dash still parses\n"
        )
        entries = _parse_entries(msg)
        assert entries == [("lesson", "multiple blank lines before first dash still parses")]

    def test_no_blank_line_between_heading_and_first_entry(self):
        """A single newline (no blank line) between heading and first dash also parses."""
        msg = (
            "Report.\n"
            "## Harvest candidates\n"
            "- lesson: no blank line required\n"
        )
        entries = _parse_entries(msg)
        assert entries == [("lesson", "no blank line required")]

    def test_body_whitespace_stripped(self):
        """Entry body is stripped of leading/trailing whitespace."""
        msg = (
            "## Harvest candidates\n\n"
            "-  lesson:   spaces around body are trimmed   \n"
        )
        entries = _parse_entries(msg)
        assert entries == [("lesson", "spaces around body are trimmed")]


class TestNegativeControls:
    """Negative: malformed / non-suffix blocks produce no entries."""

    def test_prose_between_heading_and_dashes_breaks_match(self):
        """The critical constraint: any non-dash line between heading and entries breaks SECTION_RE.

        This is the sharpest edge in the stub design. If an agent were to emit the
        stub's instruction prose verbatim between the heading and the dash list, the
        hook would produce ZERO entries — silent data loss.

        The compressed stub prevents this by instructing the agent to append a
        `## Harvest candidates` block (i.e., heading immediately followed by entries),
        not by inserting prose between heading and entries.
        """
        msg = (
            "Report.\n\n"
            "## Harvest candidates\n\n"
            "If your work surfaced anything durable, emit typed entries below.\n\n"
            "- lesson: this entry would be silently lost\n"
            "- follow-up: so would this one\n"
        )
        entries = _parse_entries(msg)
        assert entries == [], (
            "SECTION_RE should return no entries when prose appears between "
            f"the heading and the dash list, but got: {entries}"
        )

    def test_block_not_at_suffix_fails(self):
        """A harvest block followed by any non-whitespace text is rejected (not a suffix)."""
        msg = (
            "## Harvest candidates\n\n"
            "- lesson: something durable\n\n"
            "This trailing paragraph invalidates the suffix anchor.\n"
        )
        entries = _parse_entries(msg)
        assert entries == [], f"expected no entries for non-suffix block, got: {entries}"

    def test_heading_with_extra_text_fails_section_re(self):
        """'## Harvest candidates (end-of-message)' as the EMITTED heading breaks SECTION_RE.

        This confirms the stub must instruct the agent to emit exactly
        '## Harvest candidates' — not '## Harvest candidates (end-of-message)'.
        The stub's own section title in the .md file doesn't matter; only
        what the agent emits in its output message is parsed by the hook.
        """
        msg = (
            "Report.\n\n"
            "## Harvest candidates (end-of-message)\n\n"
            "- lesson: this heading variant breaks the regex\n"
        )
        entries = _parse_entries(msg)
        assert entries == [], (
            "SECTION_RE should not match '## Harvest candidates (end-of-message)' "
            f"as the emitted heading, but got: {entries}"
        )

    def test_invalid_type_prefix_not_captured(self):
        """Unknown typed prefixes (e.g., 'radar:') are silently skipped by ENTRY_RE."""
        msg = (
            "## Harvest candidates\n\n"
            "- radar: this type was renamed to follow-up in Spec A\n"
            "- lesson: this valid entry is captured\n"
        )
        entries = _parse_entries(msg)
        # Only the valid type is captured; 'radar:' is silently dropped
        assert entries == [("lesson", "this valid entry is captured")], entries

    def test_absent_harvest_section_produces_empty(self):
        """Message with no harvest block produces no entries."""
        msg = "Just a research report with no harvest candidates.\n"
        entries = _parse_entries(msg)
        assert entries == []


class TestStubInvariant:
    """Structural checks on the candidate stub itself."""

    def test_stub_contains_exact_heading_to_emit(self):
        """The stub instructs agents to emit '## Harvest candidates' verbatim."""
        assert "`## Harvest candidates`" in CANDIDATE_STUB, (
            "Stub must reference the exact heading string agents should emit"
        )

    def test_stub_names_all_six_valid_prefixes(self):
        """The stub names all six valid typed prefixes."""
        for prefix in ("lesson:", "dead-end:", "deferred:", "follow-up:", "decision:", "gotcha:"):
            assert prefix in CANDIDATE_STUB, f"Stub missing prefix: {prefix!r}"

    def test_stub_does_not_contain_invalid_radar_prefix(self):
        """The old 'radar:' prefix must not appear in the stub (it was renamed to follow-up)."""
        assert "radar:" not in CANDIDATE_STUB, "Stub must not mention the old 'radar:' prefix"

    def test_stub_section_title_contains_harvest_candidates(self):
        """The stub's section title contains 'Harvest candidates' (test_agents_generic.py target)."""
        assert "## Harvest candidates" in CANDIDATE_STUB

    def test_stub_line_count_is_compact(self):
        """The stub is ~5-6 lines (heading + blank + instruction + blank + role sentence)."""
        lines = CANDIDATE_STUB.splitlines()
        assert len(lines) <= 6, (
            f"Stub should be at most 6 lines, got {len(lines)}: {lines!r}"
        )

    def test_stub_preserves_role_specific_sentence_verbatim(self):
        """The role-specific sentence from researcher.md:86 is preserved verbatim."""
        ROLE_SENTENCE = (
            "For a researcher specifically, the highest-value emissions are **lessons** "
            "(durable invariants you discovered) and **gotchas** (surprising subsystem "
            "behavior worth patching into a subsystem profile). Skip dead-ends"
        )
        assert ROLE_SENTENCE in CANDIDATE_STUB, (
            "Role-specific sentence from researcher.md:86 not preserved verbatim"
        )
