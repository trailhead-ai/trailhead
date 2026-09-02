"""The distill ritual — backward distillation of completed specs into ADRs.

Distill is the final stage of the pipeline (brainstorm -> gauntlet ->
(slice -> plan -> execute -> review)* -> distill) and the sole writer of a spec's
completion edge (`planned -> complete` for pre-loop records, `ready -> complete` for
a spec the slice loop closed out). Everything that makes it safe is a prose contract no type system can hold, so
each one is pinned here as a literal phrase:

  - **Clustering happens before drafting.** M specs condense into N ADRs; a
    distiller that drafts per spec produces an ADR log shaped like the execution
    schedule rather than like the design.
  - **A cluster is dispositioned whole, or deferred whole.** One member still in
    flight defers the cluster; there are no partial ADRs, and a lone member of a
    larger cluster is never distilled on its own.
  - **The human gate reviews the write list, not the proposal.** After an `edit`
    disposition the final review renders the post-edit ADR body verbatim — an edit
    that never re-enters the reviewed list would land text into a record that
    convention then forbids fixing.
  - **The write order is fixed**, because each step's failure mode is different and
    a half-written cluster has to be resumable. Supersession's two edge directions
    are two CLI writes, not a transaction, so their internal order is pinned too and
    the interruption between them has a named repair.
  - **Terminal outcomes are annotations**, so nothing re-queues forever. The exact
    spellings are the queue's exclusion key — a typo silently re-queues the cluster.

The queue itself is pinned because it is computable and easy to get subtly wrong:
annotations are never indexed, so the exclusions cannot ride the KQL query and must
be re-checked per candidate.
"""

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
DISTILL = CRAFT / "skills" / "distill" / "SKILL.md"


def _text() -> str:
    return DISTILL.read_text()


def test_distill_skill_ships():
    assert DISTILL.exists(), f"Expected the /craft:distill skill at {DISTILL}"


def test_distill_is_the_final_pipeline_stage():
    assert "brainstorm → gauntlet → (slice → plan → execute → review)* → distill" in _text(), (
        "distill/SKILL.md must place itself at the end of the named pipeline — the "
        "bracketed loop repeats once per slice, and the ritual only makes sense as "
        "the stage that runs after the loop reports the spec closed out"
    )


# --- the ADR-worthiness bar ---

_WORTHINESS_TEST = (
    "would a future implementer working in this area do something wrong or "
    "wasteful without knowing this?"
)


def test_worthiness_test_is_stated():
    """The one sentence that decides what becomes an ADR and what does not.

    Without it the bar is taste, and a sweep over sixty specs produces sixty ADRs.
    """
    assert _WORTHINESS_TEST in _text(), (
        "distill/SKILL.md must state the ADR-worthiness test verbatim"
    )


def test_re_derivable_content_does_not_qualify():
    assert "re-derivable from the code does not qualify" in _text(), (
        "distill/SKILL.md must exclude content a reader could re-derive from the "
        "code — the worthiness test alone reads as a low bar without it"
    )


# --- clustering before drafting ---


def test_clustering_precedes_drafting():
    text = _text()
    assert "Cluster before drafting" in text, (
        "distill/SKILL.md must run clustering as its own step before any ADR is "
        "drafted — drafting per spec yields an ADR log shaped like the execution "
        "schedule, not like the design"
    )
    assert "M specs ↔ N ADRs" in text, (
        "distill/SKILL.md must state the many-to-many relation explicitly; a "
        "one-spec-one-ADR reading is the default an agent falls into"
    )


def test_lingering_draft_adrs_are_candidate_material():
    assert "candidate material" in _text(), (
        "distill/SKILL.md must surface lingering `draft` ADRs touching the "
        "cluster's areas as candidate material — otherwise a distillation "
        "re-decides something already half-written"
    )


def test_superseded_specs_enter_only_as_chain_context():
    assert "only as chain context" in _text(), (
        "distill/SKILL.md must state that superseded specs are never distilled "
        "individually — they enter a cluster as context for the surviving spec"
    )


# --- the deferral rule ---


def test_a_cluster_with_a_member_in_flight_is_deferred_whole():
    text = _text()
    assert "any member still in flight is deferred whole" in text, (
        "distill/SKILL.md must pin the deferral rule — a cluster is distilled whole "
        "or not at all"
    )
    assert "no partial ADRs" in text, (
        "distill/SKILL.md must say the deferral produces no partial ADRs; a "
        "partially-distilled cluster writes an immutable record of half a decision"
    )


def test_a_lone_member_of_a_larger_cluster_is_never_distilled():
    assert "never distills a lone member of a larger cluster" in _text(), (
        "distill/SKILL.md must forbid distilling one spec of a larger cluster — "
        "the targeted invocation is the obvious way to do exactly that by accident"
    )


# --- the sweep queue ---


