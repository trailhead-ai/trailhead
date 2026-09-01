"""The `craft/phase-boundary` label contract on execute.md's resumability section.

`### Phase progress and resumability` prescribes an `## End Phases` checklist that
records progress, and its clean-working-tree resume precondition reverts a dirty
tree to "the last recorded phase boundary" — a value nothing wrote. This pins the
fix: each end phase upserts `craft/phase-boundary=<sha>` (`<sha>` being `HEAD`
after that phase's commits land) onto the parent task record, and the resume
precondition reads it back structurally, failing closed when it is absent rather
than reverting to a guessed target.

Every prose pin here is scoped to the `### Phase progress and resumability`
section specifically — extracted by heading, per
[[lesson/mutation-test-a-prose-pin-whose-target-string-occurs-elsewhere-in-the-file]]
— and asserted as a contiguous substring within one physical line, per
[[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]], so a matching
sentence appearing anywhere else in the file cannot satisfy the pin.

The label round trip is not a prose assertion: it runs the real `lore` CLI
against a throwaway vault in `tmp_path`, mirroring
`tools/craft/tests/test_prior_art_label_contract.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_EXECUTE = CRAFT / "skills" / "_shared" / "execute.md"
SCHEMA_FIXTURE = Path(__file__).parent / "fixtures" / "phase_boundary_label_schema.txt"

PHASE_PROGRESS_HEADING = "### Phase progress and resumability"
MODEL_SELECTION_HEADING = "## Model Selection"

LORE_TESTS_DIR = Path(__file__).parent.parent.parent / "lore" / "tests"
sys.path.insert(0, str(LORE_TESTS_DIR))

from conftest import make_vault, run_cli  # noqa: E402


def _section(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _phase_progress_section() -> str:
    text = SHARED_EXECUTE.read_text()
    return _section(text, PHASE_PROGRESS_HEADING, MODEL_SELECTION_HEADING)


def _pin_in(section_text: str, path_label: str, phrase: str, why: str) -> None:
    """Assert *phrase* appears inside a single physical line of *section_text*,
    and that it occurs on exactly one line so the pin cannot pass on the wrong
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


# --- fixture ships ------------------------------------------------------------


def test_schema_fixture_ships():
    assert SCHEMA_FIXTURE.exists(), f"Expected the canonical phase-boundary label schema fixture at {SCHEMA_FIXTURE}"


# --- fixture literal is pinned in the section ----------------------------------


def test_fixture_literal_appears_in_phase_progress_section():
    schema = SCHEMA_FIXTURE.read_text().strip()
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        schema,
        "The canonical `craft/phase-boundary=<sha>` literal must be byte-identical "
        "between the fixture and the section that prescribes writing it, so a "
        "later reader is pinned to the same source rather than an "
        "independently-worded restatement.",
    )


# --- writer mandate -------------------------------------------------------------


def test_writer_mandate_states_upsert_at_tick():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "at the tick, upserts the phase boundary onto the parent task record",
        "The section must instruct that each phase upserts the label at the "
        "moment it ticks its `## End Phases` line.",
    )


def test_writer_mandate_names_head_after_commits_land():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "being `HEAD` **after** that phase's commits land",
        "The section must name the label's value as `HEAD` taken *after* the "
        "phase's own commits land, not before — otherwise the boundary points "
        "at the wrong tree.",
    )


# --- reader precondition, fail-closed --------------------------------------------


def test_reader_reads_structured_json_label():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        '`.sidecar.labels["craft/phase-boundary"]`',
        "The resume precondition must read the boundary structurally via "
        "`lore record show --json`, not by parsing prose.",
    )


def test_reader_fails_closed_when_label_absent():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "resume stops and reports rather than reverting to a guessed target",
        "Absent the label, resume must stop and report — never revert to a "
        "guessed target. This is the regression the task exists to prevent.",
    )


# --- writer mandate names the write mechanism (Fix 5) ---------------------------


def test_writer_mandate_names_lore_record_update_as_the_write_mechanism():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "upserts the phase boundary onto the parent task record via `lore record update`",
        "The mandate must pin the write mechanism as `lore record update` — long "
        "enough to disambiguate from the `## End Phases` checklist sentence at "
        ":535, which also names that command but for a different write.",
    )


# --- writer atomicity: tick and boundary are one write (Fix 2) ------------------


