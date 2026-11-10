# slice-closes-a-stuck-slice-and-chooses-the-next

## Under test

Step 5 of `skills/slice/SKILL.md`, the open-slice guard: before refusing on an open slice parent, the pass runs `pending_check.py stuck --spec <n> --parent <n>`; on `stuck:` it reads `../_shared/execute.md` and runs its Phase 6 as the caller that says **run Phase 6 as a no-ask close** against that parent, then restarts from step 4 so the reconcile appends the closed slice's `partially covers` line before candidates are derived, and the pass goes on to state a next slice. Prose measured: `skills/slice/SKILL.md` and `skills/_shared/execute.md`.

## Fixture

Fixture (shared scaffold, `fixtures/scaffold.sh` of the first case, driven by an `ARM` variable): `ready` spec `schedule-courier-pickups` with AC1 (automated assertion), AC2 (manual check) and AC3 (automated assertion, uncovered), no `## Slices` ledger. One slice parent `pickups-can-be-booked-and-confirmed`, labelled `craft/slice-parent` and `craft/branch`, related to the spec, covering AC1 and AC2, Phases 1 to 5 ticked, Phase 6 unticked, AC1 with an automated observation, AC2 with a `manual-check` observation and no attestation. The run directory is a git repository on `main`, clean, the task branch pushed to a bare `origin.git`. `./lore`, `./scripts/pending_check.py`, `./scripts/observation_gate.py` and the other gates are wrappers over the real scripts and log each call to `lore-calls.log`. The prompt maps `${CLAUDE_PLUGIN_ROOT}/scripts/` to `./scripts/` and names neither the detector nor the close. Arm `ARM=close`: the parent is `in-progress` with no children. The prompt is `/craft:slice spec/schedule-courier-pickups`, bounded: stop once the pass has stated the slice it chose (step 8), and do not write that slice's parent task. The bound keeps the run inside the closed budget; it names neither the detector nor the close.

## Baseline-default check

The unedited step 5 refuses on any open slice: it never runs the detector and never closes. The default action is therefore the wrong one for this fixture (refuse), so a green baseline would be a design defect.

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 3`, the runner block's grants. A run scores the fraction of graders that pass and passes only at 1.0. Baseline on the unedited `skills/slice/SKILL.md` (content fingerprint `ad193edb75f5aec9f01a0bf9f33119fa1a70f9c9`); treatment bar 3 of 3 at 1.0. At most one rerun of a failed batch per arm, stated here before any run; a rerun-green is reported as rerun-green. A grader that must be corrected after a run is corrected in a dated "Deviations from pre-registration" section appended here before the next batch, with the prose fingerprint each batch ran on.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The detector ran against the parent | `detector-ran` | `regex` on `lore-calls.log` (`pending_check.py stuck --spec`) |
| The close ran: writer, verify, observation gate, then the done write | `writer-verify-gate-close` | `regex` on `lore-calls.log` |
| The close asked no question about a check | `no-question-asked` | `llm`, trace |
| The waiting entry for AC2 is on the spec | `waiting-entry-on-the-spec` | `regex` on the spec's body |
| The ledger gains the closed slice's line with `partially covers AC2` | `ledger-line-partially-covers` | `regex` on the spec's body |
| The pass went on to state a next slice, and did not refuse | `goes-on-to-a-next-slice` | `llm`, trace: the final message states a chosen slice for AC3 with a "You will be able to" claim and does not say a slice is still open |

Targeted mutation: delete only the stuck-check clause of step 5's guard; `detector-ran` and `writer-verify-gate-close` must go red.

## Limitations

One fixture, three runs, one model tier. The bound stops before the new slice's parent task is written, so step 9 is not exercised here (the `--unattended` arm exercises it).