def test_sweep_queue_enumerates_planned_specs_via_kql():
    assert "kind:spec status:planned" in _text(), (
        "distill/SKILL.md must name the KQL query that enumerates the sweep queue"
    )


def test_sweep_queue_carries_the_one_time_migration_cohort():
    text = _text()
    assert "one-time migration" in text, (
        "distill/SKILL.md must include the one-time migration cohort — pre-contract "
        "`complete` specs predate the ritual and would otherwise never be distilled"
    )
    assert "kind:spec status:complete" in text, (
        "distill/SKILL.md must name the query behind the migration cohort, not just "
        "describe it in prose"
    )


def test_exclusions_are_checked_per_candidate_not_in_the_query():
    """The subtlest trap in the queue: the exclusion key is not indexed.

    A `distilled=` annotation lives in the sidecar and never reaches the index, so
    a KQL filter on it silently matches nothing and the whole corpus re-queues.
    """
    text = _text()
    assert "annotations are never indexed, so this cannot be a KQL filter" in text, (
        "distill/SKILL.md must state why the exclusions cannot ride the query — an "
        "agent that assumes they can writes a filter that silently matches nothing"
    )
    assert "lore record show <spec-id> --json" in text, (
        "distill/SKILL.md must name the per-candidate check command that applies "
        "the exclusions"
    )
    assert "A spec is out of the queue if it already carries **a `distilled=` annotation**" in text, (
        "distill/SKILL.md must name the annotation exclusion condition — a `distilled=` "
        "annotation"
    )
    assert "or a `related: adr`\nedge" in text, (
        "distill/SKILL.md must name a SECOND exclusion key — a `related: adr` edge. "
        "distill's own writes never land spec-side (step 1 writes the provenance edge "
        "on the ADR, never the spec), but brainstorm's altitude gate creates every "
        "forward-derived spec with `--related adr=<adr-id>` from birth, so that edge "
        "can and does appear spec-side and must also exclude the candidate"
    )
    assert "distilled=adr" in text, (
        "distill/SKILL.md must stamp the ADRs-written outcome with its own "
        "`distilled=adr` annotation, parallel to `distilled=zero-adr` and "
        "`distilled=rejected` — otherwise an ADRs-written cluster never carries an "
        "exclusion key and re-enters the queue forever"
    )


def test_task_trees_resolve_through_the_related_spec_facet_and_the_task_graph():
    text = _text()
    assert "kind:task related-spec:<name>" in text, (
        "distill/SKILL.md must resolve a spec's task tree through the forward "
        "`related-spec` facet — the supported read path for that edge"
    )
    assert "lore task graph" in text, (
        "distill/SKILL.md must expand the matched tasks through `lore task graph`; "
        "the facet returns the linked task, not its whole tree"
    )


_ZERO_TASK_EDGES_MESSAGE = "no task edges — link tasks or distill by target"


def test_a_spec_with_zero_task_edges_is_deferred_with_the_named_message():
    text = _text()
    assert _ZERO_TASK_EDGES_MESSAGE in text, (
        "distill/SKILL.md must report the zero-task-edges deferral with this exact "
        "message — it names both remedies, so the operator is not left guessing "
        "whether the spec is broken or merely unlinked"
    )
    assert "deferred, not queued" in text, (
        "a spec with no task edges must be deferred rather than distilled on prose "
        "alone — the edges are what make the cluster resolvable"
    )


def test_the_plan_task_discriminator_is_named_as_a_thin_spot():
    """Known and deliberately unsolved: nothing marks which task is the plan task.

    The skill must say so, so a distiller does not quietly invent a discriminator
    and present its guess as resolution.
    """
    assert "thin spot" in _text(), (
        "distill/SKILL.md must carry the plan-task discriminator as a stated thin "
        "spot backstopped by the human gate, not paper over it"
    )


# --- targeted mode ---


def test_targeted_mode_expands_to_the_full_cluster():
    assert "expands to the spec's full cluster" in _text(), (
        "distill/SKILL.md must expand a targeted invocation to the whole cluster — "
        "the argument selects an entry point, not the unit of work"
    )


def test_targeted_mode_is_the_sole_re_open_path_for_a_rejected_cluster():
    text = _text()
    assert "only way to re-open" in text, (
        "distill/SKILL.md must name targeted mode as the sole re-open path for a "
        "`distilled=rejected` cluster — the sweep excludes it by design"
    )


# --- the human-gated disposition ---


@pytest.mark.parametrize("verb", ["approve", "edit", "reject"])
def test_disposition_verbs_pinned(verb: str):
    """The disposition set is behavior, not prose — a missing verb removes an outcome."""
    assert f"`{verb}`" in _text(), (
        f"disposition verb `{verb}` missing from distill/SKILL.md"
    )


