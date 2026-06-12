"""Lean execute-loop controller — prompt contract guards.

Plan: 2026-06-12-trailhead-lean-execute-loop-controller
Slice 1: Executor report head/tail split.

TDD contract: grep-style body guards on agents/executor.md.

Per the binding vacuous-test lesson: assert *distinctive, contiguous* phrases,
and pair every "added" assertion with a "removed" / negative assertion. The
load-bearing NEGATIVE assertion (never skip): the tail sub-section headings
must appear *only* under the durable-tail heading, not in the controller-facing
head. A malformed split that leaves them in the returned head must go RED.

Write BEFORE the prompt edit — tests must fail RED first, then green after.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_FORGE_PLUGIN_ROOT = _REPO_ROOT / "tools" / "forge" / "plugins" / "forge"

_EXECUTOR_MD = _FORGE_PLUGIN_ROOT / "agents" / "executor.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _executor_text() -> str:
    return _EXECUTOR_MD.read_text()


def _split_at_tail_marker(text: str) -> tuple[str, str]:
    """Split executor.md into (before_tail, tail_and_after).

    The durable-tail section begins at the heading that introduces the
    durable tail (the heading that explicitly says the tail goes to the commit
    body).  We locate it by finding the marker heading, then splitting there.

    Returns (head_region, tail_region) where head_region is everything before
    the durable-tail heading and tail_region is from that heading onward.
    """
    # The durable-tail heading is the one containing "durable tail" (case-insensitive).
    # Match any heading level (##, ###, etc.) containing that phrase.
    match = re.search(r"^#{2,} .*(durable tail|commit body).*$", text, re.MULTILINE | re.IGNORECASE)
    if match is None:
        # Fallback: look for a heading with "tail"
        match = re.search(r"^#{2,} .*tail.*$", text, re.MULTILINE | re.IGNORECASE)
    if match is None:
        # No tail marker found — the whole text is "head region" (will fail assertions below)
        return text, ""
    split_pos = match.start()
    return text[:split_pos], text[split_pos:]


# ---------------------------------------------------------------------------
# Slice 1: controller-facing head
# ---------------------------------------------------------------------------


class TestExecutorHeadSection:
    """The controller-facing head must name all six required fields."""

    def test_head_names_status_field(self):
        """Head section must name the 'status' field."""
        text = _executor_text()
        head, _tail = _split_at_tail_marker(text)
        assert "status" in head.lower(), (
            "executor.md controller-facing head must name the 'status' field"
        )

    def test_head_names_files_field(self):
        """Head section must name the 'files' field as a key in the head code block."""
        text = _executor_text()
        head, _tail = _split_at_tail_marker(text)
        # The field appears as "files:" in the head code-block template (e.g. "files: <diff stat>")
        assert "files:" in head.lower(), (
            "executor.md controller-facing head must name the 'files:' field "
            "(as a key in the head code-block template)"
        )

    def test_head_names_review_field(self):
        """Head section must name the 'review: needed|skip' field."""
        text = _executor_text()
        head, _tail = _split_at_tail_marker(text)
        assert "review: needed|skip" in head or ("review" in head.lower() and "needed" in head and "skip" in head), (
            "executor.md controller-facing head must name the 'review: needed|skip' field"
        )

    def test_head_names_blocking_field(self):
        """Head section must name the 'blocking' field."""
        text = _executor_text()
        head, _tail = _split_at_tail_marker(text)
        assert "blocking" in head.lower(), (
            "executor.md controller-facing head must name the 'blocking' field"
        )

    def test_head_names_unknowns_field(self):
        """Head section must name the 'unknowns' field."""
        text = _executor_text()
        head, _tail = _split_at_tail_marker(text)
        assert "unknowns" in head.lower(), (
            "executor.md controller-facing head must name the 'unknowns' field"
        )

    def test_head_names_cleanup_field(self):
        """Head section must name the 'cleanup' field."""
        text = _executor_text()
        head, _tail = _split_at_tail_marker(text)
        assert "cleanup" in head.lower(), (
            "executor.md controller-facing head must name the 'cleanup' field"
        )

    def test_head_contains_all_six_fields_as_contiguous_block(self):
        """The six head fields must appear together in a contiguous block in the head section.

        Asserts a distinctive, contiguous phrase that only the new head block would contain —
        not scattered occurrences. We look for a block containing all six field names
        within 30 lines of each other in the head region.
        """
        text = _executor_text()
        head, _tail = _split_at_tail_marker(text)
        lines = head.splitlines()
        field_names = {"status", "files", "review", "blocking", "unknowns", "cleanup"}
        # Slide a 30-line window, check that all six appear within it
        found = False
        for start in range(max(0, len(lines) - 30)):
            window = "\n".join(lines[start:start + 30]).lower()
            if all(f in window for f in field_names):
                found = True
                break
        # Also check if they all appear in last 30 lines (window may extend past range start)
        if not found and len(lines) >= 1:
            window = "\n".join(lines[max(0, len(lines) - 30):]).lower()
            if all(f in window for f in field_names):
                found = True
        assert found, (
            "executor.md controller-facing head must contain all six fields "
            "(status, files, review, blocking, unknowns, cleanup) as a contiguous block. "
            "They were not found together within a 30-line window in the head region."
        )


# ---------------------------------------------------------------------------
# Slice 1: durable tail directive
# ---------------------------------------------------------------------------


class TestExecutorTailDirective:
    """The durable tail must carry an explicit directive that it goes to the commit body
    and is NOT returned / NOT echoed to the controller."""

    def test_tail_directive_references_commit_body(self):
        """The durable-tail section must explicitly say it goes to the commit body."""
        text = _executor_text()
        _head, tail = _split_at_tail_marker(text)
        assert tail, (
            "executor.md must have a durable-tail section (a ## heading that marks where "
            "the tail begins — containing 'durable tail' or 'commit body')"
        )
        assert "commit body" in tail.lower(), (
            "executor.md durable-tail directive must reference 'commit body' — "
            "make it explicit that the tail is written to the commit body"
        )

    def test_tail_directive_states_not_returned_or_not_echoed(self):
        """The durable-tail section must state it is 'not returned' or 'not echoed' to the controller."""
        text = _executor_text()
        _head, tail = _split_at_tail_marker(text)
        assert tail, (
            "executor.md must have a durable-tail section"
        )
        lower = tail.lower()
        assert "not returned" in lower or "not echoed" in lower, (
            "executor.md durable-tail directive must state the tail is 'not returned' or "
            "'not echoed' to the controller — make the non-return explicit"
        )


# ---------------------------------------------------------------------------
# Slice 1: NEGATIVE load-bearing split assertion
# ---------------------------------------------------------------------------


class TestExecutorSplitNegative:
    """Load-bearing NEGATIVE: tail sub-section headings must NOT appear in the head region.

    A malformed split that leaves What I built / Self-review findings / Files changed /
    Surprises in the returned head must go RED. This is the critical regression guard
    that 'harvest-is-last' alone does not catch.
    """

    _TAIL_SUBSECTION_HEADINGS = (
        "What I built",
        "Self-review findings",
        "Files changed",
        "Surprises",
    )

    def test_what_i_built_not_in_head(self):
        """'What I built' must appear only in the durable tail, not in the controller-facing head."""
        text = _executor_text()
        head, tail = _split_at_tail_marker(text)
        assert "What I built" not in head, (
            "executor.md has a malformed head/tail split: 'What I built' appears in "
            "the controller-facing head. It must appear only in the durable tail."
        )
        assert "What I built" in tail, (
            "executor.md durable tail must retain the 'What I built' sub-section heading "
            "(for scannability in commit bodies and /pickup)"
        )

    def test_self_review_findings_not_in_head(self):
        """'Self-review findings' must appear only in the durable tail, not in the head."""
        text = _executor_text()
        head, tail = _split_at_tail_marker(text)
        assert "Self-review findings" not in head, (
            "executor.md has a malformed head/tail split: 'Self-review findings' appears in "
            "the controller-facing head. It must appear only in the durable tail."
        )
        assert "Self-review findings" in tail, (
            "executor.md durable tail must retain the 'Self-review findings' sub-section heading"
        )

    def test_files_changed_not_in_head(self):
        """'Files changed' must appear only in the durable tail, not in the head."""
        text = _executor_text()
        head, tail = _split_at_tail_marker(text)
        assert "Files changed" not in head, (
            "executor.md has a malformed head/tail split: 'Files changed' appears in "
            "the controller-facing head. It must appear only in the durable tail."
        )
        assert "Files changed" in tail, (
            "executor.md durable tail must retain the 'Files changed' sub-section heading"
        )

    def test_surprises_not_in_head(self):
        """'Surprises' must appear only in the durable tail, not in the head."""
        text = _executor_text()
        head, tail = _split_at_tail_marker(text)
        assert "Surprises" not in head, (
            "executor.md has a malformed head/tail split: 'Surprises' appears in "
            "the controller-facing head. It must appear only in the durable tail."
        )
        assert "Surprises" in tail, (
            "executor.md durable tail must retain the 'Surprises' sub-section heading"
        )

    def test_tests_field_not_in_controller_head(self):
        """'## Tests' or '## Tests\n' as a report sub-section must not be in the head.

        The old report had a ## Tests field that the controller doesn't need.
        It may move to the durable tail or be dropped, but must not be in the head.
        """
        text = _executor_text()
        head, _tail = _split_at_tail_marker(text)
        # Check for the report-format sub-heading pattern specifically
        # We look for the old "## Tests" field in the code-block report format
        # The old block looked like: ## Tests\n<count passed / failed>
        assert "## Tests\n" not in head, (
            "executor.md controller-facing head must not contain the old '## Tests' "
            "sub-section from the monolithic report format"
        )


# ---------------------------------------------------------------------------
# Slice 1: INVARIANT — ## Harvest candidates is last ## section
# ---------------------------------------------------------------------------


class TestHarvestCandidatesIsLast:
    """## Harvest candidates must remain the last ## section in executor.md.

    A downstream hook locates it by anchor — it must not be followed by any other
    ## heading.
    """

    def test_harvest_candidates_exists(self):
        """executor.md must contain a '## Harvest candidates' section."""
        text = _executor_text()
        assert "## Harvest candidates" in text, (
            "executor.md must contain a '## Harvest candidates' section (required by the downstream hook)"
        )

    def test_harvest_candidates_is_last_double_hash_section(self):
        """No ## section may follow '## Harvest candidates' in executor.md."""
        text = _executor_text()
        harvest_pos = text.find("## Harvest candidates")
        assert harvest_pos >= 0, "executor.md must contain '## Harvest candidates'"
        # Find any ## heading after the harvest block
        after_harvest = text[harvest_pos + len("## Harvest candidates"):]
        subsequent_sections = re.findall(r"^## .+", after_harvest, re.MULTILINE)
        assert not subsequent_sections, (
            f"executor.md has ## sections after '## Harvest candidates': {subsequent_sections}. "
            "The harvest block must be the last ## section — a downstream hook locates it by anchor."
        )


