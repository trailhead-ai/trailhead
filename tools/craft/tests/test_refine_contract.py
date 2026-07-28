"""The refine procedure — promotion of a standalone task from `open` to `ready`.

Refine is the ritual that turns a captured, childless, parentless task into a leaf
an executor can pick up with nothing left to guess. Its whole value rests on a
handful of prose contracts that no type system can hold:

  - **Self-serve, but never inventive.** Refine fills gaps from the code and the
    vaults; a gap that survives both passes escalates rather than being guessed at.
  - **Citations are pointers, and pointers must resolve.** A payload citation is a
    `file:line`, a recorded decision, or a constraint the user stated — never an
    inlined code excerpt (the vault is git-backed; excerpts carry secrets out), and
    every one of them must mechanically resolve before a task may reach `ready`.
  - **The escalation heading is the discovery handle.** `## Refine — unresolved` is
    how any later scan finds a draft that stopped short, and it must not survive the
    promotion that answers it — a `ready` task carrying a stale `Route:` line is an
    authoritative-looking contradiction of its own payload.
  - **One payload shape, everywhere.** The bold labels come from `templates/task.md`
    verbatim, so a promoted standalone leaf and a planned child slice read the same
    to every downstream consumer.

The procedure lives once in `skills/_shared/refine.md`; `/craft:refine` is a thin
wrapper over it and execute runs it inline. The thinness guard below is what keeps
that single-source claim honest — a wrapper that starts re-inlining the procedure is
a second copy waiting to drift.
"""

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_REFINE = CRAFT / "skills" / "_shared" / "refine.md"
REFINE_SKILL = CRAFT / "skills" / "refine" / "SKILL.md"

# The escalation marker. Pinned as one literal (em dash included) because it is a
# discovery handle, not prose: a sweep greps for exactly this heading.
ESCALATION_HEADING = "## Refine — unresolved"

# The payload labels, copied from templates/task.md. Bold inline labels, NOT
# headings — a promoted standalone leaf must be indistinguishable from a planned
# child slice to anything reading the body.
PAYLOAD_LABELS = ["**Delivers:**", "**Test contract:**", "**Files:**"]


def test_shared_refine_procedure_ships():
    assert SHARED_REFINE.exists(), (
        f"Expected the single-source refine procedure at {SHARED_REFINE}"
    )


def test_refine_wrapper_skill_ships():
    assert REFINE_SKILL.exists(), f"Expected the /craft:refine wrapper at {REFINE_SKILL}"


# --- payload shape: templates/task.md verbatim ---


@pytest.mark.parametrize("label", PAYLOAD_LABELS)
def test_procedure_appends_the_template_payload_labels(label: str):
    """Refine writes the child-task template's bold labels, not a fourth spelling.

    A standalone leaf that spells its payload differently forces every downstream
    reader (execute, code-reviewer, drift-gate) to learn a second format.
    """
    assert label in SHARED_REFINE.read_text(), (
        f"_shared/refine.md must name the payload label {label!r} verbatim — the "
        "payload shape is templates/task.md's, reused exactly"
    )


def test_procedure_points_at_the_task_template():
    assert "${CLAUDE_PLUGIN_ROOT}/templates/task.md" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must resolve the payload template through the plugin-root "
        "variable — a bare relative path does not resolve at runtime"
    )


def test_procedure_appends_the_flow_out_checklist():
    """The standalone leaf is its own lifecycle handle, so it carries its own gate."""
    assert "## Flow-out" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must append the `## Flow-out` checklist — without a parent "
        "plan the leaf has nowhere else to carry the knowledge-flow-out gate"
    )


def test_flow_out_copy_names_its_canonical_source():
    """The three checklist items are inlined here and also live in templates/plan.md.

    Two copies with no stated authority drift silently: a reader who edits one has no
    way to know the other exists.
    """
    assert "canonical source for those three items" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must name `templates/plan.md` as the canonical source of "
        "the inlined `## Flow-out` items, so the copy is known to be a copy"
    )


# --- the gap definition and the self-serve resolution passes ---