def test_nothing_is_written_before_disposition():
    assert "No write happens before disposition" in _text(), (
        "distill/SKILL.md must state that every write waits on the disposition — "
        "an ADR written first and dispositioned after is immutable and wrong"
    )


def test_the_operator_reviews_the_actual_write_list():
    assert "the actual write list" in _text(), (
        "distill/SKILL.md must put the real write list in front of the operator, "
        "not just the proposal prose — the two diverge exactly where it matters"
    )


_POST_EDIT_VERBATIM_RULE = (
    "renders the post-edit ADR body verbatim, never proposal prose"
)


def test_an_edit_re_enters_the_reviewed_write_list_verbatim():
    """The gap an `edit` disposition opens, and the rule that closes it.

    Without this the edited text is confirmed only as a proposal, and the ADR that
    lands carries the pre-edit body — into a record convention then forbids fixing.
    """
    text = _text()
    assert _POST_EDIT_VERBATIM_RULE in text, (
        "distill/SKILL.md must pin the post-edit verbatim-render rule"
    )
    assert "the last thing the operator confirms before any write" in text, (
        "the post-edit render must be the final confirmation, not an intermediate "
        "step some later revision can slip behind"
    )


# --- the fixed write order ---

# Each phrase appears exactly once in the skill, in this order. The sequence IS the
# contract: ADRs first so a resumed run can detect them, spec `complete` last so an
# interruption never leaves a spec claiming a distillation that did not finish.
_WRITE_ORDER: list[str] = [
    "ADR records are created first",
    "absorbed `decision` records are flipped `superseded` with a `related: adr=` edge",
    "superseded ADRs are flipped, both edge directions written",
    "touched `area` profiles are re-synthesized",
    "member-spec `complete` flips land last",
]


@pytest.mark.parametrize("phrase", _WRITE_ORDER)
def test_write_order_step_is_stated(phrase: str):
    assert phrase in _text(), (
        f"distill/SKILL.md is missing the write-order step {phrase!r}"
    )


def test_write_order_steps_appear_in_sequence():
    """Order, not presence, is what makes an interrupted run recoverable."""
    text = _text()
    positions = [text.find(p) for p in _WRITE_ORDER]
    assert all(p >= 0 for p in positions), (
        "every write-order step must be present before its ordering can be pinned"
    )
    assert positions == sorted(positions), (
        "distill/SKILL.md states the write-order steps out of sequence: "
        f"{[(p, i) for p, i in zip(_WRITE_ORDER, positions)]}. The order is the "
        "contract — a reader follows the document top to bottom"
    )


# --- supersession: two writes, not a transaction ---

_SUPERSESSION_INTERNAL_ORDER = (
    "the successor's `related: adr=<predecessor>` edge is written at ADR creation; "
    "the predecessor's `superseded` flip and its `related: adr=<successor>` "
    "back-edge are written second"
)

_SUPERSESSION_REPAIR = (
    "An interruption between the two is healed by the resume rule: the re-run "
    "detects the successor's edge and completes the predecessor's write"
)


def test_supersession_pins_its_internal_write_order():
    assert _SUPERSESSION_INTERNAL_ORDER in _text(), (
        "distill/SKILL.md must pin which of supersession's two edge writes goes "
        "first — they are two CLI calls, not a transaction, and only one ordering "
        "leaves a recoverable intermediate state"
    )


def test_supersession_interruption_has_a_named_repair():
    text = _text()
    assert _SUPERSESSION_REPAIR in text, (
        "distill/SKILL.md must name the repair for a crash between the two edge "
        "writes — without it the operator's only options are an inconsistent pair "
        "or a convention violation"
    )
    assert "licensed as part of the supersession exception" in text, (
        "the repair writes metadata onto an `active` ADR, so the skill must license "
        "it explicitly against the immutability convention rather than leaving the "
        "reader to decide whether the rule applies"
    )


def test_supersession_is_named_as_the_sole_immutability_exception():
    assert "sole metadata exception to active-ADR immutability" in _text(), (
        "distill/SKILL.md must name the bidirectional supersession edge as the only "
        "exception — an unnamed exception generalizes into a second one"
    )


# --- area re-synthesis ---


def test_area_re_synthesis_is_scoped_and_preserves_the_lead_line():
    text = _text()
    assert "lore record update --diff" in text, (
        "distill/SKILL.md must re-synthesize area profiles with a scoped `--diff` "
        "update — a full-body replace on a hand-maintained profile discards "
        "everything the distillation did not look at"
    )
    assert "templates/area.md" in text, (
        "distill/SKILL.md must bring a touched profile to the area template's "
        "sections — distill is the named writer of that contract"
    )
    assert "preserving the frontmatter and the `## Overview` lead line" in text, (
        "distill/SKILL.md must preserve the frontmatter and the `## Overview` lead "
        "line — the area map's one-liner extraction reads them"
    )


