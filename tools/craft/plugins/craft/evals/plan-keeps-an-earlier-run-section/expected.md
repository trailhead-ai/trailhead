# plan-keeps-an-earlier-run-section

## Under test

`/craft:plan task/<slice> --unattended` on a slice parent that already carries a closed `## Unattended run` block (outcome `slice-chosen`, nothing parked) keeps that block byte for byte through step 8's full-body parent write, and appends a second block after it. The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `report-export` and the slice parent `write-totals-to-a-named-report-file` (`in-progress`, label `craft/slice-parent`, linked to the spec) whose body carries the value claim and an earlier block from a slice-only session that parked nothing (an earlier block that parked a question would make a later slice pass re-park it, which is not this case's subject). The prompt runs `/craft:plan task/write-totals-to-a-named-report-file --unattended`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent]`, `max_turns: 120`, `timeout_seconds: 900`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap of the task forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose before any prose edit. It is expected red on the preservation and second-block clauses: the unedited full-body write preserves only the value claim and enumerated states.
- A headless eval session never offers `AskUserQuestion`, so a `tool_used AskUserQuestion` grader would pass whether or not the ritual wanted to ask. "Asked nothing" is graded by behaviour: the run reaches its typed outcome line, writes the expected record state, and an `llm` trace grader confirms the last message poses no question and waits on no answer. That `llm` grader is not shown able to fail in a headless run (operator-accepted limitation); the outcome line and the record and command evidence are the primary evidence.
- The `llm` and regex graders read the last message; the regex dialect is JavaScript.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The earlier block is still present byte for byte | `earlier-block-preserved-byte-for-byte` | `regex`, `source: file` |
| A second block follows it | `a-second-block-follows` | `regex`, `source: file` |
| The last line is `outcome: plan-ready task/<name> parked=0` | `final-line-is-plan-ready` | `regex` over the final message |
| The run got past step 8's full-body write (the council section is on the parent) | `plan-reached-the-council-review` | `regex`, `source: file` |

## Limitations

- Question numbering across blocks is not graded: the earlier block parks nothing, so there is no `Q1` for a later block to reuse, and a reuse check would pass vacuously. No case exercises a `Q<n>` that follows an earlier block's question.
- Amended before any edit to the prose, after the first baseline run: the final-line clause was loosened to any outcome kind and a run that stopped before step 8 passed every grader (1 run, score 1.0, 19 turns). The final line is now `plan-ready` and the council section must be on the parent, so the case can only pass by reaching and surviving the full-body write.
- One fixture, one phrasing, one model tier.

## Addendum: no-decisions grader

Authored with the follow-ups entries-path case, before any run of it. `reports-no-found-work-decisions` is a `regex` over the final message for `The plan made no decisions about found work.`: this fixture folds nothing, so plan's closing report prints the line `found_work.py report --session plan` prints, above the outcome line. Expected red on the unedited prose and green on the edited prose.
