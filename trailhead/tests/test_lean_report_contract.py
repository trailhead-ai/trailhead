"""Lean execute-loop controller — prompt contract guards.

Plan: 2026-06-12-trailhead-lean-execute-loop-controller
- Slice 1: executor report head/tail split (agents/executor.md)
- Slice 2: code-reviewer structured verdict
  (agents/code-reviewer.md + skills/review/code-reviewer.md)
- Slice 3: execute SKILL §4 single-pass + §5 working set (skills/execute/SKILL.md)

TDD contract: grep-style body guards on the craft agent/skill prompt files.

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
_CRAFT_PLUGIN_ROOT = _REPO_ROOT / "tools" / "craft" / "plugins" / "craft"

_EXECUTOR_MD = _CRAFT_PLUGIN_ROOT / "agents" / "executor.md"


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


def _durable_tail_template(text: str) -> str:
    """Return ONLY the durable-tail template — the fenced code block immediately following the
    '### Durable tail' heading.

    The broad `_split_at_tail_marker` tail runs to end-of-file and sweeps in the '## Rules' and
    '## Harvest candidates' sections, where words like "Surprises" and "files" appear
    incidentally. Positive "the template retains heading X" assertions must look only at the
    template, or deleting a heading from the template still passes via a Rules mention (the
    vacuity flagged in PR #4 review).

    The template's own sub-headings are '## ' lines *inside* a fenced code block, so we cannot
    bound on the next '## ' heading — we extract the code fence itself. Returns "" if not found.
    """
    start = re.search(r"^#{3,} .*(durable tail|commit body).*$", text, re.MULTILINE | re.IGNORECASE)
    if start is None:
        return ""
    # The template is the first ``` … ``` fenced block after the heading.
    fence = re.search(r"```[^\n]*\n(.*?)\n```", text[start.end() :], re.DOTALL)
    return fence.group(1) if fence else ""


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

    @pytest.mark.parametrize("heading", _TAIL_SUBSECTION_HEADINGS)
    def test_tail_heading_not_in_head(self, heading: str):
        """NEGATIVE (load-bearing): a tail sub-section heading must NOT leak into the head.

        A malformed split that leaves the heading in the returned head must go RED."""
        head, _tail = _split_at_tail_marker(_executor_text())
        assert heading not in head, (
            f"executor.md has a malformed head/tail split: {heading!r} appears in the "
            "controller-facing head. It must appear only in the durable tail."
        )

    @pytest.mark.parametrize("heading", _TAIL_SUBSECTION_HEADINGS)
    def test_durable_tail_template_retains_heading(self, heading: str):
        """POSITIVE: the durable-tail TEMPLATE must retain the heading as a '## ' heading.

        Scoped to the durable-tail subsection only — NOT the broad tail-to-EOF region, which
        sweeps in '## Rules' / '## Harvest candidates' where words like 'Surprises' and 'files'
        appear incidentally. Asserting the '## <heading>' form against the bounded template
        means deleting the heading from the template goes RED (PR #4 review fix)."""
        template = _durable_tail_template(_executor_text())
        assert template, (
            "executor.md must have a durable-tail subsection ('### Durable tail …' heading "
            "followed by the template, bounded by the next '## ' heading)"
        )
        assert f"## {heading}" in template, (
            f"executor.md durable-tail template must retain the '## {heading}' heading "
            "(for scannability in commit bodies and /pickup). It was not found in the "
            "durable-tail subsection (a Rules/Harvest mention elsewhere does not count)."
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

_AGENT_REVIEWER_MD = _CRAFT_PLUGIN_ROOT / "agents" / "code-reviewer.md"
_SKILL_REVIEWER_MD = _CRAFT_PLUGIN_ROOT / "skills" / "review" / "code-reviewer.md"


def _agent_reviewer_text() -> str:
    return _AGENT_REVIEWER_MD.read_text()


def _skill_reviewer_text() -> str:
    return _SKILL_REVIEWER_MD.read_text()


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
        has_trigger = "auth" in lower or "crypto" in lower or "secrets" in lower
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
        assert "Minor" in text, "agents/code-reviewer.md must retain the 'Minor' severity label."

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


# ---------------------------------------------------------------------------
# Slice 3: execute SKILL §4 single-pass + verdict-only absorb, §5 working set
#
# Plan: 2026-06-12-trailhead-lean-execute-loop-controller, Slice 3
#
# Test contract:
#   POSITIVE §4: large-tier row has single-combined-pass directive + "second pass only
#     when saturated/over-length"; absorb directive containing "verdict + Critical".
#   NEGATIVE §4: "Dispatch `code-reviewer` twice" is ABSENT.
#   POSITIVE §5: working-set directive naming "current slice" + "unknowns checklist"
#     and contiguous "does not re-read the full plan".
#   POSITIVE head/consumer cross-check: fields §4/§5 key on are present in executor head.
#   INVARIANT §5: plan-file-as-source-of-truth + draft→in-progress still present.
# ---------------------------------------------------------------------------

_EXECUTE_SKILL_MD = _CRAFT_PLUGIN_ROOT / "skills" / "execute" / "SKILL.md"


def _execute_skill_text() -> str:
    return _EXECUTE_SKILL_MD.read_text()


def _split_execute_at_section(text: str, section_heading: str) -> str:
    """Return the text of the named section (from the heading until the next same-level heading).

    Handles headings that carry a numeric prefix, e.g. '### 4. Review (scaled to change size)'.
    The pattern matches any heading that *contains* section_heading as a substring.
    """
    pattern = re.compile(
        r"^(#{1,4}[^\n]*" + re.escape(section_heading) + r")(.+?)(?=^#{1,4} |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    return m.group(0) if m else ""


class TestExecuteSkillSection5WorkingSet:
    """§5 Update the plan file: must name the per-cycle working set and no-full-reread."""

    def test_section5_names_current_slice_in_working_set(self):
        """§5 must reference 'current slice' as part of the working-set directive."""
        text = _execute_skill_text()
        section = _split_execute_at_section(text, "Update the plan file")
        assert "current slice" in section.lower(), (
            "skills/execute/SKILL.md §5 must name 'current slice' in the working-set directive — "
            "the controller's per-cycle working set is the current slice section "
            "+ unknowns checklist."
        )

    def test_section5_names_unknowns_checklist_in_working_set(self):
        """§5 must reference 'unknowns checklist' as part of the working-set directive."""
        text = _execute_skill_text()
        section = _split_execute_at_section(text, "Update the plan file")
        assert "unknowns checklist" in section.lower(), (
            "skills/execute/SKILL.md §5 must name 'unknowns checklist' in the "
            "working-set directive."
        )

    def test_section5_states_does_not_reread_full_plan(self):
        """§5 must contain the contiguous phrase 'does not re-read the full plan'.

        This is the load-bearing POSITIVE that distinguishes a real working-set constraint
        from prose that merely mentions 'plan'. The contiguous phrase must be present.
        """
        text = _execute_skill_text()
        section = _split_execute_at_section(text, "Update the plan file")
        assert "does not re-read the full plan" in section.lower(), (
            "skills/execute/SKILL.md §5 must contain the contiguous phrase "
            "'does not re-read the full plan' — asserting the controller pins its "
            "working set to the current slice + unknowns checklist rather than re-reading "
            "the entire plan each cycle."
        )

    def test_section5_preserves_source_of_truth_language(self):
        """INVARIANT: §5 must retain plan-file-as-source-of-truth language.

        The spec Constraint forbids regressing this invariant. A rewrite that strips
        'source of truth' must go RED.
        """
        text = _execute_skill_text()
        section = _split_execute_at_section(text, "Update the plan file")
        assert "source of truth" in section.lower(), (
            "skills/execute/SKILL.md §5 must retain 'source of truth' language — "
            "spec Constraint: do not regress the plan-file-as-source-of-truth invariant."
        )

    def test_section5_preserves_draft_to_in_progress_status_flip(self):
        """INVARIANT: §5 must retain the draft→in-progress status-flip behavior.

        The spec Constraint requires this behavior to be preserved. Stripping 'draft' or
        'in-progress' from §5 must cause this test to go RED.
        """
        text = _execute_skill_text()
        section = _split_execute_at_section(text, "Update the plan file")
        has_draft = "draft" in section.lower()
        has_in_progress = "in-progress" in section.lower()
        assert has_draft and has_in_progress, (
            "skills/execute/SKILL.md §5 must retain the draft→in-progress status-flip behavior "
            "(assert 'draft' and 'in-progress' both present). "
            f"Found: draft={has_draft}, in-progress={has_in_progress}."
        )