def test_each_diff_is_verified_by_re_reading_the_body():
    assert "verify each diff applied by re-reading the body" in _text(), (
        "distill/SKILL.md must verify every `--diff` update landed — a diff that "
        "fails to apply reports no error the caller notices, and the profile then "
        "silently disagrees with the ADR that was just written"
    )


# --- terminal outcomes ---


@pytest.mark.parametrize(
    "annotation",
    ["distilled=zero-adr", "distilled=rejected"],
)
def test_terminal_annotation_spellings_pinned(annotation: str):
    """The annotation string is the queue's exclusion key, so its spelling is behavior.

    A typo here does not fail — it re-queues the cluster on every sweep, forever.
    """
    assert annotation in _text(), (
        f"distill/SKILL.md must spell the terminal annotation {annotation!r} exactly"
    )


def test_the_annotation_vocabulary_is_exactly_three_values():
    """Derived from the document, not compared against a hardcoded list — a value
    added or left behind later fails this pin rather than silently passing.
    """
    values = set(re.findall(r"distilled=([a-z][a-z-]*)", _text()))
    assert values == {"adr", "zero-adr", "rejected"}, (
        "distill/SKILL.md's documented `distilled=` vocabulary must be exactly "
        f"{{'adr', 'zero-adr', 'rejected'}}, got {values!r} — the forward-anchored "
        "cluster class is gone, and its `distilled=forward-anchored` value must "
        "not survive it in either direction"
    )


def test_zero_adr_outcome_still_completes_the_member_specs():
    assert "zero ADRs, because" in _text(), (
        "distill/SKILL.md must make the zero-ADR verdict an explicit, stated "
        "outcome — a cluster that yields nothing worth recording is a success, and "
        "its members still reach `complete`"
    )


def test_a_rejected_cluster_leaves_member_status_untouched():
    assert "leaves their status untouched" in _text(), (
        "a rejected cluster must not advance its members — `complete` means "
        "distilled, and a rejection is the opposite of that"
    )


# --- resume ---


def test_a_re_run_detects_the_existing_cluster_adr_rather_than_drafting_a_second():
    text = _text()
    assert "detect the existing cluster ADR via the forward `related-spec` facet" in text, (
        "distill/SKILL.md must detect an interrupted run's ADR before drafting — a "
        "second draft burns a second sequence number and splits one decision across "
        "two immutable records"
    )
    assert 'lore search "kind:adr related-spec:<spec-name>"' in text, (
        "distill/SKILL.md must name the concrete resume-detection query — the spec's "
        "own sidecar carries nothing, since step 1 writes the provenance edge on the "
        "ADR, not the spec, so resume must read the ADR side via the forward facet"
    )
    assert "completes the remaining writes" in text, (
        "the resumed run must finish the interrupted write order, not restart it"
    )


# --- CLI-only vault access, explicit scope ---


def test_all_vault_access_goes_through_the_lore_cli():
    text = _text()
    assert "Never read, glob, or edit a vault file directly" in text, (
        "distill/SKILL.md must route every vault read and write through the `lore` "
        "CLI — this ritual touches more records in one sitting than any other, so a "
        "direct write here corrupts the most"
    )


def test_every_batch_update_passes_vault_explicitly():
    text = _text()
    assert "Every batch update passes `--vault <name>` explicitly" in text, (
        "distill/SKILL.md must mandate explicit `--vault` on every batch update — "
        "`--product`/`--team` is destination re-routing on a create, not current-"
        "location disambiguation on an update"
    )
    assert "config order" in text, (
        "the mandate needs its reason stated — record ops locate by a config-order "
        "scan, so an unscoped update in a multi-vault install lands wherever the "
        "scan happens to hit first"
    )




# --- Slice 4: gauntlet stops flipping, distill activates on the last derived spec ---

import re  # noqa: E402

GAUNTLET = CRAFT / "skills" / "gauntlet" / "SKILL.md"

_ABSORPTION_EXCLUSION_SENTENCE = (
    "This surfacing excludes a `draft` ADR while any spec carrying a "
    "`related: adr=` edge to it has not yet reached a terminal status."
)


def _flat(text: str) -> str:
    """Whitespace-collapsed prose, so a pinned sentence survives a line wrap."""
    return " ".join(text.split())


def _section(text: str, header: str, stop: str, why: str = "") -> str:
    """One section of distill/SKILL.md, from *header* up to *stop*.

    Every section pin below scopes itself this way rather than matching the whole
    file: the skill states neighbouring rules in near-identical prose, so a
    whole-file substring check passes on a sibling section's wording and pins
    nothing. Both bounds are located with ``index``, so a missing *stop* raises
    rather than silently widening the slice to the end of the file.
    """
    assert header in text, why or f"distill/SKILL.md must carry a {header!r} section"
    start = text.index(header)
    return text[start:text.index(stop, start)]