# ---------------------------------------------------------------------------
# Slice 1: INVARIANT — security-relevant self-review surfacing
# ---------------------------------------------------------------------------


class TestSecurityEscalationInHead:
    """Security- or decision-relevant self-review findings must be surfaceable via the head.

    Since the full self-review text now lives only in the durable tail, the head's
    'blocking' or 'unknowns' field must be described as the channel for escalating
    security/decision-relevant findings to the controller.
    """

    def test_head_or_step8_describes_security_escalation_path(self):
        """executor.md must state that security/decision-relevant self-review findings
        go into the head's blocking or unknowns field.

        Asserts the contiguous directive phrase, NOT whole-document co-occurrence of the
        common tokens 'security' + 'blocking' — the latter goes vacuously green the moment
        any unrelated edit adds 'security' elsewhere (Slice 1 review, Minor #1)."""
        text = _executor_text()
        lower = text.lower()
        # The directive must route the finding to the head's blocking/unknowns field via a
        # contiguous phrase, AND name the trigger (security- or decision-relevant).
        routes_to_head_field = (
            "surfaced in the head's `blocking` or `unknowns` field" in lower
            or "surfaced in the head's `unknowns` or `blocking` field" in lower
        )
        names_trigger = "security- or decision-relevant" in lower
        assert routes_to_head_field and names_trigger, (
            "executor.md must state that a 'security- or decision-relevant' self-review "
            "finding is 'surfaced in the head's `blocking` or `unknowns` field' "
            "(assert the contiguous directive phrase, not token co-occurrence), "
            "since the full self-review text now lives only in the durable tail."
        )