def test_gap_is_defined_by_defensible_alternatives():
    assert "two or more materially different fills remain defensible" in (
        SHARED_REFINE.read_text()
    ), (
        "_shared/refine.md must define a gap mechanically — without the definition, "
        "'I could not fill this' becomes a matter of taste and refine escalates (or "
        "guesses) at whatever rate the dispatched agent feels like"
    )


def test_self_serve_pass_reads_code_and_searches_the_vault():
    text = SHARED_REFINE.read_text()
    assert "lore search" in text, (
        "_shared/refine.md must run a vault search as the second self-serve pass — a "
        "recorded decision that already settles the gap must not be re-asked"
    )
    assert "Read the touched code" in text, (
        "_shared/refine.md must run a code read as the first self-serve pass. Pinned on "
        "the pass's own phrasing: a looser 'read'+'code' probe is satisfied by half the "
        "document and would stay green with the pass deleted"
    )


def test_empty_search_result_is_not_proof_of_no_precedent():
    """A prescribed lookup returning nothing looks exactly like no prior art existing."""
    assert "An empty result is not proof" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must forbid treating an empty `lore search` as evidence "
        "that no precedent exists — one narrow query returning nothing is the most "
        "common way an agent talks itself into inventing an answer"
    )


# --- citations: pointers only, and they must resolve ---


def test_citations_are_pointers_never_excerpts():
    assert "pointers only — never inline code excerpts" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must keep the pointer-only citation rule verbatim — an "
        "inlined excerpt copies whatever the source line holds (secrets included) "
        "into a git-backed vault"
    )


def test_citation_rule_carries_all_three_arms():
    text = SHARED_REFINE.read_text()
    for arm in ("file:line", "recorded decision", "constraint stated by the user"):
        assert arm in text, (
            f"_shared/refine.md is missing the {arm!r} arm of the citation rule — it "
            "is planning's Given Axioms rule quoted in full, and the third arm is what "
            "makes an interactive answer citable"
        )


def test_every_citation_must_resolve_before_promotion():
    """The gate between a self-authored payload and unreviewed executor dispatch."""
    assert "must resolve before" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must gate promotion on mechanically resolving every "
        "citation (cited file exists with the line in range; cited record resolves). "
        "Without it, a fabricated pointer promotes exactly as smoothly as a real one"
    )


def test_user_stated_constraint_citations_have_a_defined_resolution():
    """Arm (c) has no external target, so "every citation must resolve" strands it.

    Read literally, a gate defining resolution only for `file:line` and `[[record]]`
    makes every interactively-answered question unresolvable — which routes it back to
    Step 2, then to escalation, defeating the interactive promote path entirely.
    """
    text = SHARED_REFINE.read_text()
    assert "is self-resolving" in text, (
        "_shared/refine.md's resolution gate must define the arm-(c) case: a "
        "user-stated constraint has no external target to check against"
    )
    assert "that record *is* the citation" in text, (
        "_shared/refine.md must say how an arm-(c) citation resolves — by the payload "
        "recording the question and the user's answer; a constraint cited with no such "
        "record written down still fails the gate"
    )


def test_unresolvable_citation_is_a_gap():
    assert "A citation that fails to resolve **is a gap**" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must route a citation that fails to resolve back through "
        "the gap path (escalate), not merely warn about it. Pinned on the whole "
        "sentence: a bare 'is a gap' probe is already satisfied by the unrelated "
        "empty-test-contract rule, so it would stay green with this step deleted"
    )


# --- escalation: record, never invent; and never outlive the answer ---


def test_procedure_carries_the_escalation_heading():
    assert ESCALATION_HEADING in SHARED_REFINE.read_text(), (
        f"_shared/refine.md must name {ESCALATION_HEADING!r} verbatim — it is the "
        "canonical handle a later sweep or operator scan finds escalated drafts by"
    )


def test_route_outcome_escalates_with_the_routing_question():
    """Step 1's outcome 3 lands in Step 5 too — not only a survived gap.

    A Step 5 that opens by describing survived gaps alone leaves the route outcome
    with no stated payload: the drafter has to invent what the Question field holds
    when nothing about the payload is actually unresolved.
    """
    assert "the **Question** field holds the routing question itself" in (
        SHARED_REFINE.read_text()
    ), (
        "_shared/refine.md's escalation step must say what a route outcome puts in the "
        "Question field — the routing question itself, not a payload gap"
    )