def _write_order_section(text: str) -> str:
    return _section(
        text,
        "## Step 4 — Write, in a fixed order",
        "## Terminal outcomes",
        why="distill/SKILL.md must carry write-order step 4",
    )


def _step_1(text: str) -> str:
    return _section(
        text,
        "1. **ADR records are created first.**",
        "2. Then **absorbed",
        why="distill/SKILL.md must carry write-order step 1",
    )


def test_the_write_order_terminates_at_the_completion_flip():
    """The write order's terminal step is the member-spec completion flip, pinned
    against the document's own enumerated steps — so a step appended after it,
    such as the removed activation check, fails this pin.
    """
    order = _write_order_section(_text())
    step_markers = re.findall(r"(?m)^(\d+)\.\s", order)
    assert step_markers == [str(n) for n in range(1, len(step_markers) + 1)], (
        f"distill/SKILL.md's write order must enumerate a contiguous 1..N "
        f"sequence with no gap or extra step; got {step_markers!r}"
    )
    last_step_start = order.rindex(f"\n{step_markers[-1]}. ")
    last_step = order[last_step_start:]
    assert "member-spec `complete` flips land last" in last_step, (
        "distill/SKILL.md's last enumerated write-order step must be the "
        "member-spec completion flip — nothing follows it"
    )


def test_the_step_1_create_is_distills_only_route_to_active():
    """Distill's only `--status active` ADR write is the create in write-order
    step 1, reachable at authorship with no prior condition on derived specs —
    the activation check that used to gate a later `active` write is gone.
    """
    text = _text()
    assert text.count("--status active") == 1, (
        "distill/SKILL.md must carry exactly one `--status active` write — the "
        "create in write-order step 1; a second occurrence means another route "
        "to `active` survived"
    )
    step_1 = _flat(_step_1(text))
    assert "--status active" in step_1, (
        "the sole `--status active` write must live in write-order step 1's ADR "
        "create"
    )
    assert "distill's only route to `active`" in step_1, (
        "step 1 must state explicitly that this create is distill's only route "
        "to `active`"
    )
    assert "reachable at authorship" in step_1 and "no condition on the status of the specs" in step_1, (
        "step 1 must state the create is reachable at authorship, with no prior "
        "condition on the derived specs' status"
    )


def _absorption_sweep_section(text: str) -> str:
    """Just the absorption-sweep exclusion, bounded before the deferral rule —
    the neighbouring section states the same edge in the same vocabulary, so a
    wider slice would pin nothing.
    """
    return _section(
        text,
        "**Lingering `draft` ADRs",
        "### The deferral rule",
        why=(
            "distill/SKILL.md must carry the absorption-sweep exclusion in 'Step 1 "
            "— Cluster before drafting'"
        ),
    )


def test_absorption_sweep_exclusion_is_a_pinned_procedural_step():
    assert _ABSORPTION_EXCLUSION_SENTENCE in _flat(
        _absorption_sweep_section(_text())
    ), (
        "distill/SKILL.md must state the absorption-sweep's exclusion for drafts "
        "with incomplete derived specs as a concrete, pinned procedural step"
    )


def test_status_active_write_is_scoped_to_the_step_1_create_not_banned_globally():
    """distill legitimately emits `--status active` on the step-1 create — the
    absence sweep must not ban the string globally, only from the gauntlet
    skill, which no longer advances an adr past `draft` at all.
    """
    assert "lore record create --kind adr --status active" in _text(), (
        "distill/SKILL.md must legitimately carry the step-1 ADR-creation write "
        "— a global ban on this string would be wrong, not a fix"
    )
    assert not re.compile(r"<adr-id>\s+--status\s+active").search(GAUNTLET.read_text()), (
        "gauntlet/SKILL.md must not carry an adr-activation write — the sweep is "
        "scoped to the gauntlet skill specifically, not the string everywhere"
    )


# --- Task 3: the absorption-sweep exclusion stands on its own justification ---


def test_absorption_sweep_exclusion_keys_on_the_specs_carrying_the_edge():
    """The exclusion's condition itself: it fires on specs that carry the
    `related: adr=` edge TO the draft ADR, resolved spec-side off the forward
    facet — the direction that makes the exclusion satisfiable at all. This
    must survive the activation check's removal untouched: it never depended on
    activation to be true.
    """
    section = _flat(_absorption_sweep_section(_text()))
    assert _ABSORPTION_EXCLUSION_SENTENCE in section, (
        "distill/SKILL.md's absorption-sweep exclusion must still key on a spec "
        "carrying a `related: adr=` edge to the draft ADR"
    )
    assert 'lore search "kind:spec related-adr:<adr-id>"' in section, (
        "distill/SKILL.md must resolve the exclusion's specs spec-side, off the "
        "forward `related-spec` facet — not by querying the ADR"
    )


