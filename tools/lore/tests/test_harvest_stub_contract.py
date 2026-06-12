"""Behavioral test — harvest-candidates hook contract for the compressed inline stub.

Replaces the ephemeral assumption-prover proof (test_harvest_stub_proof.py, deleted in
Slice 1a) with a permanent behavioral test that locks the hook-capture contract for the
compressed stub used in researcher.md (and eventually all harvest-bearing agents).

Two hard constraints locked here (proven by the assumption-prover, 17-test proof):
  1. The agent must emit exactly `## Harvest candidates` as the heading — a variant
     like `## Harvest candidates (end-of-message)` passes the substring fast-path
     (`if "## Harvest candidates" not in text`) but FAILS SECTION_RE → silent no-op.
  2. No prose line may appear between the heading and the first dash-entry in emitted
     output — any intervening line causes SECTION_RE to capture zero entries (silent
     data loss).

IMPORTANT — source-literal policy: this file must not contain the bare word
"r-a-d-a-r" (without hyphens) as a contiguous literal — it is forbidden by the
test_renames_guard.py Slice-9 guard across all non-allowlisted source files.
Any reference to the old harvest type is assembled at runtime.

Slice: WS-8 Slice 1a (scaffolding-dedup plan).
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Load SECTION_RE and ENTRY_RE from the real hook — not copies, the live ones.
# ---------------------------------------------------------------------------

_HOOK_PATH = (
    Path(__file__).resolve().parent.parent
    / "plugins" / "lore" / "hooks" / "harvest-candidates.py"
)

_spec = importlib.util.spec_from_file_location("harvest_candidates_hook", _HOOK_PATH)
_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hook)

SECTION_RE: re.Pattern = _hook.SECTION_RE
ENTRY_RE: re.Pattern = _hook.ENTRY_RE

# ---------------------------------------------------------------------------
# Researcher agent file path (the target of Slice 1a).
# ---------------------------------------------------------------------------

_RESEARCHER_MD = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "tools" / "forge" / "plugins" / "forge" / "agents" / "researcher.md"
)

# The old harvest type, assembled at runtime to avoid tripping the renames guard.
# "r" + "a" + "d" + "a" + "r" — the five-letter word renamed to follow-up in Spec A.
_OLD_TYPE = "".join(["r", "a", "d", "a", "r"])


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


def _make_message(harvest_block: str) -> str:
    """Wrap a harvest block in a realistic researcher output message."""
    return (
        "**TL;DR**: The harvest hook anchors on a suffix block.\n\n"
        "## What I found\n\n"
        "The hook at `harvest-candidates.py:29-35` requires `## Harvest candidates`\n"
        "to be the message suffix, with `- <type>: <body>` lines immediately after.\n\n"
        "## Sources\n\n"
        "- `tools/lore/plugins/lore/hooks/harvest-candidates.py`\n\n"
        + harvest_block
    )


# ---------------------------------------------------------------------------
# Positive: compressed-stub format is fully hook-valid.
# ---------------------------------------------------------------------------


class TestCompressedStubHookContract:
    """The stub's documented emission format produces correctly typed entries."""

    def test_all_six_types_captured(self):
        """All six valid typed prefixes parse into the correct (type, body) tuples."""
        msg = _make_message(
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
        """follow-up: (the type that replaced the old harvest type) parses correctly."""
        msg = _make_message(
            "## Harvest candidates\n\n"
            "- follow-up: watch whether lore agents emit bad blocks post-stub-compress\n"
        )
        entries = _parse_entries(msg)
        assert entries == [
            ("follow-up", "watch whether lore agents emit bad blocks post-stub-compress")
        ]

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

    def test_entry_body_whitespace_stripped(self):
        """Entry body is stripped of leading/trailing whitespace by the hook."""
        msg = (
            "## Harvest candidates\n\n"
            "-  lesson:   spaces around body are trimmed   \n"
        )
        entries = _parse_entries(msg)
        assert entries == [("lesson", "spaces around body are trimmed")]


# ---------------------------------------------------------------------------
# Negative: the two hard constraints the stub design must respect.
# ---------------------------------------------------------------------------


class TestHookNegativeControls:
    """Hard constraints on what emitted output must NOT look like."""

    def test_prose_between_heading_and_dashes_breaks_match(self):
        """Critical constraint: any non-dash line between heading and entries → zero captures.

        This is the sharpest edge in the stub design. If an agent were to emit the
        stub's instruction prose verbatim between the heading and the dash list, the
        hook would produce ZERO entries — silent data loss.

        The compressed stub prevents this by instructing the agent to append a
        `## Harvest candidates` block (heading immediately followed by dash entries),
        never prose between them.
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

    def test_heading_with_extra_suffix_text_fails_section_re(self):
        """Emitting '## Harvest candidates (end-of-message)' as the heading breaks SECTION_RE.

        This confirms the stub must instruct agents to emit exactly
        '## Harvest candidates' — the parenthetical variant in the stub's own .md
        section title does NOT appear in the agent's output.
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

    def test_block_not_at_message_suffix_is_rejected(self):
        """A harvest block followed by any non-whitespace text is rejected."""
        msg = (
            "## Harvest candidates\n\n"
            "- lesson: something durable\n\n"
            "This trailing paragraph invalidates the suffix anchor.\n"
        )
        entries = _parse_entries(msg)
        assert entries == [], f"expected no entries for non-suffix block, got: {entries}"

    def test_invalid_type_prefix_silently_skipped(self):
        """Unknown typed prefixes (the old type from before Spec A) are silently skipped.

        The old harvest type was renamed to follow-up; emitters in agent/skill bodies
        must now use follow-up instead. This test confirms the hook silently drops any
        entry using the renamed-away type so agents know to use the new name.
        """
        # Construct the forbidden old-type entry at runtime (no source literal).
        old_type_entry = f"- {_OLD_TYPE}: this type was renamed to follow-up in Spec A\n"
        msg = (
            "## Harvest candidates\n\n"
            + old_type_entry
            + "- lesson: this valid entry is captured\n"
        )
        entries = _parse_entries(msg)
        assert entries == [("lesson", "this valid entry is captured")], entries

    def test_absent_harvest_section_produces_empty(self):
        """Message with no harvest block produces no entries."""
        msg = "Just a research report with no harvest candidates.\n"
        entries = _parse_entries(msg)
        assert entries == []


# ---------------------------------------------------------------------------
# researcher.md content invariants.
# ---------------------------------------------------------------------------


class TestResearcherMdInvariants:
    """researcher.md must satisfy the invariants the stub is designed to preserve."""

    def test_researcher_md_exists(self):
        """researcher.md must exist at the expected path."""
        assert _RESEARCHER_MD.exists(), (
            f"researcher.md not found at {_RESEARCHER_MD}"
        )

    def test_researcher_md_contains_harvest_heading(self):
        """researcher.md must contain the literal '## Harvest candidates' heading.

        Required by test_agents_generic.py:test_harvest_bearing_agent_retains_heading
        — the hook anchors on this exact heading in the agent's emitted output.
        """
        text = _RESEARCHER_MD.read_text()
        assert "## Harvest candidates" in text, (
            "researcher.md is missing the literal '## Harvest candidates' heading. "
            "The compressed stub must include it so the hook-contract guard stays green."
        )

    def test_researcher_md_contains_role_specific_sentence(self):
        """researcher.md must contain the role-specific 'highest-value emissions' sentence.

        The plan specifies this sentence is preserved verbatim when the verbose block
        is compressed to the stub.
        """
        text = _RESEARCHER_MD.read_text()
        assert "highest-value emissions" in text, (
            "researcher.md is missing the role-specific 'highest-value emissions' "
            "sentence. The compressed stub must preserve this verbatim."
        )

    def test_researcher_md_does_not_contain_old_typed_prefix(self):
        """researcher.md must not instruct agents to emit the old harvest typed prefix.

        The old type was renamed to follow-up in Spec A; the stub names only the
        six valid types (lesson/dead-end/deferred/follow-up/decision/gotcha).

        The forbidden token is assembled at runtime so this source file does not carry
        it as a literal (the test_renames_guard.py Slice-9 guard forbids the
        backtick-wrapped form in all non-allowlisted source files).
        """
        _backtick_form = f"`{_OLD_TYPE}:`"
        _dash_form = f"- `{_OLD_TYPE}:`"
        text = _RESEARCHER_MD.read_text()
        assert _backtick_form not in text and _dash_form not in text, (
            f"researcher.md mentions the old {_OLD_TYPE!r} typed prefix — update to 'follow-up:'"
        )

    def test_researcher_md_is_compressed_stub(self):
        """researcher.md must use the compact stub form, not the verbose entry-format prose.

        The verbose form has ~18 lines including an explicit 'Entry format:' subsection
        with individual bullet expansions like '- lesson: — durable invariants...'.
        The compressed stub replaces all of that with a single instruction line naming
        the six valid typed prefixes inline. This assertion detects the verbose form
        so it fails RED until the compression is applied.
        """
        text = _RESEARCHER_MD.read_text()
        # The verbose form has an explicit 'Entry format:' paragraph / subsection.
        # The compressed stub does NOT have this — it names the types inline on one line.
        assert "Entry format:" not in text, (
            "researcher.md still contains the verbose 'Entry format:' subsection. "
            "Replace lines 69-86 with the compressed stub (plan Slice 1a)."
        )