def test_route_line_is_optional_inside_the_escalation_template():
    """The fenced template is copied literally; an unconditional line gets emitted."""
    assert "[Route: /craft:plan" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must mark the `Route:` line optional *inside* the fenced "
        "escalation template (a bracketed placeholder), not only in the prose after "
        "it — a bare literal in the fence is emitted on every escalation, including "
        "the ordinary surviving-question ones the prose says to omit it for"
    )


def test_interactive_mode_covers_the_route_outcome():
    assert "the routing recommendation *is* the question" in SHARED_REFINE.read_text(), (
        "_shared/refine.md's interactive escalation must cover the route outcome too: "
        "present the routing recommendation as the question, and fall back to the "
        "unattended escalation (Route line included) on a defer"
    )


def test_escalation_carries_a_route_line():
    text = SHARED_REFINE.read_text()
    assert "Route: /craft:plan" in text, (
        "_shared/refine.md must specify the `Route: /craft:plan` line for a "
        "route-to-plan outcome — a bare 'needs planning' note is not actionable"
    )
    assert "/craft:brainstorm" in text, (
        "_shared/refine.md must offer /craft:brainstorm as the route when the "
        "what/why itself is unsettled, not only /craft:plan"
    )


def test_route_outcome_states_what_gets_written():
    """"Write the drafted partial payload" is ambiguous when nothing was unresolved.

    On a route outcome the payload is not a promotion candidate at all — it is context
    for whoever picks the work up in planning, and saying so stops a drafter from
    filling in fields to make the write look complete.
    """
    text = SHARED_REFINE.read_text()
    assert "not a promotion candidate" in text, (
        "_shared/refine.md must say a route outcome's payload is informational for the "
        "planner rather than a candidate for promotion"
    )


def test_unattended_escalation_never_invents():
    text = SHARED_REFINE.read_text()
    assert "Never invent" in text, (
        "_shared/refine.md must forbid inventing an answer in unattended mode — the "
        "whole safety case for dispatching refine as a subagent rests on it"
    )
    assert "status stays `open`" in text, (
        "_shared/refine.md must keep an escalated task at `open` — a partial payload "
        "on a `ready` task would reach executor dispatch"
    )


def test_operator_answer_slot_is_documented():
    """The `**Answer:**` line is how an operator answers an escalated question offline.

    Pinned as a single physical line in the source (lesson: phrase-pinned prose
    contracts break on line wraps), with the escalation heading spelled out exactly
    (em dash included) since a later refine run keys off both together.
    """
    text = SHARED_REFINE.read_text()
    assert (
        "adding a line beginning `**Answer:**` inside the `## Refine — unresolved` "
        "section" in text
    ), (
        "_shared/refine.md must document the `**Answer:**` slot: an operator answers "
        "an escalated question by adding a line beginning `**Answer:**` inside the "
        "`## Refine — unresolved` section"
    )
    assert "operator-stated, citable constraint" in text, (
        "_shared/refine.md must state that refine treats an `**Answer:**` line as an "
        "operator-stated, citable constraint (arm (c) of the citation rule)"
    )


def test_promotion_removes_the_escalation_section():
    assert f"On promotion, remove the `{ESCALATION_HEADING}` section" in (
        SHARED_REFINE.read_text()
    ), (
        "_shared/refine.md must remove the escalation section on promotion — a "
        "`ready` task carrying a stale `Route:` line contradicts its own payload with "
        "equal authority, and the reader cannot tell which one is current"
    )


def test_interactive_escalation_asks_one_question_at_a_time():
    text = SHARED_REFINE.read_text()
    assert "--interactive" in text, (
        "_shared/refine.md must name the `--interactive` flag — mode follows the "
        "caller, and the flag string is the seam between the wrapper, execute, and "
        "this procedure"
    )
    assert "one question at a time" in text, (
        "_shared/refine.md must keep interactive escalation to one question at a "
        "time — each answer can reshape the drafting that follows it"
    )
    assert "defer" in text.lower(), (
        "_shared/refine.md must define the defer response as falling back to the "
        "unattended escalation behavior, or a deferred question strands the run"
    )


