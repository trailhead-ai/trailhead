# slice-keeps-a-blocked-parent-in-progress-when-its-close-is-refused

## Under test

Step 5 of `skills/slice/SKILL.md` with a `blocked` stuck parent whose close is refused: the pass moves it to `in-progress` first (execute's single move paragraph), the close is then refused, the parent stays `in-progress`, and the report says "It had been set aside as blocked; it is now in progress." Prose measured: `skills/slice/SKILL.md`, `skills/_shared/execute.md`.

## Fixture

Fixture (shared scaffold, `fixtures/scaffold.sh` of the first case, driven by an `ARM` variable): `ready` spec `schedule-courier-pickups` with AC1 (automated assertion), AC2 (manual check) and AC3 (automated assertion, uncovered), no `## Slices` ledger. One slice parent `pickups-can-be-booked-and-confirmed`, labelled `craft/slice-parent` and `craft/branch`, related to the spec, covering AC1 and AC2, Phases 1 to 5 ticked, Phase 6 unticked, AC1 with an automated observation, AC2 with a `manual-check` observation and no attestation. The run directory is a git repository on `main`, clean, the task branch pushed to a bare `origin.git`. `./lore`, `./scripts/pending_check.py`, `./scripts/observation_gate.py` and the other gates are wrappers over the real scripts and log each call to `lore-calls.log`. The prompt maps `${CLAUDE_PLUGIN_ROOT}/scripts/` to `./scripts/` and names neither the detector nor the close. Arm `ARM=blockednorepo`: the parent is `blocked` and its `craft/branch` label names a branch held by no repository here, so the close is refused after the move. The prompt is `/craft:slice spec/schedule-courier-pickups`.

## Baseline-default check

The unedited prose refuses on the open slice and leaves the parent `blocked`, so `parent-moved-to-in-progress` and the report clause are red on the baseline.

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 3`, the runner block's grants. A run scores the fraction of graders that pass and passes only at 1.0. Baseline on the unedited `skills/slice/SKILL.md` (content fingerprint `ad193edb75f5aec9f01a0bf9f33119fa1a70f9c9`); treatment bar 3 of 3 at 1.0. At most one rerun of a failed batch per arm, stated here before any run; a rerun-green is reported as rerun-green. A grader that must be corrected after a run is corrected in a dated "Deviations from pre-registration" section appended here before the next batch, with the prose fingerprint each batch ran on.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The detector ran | `detector-ran` | `regex` on `lore-calls.log` |
| The parent is `in-progress` after the refusal | `parent-moved-to-in-progress` | `regex` on the parent's `.json` |
| Nothing else was written: no writer, no done write | `writer-and-close-not-run` | `regex` not_contains on `lore-calls.log` |
| The spec carries no waiting entry | `spec-has-no-waiting-entry` | `regex` not_contains `## Waiting` |
| The reply says it had been set aside as blocked and is now in progress, and that the close was refused (repository not in this workspace), with the `/craft:execute` remedy | `reply-reports-the-move` | `llm`, trace |

## Limitations

One fixture, three runs, one model tier.
