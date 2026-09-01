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
    ["distilled=zero-adr", "distilled=forward-anchored", "distilled=rejected"],
)
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




# --- Slice 4: gauntlet stops flipping, distill activates on the last derived spec ---

import re  # noqa: E402
import sys  # noqa: E402

_LORE_PLUGIN_DIR = Path(__file__).parent.parent.parent / "lore" / "plugins" / "lore"
sys.path.insert(0, str(_LORE_PLUGIN_DIR))

from lore.pipeline.derive import TERMINAL_SPEC_STATUSES  # noqa: E402

GAUNTLET = CRAFT / "skills" / "gauntlet" / "SKILL.md"

_ACTIVATION_STEP_HEADER = "6. **Then check activation"
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


def _activation_step(text: str) -> str:
    return _section(
        text,
        _ACTIVATION_STEP_HEADER,
        "## Terminal outcomes",
        why=(
            "distill/SKILL.md must carry the activation-check step in 'Write, in a "
            "fixed order'"
        ),
    )


def _would_activate(sibling_statuses: set[str]) -> bool:
    """The activation predicate distill's prose describes, built off the real
    `TERMINAL_SPEC_STATUSES` constant rather than a duplicated literal — so this
    helper and distill's own condition can never silently drift apart.
    """
    all_terminal = all(status in TERMINAL_SPEC_STATUSES for status in sibling_statuses)
    at_least_one_complete = "complete" in sibling_statuses
    return all_terminal and at_least_one_complete


def test_activation_check_states_trigger_condition_and_mechanism_together():
    """Trigger, condition, and mechanism must read as one clause — not three
    separate sentences a later edit could quietly pull apart.
    """
    step = _flat(_activation_step(_text()))
    assert "completing a member spec is also the trigger" in step, (
        "the activation check must name its trigger: a member spec reaching "
        "`complete`"
    )
    assert "Activate only when **every** sibling has reached a terminal status" in step, (
        "the activation check must state its condition: every related spec "
        "terminal AND at least one complete"
    )
    assert "and at least one** reached `complete`" in step, (
        "the activation check must also require at least one `complete` sibling"
    )
    assert '"complete", "superseded", "dropped"' in step, (
        'the activation check must name the mechanism verbatim: '
        'TERMINAL_SPEC_STATUSES = {"complete", "superseded", "dropped"}'
    )
    assert "pipeline/derive.py:97" in step, (
        "the mechanism must cite where the real constant lives, not just its value"
    )


def test_an_adr_with_complete_and_dropped_derived_specs_does_activate():
    """The case a `complete`-only condition would strand forever.

    No executable activation path exists in this repo — distill is pure prose,
    run by an agent rather than code — so this pins the predicate the prose
    describes against the real constant; the prose itself is pinned separately
    below.
    """
    assert _would_activate({"complete", "dropped"}) is True


def test_an_adr_whose_derived_specs_are_all_dropped_does_not_activate():
    assert _would_activate({"dropped", "dropped"}) is False


def _absorption_sweep_section(text: str) -> str:
    """Just the absorption-sweep exclusion, bounded before the forward-anchored
    rule — the neighbouring section states the same edge in the same vocabulary,
    so a wider slice would pin nothing.
    """
    return _section(
        text,
        "**Lingering `draft` ADRs",
        _FORWARD_ANCHORED_HEADER,
        why=(
            "distill/SKILL.md must carry the absorption-sweep exclusion in 'Step 1 "
            "— Cluster before drafting'"
        ),
    )


