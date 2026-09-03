"""Mutation-evidence and observation-point contract.

The executor's evidence contract has two halves, and each is pinned where it
binds.

**Evidence is an artifact, not a claim.** The mutation transcript lives in the
COMMIT BODY, which the conformance gate reads; the controller-facing head
carries a pointer and a summary. Evidence returned only in the reply is
evidence the gate structurally cannot see, so a correct build reads as
unevidenced and burns a gate cycle being re-derived — see
[[lesson/require-the-mutation-transcript-in-the-commit-body-a-conformance-gate-reads-the-commit-never-the-reply]].
Correspondingly, `drift-gate` is pinned to OPEN the commit body and confirm the
transcript is there, phrased as its own instruction, because a gate asked only
to verify the behaviour will verify the behaviour and never notice the evidence
is missing —
[[lesson/make-the-gate-verify-the-evidence-artifact-exists-not-just-the-claim-it-evidences]].

**The mutation kind is named, not chosen.** A dispatch that says only "break the
behaviour" gets deletion every time, because deletion is the cheapest mutation
satisfying that wording, and it proves a pin exists while proving nothing about
whether the pin is scoped —
[[lesson/name-the-mutation-kind-in-an-executor-dispatch-or-it-will-pick-deletion]].
A mutation that stays GREEN is pinned as a reportable finding with three
candidate explanations, because given only a way to report success an agent will
narrate one —
[[lesson/a-mutation-that-stays-green-needs-a-third-explanation-not-a-comfortable-one]].

**The restore mechanism is named, and it is not `git diff`.** A mid-build
`git diff --exit-code` cannot pass — the executor's own uncommitted work is in
the tree by construction — and an instruction that cannot be satisfied is an
invitation to improvise near a dirty tree —
[[lesson/a-mid-build-restore-check-needs-a-scratch-baseline-git-diff-against-head-cannot-pass]].

**Observation points are enumerated mechanically.** The executor establishes
where an asserted property must hold by running a command, and stops when that
enumeration disagrees with the task's declared files; the gate re-runs one.
Pre-registered behavioural evidence for this pair lives in
`plugins/craft/evals/observation-point-enumeration/`.

Every pin here is scoped to the section it guards — extracted by heading (or,
where a section has no heading of its own, by an exact-text boundary), per
[[lesson/mutation-test-a-prose-pin-whose-target-string-occurs-elsewhere-in-the-file]]
— and asserted as a contiguous substring within one physical line, per
[[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]]. Pins over a
line whose exact position (which section, which sub-block) is itself part of
the contract are additionally mutation-checked against relocation and decoy,
not just deletion, per
[[lesson/a-green-prose-contract-suite-is-not-evidence-the-pins-bind]] — a pin
that stays green when its line is moved somewhere the contract forbids is
decorative.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_EXECUTE = CRAFT / "skills" / "_shared" / "execute.md"
EXECUTOR_AGENT = CRAFT / "agents" / "executor.md"
DRIFT_GATE_AGENT = CRAFT / "agents" / "drift-gate.md"

FIXTURES = Path(__file__).parent / "fixtures"
CONTROLLER_HEAD_FIXTURE = FIXTURES / "controller_head_schema.txt"
COMMIT_BODY_FIXTURE = FIXTURES / "commit_body_schema.txt"

DISPATCH_HEADING = "### 3. Dispatch `executor`"
REVIEW_HEADING = "### 4. Review"
NEXT_TASK_HEADING = "### 5. Update the task graph"

DISPATCH_EXPECTS_START = "The agent expects four things:"
DISPATCH_EXPECTS_END = "**4.** Applicable dispatch lessons"

STEP3_HEADING = "## Step 3: Establish the observation points — and stop if they disagree"
STEP4_HEADING = "## Step 4: Repo conventions"
STEP8_HEADING = "## Step 8: Verify, then mutation-check every contract item"
STEP9_HEADING = "## Step 9: Commit — with the transcript in the body"
STEP10_HEADING = "## Step 10: Self-review"

MECHANISMS_HEADING = "## Mechanisms"
RESTORE_HEADING = "### Restoring after a mutation check"
UNDO_HEADING = "### Undoing your own edit"
READ_PREVIOUS_HEADING = "### Reading a previous version of a file"
PARALLEL_HEADING = "### Working alongside a parallel executor"
RUNNING_TESTS_HEADING = "### Running tests"
MUTATION_CHECKS_HEADING = "### Mutation checks"
OVER_YOUR_HEAD_HEADING = "## When you're in over your head"

REPORT_FORMAT_HEADING = "## Report format"
RULES_HEADING = "## Rules"
CONTROLLER_HEAD_HEADING = "### Controller-facing head (return this)"
COMMIT_BODY_HEADING = "### Commit body (write this; not returned)"

WHAT_YOU_CHECK_HEADING = "## What you check"
WHAT_YOU_DO_NOT_CHECK_HEADING = "## What you do NOT check"

DRIFT_GATE_RULES_START = "Rules:"
DRIFT_GATE_RULES_END = "## What you check"

SMALL_ROW_START = "| **Small**"
MEDIUM_ROW_START = "| **Medium**"

PASS_BULLET_START = "- `PASS`"
DRIFT_BULLET_START = "- `DRIFT`"
BLOCKED_BULLET_START = "- `BLOCKED`"


class SectionBoundaryError(Exception):
    """Raised when a boundary string used to slice out a section can no
    longer be found in the source text — e.g. a paragraph the boundary
    quotes verbatim got reworded elsewhere in the file."""


def _section(text: str, start_heading: str, end_heading: str, *, context: str) -> str:
    try:
        start = text.index(start_heading)
    except ValueError:
        raise SectionBoundaryError(
            f"{context}: start boundary {start_heading!r} not found in the source "
            f"text — this section can no longer be located."
        ) from None
    try:
        end = text.index(end_heading, start)
    except ValueError:
        raise SectionBoundaryError(
            f"{context}: end boundary {end_heading!r} not found in the source "
            f"text — this section can no longer be located."
        ) from None
    return text[start:end]


def _executor_section(start: str, end: str, *, context: str) -> str:
    return _section(EXECUTOR_AGENT.read_text(), start, end, context=context)


def _dispatch_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, DISPATCH_HEADING, REVIEW_HEADING, context="execute.md dispatch step")


def _dispatch_expects_section() -> str:
    """Narrower than `_dispatch_section` — bounded to the four-input list,
    excluding the lesson-forwarding block, the model-escalation prose, and the
    trailing `Returns:` line that follow it in the same step."""
    return _section(
        _dispatch_section(),
        DISPATCH_EXPECTS_START,
        DISPATCH_EXPECTS_END,
        context="execute.md dispatch step's four-input list",
    )


def _review_table_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, REVIEW_HEADING, NEXT_TASK_HEADING, context="execute.md review step")


def _small_row_section() -> str:
    """Narrower than `_review_table_section` — bounded to just the physical
    `| **Small**` table row. Small is the one path with no drift-gate
    dispatch, so its row is the sole evidence guard on it, and a clause that
    reads fine after being relocated to the Medium row leaves Small
    unguarded."""
    return _section(
        _review_table_section(),
        SMALL_ROW_START,
        MEDIUM_ROW_START,
        context="execute.md review table's Small row",
    )


def _report_format_section() -> str:
    return _executor_section(
        REPORT_FORMAT_HEADING, RULES_HEADING, context="executor.md report format"
    )


def _controller_head_fence() -> str:
    """The fenced block under the controller-facing head heading, bounded
    below by the commit-body heading so a field relocated into the commit body
    is not credited here."""
    section = _executor_section(
        CONTROLLER_HEAD_HEADING, COMMIT_BODY_HEADING, context="executor.md controller-facing head"
    )
    return _fence_body(section, "executor.md#controller-facing-head")


def _commit_body_fence() -> str:
    """The fenced block under the commit-body heading, bounded below by the
    Rules heading."""
    section = _executor_section(
        COMMIT_BODY_HEADING, RULES_HEADING, context="executor.md commit body"
    )
    return _fence_body(section, "executor.md#commit-body")


def _fence_body(section_text: str, path_label: str) -> str:
    """Return the contents of the first triple-backtick fence in *section_text*."""
    lines = section_text.splitlines()
    fences = [i for i, line in enumerate(lines) if line.strip() == "```"]
    if len(fences) < 2:
        pytest.fail(
            f"{path_label}: expected a fenced block in this section and found "
            f"{len(fences)} fence delimiter(s). The schema fixture is compared "
            f"against the fence contents, so the fence must exist."
        )
    return "\n".join(lines[fences[0] + 1 : fences[1]]).strip()


def _mechanisms_subsection(start: str, end: str, *, context: str) -> str:
    return _executor_section(start, end, context=context)


def _what_you_check_section() -> str:
    text = DRIFT_GATE_AGENT.read_text()
    return _section(
        text,
        WHAT_YOU_CHECK_HEADING,
        WHAT_YOU_DO_NOT_CHECK_HEADING,
        context="drift-gate.md 'What you check'",
    )


def _drift_gate_rules_section() -> str:
    text = DRIFT_GATE_AGENT.read_text()
    return _section(
        text, DRIFT_GATE_RULES_START, DRIFT_GATE_RULES_END, context="drift-gate.md Rules block"
    )


def _pass_bullet_section() -> str:
    """Narrower than `_drift_gate_rules_section` — a pin must guard the verdict
    it names, so relocating a clause into another bullet must fail."""
    return _section(
        _drift_gate_rules_section(),
        PASS_BULLET_START,
        DRIFT_BULLET_START,
        context="drift-gate.md Rules block's PASS bullet",
    )


def _drift_bullet_section() -> str:
    """Narrower than `_drift_gate_rules_section`. See `_pass_bullet_section`."""
    return _section(
        _drift_gate_rules_section(),
        DRIFT_BULLET_START,
        BLOCKED_BULLET_START,
        context="drift-gate.md Rules block's DRIFT bullet",
    )


def _pin_in(section_text: str, path_label: str, phrase: str, why: str) -> None:
    """Assert *phrase* appears inside a single physical line of *section_text*,
    and that it occurs exactly once so the pin cannot pass on the wrong
    occurrence."""
    matching_lines = [line for line in section_text.splitlines() if phrase in line]
    if len(matching_lines) == 1:
        return
    if len(matching_lines) > 1:
        pytest.fail(
            f"{path_label}: the pinned span {phrase!r} occurs {len(matching_lines)} "
            f"times in this section — reword the incidental occurrence so the pin "
            f"guards exactly one line. {why}"
        )
    if phrase in " ".join(section_text.split()):
        pytest.fail(
            f"{path_label}: the pinned span {phrase!r} is present but straddles a "
            f"line wrap — keep it on one physical line. {why}"
        )
    pytest.fail(f"{path_label}: missing the pinned span {phrase!r}. {why}")


def _absent_from(section_text: str, path_label: str, phrase: str, why: str) -> None:
    """Assert *phrase* does NOT appear in *section_text*.

    Used only where the contract is that a field lives in one channel and not
    the other — the point of the evidence relocation is defeated if the
    transcript is also carried in the head.
    """
    if phrase in " ".join(section_text.split()):
        pytest.fail(f"{path_label}: {phrase!r} must not appear in this section. {why}")


# --- The head is a pointer; the commit body is the evidence ----------------


def test_controller_head_matches_the_canonical_schema():
    expected = CONTROLLER_HEAD_FIXTURE.read_text().strip()
    actual = _controller_head_fence()
    assert actual == expected, (
        "executor.md's controller-facing head fence must match "
        f"{CONTROLLER_HEAD_FIXTURE.name} exactly. The head is the controller's "
        "working surface: it carries a `commits:` pointer telling the gate where "
        "the transcript lives, an `observation-points:` field, and a "
        "`mutation-summary:` count — not the evidence itself.\n\n"
        f"--- expected ---\n{expected}\n\n--- actual ---\n{actual}"
    )


def test_commit_body_matches_the_canonical_schema():
    expected = COMMIT_BODY_FIXTURE.read_text().strip()
    actual = _commit_body_fence()
    assert actual == expected, (
        "executor.md's commit-body fence must match "
        f"{COMMIT_BODY_FIXTURE.name} exactly. The commit body is the durable "
        "artifact the fresh-context gate reads, so the transcript and the "
        "observation-point enumeration must be specified here.\n\n"
        f"--- expected ---\n{expected}\n\n--- actual ---\n{actual}"
    )


def test_head_does_not_carry_the_transcript_itself():
    _absent_from(
        _controller_head_fence(),
        "executor.md#controller-facing-head",
        "empty-diff confirmed",
        "The per-item evidence fields belong to the commit body. A head that "
        "also carries the transcript re-creates the failure this contract "
        "exists to fix: the executor satisfies the head, treats the reply as "
        "the delivery channel, and the gate — which reads the commit — still "
        "finds nothing.",
    )


def test_commit_step_states_why_the_transcript_goes_in_the_commit():
    _pin_in(
        _executor_section(STEP9_HEADING, STEP10_HEADING, context="executor.md Step 9"),
        "executor.md#step-9",
        "it never sees your reply to the controller",
        "The relocation must carry its reason. An executor told only WHERE to "
        "put the transcript will treat it as a formatting preference and "
        "summarise; told that the reviewer cannot see the reply, it has a "
        "reason to write the real thing.",
    )


def test_mutation_pass_precedes_the_commit():
    _pin_in(
        _executor_section(STEP8_HEADING, STEP9_HEADING, context="executor.md Step 8"),
        "executor.md#step-8",
        "before Step 9's commit",
        "Mutation checking must happen before the commit, so a mutation that "
        "exposes a defect is fixed in the same commit rather than by amending "
        "a GPG-signed one. The step that runs the tests is where that ordering "
        "has to be stated.",
    )


# --- The mutation kind is named, not chosen --------------------------------


def _mutation_checks_section() -> str:
    return _mechanisms_subsection(
        MUTATION_CHECKS_HEADING, OVER_YOUR_HEAD_HEADING, context="executor.md mutation checks"
    )


def test_mutation_kind_comes_from_the_contract_item():
    _pin_in(
        _mutation_checks_section(),
        "executor.md#mutation-checks",
        "The kind of mutation is named by the contract item",
        "Left to choose, an executor picks deletion every time — the cheapest "
        "mutation that satisfies 'break the behaviour'. The kind has to arrive "
        "from the contract rather than from the agent applying it.",
    )


def test_default_mutation_kind_is_reverting_the_fix():
    _pin_in(
        _mutation_checks_section(),
        "executor.md#mutation-checks",
        "the default is **reverting the fix under test**",
        "A contract item is often authored before the code exists, so a named "
        "kind cannot be mandatory. The default must be stated and must not be "
        "deletion.",
    )


def test_executor_may_upgrade_the_kind_but_never_weaken_it():
    _pin_in(
        _mutation_checks_section(),
        "executor.md#mutation-checks",
        "You may apply a stronger kind than the one named; you may never apply a weaker one",
        "The executor sees things the contract's author could not — that the "
        "guarded string occurs twice, that position is part of the contract. "
        "Upgrading must be permitted, downgrading must not, and the asymmetry "
        "has to be explicit or the escape hatch becomes a route back to "
        "deletion.",
    )


def test_deletion_is_demoted_with_its_reason():
    _pin_in(
        _mutation_checks_section(),
        "executor.md#mutation-checks",
        "it proves nothing about whether the pin is *scoped*",
        "Naming deletion as weak is not enough; the prose must say what it "
        "fails to prove, because 'the pin exists' is exactly the reassurance a "
        "delete-only pass produces on the defect that ships.",
    )


def test_stayed_green_is_a_finding_with_three_explanations():
    _pin_in(
        _mutation_checks_section(),
        "executor.md#mutation-checks",
        "A mutation that stays GREEN is a finding, and reporting it is the job",
        "Given only a way to report success, an agent narrates one. The "
        "stayed-GREEN outcome must be framed as the job rather than as a "
        "failure to explain away, or the comfortable explanation wins.",
    )


def test_defence_in_depth_is_named_as_the_comfortable_answer():
    _pin_in(
        _mutation_checks_section(),
        "executor.md#mutation-checks",
        "remove **all** the protections you cite, together",
        "'Defence in depth' is the explanation that sounds reasonable and is "
        "usually an uncredited third condition unexamined. Naming the "
        "disproof — remove every cited protection at once and show the test "
        "still passes — is what makes the claim falsifiable.",
    )


def test_unevidenced_item_is_not_done_regardless_of_status():
    section = _mutation_checks_section()
    _pin_in(
        section,
        "executor.md#mutation-checks",
        "A contract item with no mutation evidence is not DONE",
        "The report contract must state that an item without evidence cannot "
        "be claimed DONE.",
    )
    _pin_in(
        section,
        "executor.md#mutation-checks",
        "Downgrading the status does not exempt the item",
        "A sanctioned downgrade to DONE_WITH_CONCERNS must not read as a way "
        "to route an unevidenced item around the gate's status-agnostic check.",
    )


# --- The restore mechanism is named, and it is not `git diff` --------------


def _restore_section() -> str:
    return _mechanisms_subsection(
        RESTORE_HEADING, UNDO_HEADING, context="executor.md restore mechanism"
    )


def test_restore_names_the_scratch_baseline_mechanism():
    _pin_in(
        _restore_section(),
        "executor.md#restoring",
        "Capture a scratch baseline **before** mutating",
        "Three consecutive dispatches told executors to verify a restore with "
        "`git diff --exit-code`, which cannot pass mid-build. A ban with no "
        "named replacement has already failed three times on this surface, so "
        "the mechanism must be stated, not implied.",
    )


def test_restore_rules_out_git_diff_with_its_reason():
    _pin_in(
        _restore_section(),
        "executor.md#restoring",
        "Your own uncommitted",
        "The prose must say WHY `git diff --exit-code` cannot verify a "
        "mid-build restore — the executor's own work is in the tree by "
        "construction — or it reads as an arbitrary preference and gets "
        "substituted back.",
    )


def test_undo_names_the_forward_path_before_the_prohibition():
    section = _mechanisms_subsection(
        UNDO_HEADING, READ_PREVIOUS_HEADING, context="executor.md undo mechanism"
    )
    _pin_in(
        section,
        "executor.md#undoing",
        "Re-edit forward to the intended content",
        "A ban removes a candidate without supplying a replacement while the "
        "executor still needs some restore. The safe path must be stated in "
        "the same breath as the prohibition.",
    )
    _pin_in(
        section,
        "executor.md#undoing",
        "Untracked files are covered by the same",
        "Deleting untracked files to 'clean' the tree is its own destructive "
        "operation and is not covered by a prohibition phrased over checkout "
        "and stash.",
    )


def test_reading_a_previous_version_never_touches_the_working_tree():
    section = _mechanisms_subsection(
        READ_PREVIOUS_HEADING, PARALLEL_HEADING, context="executor.md read-previous mechanism"
    )
    _pin_in(
        section,
        "executor.md#reading-a-previous-version",
        "git show <rev>:<path> > <scratch>/<name>.<rev>",
        "Banning `git stash` while still demanding a mutation check against "
        "pre-change content leaves the banned command the only visible way to "
        "do what was just asked. This is the third distinct operation the one "
        "dangerous command was serving, and it needs its own named mechanism.",
    )


def test_parallel_executor_guidance_names_the_shared_index():
    section = _mechanisms_subsection(
        PARALLEL_HEADING, RUNNING_TESTS_HEADING, context="executor.md parallel-executor guidance"
    )
    _pin_in(
        section,
        "executor.md#parallel",
        "you share not just a file tree but a **git index**",
        "Parallel executors in one worktree share the index, so a bare "
        "`git add .` stages another agent's work. Naming the shared index is "
        "what makes commit-by-pathspec follow.",
    )
    _pin_in(
        section,
        "executor.md#parallel",
        "git commit -- <your paths>",
        "The safe commit form must be named concretely, with real flags, or "
        "the executor writes the consistent-looking wrong one.",
    )


# --- Observation points are enumerated mechanically ------------------------


def _step3_section() -> str:
    return _executor_section(STEP3_HEADING, STEP4_HEADING, context="executor.md Step 3")


def test_step3_requires_a_reproducible_enumeration():
    _pin_in(
        _step3_section(),
        "executor.md#step-3",
        "by running a command, never by recall",
        "The failure this step exists to catch is mirroring a pattern's shape "
        "while missing its sites. Recall reproduces the shape; only a command "
        "produces the site set, and only a stated command lets the gate "
        "re-run it.",
    )


def test_step3_stops_when_the_enumeration_disagrees_with_the_files_list():
    _pin_in(
        _step3_section(),
        "executor.md#step-3",
        "**Stop and report `NEEDS_CONTEXT`**",
        "The hard stop is the whole value of the step. Reporting the "
        "disagreement and building anyway leaves the finding competing for "
        "attention in a report, and the lesson corpus is consistent that "
        "unread findings do not change outcomes.",
    )


def test_step3_distinguishes_no_sites_from_a_skipped_step():
    _pin_in(
        _step3_section(),
        "executor.md#step-3",
        "Skipping the step silently and having nothing to enumerate are different",
        "Without an explicit sentinel for the zero-site case, a skipped step "
        "and a genuinely inapplicable one are indistinguishable in the report, "
        "and the gate's re-run check passes vacuously.",
    )


def test_dispatch_treats_an_enumeration_disagreement_as_a_task_shape_finding():
    _pin_in(
        _dispatch_section(),
        "execute.md#3",
        "is a task-shape finding, not a failed dispatch",
        "Step 3's stop degrades into a speed bump within two runs if the "
        "controller's recovery is to re-send the same body. The dispatch step "
        "must say the task gets reshaped.",
    )


# --- The dispatch carries per-task facts, not mechanism --------------------


def test_dispatch_names_the_scoped_command_with_real_flags():
    _pin_in(
        _dispatch_expects_section(),
        "execute.md#3",
        "Name the\ncommand with real flags".replace("\n", " "),
        "A described command gets a consistent-looking wrong one written. The "
        "command and its timeout are the per-task facts the executor cannot "
        "derive, so they stay in the dispatch when the mechanism moves out.",
    )


def test_dispatch_requires_measuring_before_mandating_a_foreground_run():
    _pin_in(
        _dispatch_expects_section(),
        "execute.md#3",
        "Measure before you mandate:",
        "Five independent executors stalled on a foreground mandate that was "
        "mechanically impossible to obey. The controller's obligation is to "
        "measure the suite against the harness ceiling, not to harden the "
        "wording again.",
    )


def test_dispatch_says_absent_scope_facts_are_stated_not_omitted():
    _pin_in(
        _dispatch_expects_section(),
        "execute.md#3",
        "so the executor can tell \"not applicable\" from \"forgotten\"",
        "An omitted scope fact and an inapplicable one look identical to the "
        "executor, and the difference decides whether it proceeds or asks.",
    )


def test_dispatch_bars_restating_mechanism():
    _pin_in(
        _dispatch_section(),
        "execute.md#3",
        "Do not restate it here",
        "The payload grew to eight inputs because every fix landed in it. If "
        "mechanism may be restated in the dispatch, the two copies drift and "
        "the accretion resumes — the executor's surface is the single home.",
    )


def test_dispatch_suspects_the_environment_before_the_agents():
    _pin_in(
        _dispatch_section(),
        "execute.md#3",
        "suspect the environment\nbefore the agents".replace("\n", " "),
        "The one fix that measurably moved the loop came from recognising a "
        "mechanism story wearing a compliance costume. This is the controller's "
        "highest-leverage diagnostic and it was absent from the ritual text.",
    )


def test_dispatch_bars_asserting_unverified_claims():
    _pin_in(
        _dispatch_section(),
        "execute.md#3",
        "Do not assert into a dispatch what you have not checked",
        "A claim the controller carries forward arrives in the executor's "
        "prompt as fact and gets built on. Several lessons record a dispatch "
        "asserting a repo property that was false.",
    )


def test_dispatch_bars_mid_flight_instructions():
    _pin_in(
        _dispatch_section(),
        "execute.md#3",
        "Never send new instructions to a running executor",
        "A scope change sent mid-run reaches an agent whose plan is already "
        "committed; it must be a fresh dispatch against a rewritten body.",
    )


def test_review_table_names_the_evidence_check_for_small_slices():
    _pin_in(
        _small_row_section(),
        "execute.md#4",
        "confirm the commit body carries a mutation transcript for every test-contract item",
        "A Small slice skips the drift-gate dispatch entirely, so the inline "
        "review the table prescribes is the sole evidence guard on that path. "
        "Scoped to the physical Small row, because a clause that reads fine "
        "after relocation into the Medium row leaves Small unguarded.",
    )


# --- The gate reads the artifact -------------------------------------------


def test_gate_opens_the_commit_body_rather_than_trusting_the_report():
    _pin_in(
        _what_you_check_section(),
        "drift-gate.md#what-you-check",
        "open the commit body and confirm the",
        "A gate pointed at the executor's report is pointed at the one channel "
        "it structurally cannot verify. The instruction must name the artifact "
        "and the act of opening it.",
    )


def test_gate_is_told_why_the_artifact_check_is_its_own_instruction():
    _pin_in(
        _what_you_check_section(),
        "drift-gate.md#what-you-check",
        "never notice that the evidence it was supposed to read does not exist",
        "Measured in the field: a gate asked only to verify the findings "
        "verified the findings and missed a fabricated evidence location. The "
        "reason has to travel with the instruction or it reads as redundant "
        "with check 1 and gets collapsed into it.",
    )


def test_gate_does_not_reconstruct_a_missing_transcript():
    _pin_in(
        _what_you_check_section(),
        "drift-gate.md#what-you-check",
        "Do not reconstruct a missing transcript",
        "Re-running the mutations itself converts a reporting defect into a "
        "silent pass and burns exactly the cycle the transcript exists to "
        "save. This is the observed baseline behaviour, not a hypothesis.",
    )


def test_gate_grades_a_stayed_green_transcript_as_evidence():
    _pin_in(
        _what_you_check_section(),
        "drift-gate.md#what-you-check",
        "A transcript recording a stayed-GREEN mutation is complete evidence, not a gap",
        "If the gate treats a stayed-GREEN outcome as deficient, it re-creates "
        "on the reviewing side the incentive the executor's prose just removed "
        "— and the honest report becomes the punished one.",
    )


def test_gate_requires_the_transcript_to_name_the_assertion():
    _pin_in(
        _what_you_check_section(),
        "drift-gate.md#what-you-check",
        '"The test went RED" is not a transcript',
        "'The test went RED' and 'this assertion pinned the behaviour' are "
        "different claims, and only the second is evidence. The gate has to "
        "reject the first explicitly.",
    )


def test_gate_re_runs_one_observation_point_enumeration():
    _pin_in(
        _what_you_check_section(),
        "drift-gate.md#what-you-check",
        "Re-run one of them",
        "An enumeration nobody re-runs is a report field, not a gate. Re-running "
        "one is what makes the executor's stated command load-bearing.",
    )


def test_gate_treats_a_missing_observation_field_as_drift():
    _pin_in(
        _what_you_check_section(),
        "drift-gate.md#what-you-check",
        "A report with the field missing entirely is DRIFT",
        "`none` is a legitimate answer and a missing field is not; without "
        "this the skipped step and the inapplicable one are indistinguishable "
        "and the check passes vacuously.",
    )


def test_rules_block_pass_requires_the_transcript_in_the_commit_body():
    _pin_in(
        _pass_bullet_section(),
        "drift-gate.md#rules",
        "the commit body carries a mutation transcript for\n  every test-contract item".replace(
            "\n  ", " "
        ),
        "The verdict block is the gate's operative decision table. A reader of "
        "the Rules block alone must not be able to satisfy PASS without the "
        "artifact. Scoped to the PASS bullet, so relocating the clause into "
        "DRIFT or BLOCKED — where it no longer guards PASS — fails.",
    )


def test_rules_block_drift_fires_regardless_of_claimed_status():
    _pin_in(
        _drift_bullet_section(),
        "drift-gate.md#rules",
        "regardless of the claimed status",
        "A sanctioned downgrade to DONE_WITH_CONCERNS must not ship an "
        "unevidenced item past the gate. Scoped to the DRIFT bullet, so "
        "relocating the clause elsewhere fails.",
    )


def test_rules_block_drift_covers_an_unmet_observation_point():
    _pin_in(
        _drift_bullet_section(),
        "drift-gate.md#rules",
        "an enumerated observation point does not carry the property",
        "The enumeration check needs a verdict to resolve to, or the gate "
        "re-runs a command and has nowhere to record that it disagreed.",
    )


# --- test-file infrastructure ---------------------------------------------


def test_section_raises_named_error_when_boundary_missing():
    with pytest.raises(SectionBoundaryError, match=r"nonexistent-boundary.*not found"):
        _section("some prose with a start marker in it", "start", "nonexistent-boundary", context="a test fixture")


def test_fence_body_fails_loudly_when_the_fence_is_missing():
    # `pytest.fail` raises `Failed`, which derives from BaseException rather
    # than Exception — matching on Exception here would let the helper return
    # silently and still pass.
    with pytest.raises(BaseException, match=r"expected a fenced block") as excinfo:
        _fence_body("### A heading\n\nno fence here at all\n", "a test fixture")
    assert excinfo.type is not AssertionError
