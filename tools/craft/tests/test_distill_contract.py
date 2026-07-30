"""The distill ritual — backward distillation of completed specs into ADRs.

Distill is the final stage of the pipeline (brainstorm -> gauntlet -> plan ->
execute -> review -> distill) and the sole writer of a spec's `planned -> complete`
edge. Everything that makes it safe is a prose contract no type system can hold, so
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
    assert "brainstorm → gauntlet → plan → execute → review → distill" in _text(), (
        "distill/SKILL.md must place itself at the end of the named pipeline — the "
        "ritual only makes sense as the stage that runs after the work landed"
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


@pytest.mark.parametrize("annotation", ["distilled=zero-adr", "distilled=rejected"])
def test_terminal_annotation_spellings_pinned(annotation: str):
    """The annotation string is the queue's exclusion key, so its spelling is behavior.

    A typo here does not fail — it re-queues the cluster on every sweep, forever.
    """
    assert annotation in _text(), (
        f"distill/SKILL.md must spell the terminal annotation {annotation!r} exactly"
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
