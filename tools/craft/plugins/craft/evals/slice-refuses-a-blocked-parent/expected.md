# slice-refuses-a-blocked-parent

## Under test

Step 5 of `skills/slice/SKILL.md` with a `blocked` slice parent: the stuck detector exits 1 with `reason-code: parent-blocked`, the pass refuses to choose a new slice, writes nothing, leaves the parent `blocked`, and the report says a pass never closes a blocked slice and gives the remedy (set the parent back to in progress by hand, or `/craft:execute task/<parent>`). Prose measured: `skills/slice/SKILL.md`, `skills/_shared/execute.md`.

## Fixture

Fixture (shared scaffold, `fixtures/scaffold.sh` of the first case, driven by an `ARM` variable): `ready` spec `schedule-courier-pickups` with AC1 (automated assertion), AC2 (manual check) and AC3 (automated assertion, uncovered), no `## Slices` ledger. One slice parent `pickups-can-be-booked-and-confirmed`, labelled `craft/slice-parent` and `craft/branch`, related to the spec, covering AC1 and AC2, Phases 1 to 5 ticked, Phase 6 unticked, AC1 with an automated observation, AC2 with a `manual-check` observation and no attestation. The run directory is a git repository on `main`, clean, the task branch pushed to a bare `origin.git`. `./lore`, `./scripts/pending_check.py`, `./scripts/observation_gate.py` and the other gates are wrappers over the real scripts and log each call to `lore-calls.log`. The prompt maps `${CLAUDE_PLUGIN_ROOT}/scripts/` to `./scripts/` and names neither the detector nor the close. Arm `ARM=blockednorepo`: the parent is `blocked` and its `craft/branch` label names a branch held by no repository here (the branch plays no part, because the detector refuses the blocked parent first). The prompt is `/craft:slice spec/schedule-courier-pickups`.

## Baseline-default check

Prose before this edit moved a blocked parent to `in-progress` and tried to close it, so `parent-still-blocked` is red on it. This arm was not baselined: the case is a guard on the shipped prose.

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 3`, the runner block's grants. A run scores the fraction of graders that pass and passes only at 1.0. Baseline on the unedited `skills/slice/SKILL.md` (content fingerprint `ad193edb75f5aec9f01a0bf9f33119fa1a70f9c9`); treatment bar 3 of 3 at 1.0. At most one rerun of a failed batch per arm, stated here before any run; a rerun-green is reported as rerun-green. A grader that must be corrected after a run is corrected in a dated "Deviations from pre-registration" section appended here before the next batch, with the prose fingerprint each batch ran on.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The detector ran | `detector-ran` | `regex` on `lore-calls.log` |
| The parent is still `blocked` | `parent-still-blocked` | `regex` on the parent's `.json` |
| Nothing was written: no writer, no done write | `writer-and-close-not-run` | `regex` not_contains on `lore-calls.log` |
| The spec carries no waiting entry | `spec-has-no-waiting-entry` | `regex` not_contains `## Waiting` |
| The reply refuses with the blocked reason and the remedy | `reply-refuses-with-the-blocked-remedy` | `llm`, trace |

## Limitations

One fixture, three runs, one model tier.

## Revision (2026-11-10): rework into the blocked-parent refusal

This case used to measure the `blocked` to `in-progress` move followed by a refused close. The operator removed the move: a blocked parent is never closed by a pass, and the detector answers `parent-blocked`. The directory was renamed from `slice-keeps-a-blocked-parent-in-progress-when-its-close-is-refused`; the scaffold arm is unchanged. Graders changed: `parent-moved-to-in-progress` became `parent-still-blocked` (status `blocked`), and `reply-reports-the-move` became `reply-refuses-with-the-blocked-remedy`. Bar 3 of 3 at 1.0 on the shipped prose; at most one rerun.