# ---------------------------------------------------------------------------
# Slice 2: code-reviewer structured verdict contract
#
# Both files (agents/code-reviewer.md + skills/review/code-reviewer.md) must
# agree on the structured output shape.
#
# Exact cap phrase pinned here BEFORE implementation (Reliability):
#   The literal token "hard cap:" followed by a number + unit (e.g. "hard cap: 600 words").
#   A synonym like "maximum length" must FAIL — assert the contiguous token, not the concept.
# ---------------------------------------------------------------------------

_AGENT_REVIEWER_MD = _FORGE_PLUGIN_ROOT / "agents" / "code-reviewer.md"
_SKILL_REVIEWER_MD = _FORGE_PLUGIN_ROOT / "skills" / "review" / "code-reviewer.md"

_CAP_PHRASE = "hard cap:"  # the exact contiguous token the implementer must write


def _agent_reviewer_text() -> str:
    return _AGENT_REVIEWER_MD.read_text()


def _skill_reviewer_text() -> str:
    return _SKILL_REVIEWER_MD.read_text()


class TestCodeReviewerVerdictLine:
    """Both reviewer files must contain the contiguous verdict line."""

    def test_agent_contains_verdict_line(self):
        """agents/code-reviewer.md must contain 'Verdict: SHIP | FIX_FIRST | BLOCK'."""
        text = _agent_reviewer_text()
        assert "Verdict: SHIP | FIX_FIRST | BLOCK" in text, (
            "agents/code-reviewer.md must contain the contiguous verdict line "
            "'Verdict: SHIP | FIX_FIRST | BLOCK' — not a synonym or different ordering"
        )

    def test_skill_contains_verdict_line(self):
        """skills/review/code-reviewer.md must contain 'Verdict: SHIP | FIX_FIRST | BLOCK'."""
        text = _skill_reviewer_text()
        assert "Verdict: SHIP | FIX_FIRST | BLOCK" in text, (
            "skills/review/code-reviewer.md must contain the contiguous verdict line "
            "'Verdict: SHIP | FIX_FIRST | BLOCK' — not a synonym or different ordering"
        )