# --- status gate ---


def test_blocked_status_is_never_flipped():
    assert "NEVER flip a `blocked` task's status" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must refuse to advance a `blocked` task — `blocked` "
        "encodes an external condition refine cannot observe, let alone clear"
    )


def test_task_with_children_is_refused_toward_planning():
    assert "any task with children — refuse and route to `/craft:plan`" in (
        SHARED_REFINE.read_text()
    ), (
        "_shared/refine.md must refuse a task that already has children and point at "
        "/craft:plan — refine promotes leaves, and a parent is a plan"
    )


def test_shape_refusals_are_a_mechanical_check():
    """Childless-and-parentless is refine's precondition, so it gets checked, not eyeballed.

    Mirrors execute's shape detection deliberately: same command, same sidecar key, so
    the two callers cannot disagree about what "standalone" means.
    """
    text = SHARED_REFINE.read_text()
    assert "lore task graph <name>" in text, (
        "_shared/refine.md must check for children mechanically (the same "
        "`lore task graph <name>` render execute's shape detection uses) — 'a task with "
        "children' is otherwise a judgment call made from the prose"
    )
    assert "carries a `parent` key" in text, (
        "_shared/refine.md must also refuse a task that already has a parent — refine "
        "promotes a *parentless* leaf, and a child slice's payload belongs to the "
        "parent plan; the sidecar's `parent` key is how that is detected"
    )


def test_parent_refusal_waits_for_the_parent_value_to_resolve():
    """An unresolvable `parent` value must never be redirected to.

    A refusal bullet that fires on the presence of the key alone sends the operator to
    re-root at a parent that does not exist — the mis-wired-edge case, whose whole
    point is that the value names nothing.
    """
    text = SHARED_REFINE.read_text()
    assert "do not redirect before the value resolves" in text, (
        "_shared/refine.md's parent-key refusal must be gated on resolving the value "
        "first — the disambiguation cannot sit after an unconditional refusal"
    )
    assert "never redirect to a parent that does not exist" in text, (
        "_shared/refine.md must state the unresolvable-parent outcome explicitly: "
        "report the suspected mis-wired edge, never redirect, never fall through to "
        "standalone"
    )


def test_shape_check_pins_the_same_parent_resolution_command_execute_does():
    """Two callers claiming to agree on "standalone" must agree on "resolves"."""
    text = SHARED_REFINE.read_text()
    command = "lore record show task/<parent-value>"
    assert command in text, (
        f"_shared/refine.md must name {command!r} — execute pins that exact command "
        "for resolving a `parent` value, and a shape check that only says 'resolve it' "
        "lets the two callers disagree about what resolution means"
    )
    execute_skill = CRAFT / "skills" / "execute" / "SKILL.md"
    assert command in execute_skill.read_text(), (
        f"execute/SKILL.md must keep {command!r} — _shared/refine.md mirrors it "
        "deliberately, so a rename in one file has to fail here rather than silently "
        "split the two shape checks"
    )


def test_no_size_gate():
    assert "No size gate" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must state that line counts do not decide leaf vs plan — "
        "the draft attempt is the whole bar"
    )


# --- idempotency and preservation ---


def test_rerefine_is_idempotent_on_labels_and_the_escalation_heading():
    text = SHARED_REFINE.read_text()
    assert "update in place" in text, (
        "_shared/refine.md must update existing payload sections in place on "
        "re-refine — a second appended set gives the reader two payloads and no rule "
        "for which wins"
    )
    assert "report the conflict" in text, (
        "_shared/refine.md must report (not silently resolve) a body that already "
        "carries two payload sets — guessing which is canonical is how the wrong one "
        "reaches an executor"
    )


def test_reescalation_clears_a_prior_operator_answer():
    """An updated-in-place escalation section must not keep the old answer.

    The answered predicate a sweep applies is "a `**Answer:**` line inside the
    section" — nothing about which question it answers. A re-escalation that
    writes a new question but leaves the previous answer in place therefore
    reads as already answered, and the task is dispatched again on every sweep
    with the churn guard doing nothing.
    """
    assert (
        "Re-escalation replaces the section's content entirely, including any prior "
        "`**Answer:**` line" in SHARED_REFINE.read_text()
    ), (
        "_shared/refine.md must state that re-escalation replaces the whole "
        "`## Refine — unresolved` section, prior `**Answer:**` line included — a "
        "stale answer makes a freshly re-escalated task read as answered"
    )


