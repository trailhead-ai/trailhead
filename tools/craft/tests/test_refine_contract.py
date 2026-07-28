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
    assert "read the" in text.lower() and "code" in text.lower(), (
        "_shared/refine.md must run a code read as the first self-serve pass"
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


def test_unresolvable_citation_is_a_gap():
    assert "is a gap" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must route a citation that fails to resolve back through "
        "the gap path (escalate), not merely warn about it"
    )


# --- escalation: record, never invent; and never outlive the answer ---


def test_procedure_carries_the_escalation_heading():
    assert ESCALATION_HEADING in SHARED_REFINE.read_text(), (
        f"_shared/refine.md must name {ESCALATION_HEADING!r} verbatim — it is the "
        "canonical handle a later sweep or operator scan finds escalated drafts by"
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
