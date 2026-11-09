# slice-still-refuses-an-unfinished-open-slice

## Under test

Step 5 of `skills/slice/SKILL.md`: an open slice parent whose build is not finished (the detector exits 1 with `build-unfinished`) is still refused, nothing is closed or written, and the refusal carries the one added reason line that says it was not closed for the operator because its build is not finished. Paired control of `slice-closes-a-stuck-slice-and-chooses-the-next`. Prose measured: `skills/slice/SKILL.md`, `skills/_shared/execute.md`.

## Fixture

Fixture (shared scaffold, `fixtures/scaffold.sh` of the first case, driven by an `ARM` variable): `ready` spec `schedule-courier-pickups` with AC1 (automated assertion), AC2 (manual check) and AC3 (automated assertion, uncovered), no `## Slices` ledger. One slice parent `pickups-can-be-booked-and-confirmed`, labelled `craft/slice-parent` and `craft/branch`, related to the spec, covering AC1 and AC2, Phases 1 to 5 ticked, Phase 6 unticked, AC1 with an automated observation, AC2 with a `manual-check` observation and no attestation. The run directory is a git repository on `main`, clean, the task branch pushed to a bare `origin.git`. `./lore`, `./scripts/pending_check.py`, `./scripts/observation_gate.py` and the other gates are wrappers over the real scripts and log each call to `lore-calls.log`. The prompt maps `${CLAUDE_PLUGIN_ROOT}/scripts/` to `./scripts/` and names neither the detector nor the close. Arm `ARM=unfinished`: the same parent with one child `done` and one child `ready`. The prompt is `/craft:slice spec/schedule-courier-pickups`.

## Baseline-default check

This is a paired control, so the unedited prose is expected to refuse already: every clause but the added reason line holds on the baseline. The baseline is expected red on `reason-line-not-closed-for-you` only (and on `detector-ran`), and green on the others, by design.

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 3`, the runner block's grants. A run scores the fraction of graders that pass and passes only at 1.0. Baseline on the unedited `skills/slice/SKILL.md` (content fingerprint `ad193edb75f5aec9f01a0bf9f33119fa1a70f9c9`); treatment bar 3 of 3 at 1.0. At most one rerun of a failed batch per arm, stated here before any run; a rerun-green is reported as rerun-green. A grader that must be corrected after a run is corrected in a dated "Deviations from pre-registration" section appended here before the next batch, with the prose fingerprint each batch ran on.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The detector ran | `detector-ran` | `regex` on `lore-calls.log` |
| Neither the writer nor the close ran | `writer-and-close-not-run` | `regex` not_contains on `lore-calls.log`: `pending_check.py (?!stuck\|describe)\|--status done` |
| The parent is still `in-progress` | `parent-still-in-progress` | `regex` on the parent's `.json` |
| The spec carries no waiting entry | `spec-has-no-waiting-entry` | `regex` not_contains `## Waiting` on the spec's body |
| The refusal names the open slice, says it was not closed for the operator because its build is not finished, and offers resume or drop | `reason-line-not-closed-for-you` | `llm`, trace |

## Limitations

One fixture, three runs, one model tier.
