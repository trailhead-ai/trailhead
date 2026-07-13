"""Contract pins for the review-altitude split (slices 1-2).

`drift-gate` replaces `code-reviewer` as execute's per-slice conformance gate;
`code-reviewer` is rechartered (slice 2) as the whole-change/PR adversarial
reviewer — fresh-context review of the full `base..HEAD` diff against the
spec and plan, correctness/requirements only (style out of scope). Its name,
Opus/high pinning, explicit tools list, and SHIP/FIX_FIRST/BLOCK verdict
vocabulary are portage-compat pins that must never weaken (portage's
green-driver dispatches this exact contract). These tests pin the shape of
the new agent, the boundary of what changed in execute's skill text, and the
recharter of code-reviewer.md + review/SKILL.md — string-pin/structural
checks, not runtime behavior, consistent with this suite's existing style
(see test_craft_skills_generic.py, test_agents_generic.py).
"""

from pathlib import Path

AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "agents"
SKILLS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "skills"

DRIFT_GATE_MD = AGENTS_DIR / "drift-gate.md"
CODE_REVIEWER_MD = AGENTS_DIR / "code-reviewer.md"
EXECUTE_SKILL_MD = SKILLS_DIR / "execute" / "SKILL.md"
REVIEW_SKILL_MD = SKILLS_DIR / "review" / "SKILL.md"


def _frontmatter(text: str) -> str:
    assert text.startswith("---\n")
    end = text.find("\n---", 3)
    assert end > 0
    return text[3:end]


def _tools_line(frontmatter: str) -> str | None:
    for ln in frontmatter.splitlines():
        if ln.strip().startswith("tools:"):
            return ln.split(":", 1)[1].strip()
    return None


def _field(frontmatter: str, field: str) -> str | None:
    for ln in frontmatter.splitlines():
        if ln.strip().startswith(f"{field}:"):
            return ln.split(":", 1)[1].strip()
    return None


def test_drift_gate_agent_file_exists():
    assert DRIFT_GATE_MD.exists(), (
        f"Expected {DRIFT_GATE_MD} — the new per-slice conformance gate agent."
    )


def test_drift_gate_model_is_sonnet():
    text = DRIFT_GATE_MD.read_text()
    frontmatter = _frontmatter(text)
    assert _field(frontmatter, "model") == "sonnet", (
        "drift-gate.md frontmatter must pin `model: sonnet` — the whole point "
        "of the split is dropping the per-slice gate's cost off Opus."
    )


def test_drift_gate_tools_list_omits_skill():
    text = DRIFT_GATE_MD.read_text()
    frontmatter = _frontmatter(text)
    tools = _tools_line(frontmatter)
    assert tools is not None, "drift-gate.md frontmatter must carry an explicit `tools:` line"
    declared = [t.strip() for t in tools.split(",") if t.strip()]
    assert declared == ["Read", "Grep", "Glob", "Bash"], (
        f"drift-gate.md `tools:` must be exactly Read, Grep, Glob, Bash "
        f"(no Skill — this agent cannot invoke skills), got {tools!r}"
    )


def test_drift_gate_charter_forbids_quality_findings():
    text = DRIFT_GATE_MD.read_text()
    # The prohibition must be an explicit statement, not an omission.
    assert "explicitly out of scope" in text, (
        "drift-gate.md must explicitly state that style/design/quality findings "
        "are out of scope — not merely omit them from the checklist."
    )


def test_drift_gate_charter_has_security_surface_clause():
    text = DRIFT_GATE_MD.read_text()
    for category in ["auth", "input validation", "crypto", "secrets", "session handling"]:
        assert category in text, (
            f"drift-gate.md security-surface clause must name {category!r} as a "
            "security-sensitive category that triggers a flag."
        )
    assert "Security-surface:" in text, (
        "drift-gate.md must define the `Security-surface:` output line — these "
        "accumulate for the whole-change security trigger built in a later slice."
    )


def test_drift_gate_verdict_vocabulary_pinned():
    text = DRIFT_GATE_MD.read_text()
    for verdict in ["PASS", "DRIFT", "BLOCKED"]:
        assert verdict in text, f"drift-gate.md must use the {verdict!r} verdict vocabulary."


def test_drift_gate_has_word_cap():
    text = DRIFT_GATE_MD.read_text()
    assert "600" in text, "drift-gate.md must pin the 600-word hard cap on its output."


def test_execute_skill_verdict_vocabulary_pinned():
    text = EXECUTE_SKILL_MD.read_text()
    for verdict in ["PASS", "DRIFT", "BLOCKED"]:
        assert verdict in text, (
            f"execute/SKILL.md must reference drift-gate's {verdict!r} verdict "
            "in the absorption line."
        )


def test_execute_skill_has_no_code_reviewer_per_slice_reference():
    text = EXECUTE_SKILL_MD.read_text()
    assert "code-reviewer" not in text, (
        "execute/SKILL.md must not reference `code-reviewer` anywhere — every "
        "per-slice-review reference (role table, step 4, absorption line, "
        "model-selection table, Red Flags) must retarget to `drift-gate` in "
        "this slice. code-reviewer itself is untouched elsewhere in the repo."
    )


def test_execute_skill_has_no_combined_charter_phrase():
    text = EXECUTE_SKILL_MD.read_text()
    for stale_phrase in ["spec compliance + code quality", "spec + quality"]:
        assert stale_phrase not in text.lower(), (
            f"execute/SKILL.md still contains the stale combined-charter phrase "
            f"{stale_phrase!r} — replace with drift-gate's conformance-only framing."
        )