def test_tick_and_boundary_land_from_one_invocation():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "--diff --label craft/phase-boundary=<sha>",
        "The tick (a body diff) and the boundary (a label) must land in the same "
        "`lore record update` invocation — a separate body write followed by a "
        "separate label write leaves a crash window where the ticked phase's "
        "recorded boundary still names the previous phase's tree.",
    )
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "must land from the same `lore record update` invocation, never a separate "
        "command for the body followed by a separate command for the label",
        "The section must state the single-invocation requirement explicitly, not "
        "just show a command that happens to combine the flags — and must not "
        "call it a single atomic write, which the CLI does not provide (it "
        "performs two sequential atomic renames, body then sidecar).",
    )


def test_writer_states_residual_crash_window_is_narrowed_not_closed():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "narrows the crash window between the two to microseconds rather than closing it",
        "The document must not claim the single invocation closes the crash "
        "window entirely — `validate_and_write` (tools/lore/plugins/lore/lore/"
        "record/store.py:791-792) performs it as two separate atomic renames, "
        "body then sidecar, so the honest claim is a narrowed window, not an "
        "eliminated one.",
    )


# --- writer failure policy for the boundary write (Fix 4) -----------------------


def test_writer_states_policy_for_a_failed_boundary_write():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "the phase logs the failure and retries the write",
        "A failed boundary write needs a stated policy, consistent with the "
        "postmortem's log-and-continue-plus-flag rule for its own failed write "
        "(:496) — a silently failed write leaves a stale or absent boundary that "
        "only surfaces as a fail-closed stop on the next resume.",
    )


def test_writer_states_terminal_escalation_for_persistent_boundary_write_failure():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "If retries keep failing, the phase stops and reports rather than proceeding",
        "Unlike the postmortem's own failed write (:496, log-and-continue-plus-"
        "flag), a persistent boundary-write failure needs a named terminal "
        "outcome: proceeding with a stale boundary would let the next resume's "
        "revert discard this phase's own commits, so the escalation must stop "
        "the run rather than carry the failure forward silently.",
    )


# --- reader validates the sha before a destructive revert (Fix 1) ---------------


def test_reader_requires_sha_shape_validation():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "match `\\A[0-9a-f]{7,40}\\Z`",
        "The boundary sha must be shape-validated before it is substituted into "
        "the destructive revert command — the same untrusted-input rule at :69 "
        "governs every vault-sourced value substituted into a command in this "
        "document, and this label is one such value. The pattern is pinned in "
        "explicit full-string match form (`\\A...\\Z`) rather than `^...$`, "
        "since `$` matches immediately before a trailing newline in some regex "
        "engines (security-fix Informational finding).",
    )


def test_reader_requires_reachability_validation():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "resolve to a commit reachable from",
        "A shape-valid sha is not necessarily a real, reachable commit — the "
        "value must also resolve on the task branch before it is used as a "
        "revert target, or a shape-valid-but-bogus label would still reach the "
        "destructive git command.",
    )


def test_reader_states_shape_check_as_precondition_of_reachability_probe():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "the regex match is a precondition of running that git command",
        "The reachability probe is itself a git command receiving the raw "
        "label value, so it is governed by the same untrusted-input rule as "
        "any other command shown in this document (:69) — the shape check "
        "must be stated as an ordered precondition of running `git merge-base`, "
        "not merely joined to the reachability check by a plain conjunction.",
    )


def test_reader_fails_closed_on_malformed_or_unreachable_sha():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "if the label is missing, does not match that shape, or does not resolve on the branch",
        "A malformed or unreachable sha must be treated exactly like an absent "
        "label — stop and report, never substituted into the revert command.",
    )


# --- end-phase label existence is expected, not guaranteed (Fix 3) -------------


def test_end_phase_label_stated_as_expected_not_guaranteed():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "is **expected** to exist by construction",
        "A run already in flight when the phase-boundary label mandate landed "
        "ticked phases before any phase existed to write the label, so the "
        "end-phase branch cannot claim the label is guaranteed to exist — only "
        "expected, with the fail-closed clause covering the gap.",
    )


def test_end_phase_branch_names_the_migration_case():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "a run already in flight when this label mandate landed",
        "The migration case is not hypothetical — an in-flight run's earlier "
        "phases ticked before this mandate existed, so it must be named "
        "explicitly rather than left to the reader to infer from the "
        "fail-closed clause alone.",
    )


