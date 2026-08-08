"""Contract pins for the review-altitude split (slices 1-3).

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

Slice 3 adds `simplifier` — the whole-change simplify mutation phase agent —
pinning the agent charter's shape and its mechanically enforced write-scope
story (footprint_guard.py, tested separately in test_footprint_guard.py).

Slice 4 wires the whole-change phase pipeline into execute's After All Slices
section: simplify → correctness → conditional-security → flow-out → close.
These tests pin the phase ordering, the fail-closed security trigger, the
one-re-review cap, the guard-failure→revert mapping, the credential scrub, the
completion-report enumeration + worked example, and the `## End Phases`
resumability contract.
"""

from pathlib import Path

AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "agents"
SKILLS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "skills"

DRIFT_GATE_MD = AGENTS_DIR / "drift-gate.md"
CODE_REVIEWER_MD = AGENTS_DIR / "code-reviewer.md"
SIMPLIFIER_MD = AGENTS_DIR / "simplifier.md"
# The per-slice loop, phase pipeline, and status-handling content this file pins
# lives in `_shared/execute.md` (the single source of truth `execute/SKILL.md`
# wraps) — read from there so these pins hold after the shared-procedure extraction.
EXECUTE_SKILL_MD = SKILLS_DIR / "_shared" / "execute.md"
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