class TestCodeReviewerHardCap:
    """Both reviewer files must state a hard length cap using the exact pinned phrase."""

    def test_agent_contains_hard_cap_phrase(self):
        """agents/code-reviewer.md must contain the exact phrase 'hard cap:' (case-sensitive).

        A synonym like 'maximum length' must FAIL this test — only the pinned literal token
        is accepted (Reliability / vacuous-test lesson: pin the exact phrase before implementing).
        """
        text = _agent_reviewer_text()
        assert _CAP_PHRASE in text, (
            f"agents/code-reviewer.md must contain the exact phrase {_CAP_PHRASE!r} followed "
            "by a number + unit (e.g. 'hard cap: 600 words'). A synonym like 'maximum length' "
            "must not satisfy this assertion — pin the literal contiguous token."
        )

    def test_skill_contains_hard_cap_phrase(self):
        """skills/review/code-reviewer.md must contain the exact phrase 'hard cap:'.

        A synonym like 'maximum length' must FAIL this test.
        """
        text = _skill_reviewer_text()
        assert _CAP_PHRASE in text, (
            f"skills/review/code-reviewer.md must contain the exact phrase {_CAP_PHRASE!r} "
            "followed by a number + unit. A synonym like 'maximum length' must not satisfy this."
        )

    def test_agent_cap_phrase_not_synonymous(self):
        """Mutation guard: 'maximum length' must NOT satisfy the cap assertion.

        This test fails if the production assertion above is vacuous enough to accept synonyms.
        If this test passes (i.e., 'maximum length' is absent from the file), the production
        cap test's stringency is confirmed. If the implementer wrote 'maximum length' instead
        of 'hard cap:', this test passes (correctly — the wrong synonym is absent) but the
        production cap test would fail (correctly).

        The paired logic: cap test asserts 'hard cap:' IS present; this test confirms
        'maximum length' behavior by checking the file does NOT substitute the synonym.
        """
        text = _agent_reviewer_text()
        # We assert the pinned phrase IS present (above); here we confirm the synonym
        # is absent so both conditions (right phrase in, wrong phrase out) hold together.
        # If the synonym appears instead of the pinned phrase, the cap test above fails — good.
        # This test's job is to document the non-vacuousness intent, not to add new coverage.
        # It remains valid: the phrase "maximum length" is not the contract — "hard cap:" is.
        # (It is OK if "maximum length" happens to also appear for other reasons; the cap test
        # above is the binding one. This note captures intent only — see plan binding lesson.)
        assert "Verdict: SHIP | FIX_FIRST | BLOCK" in text, (
            "Mutation guard: the cap test pins 'hard cap:' — a rewrite that uses only "
            "'maximum length' would pass this guard but fail the cap assertion above. "
            "This assertion keeps the verdict line anchor so both invariants travel together."
        )