# --- mid-build resume branch is distinguished from end-phase resume (Fix 3) -----


def test_mid_build_branch_is_not_a_fail_closed_stop():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "so no boundary label exists yet by construction",
        "The mid-build resume branch (before any phase has ticked) must be "
        "named explicitly as the case where no `craft/phase-boundary` label "
        "can exist yet — otherwise the fail-closed rule for the end-phase "
        "branch would deadlock every dirty mid-build resume.",
    )


def test_mid_build_branch_discards_drift_against_head_not_base():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "discarding uncommitted and untracked changes against the current `HEAD`",
        "Without a boundary label, the mid-build branch must still reach a "
        "clean tree — by discarding drift against `HEAD`, not by reverting to "
        "a guessed target and not by skipping the clean-tree requirement.",
    )


def test_mid_build_branch_states_why_base_would_be_wrong():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "reverting to it would discard whatever task commits the build has already landed",
        "The document's habit is to state the reason, not just the rule: "
        "reverting to `base` in the mid-build case would discard committed "
        "task work, and that reason must be spelled out, not implied.",
    )


# --- anchors point at the section that actually defines the branches (Fix 5) ----


def test_in_progress_cases_link_targets_determine_the_task_shape():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "the two `in-progress` cases at [Determine the task shape](#determine-the-task-shape)",
        "The two `in-progress` branches are defined under '### Determine the "
        "task shape', not under '### Resuming a run' — the anchor must point "
        "where the branches are actually defined.",
    )


def test_no_ticked_phase_line_link_targets_determine_the_task_shape():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "[No ticked phase line](#determine-the-task-shape)",
        "Same mispointed-anchor defect as the case above, for the mid-build "
        "bullet's own link.",
    )


# --- `base` is not claimed to be recorded in `## End Phases` (Fix 6) ------------


def test_mid_build_bullet_refers_to_base_by_establishment_not_a_recording_claim():
    """The mid-build bullet's own account of `base` must keep referring to how
    `base` is established (fixed once at pipeline start), not assert that it is
    itself the thing that records `base` into `## End Phases` — the actual
    recording mandate lives once, at pipeline entry (see the security-fix Medium
    finding test below), not restated per-bullet."""
    section = _phase_progress_section()
    _pin_in(
        section,
        "execute.md#phase-progress-and-resumability",
        "fixed once at the start of the phase pipeline",
        "Refer to `base` by how it is actually established — fixed once at the "
        "start of the whole-change phase pipeline — rather than by a false "
        "claim about where it is recorded.",
    )


# --- `base` is recorded durably into `## End Phases` at pipeline entry (Fix 1, Medium) --


def test_base_is_recorded_into_end_phases_at_pipeline_entry():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "record the run's `base` (the pre-execution SHA, fixed once per "
        "[After All Tasks](#after-all-tasks)) into the `## End Phases` checklist",
        "The security-fix Medium finding requires recording `base` durably so a "
        "resumed run can recompute it and bound the revert target — this is the "
        "enabling state for the base-bound reachability check, stated once at "
        "entry to the end-phase pipeline.",
    )


# --- reader bounds the revert target to this run's own commit range (Fix 1, Medium) ---


def test_reader_requires_base_bound_reachability():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "`git merge-base --is-ancestor <base> <sha>`",
        "Reachability from `HEAD` alone proves the boundary is *some* ancestor, "
        "not the *recent* one — a stale label set to an old commit would still "
        "pass. The boundary must also lie at or after this run's own `base`, "
        "bounding the revert's blast radius to this run's commit range.",
    )


def test_reader_states_shape_check_precedes_both_merge_base_probes():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "the shape check runs first, and only a shape-valid value is ever "
        "passed to any git command, including both `merge-base` probes below",
        "Ordering must be stated explicitly: the shape check comes before "
        "either `merge-base` probe runs, since both receive the raw label "
        "value as an argument.",
    )


def test_reader_fails_closed_when_boundary_is_outside_this_runs_range():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "or resolves but lies outside this run's own `base..HEAD` range",
        "A boundary that is shape-valid and reachable from `HEAD` but predates "
        "this run's own `base` must still fail closed — the whole point of the "
        "bound is to refuse an out-of-range target, not merely check it.",
    )