def test_execute_skill_per_slice_review_does_not_name_code_reviewer():
    """The per-slice loop must not name `code-reviewer` (retargeted to `drift-gate`).

    Scoped to the region BEFORE `## After All Slices` — the per-slice loop.
    Slice 4 legitimately reintroduces `code-reviewer` as the whole-change
    correctness-phase dispatch inside After All Slices, so a blanket file-wide
    absence would now be wrong. This narrower pin still catches a regression of
    the per-slice retargeting (role table, step 4, absorption line) while
    allowing the whole-change reference.
    """
    text = EXECUTE_SKILL_MD.read_text()
    marker = "## After All Slices"
    assert marker in text, "execute/SKILL.md must retain the After All Slices section"
    per_slice_region = text[: text.index(marker)]
    assert "code-reviewer" not in per_slice_region, (
        "execute/SKILL.md's per-slice loop (everything before `## After All "
        "Slices`) must not reference `code-reviewer` — the per-slice conformance "
        "gate is `drift-gate`. The whole-change correctness phase may reference "
        "`code-reviewer`, but only inside After All Slices."
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


# ---------------------------------------------------------------------------
# Slice 3: simplifier agent + footprint guard charter
# ---------------------------------------------------------------------------


def test_simplifier_agent_file_exists():
    assert SIMPLIFIER_MD.exists(), (
        f"Expected {SIMPLIFIER_MD} — the whole-change simplify mutation phase agent."
    )


def test_simplifier_model_is_opus_high():
    text = SIMPLIFIER_MD.read_text()
    frontmatter = _frontmatter(text)
    assert _field(frontmatter, "model") == "opus", (
        "simplifier.md frontmatter must pin `model: opus` — a whole-change "
        "dedup/simplify pass needs the deep-reasoning tier."
    )
    assert _field(frontmatter, "effort") == "high", (
        "simplifier.md frontmatter must pin `effort: high`."
    )


def test_simplifier_omits_tools_line_for_full_inheritance():
    text = SIMPLIFIER_MD.read_text()
    frontmatter = _frontmatter(text)
    assert _tools_line(frontmatter) is None, (
        "simplifier.md frontmatter must carry NO `tools:` line — it needs to "
        "edit files, run tests, and commit, so it must inherit the full tool "
        "set (like executor.md and assumption-prover.md do), not an explicit "
        "restricted list."
    )


def test_simplifier_charter_has_revert_on_failed_regreen_clause():
    text = SIMPLIFIER_MD.read_text()
    lowered = text.lower()
    assert "revert" in lowered and "pre-simplify" in lowered, (
        "simplifier.md must state that a failed re-green reverts to the "
        "pre-simplify state — never commit broken, never leave a dirty tree."
    )


def test_simplifier_charter_has_separate_commit_clause():
    text = SIMPLIFIER_MD.read_text()
    assert "separately from slice commits" in text or (
        "separate" in text.lower() and "slice commit" in text.lower()
    ), (
        "simplifier.md must state it commits its change separately from "
        "slice commits, GPG-signed with a conventional commit prefix."
    )


def test_simplifier_charter_names_flag_dont_apply_rubric():
    text = SIMPLIFIER_MD.read_text()
    lowered = text.lower()
    for term in ["authz", "public", "test coverage"]:
        assert term in lowered, (
            f"simplifier.md's flag-don't-apply rubric must name {term!r} as one "
            "of the categories that are always flagged, never auto-applied."
        )


def test_simplifier_charter_pins_footprint_guard_precommit_invocation():
    text = SIMPLIFIER_MD.read_text()
    assert "footprint_guard.py" in text, (
        "simplifier.md must instruct running footprint_guard.py before committing."
    )
    lowered = text.lower()
    assert "non-zero" in lowered and "revert" in lowered, (
        "simplifier.md must treat a non-zero footprint_guard.py exit as a "
        "failed re-green — same remediation path (revert, flag) as a failed "
        "test re-green."
    )


def test_simplifier_charter_retains_executor_status_vocabulary():
    text = SIMPLIFIER_MD.read_text()
    for status in ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"]:
        assert status in text, (
            f"simplifier.md must return the executor status vocabulary "
            f"({status!r} missing)."
        )


def test_execute_skill_wires_simplifier():
    text = EXECUTE_SKILL_MD.read_text()
    assert "simplifier" in text, (
        "simplifier must now be wired into execute's After All Slices simplify "
        "phase — slice 4 dispatches it as the whole-change simplify pass."
    )


# ---------------------------------------------------------------------------
# Slice 4: After All Slices phase pipeline
# ---------------------------------------------------------------------------


def _after_all_slices(text: str) -> str:
    """The After All Slices section body — the phase-pipeline scope."""
    marker = "## After All Slices"
    assert marker in text, "execute/SKILL.md must retain the After All Slices section"
    return text[text.index(marker):]


def _phase_section(text: str, start: str, end: str | None) -> str:
    assert start in text, f"expected phase header {start!r} in execute/SKILL.md"
    lo = text.index(start)
    hi = text.index(end, lo) if end and end in text[lo:] else len(text)
    return text[lo:hi]


def test_after_all_slices_phase_ordering():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    simplify = text.index("Phase 2: Simplify")
    correctness = text.index("Phase 3: Correctness")
    security = text.index("Phase 4: Security")
    assert simplify < correctness < security, (
        "After All Slices phases must run simplify → correctness → security in "
        "that order."
    )


def test_after_all_slices_simplify_dispatches_simplifier():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 2: Simplify", "Phase 3: Correctness")
    assert "`simplifier`" in section, (
        "the simplify phase must dispatch `simplifier` (base SHA + pre-simplify SHA)."
    )
    assert "pre-simplify" in section.lower()


def test_after_all_slices_guard_failure_maps_to_revert():
    """Any non-zero footprint_guard exit — including post-commit — reverts."""
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 2: Simplify", "Phase 3: Correctness")
    lowered = section.lower()
    assert "footprint_guard.py" in section, (
        "the simplify phase must re-run footprint_guard.py after simplifier returns."
    )
    assert "revert the simplify commit" in lowered, (
        "any non-zero guard exit must map to reverting the simplify commit."
    )
    # Both exit codes named, and the wording distinguishes them.
    assert "exit 1" in lowered and "exit 2" in lowered, (
        "the guard-failure mapping must name both exit 1 and exit 2."
    )
    assert "violation" in lowered and "guard error" in lowered, (
        "exit 1 must be reported as a violation, exit 2 as a guard error — "
        "distinct wording even though remediation is identical."
    )


def test_after_all_slices_correctness_dispatches_code_reviewer_whole_change():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 3: Correctness", "Phase 4: Security")
    assert "`code-reviewer`" in section, "the correctness phase must dispatch `code-reviewer`."
    assert "base..HEAD" in section, (
        "the correctness dispatch must pass the whole-change base..HEAD diff."
    )


def test_after_all_slices_correctness_directs_simplify_commit_auth_scrutiny():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 3: Correctness", "Phase 4: Security")
    lowered = section.lower()
    assert "scrutin" in lowered and "simplify commit" in lowered, (
        "the correctness dispatch must direct explicit scrutiny at the simplify commit."
    )
    assert "control-flow" in lowered, (
        "the scrutiny must target control-flow changes."
    )
    for surface in ["auth", "session", "permission"]:
        assert surface in lowered, (
            f"the simplify-commit scrutiny must name the {surface!r} surface."
        )


def test_after_all_slices_correctness_names_receiving_code_review_binding():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 3: Correctness", "Phase 4: Security")
    assert "receiving-code-review" in section, (
        "the correctness-fix triage must be governed by the receiving-code-review pattern."
    )
    assert "binding" in section.lower(), (
        "receiving-code-review must be named as BINDING for the triage step."
    )


def test_after_all_slices_correctness_one_re_review_cap():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 3: Correctness", "Phase 4: Security")
    lowered = section.lower()
    assert "at most one re-review" in lowered, (
        "the correctness phase must cap re-review at one round."
    )
    assert "never just the fix commits" in lowered, (
        "the re-review must re-diff the full base..HEAD, never just the fix commits."
    )
    assert "surface" in lowered and "user" in lowered, (
        "survivors after the one round must surface to the user, not loop further."
    )


def test_after_all_slices_security_trigger_categories():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 4: Security", "Phase 5: Flow-out")
    lowered = section.lower()
    for category in ["auth", "crypto", "secret", "session", "token", "permission"]:
        assert category in lowered, (
            f"the deterministic security-trigger list must enumerate {category!r}."
        )
    assert "security-surface:" in lowered, (
        "the security trigger must union the accumulated drift-gate Security-surface flags."
    )


def test_after_all_slices_security_fail_closed():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 4: Security", "Phase 5: Flow-out")
    lowered = section.lower()
    assert "fail-closed" in lowered, "the security trigger must be fail-closed."
    assert "ambiguity" in lowered, (
        "fail-closed wording: any ambiguity about whether the trigger fires → run the audit."
    )
    assert "`security-auditor`" in section, (
        "fail-closed remediation must run `security-auditor`."
    )
    assert "final form" in lowered, (
        "the security audit runs on the final form, after correctness fixes settle."
    )


def test_after_all_slices_flow_out_credential_scrub():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 5: Flow-out", "Phase 6: Close")
    lowered = section.lower()
    assert "credential-pattern scrub" in lowered, (
        "the flow-out step must run a mechanical credential-pattern scrub before "
        "any finding text enters a session candidate."
    )
    assert "regex" in lowered, "the scrub must document a regex list."
    for shape in ["bearer", "api-key", "high-entropy"]:
        assert shape in lowered, (
            f"the scrub regex list must describe the {shape!r} token shape."
        )
    assert "never" in lowered and "verbatim" in lowered, (
        "report bodies must be summarized, never captured verbatim."
    )


def test_after_all_slices_completion_report_enumeration_and_example():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 6: Close", None)
    lowered = section.lower()
    assert "enumerate every phase" in lowered, (
        "the completion report must enumerate every phase's outcome explicitly."
    )
    assert "skipped" in lowered, (
        "the enumeration requirement must cover clean/empty/skipped phases."
    )
    assert "simplify: no changes; correctness: SHIP, 0 findings; security: skipped — no trigger" in text, (
        "the completion-report worked example must appear verbatim in the skill text."
    )


def test_after_all_slices_completion_report_per_finding_citation():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    section = _phase_section(text, "Phase 6: Close", None)
    lowered = section.lower()
    assert "local-to-one-slice" in lowered and "cross-slice" in lowered, (
        "each finding must be classified local-to-one-slice vs cross-slice."
    )
    assert "plan section" in lowered and "cited" in lowered, (
        "each Critical/Important finding must be cited against its plan section."
    )
    assert "5" in section and "restore" in lowered, (
        "the 5-plan measurement revisit condition must be stated."
    )


def test_after_all_slices_end_phases_checklist_and_clean_tree_resume():
    text = _after_all_slices(EXECUTE_SKILL_MD.read_text())
    assert "## End Phases" in text, (
        "phase progress must be recorded as an `## End Phases` checklist on the parent."
    )
    lowered = text.lower()
    assert "clean-working-tree precondition" in lowered, (
        "re-entering any end phase on resume requires a clean-working-tree precondition."
    )
    assert "phase boundary" in lowered, (
        "a dirty tree on resume must be reverted to the last recorded phase boundary."
    )