def test_absorption_sweep_exclusion_reads_terminality_off_terminal_spec_statuses():
    """The exclusion's terminality check: the same `TERMINAL_SPEC_STATUSES` set
    the rest of distill and `pipeline/derive.py` use, cited by file:line so drift
    there is not silent to this skill.
    """
    section = _flat(_absorption_sweep_section(_text()))
    assert (
        'TERMINAL_SPEC_STATUSES = {"complete", "superseded", "dropped"}'
        in section
    ), (
        "distill/SKILL.md's absorption-sweep exclusion must read terminality off "
        "the literal `TERMINAL_SPEC_STATUSES` set"
    )
    assert "pipeline/derive.py:97" in section, (
        "distill/SKILL.md must cite where `TERMINAL_SPEC_STATUSES` is defined, so "
        "drift there is not silent to this skill"
    )


def test_absorption_sweep_rationale_stands_without_an_activation_reference():
    """The exclusion's justification, re-anchored: it protects decision context
    unlanded sibling specs are still relying on. Pinned on the rationale it DOES
    state — never on the absence of the deleted activation check.
    """
    section = _flat(_absorption_sweep_section(_text()))
    assert (
        "protects decision context those unlanded specs are still relying on"
        in section
    ), (
        "distill/SKILL.md must justify the absorption-sweep exclusion on its own "
        "terms — the decision context unlanded sibling specs still rely on — "
        "rather than by reference to the deleted activation check"
    )
    assert "a spec carries `related: adr=<adr-id>` when it descends from that decision" in section, (
        "distill/SKILL.md must describe the `related: adr=` edge as something a "
        "spec carries, not as something brainstorm's altitude gate writes — that "
        "gate no longer creates ADRs or derived specs at all"
    )


def test_lingering_draft_surfacing_also_checks_cluster_members_related_adr_edges():
    """Absorption surfacing must be keyed on the `related: adr=` edge, not left to
    area overlap alone — otherwise the six specs descended from the one in-flight
    ADR could land without ever surfacing it as candidate material, and the
    disposition this spec promises (absorbed by the backward pass) never fires.
    """
    section = _flat(_absorption_sweep_section(_text()))
    assert (
        "an ADR any cluster member carries a `related: adr=` edge to" in section
    ), (
        "distill/SKILL.md must surface a lingering `draft` ADR as candidate "
        "material whenever a cluster member carries a `related: adr=` edge to "
        "it, not only on area overlap"
    )
    assert "touching the cluster's areas" in section, (
        "distill/SKILL.md must keep area overlap as an additional surfacing "
        "trigger — the edge check adds to it, it does not replace it"
    )


# --- Slice 6: the forward-anchored cluster class collapses into the ordinary path ---


def _queue_exclusion_section(text: str) -> str:
    """Just §2 of the sweep queue — the per-candidate exclusion rule itself."""
    return _section(
        text,
        "### 2. Apply the exclusion per candidate",
        "### 3. Resolve each surviving spec's task tree",
    )


def _proposal_step(text: str) -> str:
    return _section(text, "## Step 2 — Draft the proposal", "## Step 3 — Disposition")


def _outcome_bullet(text: str, label: str) -> str:
    """One bullet of '## Terminal outcomes', so an assertion cannot pass on a
    sibling outcome's prose.
    """
    outcomes = _section(text, "## Terminal outcomes", "## Resuming an interrupted run")
    start = outcomes.index(f"- **{label}**")
    nxt = outcomes.find("\n- **", start + 1)
    return outcomes[start:] if nxt == -1 else outcomes[start:nxt]


def test_step_2s_zero_adr_verdict_has_one_form_with_no_anchoring_adr_id_variant():
    """The forward-anchored cluster class is gone, and with it the second,
    anchoring-ADR-id-naming form of the zero-ADR verdict it required. There is
    exactly one verdict shape now.
    """
    step = _flat(_proposal_step(_text()))
    assert (
        "the explicit verdict **zero ADRs, because …**, which is a real outcome "
        "and not a failure" in step
    ), "Step 2 must state the single zero-ADR verdict form"
    assert step.count("zero ADRs, because") == 1, (
        "Step 2 must state exactly one zero-ADR verdict shape — a second, "
        "anchoring-ADR-id-naming variant would mean the forward-anchored routing "
        "this slice removes had survived"
    )


def test_the_queue_exclusion_imperative_carries_the_anchor_status_narrowing():
    """The operative sentence must state the narrowed rule itself.

    "drop the candidate on either hit" is the imperative a reader acts on; a
    correction that arrives in a later paragraph arrives after the drop.
    """
    flat = _flat(_queue_exclusion_section(_text()))
    assert (
        "drop the candidate on an annotation hit, or on a `related: adr` edge "
        "whose anchoring ADR has itself reached `active` or a terminal status" in flat
    ), (
        "§2's operative imperative must carry the anchor-status narrowing in the "
        "same sentence — stating the un-narrowed rule and correcting it later "
        "drops in-flight forward-anchored specs out of the queue"
    )
    assert "never on a bare edge whose anchor is still `draft`" in flat, (
        "the imperative must name the case it must NOT drop on — the whole point "
        "of the narrowing is the candidate it keeps"
    )
    assert "drop the candidate on either hit" not in flat, (
        "the un-narrowed imperative must be gone, not merely followed by a "
        "correction"
    )