def test_execute_skill_role_table_dispatches_drift_gate():
    text = EXECUTE_SKILL_MD.read_text()
    assert "`drift-gate`" in text, (
        "execute/SKILL.md must name `drift-gate` (backtick-quoted, matching the "
        "existing table style) somewhere in the skill text."
    )
    assert "| Review work |" in text, "the top-of-skill role table's 'Review work' row must remain"


def test_execute_skill_step4_table_dispatches_drift_gate():
    text = EXECUTE_SKILL_MD.read_text()
    assert "Dispatch `drift-gate`" in text, (
        "execute/SKILL.md step 4's Medium/Large review-approach table must "
        "dispatch `drift-gate` instead of `code-reviewer`."
    )


def test_execute_skill_model_selection_table_has_drift_gate():
    text = EXECUTE_SKILL_MD.read_text()
    assert "| `drift-gate` |" in text, (
        "execute/SKILL.md's Model Selection table must carry a `drift-gate` row."
    )


def test_execute_skill_accumulates_security_surface_flags():
    text = EXECUTE_SKILL_MD.read_text()
    assert "Security-surface:" in text, (
        "execute/SKILL.md step 4 prose must instruct the controller to "
        "accumulate `Security-surface:` flags emitted by drift-gate, for the "
        "later whole-change security trigger."
    )


def test_execute_skill_reassures_quality_review_deferred_not_dropped():
    text = EXECUTE_SKILL_MD.read_text()
    assert "deferred" in text.lower() and "not dropped" in text.lower(), (
        "execute/SKILL.md step 4 must reassure that quality/style review is "
        "deferred to the whole-change phases (a later slice), not dropped "
        "entirely."
    )


# ---------------------------------------------------------------------------
# Slice 2: code-reviewer rechartered as the whole-change/PR reviewer
# ---------------------------------------------------------------------------


def test_code_reviewer_has_no_word_cap():
    text = CODE_REVIEWER_MD.read_text()
    assert "600-word" not in text, (
        "code-reviewer.md must no longer carry the 600-word hard cap — a "
        "whole-change review's findings can legitimately run longer."
    )


def test_code_reviewer_charter_requires_base_head_diff_and_intent_context():
    text = CODE_REVIEWER_MD.read_text()
    assert "base..HEAD" in text, (
        "code-reviewer.md must state it reviews the full `base..HEAD` diff, "
        "not a single slice."
    )
    lowered = text.lower()
    assert "spec" in lowered and "plan" in lowered, (
        "code-reviewer.md must name the spec and the plan as required intent "
        "context the caller provides."
    )


def test_code_reviewer_charter_states_style_out_of_scope():
    text = CODE_REVIEWER_MD.read_text()
    assert "style" in text.lower() and "out of scope" in text.lower(), (
        "code-reviewer.md must explicitly state that style is out of scope — "
        "not merely omit it from the checklist."
    )


def test_code_reviewer_good_fits_drop_per_slice_framing():
    text = CODE_REVIEWER_MD.read_text()
    assert "Slice N" not in text, (
        "code-reviewer.md's description must drop the old per-slice "
        "'Review Slice N of plan X' good-fit example — that's drift-gate's "
        "job now."
    )


def test_code_reviewer_retains_verdict_vocabulary():
    text = CODE_REVIEWER_MD.read_text()
    for verdict in ["SHIP", "FIX_FIRST", "BLOCK"]:
        assert verdict in text, (
            f"code-reviewer.md must retain the {verdict!r} verdict — portage's "
            "green-driver depends on this exact vocabulary."
        )


def test_code_reviewer_retains_security_auditor_escalation():
    text = CODE_REVIEWER_MD.read_text()
    assert "security-auditor" in text, (
        "code-reviewer.md must retain the security-auditor escalation clause."
    )


def test_code_reviewer_frontmatter_still_opus_high_explicit_tools():
    text = CODE_REVIEWER_MD.read_text()
    frontmatter = _frontmatter(text)
    assert _field(frontmatter, "model") == "opus", (
        "code-reviewer.md must stay pinned to `model: opus` — portage-compat."
    )
    assert _field(frontmatter, "effort") == "high", (
        "code-reviewer.md must stay pinned to `effort: high` — portage-compat."
    )
    tools = _tools_line(frontmatter)
    assert tools is not None, "code-reviewer.md frontmatter must carry an explicit `tools:` line"
    declared = [t.strip() for t in tools.split(",") if t.strip()]
    assert declared == ["Read", "Grep", "Glob", "Bash"], (
        f"code-reviewer.md `tools:` must stay exactly Read, Grep, Glob, Bash, got {tools!r}"
    )


def test_review_skill_no_longer_frames_code_reviewer_as_per_slice():
    text = REVIEW_SKILL_MD.read_text()
    assert "Review after EACH task" not in text, (
        "review/SKILL.md's execute-integration section still frames "
        "code-reviewer as the per-slice reviewer — retarget to the "
        "drift-gate/step-4 flow execute now uses."
    )


def test_review_skill_retargets_execute_integration_to_drift_gate():
    text = REVIEW_SKILL_MD.read_text()
    assert "drift-gate" in text, (
        "review/SKILL.md must point to `drift-gate` as execute's per-slice "
        "conformance gate, distinguishing it from this skill's whole-change/"
        "ad-hoc dispatch of code-reviewer."
    )
