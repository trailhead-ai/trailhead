# slice-refuses-when-the-slice-repository-is-not-here

## Under test

Step 5 of `skills/slice/SKILL.md` when the stuck slice's task branch is held by no repository in this workspace (execute's no-ask close branch recovery owns the refusal): nothing is written, the slice is not closed, the pass refuses to choose, and the report says the repository is not in this workspace and names `/craft:execute task/<parent>` run from the workspace that holds it. Prose measured: `skills/slice/SKILL.md`, `skills/_shared/execute.md`.

## Fixture

Fixture (shared scaffold, `fixtures/scaffold.sh` of the first case, driven by an `ARM` variable): `ready` spec `schedule-courier-pickups` with AC1 (automated assertion), AC2 (manual check) and AC3 (automated assertion, uncovered), no `## Slices` ledger. One slice parent `pickups-can-be-booked-and-confirmed`, labelled `craft/slice-parent` and `craft/branch`, related to the spec, covering AC1 and AC2, Phases 1 to 5 ticked, Phase 6 unticked, AC1 with an automated observation, AC2 with a `manual-check` observation and no attestation. The run directory is a git repository on `main`, clean, the task branch pushed to a bare `origin.git`. `./lore`, `./scripts/pending_check.py`, `./scripts/observation_gate.py` and the other gates are wrappers over the real scripts and log each call to `lore-calls.log`. The prompt maps `${CLAUDE_PLUGIN_ROOT}/scripts/` to `./scripts/` and names neither the detector nor the close. Arm `ARM=norepo`: the parent's `craft/branch` label names a branch that exists neither locally nor on `origin`. The prompt is `/craft:slice spec/schedule-courier-pickups`.

## Baseline-default check

The unedited prose refuses on the open slice without running the detector, so `detector-ran` and the repository clause are red on the baseline.

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 3`, the runner block's grants. A run scores the fraction of graders that pass and passes only at 1.0. Baseline on the unedited `skills/slice/SKILL.md` (content fingerprint `ad193edb75f5aec9f01a0bf9f33119fa1a70f9c9`); treatment bar 3 of 3 at 1.0. At most one rerun of a failed batch per arm, stated here before any run; a rerun-green is reported as rerun-green. A grader that must be corrected after a run is corrected in a dated "Deviations from pre-registration" section appended here before the next batch, with the prose fingerprint each batch ran on.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The detector ran | `detector-ran` | `regex` on `lore-calls.log` |
| Neither the writer nor the close ran | `writer-and-close-not-run` | `regex` not_contains on `lore-calls.log`: `pending_check.py (?!stuck\|describe)\|--status done` |
| The parent is still `in-progress` | `parent-still-in-progress` | `regex` on the parent's `.json` |
| The spec carries no waiting entry | `spec-has-no-waiting-entry` | `regex` not_contains `## Waiting` |
| The reply says the slice's repository is not in this workspace, gives the `/craft:execute task/pickups-can-be-booked-and-confirmed` remedy, and chooses no new slice | `reply-reports-the-missing-repository` | `llm`, trace |

## Limitations

The design doc says the reason line names the repository; the vault records no repository for a slice, so the reply is graded on naming the slice and the remedy execute's paragraph fixes. One fixture, three runs, one model tier.

## Revision (2026-11-10, the label is the task's own name)

The eval gate ran this case on the merged prose and it scored 0 of 3, every run failing `reply-reports-the-missing-repository`. Each reply said the close was refused because the `craft/branch` label (`pickups-no-repository-holds-this`) is not the task's own name, and none said the repository is not in this workspace. That is the prose working as written: branch recovery step 1 now refuses any label other than the parent's name before it looks for a repository, so a fixture label that differs from the name never reaches the repository check this case exists to pin. No grader changes.

Change to the fixture only: arm `norepo` now labels the parent with its own name, `pickups-can-be-booked-and-confirmed`, and does not create that branch locally or on `origin`, so the label passes the structural checks and no repository in the run directory holds the branch. Bar unchanged: 3 of 3 at 1.0, on the prose fingerprint recorded in `tools/craft/MANUAL-EVAL.md`; at most one rerun, stated here before any run.