def test_reader_states_residual_risk_within_the_bounded_range():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "does not make the label trustworthy",
        "The document's habit is to state what a control does not cover: "
        "bounding the revert target to this run's own range still leaves a "
        "vault writer able to steer a resume into discarding this run's own "
        "already-completed phase commits, and that residual risk must be "
        "named explicitly rather than implied.",
    )


# --- reader shows the revert command literally (Fix 2, Low) ---------------------


def test_reader_shows_the_revert_command_literally():
    _pin_in(
        _phase_progress_section(),
        "execute.md#phase-progress-and-resumability",
        "`git reset --hard <sha> && git clean -fd`",
        "Every other consequential operation in this section is given as a "
        "literal backtick command; the destructive revert itself must be shown "
        "the same way, with the validated `<sha>` substituted, rather than left "
        "as prose only — matching the section's convention and closing the "
        "hyper-literal reading that the untrusted-input rule only binds a "
        "command 'shown' in the document.",
    )


# --- label round trip through the real CLI --------------------------------------


def test_phase_boundary_label_round_trips_and_upserts(tmp_path):
    """Five properties in one round trip:

    1. `record create --label craft/phase-boundary=<sha>` is accepted by the
       write-time reserved-key guard.
    2. `search 'has:label.craft.phase-boundary'` finds it.
    3. `search 'label.craft.phase-boundary:<sha>'` matches it exactly.
    4. A second `record update --label craft/phase-boundary=<sha2>` upserts:
       the record ends holding exactly one value, the newer one, with no
       history of the first.
    5. The bare key `phase` is refused (non-zero exit) while
       `craft/phase-boundary` is accepted — through the CLI's actual refusal,
       not by importing the predicate.
    """
    vault, state = make_vault(tmp_path)
    sha1 = "aaaaaaa1111111111111111111111111111111"
    sha2 = "bbbbbbb2222222222222222222222222222222"

    create = run_cli(
        [
            "record",
            "create",
            "--kind",
            "task",
            "--title",
            "Phase Boundary Probe Task",
            "--keyword",
            "probe",
            "--label",
            f"craft/phase-boundary={sha1}",
        ],
        vault=vault,
        state_dir=state,
        stdin_text="body\n",
    )
    assert create.returncode == 0, create.stderr  # write-time reserved-key guard accepted it
    record_id = create.stdout.strip()
    assert record_id.startswith("task/"), f"expected task/<name>, got {record_id!r}"
    name = record_id.split("/", 1)[1]

    exists_search = run_cli(
        ["search", "has:label.craft.phase-boundary"],
        vault=vault,
        state_dir=state,
    )
    assert exists_search.returncode == 0, exists_search.stderr
    assert name in exists_search.stdout, (  # existence lookup
        f"expected {name!r} in search output for has:label.craft.phase-boundary, "
        f"got: {exists_search.stdout!r}"
    )

    eq_search = run_cli(
        ["search", f"label.craft.phase-boundary:{sha1}"],
        vault=vault,
        state_dir=state,
    )
    assert eq_search.returncode == 0, eq_search.stderr
    assert name in eq_search.stdout, (  # exact-value lookup
        f"expected {name!r} in search output for label.craft.phase-boundary:{sha1}, "
        f"got: {eq_search.stdout!r}"
    )

    update = run_cli(
        ["record", "update", record_id, "--label", f"craft/phase-boundary={sha2}"],
        vault=vault,
        state_dir=state,
    )
    assert update.returncode == 0, update.stderr

    show = run_cli(
        ["record", "show", record_id, "--json"],
        vault=vault,
        state_dir=state,
    )
    assert show.returncode == 0, show.stderr
    import json

    payload = json.loads(show.stdout)
    labels = payload["sidecar"]["labels"]
    # exact final map, not just "the new value is present" — an appending CLI
    # would also pass a bare containment check
    assert labels == {"craft/phase-boundary": sha2}, (
        f"expected the label to hold exactly the newer value with no history "
        f"of the first, got {labels!r}"
    )

    refused = run_cli(
        ["record", "update", record_id, "--label", f"phase={sha2}"],
        vault=vault,
        state_dir=state,
    )
    assert refused.returncode != 0, (
        "the bare key 'phase' shadows a KQL query field and must be refused "
        "at write time"
    )