def test_absorption_sweep_and_activation_read_the_same_edge_and_the_same_set():
    """The "the two sweeps cannot disagree" claim is about edge DIRECTION first.

    A forward ADR never carries a `related: spec=` edge of its own: brainstorm's
    altitude gate writes the edge **spec-side** on each derived seed (`--related
    adr=<adr-id>` from birth), and distill writes it ADR-side only on the backward
    path ("never on the spec"). So an exclusion keyed on the draft ADR's own
    `related: spec=` edges is unsatisfiable for exactly the population it
    protects, and the sweep would retire in-flight forward ADRs with `--status
    dropped`. Both checks must traverse spec-side, off the same forward facet —
    and only then read terminality off the same constant.
    """
    text = _text()
    sweep_section = _absorption_sweep_section(text)
    activation_section = _flat(_activation_step(text))
    flat_sweep = _flat(sweep_section)
    assert "any spec carrying a `related: adr=` edge to it" in flat_sweep, (
        "the absorption-sweep exclusion must key on the specs that carry a "
        "`related: adr=` edge TO the draft ADR — a forward ADR carries no "
        "`related: spec=` edge of its own, so keying on one excludes nothing"
    )
    assert "carrying any `related: spec=` edge" not in flat_sweep, (
        "the exclusion must not key on a `related: spec=` edge carried by the "
        "draft ADR — that edge only ever exists on the backward path"
    )
    for section, which in (
        (flat_sweep, "absorption-sweep exclusion"),
        (activation_section, "activation check"),
    ):
        assert 'lore search "kind:spec related-adr:<adr-id>"' in section, (
            f"the {which} must resolve the ADR's derived specs off the forward "
            "`related-adr` facet — the two cannot disagree only if they traverse "
            "the same edge in the same direction"
        )
    for status in TERMINAL_SPEC_STATUSES:
        assert f'"{status}"' in sweep_section, (
            f"the absorption-sweep exclusion must name {status!r} from the real "
            "TERMINAL_SPEC_STATUSES constant"
        )
        assert f'"{status}"' in activation_section, (
            f"the activation condition must name {status!r} from the real "
            "TERMINAL_SPEC_STATUSES constant"
        )


def test_distill_emits_the_activation_write_guarded_on_the_condition():
    step = _activation_step(_text())
    write = "lore record update <adr-id> --status active --vault <name>"
    assert write in step, "distill/SKILL.md must emit the adr-activation write"
    guard_idx = step.index("Activate only when")
    write_idx = step.index(write)
    assert guard_idx < write_idx, (
        "the activation write must be guarded by the condition stated ahead of it"
    )


def test_distill_is_the_sole_writer_of_draft_to_active_on_both_paths():
    step = _flat(_activation_step(_text()))
    assert "Distill is the sole writer of `draft -> active` on this path" in step, (
        "distill/SKILL.md must state it is the sole writer of the activation edge"
    )
    assert (
        "exactly as it is the sole writer of the completion edge above" in step
    ), (
        "distill must tie the new sole-writer claim to the existing one for the "
        "spec completion edge — which is `planned -> complete` for pre-loop "
        "records and `ready -> complete` for a spec the slice loop closed out"
    )
    assert "the gauntlet, no longer advances an adr past `draft` at all" in step, (
        "distill must state the other forward-path writer (the gauntlet) no "
        "longer advances an adr at all"
    )


def test_absorption_sweep_exclusion_is_a_pinned_procedural_step():
    assert _ABSORPTION_EXCLUSION_SENTENCE in _flat(
        _absorption_sweep_section(_text())
    ), (
        "distill/SKILL.md must state the absorption-sweep's exclusion for drafts "
        "with incomplete derived specs as a concrete, pinned procedural step"
    )


def test_activation_states_active_immutability_is_unchanged():
    step = _flat(_activation_step(_text()))
    assert "`active` immutability is unchanged by this" in step, (
        "distill must restate that `active` immutability is unchanged by moving "
        "when activation happens"
    )
    assert "never whether an `active` record can still be edited" in step, (
        "distill must state explicitly this does not reopen an editable-active "
        "adr — only WHEN activation happens moves"
    )


def test_amendment_while_draft_is_unrestricted_in_distill():
    step = _flat(_activation_step(_text()))
    assert "Amendment while `draft` remains unrestricted" in step, (
        "distill must state amendment while `draft` is unrestricted, with no "
        "material/immaterial distinction to adjudicate"
    )


def test_status_active_absence_sweep_is_scoped_to_gauntlet_not_banned_globally():
    """distill legitimately emits `--status active` — the absence sweep must not
    ban the string globally, only from the gauntlet skill.
    """
    assert "lore record update <adr-id> --status active" in _text(), (
        "distill/SKILL.md must legitimately carry the adr-activation write — a "
        "global ban on this string would be wrong, not a fix"
    )
    assert not re.compile(r"<adr-id>\s+--status\s+active").search(GAUNTLET.read_text()), (
        "gauntlet/SKILL.md must not carry the adr-activation write — the sweep is "
        "scoped to the gauntlet skill specifically, not the string everywhere"
    )


# --- Slice 5: forward-anchored clusters route to zero-ADR so activation can fire ---

_FORWARD_ANCHORED_HEADER = "### Forward-anchored clusters"