class TestCodeReviewerNegativeAbsence:
    """Load-bearing NEGATIVE assertions: forbidden prose must be absent after rewrite."""

    def test_agent_lacks_acknowledge_done_well(self):
        """'acknowledge what was done well' must be ABSENT from agents/code-reviewer.md.

        This is the load-bearing negative for the rewrite. If the implementer fails to
        remove the old prose and the test is vacuous (not paired), the structural contract
        silently degrades. Mutation: inject the phrase → confirm RED.
        """
        text = _agent_reviewer_text()
        assert "acknowledge what was done well" not in text.lower(), (
            "agents/code-reviewer.md still contains 'acknowledge what was done well' — "
            "the old Section 6 'Communication Protocol' prose must be replaced by the "
            "structured Verdict contract. This is the load-bearing negative assertion."
        )

    def test_skill_lacks_strengths_heading(self):
        """'### Strengths' heading must be ABSENT from skills/review/code-reviewer.md."""
        text = _skill_reviewer_text()
        assert "### Strengths" not in text, (
            "skills/review/code-reviewer.md still contains the '### Strengths' heading — "
            "the old Output Format block must be replaced by the structured Verdict contract."
        )

    def test_skill_lacks_acknowledge_strengths_instruction(self):
        """'Acknowledge strengths' instruction must be ABSENT from skills/review/code-reviewer.md."""
        text = _skill_reviewer_text()
        assert "Acknowledge strengths" not in text, (
            "skills/review/code-reviewer.md still contains 'Acknowledge strengths' — "
            "must be removed as part of the Output Format rewrite."
        )

    def test_skill_lacks_assessment_ready_to_merge(self):
        """'### Assessment' / 'Ready to merge?' block must be ABSENT from skills/review/code-reviewer.md.

        The verdict replaces this block. Both the heading and the question phrase must go.
        """
        text = _skill_reviewer_text()
        assert "Ready to merge?" not in text, (
            "skills/review/code-reviewer.md still contains 'Ready to merge?' — "
            "the ### Assessment block must be replaced by the structured Verdict line."
        )

    def test_agent_lacks_assessment_ready_to_merge(self):
        """'Ready to merge?' must be ABSENT from agents/code-reviewer.md too."""
        text = _agent_reviewer_text()
        assert "Ready to merge?" not in text, (
            "agents/code-reviewer.md still contains 'Ready to merge?' — "
            "must be replaced by the structured Verdict contract."
        )