def test_the_queue_keeps_specs_whose_anchoring_adr_is_still_draft_and_treats_them_ordinarily():
    """The forward-anchored cluster class is gone: a spec whose anchoring ADR is
    still `draft` is no longer routed anywhere special. §2 still narrows the
    exclusion (a blanket exclusion would hold such a spec at `planned` forever
    with no way back into the queue), but what it states now is that the
    candidate is treated like any other — no forward-anchored class, no
    activation cross-reference.
    """
    section = _queue_exclusion_section(_text())
    flat = _flat(section)
    assert "edge whose anchoring ADR has itself reached `active` or a terminal status" in flat, (
        "§2's `related: adr` exclusion must be narrowed to anchoring ADRs that are "
        "`active` or terminal — a blanket exclusion would strand every candidate "
        "whose anchor is still `draft`"
    )
    assert "stays in the queue" in flat and "still `draft`" in flat, (
        "§2 must say what happens to a spec whose anchoring ADR is still `draft` — "
        "it stays in the queue"
    )
    assert (
        "it clusters normally and takes the ordinary drafting path, exactly like "
        "a candidate with no anchor at all" in flat
    ), (
        "§2 must state the ordinary-path outcome explicitly — a spec kept in the "
        "queue must not be left to route somewhere the deleted forward-anchored "
        "class used to send it"
    )


# --- the slice loop's terminus: queueing and completing a loop-closed spec ---


def test_sweep_queue_enumerates_loop_closed_ready_specs():
    """A spec the slice loop closed out never reaches `planned`, so the `planned`
    queue alone cannot see it.

    The loop holds a spec at `ready` from the gauntlet until distill completes it
    (`spec/specs-are-delivered-one-vertical-slice-at-a-time`). Without this query
    every loop-driven spec is invisible to distill forever and the pipeline has no
    terminus. `has:label.` is the presence form, so it matches both values the
    marker takes — narrowing it to one value would strand the other.
    """
    flat = _flat(_text())
    assert "kind:spec status:ready has:label.craft.slice-loop" in flat, (
        "distill/SKILL.md's sweep queue must name the KQL query that enumerates "
        "specs the slice loop closed out — the `has:label.` presence form, so it "
        "matches both `complete` and `stopped`"
    )


def test_the_loop_closed_query_does_not_displace_the_planned_cohort():
    """Records already carrying `planned` must stay reachable."""
    assert "kind:spec status:planned" in _text(), (
        "distill/SKILL.md must keep enumerating `planned` specs — the loop-closed "
        "query is an addition, not a replacement, or every pre-loop record is "
        "stranded"
    )


def _completion_gate(text: str) -> str:
    """Step 5's member-completion write, up to the Terminal outcomes section.

    Scoped rather than whole-file: step 1's prose names both marker values too, so
    a whole-file check passes even with this gate deleted outright.
    """
    return _section(
        text,
        "5. Finally, **member-spec `complete` flips land last**",
        "## Terminal outcomes",
        why="distill/SKILL.md must carry step 5's member-completion write",
    )


def test_the_completion_flip_is_conditioned_on_the_complete_marker_value():
    """`stopped` and `complete` are not the same outcome and must not share a fate.

    The queue matches both marker values, but only one of them means the spec's
    acceptance criteria were met. Flipping a `stopped` spec to `complete` is
    irreversible in practice: `/craft:slice`'s own guard then refuses to select
    against a `complete` spec, and its stated remedy is to start a new spec.
    """
    gate = _flat(_completion_gate(_text()))
    assert "craft/slice-loop=complete" in gate, (
        "distill/SKILL.md's completion gate must name the marker value it accepts "
        "— `craft/slice-loop=complete` — not merely the presence of the label"
    )
    assert "craft/slice-loop=stopped" in gate, (
        "distill/SKILL.md must name the `stopped` marker value it deliberately "
        "does not complete, or a reader cannot tell the exclusion is intentional"
    )
    assert "Two spec shapes count as closed out, and no others" in gate, (
        "the gate must state that its accepted shapes are exhaustive — an open "
        "list would let a third shape be read in later"
    )


def test_a_stopped_spec_is_distilled_without_being_completed():
    flat = _flat(_text())
    assert "is distilled but is not flipped `complete`" in flat, (
        "distill/SKILL.md must state the `stopped` outcome plainly: such a spec is "
        "distilled and annotated, but keeps its `ready` status"
    )
    assert "stop-reason" in flat, (
        "a `stopped` spec must surface its recorded stop reason in the write list, "
        "so closing one out is a visible choice rather than a side effect of "
        "generic disposition"
    )