def test_append_never_overwrite():
    assert "Append, never overwrite" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must preserve the captured prose — it is the task's why, "
        "and refine has no mandate to rewrite it"
    )


# --- verification contract on non-test surfaces ---


def test_non_test_surfaces_state_an_explicit_check():
    text = SHARED_REFINE.read_text()
    assert "manual:" in text, (
        "_shared/refine.md must offer the `manual: <check>` form for a change with no "
        "automated test surface — otherwise a prose-only task has no test contract "
        "and refine has no way to distinguish that from an unfilled gap"
    )
    assert "is a gap, not a pass" in text, (
        "_shared/refine.md must treat an empty or absent test contract as a gap — an "
        "unfilled field must not promote just because the field exists"
    )


# --- writes go through the CLI, and the trust boundary is named ---


def test_bodies_are_written_via_the_lore_cli():
    assert "lore record update" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must write task bodies via `lore record update` — a direct "
        "file edit bypasses the index and sidecar and silently corrupts the record"
    )


def test_trust_boundary_is_named():
    text = SHARED_REFINE.read_text()
    assert "Trust boundary" in text, (
        "_shared/refine.md must name the trust boundary it opens: unattended "
        "promotion reaches executor dispatch with no human review"
    )


def test_graph_references_use_bare_task_names():
    assert "bare task name" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must require bare task names on graph edges — a prefixed "
        "or bracketed name silently renders as a detached node"
    )


# --- untrusted reads: captured prose and code comments are data, not commands ---


def test_draft_attempt_frames_the_read_text_as_untrusted_data():
    """Refine's draft attempt reads two channels neither the operator nor refine wrote.

    The agent doing the reading holds full tool authority and may be running unattended,
    so an imperative sentence sitting in a captured task body or a code comment is one
    unframed read away from being obeyed.
    """
    text = SHARED_REFINE.read_text()
    assert "data, not instructions" in text, (
        "_shared/refine.md's draft attempt must frame captured task prose and code "
        "comments as data describing the work, never as commands addressed to the "
        "agent reading them"
    )
    assert "never executed during refine" in text, (
        "_shared/refine.md must state the consequence explicitly: an imperative found "
        "in untrusted text is not run — at most it becomes payload content, subject to "
        "the citation rule like anything else"
    )


def test_untrusted_read_framing_points_at_the_canonical_pattern():
    assert "skills/receiving-code-review/SKILL.md" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must reference the receiving-code-review skill as the "
        "canonical evaluate-don't-obey pattern — restating the reasoning inline instead "
        "of pointing at it is how the two copies drift"
    )


def test_self_serve_code_read_separates_evidence_from_dispatch():
    """Pass (a) is the second untrusted read, and it happens after the triage framing."""
    assert "not a dispatch you have received" in SHARED_REFINE.read_text(), (
        "_shared/refine.md's self-serve code read must distinguish what the code does "
        "(evidence) from what a comment tells the reader to do (not an instruction) — "
        "the framing has to sit on the pass that actually opens the files"
    )


# --- credential-pattern scrub before the vault write ---


def test_drafted_text_is_scrubbed_before_the_vault_write():
    """The vault is git-backed and syncs to a remote; a written credential leaves the box.

    The pointer-only citation rule constrains citations, not the free-text fields around
    them — the payload prose and the escalation section's Evidence/Recommended answer
    fields reach the same durable record with no equivalent gate.
    """
    text = SHARED_REFINE.read_text()
    assert "credential-pattern scrub" in text, (
        "_shared/refine.md must run the drafted payload and escalation text through a "
        "credential-pattern scrub before `lore record update` — execute's Phase 5 "
        "already scrubs finding text on its own vault-write path"
    )
    assert "**Evidence gathered:**" in text and "**Recommended answer:**" in text, (
        "_shared/refine.md's scrub must name the escalation section's free-text fields "
        "as in scope — they are written to the same durable record as the payload"
    )