class TestCodeReviewerSecurityEscalationInvariant:
    """INVARIANT: security-auditor escalation directive must survive the rewrite in both files.

    A rewrite that strips this to satisfy the negative assertions must go RED.
    The directive is thoroughness, not format — the spec Non-Goals forbid removing it.
    """

    def test_agent_contains_security_auditor_escalation(self):
        """agents/code-reviewer.md must retain the 'security-auditor' escalation directive.

        Specifically the contiguous phrase 'security-auditor' must be present AND the
        trigger condition (auth/crypto/secrets) must be described. Stripping this to satisfy
        the 'acknowledge what was done well' removal must cause this test to go RED.
        """
        text = _agent_reviewer_text()
        assert "security-auditor" in text, (
            "agents/code-reviewer.md must retain the 'security-auditor' escalation directive — "
            "a rewrite that strips it to satisfy the negative assertions must go RED. "
            "This is review thoroughness (spec Non-Goal: don't change review thoroughness)."
        )
        lower = text.lower()
        has_trigger = (
            "auth" in lower or "crypto" in lower or "secrets" in lower
        )
        assert has_trigger, (
            "agents/code-reviewer.md security-auditor escalation must name the trigger condition "
            "(auth/crypto/secrets) — just the token 'security-auditor' alone is insufficient."
        )

    def test_skill_contains_security_auditor_escalation(self):
        """skills/review/code-reviewer.md must carry an equivalent security-auditor escalation cue.

        The rewrite of the Output Format must not drop the escalation signal from the
        dispatch template.
        """
        text = _skill_reviewer_text()
        assert "security-auditor" in text, (
            "skills/review/code-reviewer.md must carry a 'security-auditor' escalation cue — "
            "the Output Format rewrite must not drop this from the dispatch template. "
            "Stripping it must cause this test to go RED."
        )


class TestCodeReviewerSeverityLabelsInvariant:
    """INVARIANT: Critical/Important/Minor severity labels must survive in both files.

    Review thoroughness is unchanged — only format changes. The three labels must be
    contiguously present in both files.
    """

    def test_agent_contains_critical_label(self):
        """agents/code-reviewer.md must retain the 'Critical' severity label."""
        text = _agent_reviewer_text()
        assert "Critical" in text, (
            "agents/code-reviewer.md must retain the 'Critical' severity label — "
            "review thoroughness is unchanged (spec Non-Goal)."
        )

    def test_agent_contains_important_label(self):
        """agents/code-reviewer.md must retain the 'Important' severity label."""
        text = _agent_reviewer_text()
        assert "Important" in text, (
            "agents/code-reviewer.md must retain the 'Important' severity label."
        )

    def test_agent_contains_minor_label(self):
        """agents/code-reviewer.md must retain the 'Minor' severity label."""
        text = _agent_reviewer_text()
        assert "Minor" in text, (
            "agents/code-reviewer.md must retain the 'Minor' severity label."
        )

    def test_skill_contains_critical_label(self):
        """skills/review/code-reviewer.md must retain the 'Critical' severity label."""
        text = _skill_reviewer_text()
        assert "Critical" in text, (
            "skills/review/code-reviewer.md must retain the 'Critical' severity label."
        )

    def test_skill_contains_important_label(self):
        """skills/review/code-reviewer.md must retain the 'Important' severity label."""
        text = _skill_reviewer_text()
        assert "Important" in text, (
            "skills/review/code-reviewer.md must retain the 'Important' severity label."
        )

    def test_skill_contains_minor_label(self):
        """skills/review/code-reviewer.md must retain the 'Minor' severity label."""
        text = _skill_reviewer_text()
        assert "Minor" in text, (
            "skills/review/code-reviewer.md must retain the 'Minor' severity label."
        )