def test_the_completion_write_re_reads_the_spec_immediately_before_writing():
    """The marker is checked at queue-build; a human dispositions; then the write
    lands. `/craft:slice` clears and re-asserts `craft/slice-loop` on every
    re-entry, so without a re-check an operator adding a slice during that gap
    leaves a `complete` spec with an `in-progress` slice orphaned beneath it.
    """
    flat = _flat(_text())
    assert "Re-read the spec and re-check the marker immediately before" in flat, (
        "distill/SKILL.md's completion write must re-read the spec immediately "
        "before the flip — the same discipline `/craft:slice` applies to its own "
        "spec writes — rather than trusting a marker read at queue-build time"
    )


def test_the_queue_exclusion_stays_decoupled_from_the_planned_status():
    """§2's `related: adr` exclusion keys off the edge and terminal statuses,
    never `planned`. Pinning that keeps a future edit from quietly coupling it to
    a status the slice loop leaves unused.
    """
    flat = _flat(_queue_exclusion_section(_text()))
    assert "related: adr" in flat, (
        "§2's exclusion must be defined by the `related: adr` edge, not by a "
        "spec status"
    )
    assert "status:planned" not in flat, (
        "§2's exclusion must not key off `planned` — that status is unused under "
        "the slice loop, and coupling to it would strand every draft-anchored "
        "candidate"
    )


def test_the_edge_direction_sense_of_forward_survives_the_amputation():
    """"Forward" names two things in this document: the doomed forward-anchored
    ADR concept, now gone, and an edge *direction* that stays — the forward
    `related-spec` facet distill uses to resolve a spec's task tree and to
    detect an existing cluster ADR on resume. This pin exists to catch an
    amputation done by keyword rather than by meaning: deleting the cluster
    class must not touch the edge-direction sense of the word.
    """
    text = _text()
    assert "kind:task related-spec:<name>" in text, (
        "distill/SKILL.md must still resolve a spec's task tree through the "
        "forward `related-spec` facet — this is edge DIRECTION, not the deleted "
        "forward-anchored cluster class"
    )
    assert "detect the existing cluster ADR via the forward `related-spec` facet" in text, (
        "distill/SKILL.md must still detect an existing cluster ADR on resume via "
        "the forward `related-spec` facet — this is edge DIRECTION, not the "
        "deleted forward-anchored cluster class"
    )


# --- untrusted vault-sourced values entering a command line ---


def test_distill_validates_vault_sourced_values_before_substitution():
    """Distill interpolates `<spec-id>`, `<adr-id>`, and `<name>` — all read out of
    a git-synced, teammate-writable vault — into `lore record show` / `lore record
    update` command lines.

    `_shared/execute.md` codifies the rule and `plan/SKILL.md` and `slice/SKILL.md`
    both carry it. Distill carried it nowhere, and the loop-closed query above is
    what newly routes loop-driven specs into those sites.
    """
    flat = _flat(_text())
    assert "^[A-Za-z0-9._/-]+$" in flat, (
        "distill/SKILL.md must name the safe-value shape every vault-sourced value "
        "is validated against before it is substituted into a command"
    )


def test_a_value_failing_the_shape_check_is_refused_not_omitted():
    flat = _flat(_text())
    assert "never substituted, quoted, or escaped in" in flat, (
        "distill/SKILL.md must state that a value failing the shape check is "
        "refused outright — silently omitting it would turn a refusal into a "
        "query that returns nothing and reads as 'nothing found'"
    )


def test_the_shape_check_governs_every_substitution_site_not_a_fixed_count():
    flat = _flat(_text())
    assert "governs every substitution site" in flat, (
        "distill/SKILL.md's guard must be stated as governing every substitution "
        "site in the file — a guard scoped to an enumerated list silently stops "
        "covering the next site someone adds"
    )


def test_queue_exclusion_describes_the_adr_edge_as_carried_not_as_authored_by_brainstorm():
    """§2 explains where a spec's `related: adr=` edge comes from, to justify treating
    it as a distinct exclusion key from the `distilled=` annotation.

    That explanation must describe the edge as something a record carries. Naming a
    live authoring mechanism instead states a present-tense claim about who writes the
    edge, and the reader's next question — "so I should look for new ones" — has no
    true answer.
    """
    # Lowercased: whether the phrase opens a sentence is a wrapping artifact, not
    # part of the claim being pinned.
    section = _flat(_queue_exclusion_section(_text())).lower()
    assert "a spec carries" in section, (
        "§2 must describe the `related: adr=` edge as something a spec carries, "
        "since the exclusion's job is to recognise the edge wherever it already is"
    )