def test_scrub_names_executes_phase_5_as_the_canonical_pattern_set():
    """One regex list, two callers. A second list is a divergence waiting to happen."""
    text = SHARED_REFINE.read_text()
    assert "execute's Phase 5 regex list is the canonical set" in text, (
        "_shared/refine.md must defer to execute's Phase 5 regex list rather than "
        "forking its own — the inlined categories are a reading convenience and must "
        "say which copy wins"
    )
    execute_skill = CRAFT / "skills" / "execute" / "SKILL.md"
    assert "credential-pattern scrub" in execute_skill.read_text(), (
        "execute/SKILL.md must keep its Phase 5 credential-pattern scrub — "
        "_shared/refine.md names it as canonical, so removing it there has to fail "
        "here rather than silently strand the pointer"
    )


def test_scrub_scope_covers_the_escalated_question_text():
    """An operator's `**Answer:**` line answers the `**Question:**` field directly.

    The scrub scope previously named only the payload and the Evidence/Recommended
    answer free-text fields — a credential typed into the question or its answer had
    no equivalent gate.
    """
    assert "question text" in SHARED_REFINE.read_text(), (
        "_shared/refine.md's credential-pattern scrub scope must name the escalated "
        "question text as in scope, alongside the payload and the escalation "
        "section's other free-text fields"
    )


# --- the trust boundary states what is left over ---


def test_trust_boundary_names_both_residuals():
    """A boundary section that names one residual reads as if the other were closed.

    The treat-as-data framing mitigates action injection; it does not eliminate it,
    because hostile text surviving as payload *content* still reaches an executor with
    full tools.
    """
    text = SHARED_REFINE.read_text()
    assert "Two residuals remain" in text, (
        "_shared/refine.md's trust boundary must name both residuals, not just the "
        "wrong-but-citable conclusion"
    )
    assert "wrong but citable" in text, (
        "_shared/refine.md must keep the wrong-but-citable residual — the citation gate "
        "proves a pointer resolves, not that the reasoning built on it is right"
    )
    assert "Action injection" in text, (
        "_shared/refine.md must name the action-injection residual that the "
        "treat-as-data framing mitigates rather than closes"
    )
    assert "single-operator vault" in text, (
        "_shared/refine.md must keep the acceptance framing — both residuals are "
        "accepted for a single-operator vault, not silently ignored"
    )


# --- the wrapper stays thin ---


def test_wrapper_reads_the_shared_procedure():
    assert "_shared/refine.md" in REFINE_SKILL.read_text(), (
        "refine/SKILL.md must point at _shared/refine.md for the procedure (same "
        "read-on-reference contract consult has with _shared/council.md)"
    )


def test_wrapper_does_not_reinline_the_procedure():
    """The thinness guard: one copy of the procedure, in _shared.

    execute runs the same procedure inline. The moment the wrapper carries its own
    copy of the escalation contract, the two callers can disagree about what refine
    does — and the disagreement is invisible until a task promotes wrongly.
    """
    text = REFINE_SKILL.read_text()
    assert ESCALATION_HEADING not in text, (
        f"refine/SKILL.md re-inlines the escalation section ({ESCALATION_HEADING!r}), "
        "which belongs only in _shared/refine.md — duplication is how the wrapper and "
        "execute's inline path drift apart"
    )


def test_wrapper_defaults_to_unattended_and_offers_the_flag():
    text = REFINE_SKILL.read_text()
    assert "--interactive" in text, (
        "refine/SKILL.md must document the `--interactive` opt-in; the flag string "
        "must match the one _shared/refine.md branches on"
    )
    assert "unattended" in text.lower(), (
        "refine/SKILL.md must state that a bare invocation runs unattended — the "
        "default posture is what makes refine dispatchable as a subagent"
    )


def test_wrapper_names_the_review_before_dispatch_workaround():
    """There is no preview gate between an inline promote and executor dispatch."""
    assert "/craft:refine --interactive" in REFINE_SKILL.read_text(), (
        "refine/SKILL.md must name the review-before-dispatch workaround: run "
        "`/craft:refine --interactive` standalone first when you want to see the "
        "drafted payload before handing the task to execute"
    )
