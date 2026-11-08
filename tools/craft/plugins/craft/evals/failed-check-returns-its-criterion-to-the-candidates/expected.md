# failed-check-returns-its-criterion-to-the-candidates

## Under test

When the check waiting on a criterion has failed (the entry's latest history line is `failed`), the criterion is an ordinary candidate again: the slice ritual's pass chooses it, states the slice's value claim, and neither reports the spec complete nor records the loop stopped nor takes the waiting branch. The prose under test is `skills/slice/SKILL.md` (steps 4, 6 and 8: waiting criteria are absent from selection, and only a `waiting` entry puts one there), as it stands when the baseline runs and after the edit. This is the companion of `waiting-checks-hold-the-loop-open`; the two share one spec shape and differ only in the entry's latest status.

## Fixture

`fixtures/scaffold.sh` seeds the same `ready` spec `print-shipping-labels` as the companion case, but its `## Waiting` entry for AC3 has a second history line, `failed`. The gate output is `candidates: AC3`, `partial: AC3`, `waiting: none`, `complete-eligible: yes`. The `./lore` wrapper logs every call's arguments to `lore-calls.log`. The prompt runs `/craft:slice spec/print-shipping-labels`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants from craft's `eval-gate.json` runner block. Limits as the companion case.
- A run scores the fraction of graders that pass and passes only at 1.0; the bar is 3 of 3 runs. One rerun of a failed batch is permitted, stated here before any run.
- The baseline on the unedited prose is expected green: today's prose already selects a candidate when `candidates:` is non-empty, so this case is a guard against the edit over-reaching (reading any `## Waiting` entry as a hold), not a detector. That is stated before the run.
- The runner block grants no `covers_gate.py`, so the pass may be unable to materialize the parent after it states the claim; no clause depends on the write landing.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The run reached the gate | `reached-the-gate` | `regex` on `lore-calls.log`, `record criteria`, contains |
| No completion write | `no-completion-write` | `regex` on `lore-calls.log`, `craft/slice-loop=complete`, not_contains |
| No stop write | `no-stop-write` | `regex` on `lore-calls.log`, `craft/slice-loop=stopped`, not_contains |
| No amend | `no-amend` | `regex` on `lore-calls.log`, `record amend`, not_contains |
| The spec carries no loop label | `spec-carries-no-loop-label` | `regex` on `vault/spec/print-shipping-labels.json`, not_contains |
| The reply chooses the AC3 slice and states what it delivers, and neither calls the spec complete, stopped, nor waiting | `chose-the-failed-criterion` | `llm`, `focus: trace` |

### Each pass line against each branch, checked before any arm ran

| Branch of the prose | no completion | no stop | no amend | no loop label | reply clause | Whole run |
|---|---|---|---|---|---|---|
| Complete | **fail** | pass | pass | **fail** | **fail** | fails |
| Stopped | pass | **fail** | **fail** | **fail** | **fail** | fails |
| Waiting (companion case's pass path) | pass | pass | pass | pass | **fail** (reports a wait, chooses nothing) | fails |
| Selection of AC3 | pass | pass | pass | pass | pass | passes |

Only the selection branch passes; the `failed` entry must not be read as a hold.

## Limitations

- One spec, one phrasing, one model tier, three runs.
- The sanctioned write for the chosen slice (the parent task) may be denied by the runner's grants; the reply clause allows that. Whether a created parent carries the right fields is not measured here.
- The `llm` reply clause sees the first and last 12 events of the run.

## Amendment after the baseline

The `llm` clause first required the phrase "You will be able to". One baseline run chose AC3 correctly and stated its value, but worded it "What you'll be able to do", and the judge failed it. That is a grader defect, not a result: the clause now asks for a stated value claim in the run's own words. Edited after seeing a run; the baseline of this guard case was otherwise green. The first baseline batch is reported as run, 2 of 3.