def _queue_exclusion_section(text: str) -> str:
    """Just §2 of the sweep queue — the per-candidate exclusion rule itself."""
    return _section(
        text,
        "### 2. Apply the exclusion per candidate",
        "### 3. Resolve each surviving spec's task tree",
    )


def _forward_anchored_section(text: str) -> str:
    return _section(
        text,
        _FORWARD_ANCHORED_HEADER,
        "### The deferral rule",
        why=(
            "distill/SKILL.md must carry a forward-anchored cluster rule in 'Step 1 "
            "— Cluster before drafting'"
        ),
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


def test_forward_anchored_cluster_is_defined_by_the_related_adr_edge_on_every_member():
    """The recognition condition, stated where clustering happens."""
    section = _flat(_forward_anchored_section(_text()))
    assert "every member carries a `related: adr=` edge to an existing adr" in section, (
        "distill/SKILL.md must define a forward-anchored cluster by the "
        "`related: adr=` edge carried by EVERY member — the whole recognition "
        "turns on 'every', not 'any'"
    )
    assert "`lore record show <spec-id> --json`" in section, (
        "the anchor id must be read off the per-candidate `--json` the queue pass "
        "already returned — no new index read, no pipeline dependency"
    )


def test_a_forward_anchored_cluster_routes_to_the_zero_adr_path_and_drafts_nothing():
    section = _flat(_forward_anchored_section(_text()))
    assert "routes to the zero-ADR disposition path" in section, (
        "a forward-anchored cluster must route to the EXISTING zero-ADR "
        "disposition path — this slice adds a routing rule, not new machinery"
    )
    assert "no ADR is drafted for it" in section, (
        "distill must state that no ADR is drafted for a forward-anchored cluster "
        "— drafting one would restate its own parent ADR"
    )


def test_forward_anchored_members_complete_under_their_own_annotation_value():
    """`forward-anchored` is a third outcome, not a spelling of `zero-adr`.

    `zero-adr` means 'distilled, yielded nothing'; forward-anchored means 'the
    decision is already recorded upstream'. A later reader must be able to
    separate them mechanically, so the two annotation values stay distinct.
    """
    bullet = _outcome_bullet(_text(), "Forward-anchored.")
    assert (
        "lore record update <spec-id> --status complete "
        "--annotation distilled=forward-anchored --vault <name>" in bullet
    ), (
        "the forward-anchored outcome must emit the member flip stamped "
        "`distilled=forward-anchored`"
    )
    assert "distilled=zero-adr" not in _flat(bullet).replace(
        "**not** `distilled=zero-adr`", ""
    ), (
        "the forward-anchored outcome must NOT reuse `distilled=zero-adr` for its "
        "own write — the two outcomes must stay machine-separable"
    )
    assert "**not** `distilled=zero-adr`" in _flat(bullet), (
        "the forward-anchored outcome must say explicitly that it is not "
        "`distilled=zero-adr`, so a later editor cannot collapse the two"
    )
    zero = _outcome_bullet(_text(), "Zero ADRs.")
    assert "--annotation distilled=zero-adr --vault <name>" in zero, (
        "the zero-ADR outcome must keep its own distinct annotation value"
    )
    assert "distilled=forward-anchored" not in zero, (
        "the zero-ADR outcome must not absorb the forward-anchored value"
    )


def test_the_forward_anchored_proposal_names_the_anchoring_adr_by_id():
    """The operator's only signal that rejecting this strands activation."""
    step = _flat(_proposal_step(_text()))
    assert "names the anchoring ADR by id" in step, (
        "the Step 2 proposal for a forward-anchored cluster must name the "
        "anchoring ADR by id"
    )
    assert "zero ADRs, because the decision is already recorded in adr/<id>" in step, (
        "the `zero ADRs, because …` clause must be spelled out with the anchoring "
        "adr id in it — a bare null verdict is what this exists to prevent"
    )
    assert "never a bare null verdict" in step, (
        "Step 2 must forbid the bare null verdict for a forward-anchored cluster "
        "— an operator who cannot tell it from a genuine nothing-to-record verdict "
        "may reject it and strand the anchoring ADR `draft` forever"
    )


def test_forward_anchored_recognition_is_a_proposal_not_an_auto_write():
    section = _flat(_forward_anchored_section(_text()))
    assert "Recognition is a proposal, not an auto-write" in section, (
        "the forward-anchored rule must state it proposes rather than writes"
    )
    assert "Step 3's disposition gate" in section, (
        "the forward-anchored verdict must be routed through the existing "
        "disposition gate by name — no write happens before the operator "
        "dispositions it"
    )


def test_a_partly_anchored_cluster_partitions_rather_than_merging_or_dropping():
    """Partly-anchored clusters are pinned to a stated behaviour, not left to the
    reader.
    """
    section = _flat(_forward_anchored_section(_text()))
    assert (
        "A partly-anchored cluster partitions; it never merges and never drops."
        in section
    ), (
        "distill/SKILL.md must state the partly-anchored rule explicitly — some "
        "members anchored, some not is otherwise left to the reader"
    )
    assert (
        "split the anchored members into their own forward-anchored cluster and "
        "let the rest cluster normally" in section
    ), (
        "the partly-anchored rule must say concretely how it partitions"
    )
    assert (
        "merging them would force one group into the wrong one" in section
        and "dropping the cluster whole would re-strand" in section
    ), (
        "the partly-anchored rule must carry its reason in the same clause — the "
        "two groups have categorically different correct outcomes"
    )


def test_the_deferral_rule_is_unchanged_for_forward_anchored_clusters():
    section = _flat(_forward_anchored_section(_text()))
    assert (
        "a forward-anchored cluster with any member still in flight defers whole"
        in section
    ), (
        "the deferral rule must be restated as unchanged for forward-anchored "
        "clusters — recognition changes which proposal is presented, nothing else"
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


def test_the_queue_keeps_specs_whose_anchoring_adr_is_still_draft():
    """The placement correction: the exclusion in §2 is what had to narrow.

    §2 drops any candidate carrying a `related: adr` edge *before* Step 1 ever
    clusters anything, so under a blanket exclusion every cluster is by
    construction 100% non-anchored and a recognition rule inside clustering
    would be unreachable — as would the activation check that reads specs
    carrying exactly that edge.
    """
    section = _queue_exclusion_section(_text())
    flat = _flat(section)
    assert "edge whose anchoring ADR has itself reached `active` or a terminal status" in flat, (
        "§2's `related: adr` exclusion must be narrowed to anchoring ADRs that are "
        "`active` or terminal — a blanket exclusion makes both the forward-anchored "
        "recognition and the activation check unreachable"
    )
    assert "stays in the queue" in flat and "still `draft`" in flat, (
        "§2 must say what happens to a spec whose anchoring ADR is still `draft` — "
        "it stays in the queue"
    )
    assert "step 6's activation check" in flat, (
        "§2 must name the activation step it feeds, in the same clause that states "
        "the rule — the rule and its reason must not live in separate sections"
    )
    assert "distilled=forward-anchored" in flat, (
        "§2's annotation exclusion list must include the forward-anchored value, "
        "or a distilled forward-anchored cluster re-enters the queue forever"
    )


def test_the_activation_step_names_the_narrowed_exclusion_that_feeds_it():
    """The cross-reference must be mutual, not one-directional.

    §2 names step 6 three times as the reason its edge exclusion is narrow. Step
    6 must name §2 back: a reader who arrives at the activation check alone
    otherwise cannot tell why a spec carrying a `related: adr=` edge is reachable
    here at all, since the unnarrowed reading of §2 excludes exactly those specs
    before clustering. A commitment whose enforcement lives in an unnamed other
    section is the failure mode both clauses exist to prevent.
    """
    flat = _flat(_activation_step(_text()))
    assert "still `draft`" in flat and "queue" in flat, (
        "the activation step must name the narrowed §2 exclusion that lets a "
        "`draft`-anchored spec stay in the queue and reach this check"
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


def test_the_forward_machinery_stays_decoupled_from_the_planned_status():
    """The forward-anchored cluster class and the activation check key off the
    `related: adr` edge and terminal statuses, never `planned`. Pinning that keeps
    a future edit from quietly coupling them to a status this slice is retiring.
    """
    flat = _flat(_forward_anchored_section(_text()))
    assert "related: adr" in flat, (
        "the forward-anchored cluster class must be defined by the `related: adr` "
        "edge, not by a spec status"
    )
    assert "status:planned" not in flat, (
        "the forward-anchored cluster class must not key off `planned` — that "
        "status is unused under the slice loop, and coupling to it would strand "
        "every forward-anchored cluster"
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
