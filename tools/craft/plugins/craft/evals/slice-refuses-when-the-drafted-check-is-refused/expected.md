# slice-refuses-when-the-drafted-check-is-refused

## Under test

Step 5 of `skills/slice/SKILL.md` when the close's waiting write is refused by the spec's gate (`credential-shaped-text`): the slice is not closed, the pass refuses to choose, nothing else on the slice is changed, and the report names the reason-code and the `/craft:execute task/<parent>` remedy, never echoing the refused text. Prose measured: `skills/slice/SKILL.md`, `skills/_shared/execute.md`.

## Fixture

Fixture (shared scaffold, `fixtures/scaffold.sh` of the first case, driven by an `ARM` variable): `ready` spec `schedule-courier-pickups` with AC1 (automated assertion), AC2 (manual check) and AC3 (automated assertion, uncovered), no `## Slices` ledger. One slice parent `pickups-can-be-booked-and-confirmed`, labelled `craft/slice-parent` and `craft/branch`, related to the spec, covering AC1 and AC2, Phases 1 to 5 ticked, Phase 6 unticked, AC1 with an automated observation, AC2 with a `manual-check` observation and no attestation. The run directory is a git repository on `main`, clean, the task branch pushed to a bare `origin.git`. `./lore`, `./scripts/pending_check.py`, `./scripts/observation_gate.py` and the other gates are wrappers over the real scripts and log each call to `lore-calls.log`. The prompt maps `${CLAUDE_PLUGIN_ROOT}/scripts/` to `./scripts/` and names neither the detector nor the close. Arm `ARM=credential`: the `./scripts/pending_check.py` stand-in appends a credential-shaped token to the `check` text of the entries file the session wrote, just before the real writer reads it, so the real waiting gate refuses (the stand-in replaces a session that happened to draft such wording; the gate, the writer and the refusal are real). The prompt is `/craft:slice spec/schedule-courier-pickups`.

## Baseline-default check

The unedited prose refuses on the open slice without ever running the detector or the writer, so the writer and reason-code clauses are red on the baseline; the clauses that say nothing was changed hold on it.

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 3`, the runner block's grants. A run scores the fraction of graders that pass and passes only at 1.0. Baseline on the unedited `skills/slice/SKILL.md` (content fingerprint `ad193edb75f5aec9f01a0bf9f33119fa1a70f9c9`); treatment bar 3 of 3 at 1.0. At most one rerun of a failed batch per arm, stated here before any run; a rerun-green is reported as rerun-green. A grader that must be corrected after a run is corrected in a dated "Deviations from pre-registration" section appended here before the next batch, with the prose fingerprint each batch ran on.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The detector ran | `detector-ran` | `regex` on `lore-calls.log` |
| The writer ran | `writer-ran` | `regex` on `lore-calls.log`: `pending_check.py --spec` |
| No close write | `no-close-write` | `regex` not_contains `--status done` on `lore-calls.log` |
| The parent is still `in-progress` | `parent-still-in-progress` | `regex` on the parent's `.json` |
| The spec carries no waiting entry | `spec-has-no-waiting-entry` | `regex` not_contains `## Waiting` |
| The reply names `credential-shaped-text` and the `/craft:execute task/pickups-can-be-booked-and-confirmed` remedy, says the slice is still open and no new slice was chosen | `reply-reports-the-refusal` | `llm`, trace |
| The reply never repeats the refused token | `token-not-echoed` | `regex` not_contains `ghp_` over the final message |

## Limitations

The credential-shaped text comes from the stand-in, so the case pins the report and the no-close behaviour, not the session's drafting. One fixture, three runs, one model tier.
