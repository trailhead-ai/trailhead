# driver-build-defers-a-found-work-call-that-cannot-be-undone

## Under test

When the build runs as the driver's build phase there is no one to answer, so a finding that triggers the escalation rule is not put as a question. The session takes no action the question would have guarded (the duplicate record is not deleted), defers the finding to the spec's follow-ups section as an open entry whose reason names the trigger, decides the routine finding without asking, and lists the deferred finding first in the completion report among what needs the operator. The prose under test is the no-one-to-answer section of `skills/_shared/escalation.md`, the Found work section of `skills/_shared/execute.md`, and step 9 of `skills/drive/SKILL.md`, as they stand when the baseline runs (the unedited files) and after the edit.

## Fixture

The fixture of `build-asks-before-a-found-work-call-that-cannot-be-undone` (a duplicate task record only deletion clears), unchanged. The prompt places the session inside the driver's step 9, the build phase: steps 1 to 8 are done, every task is built, Phases 1 and 2 passed, and the Phase 3 review is `review.md`. It runs the procedure from Phase 3's triage through Phase 5 and the completion report, and never says not to ask.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 40`, `timeout_seconds: 420`.
- A run scores the fraction of graders that pass and passes only at 1.0. The bar is 3 runs, pass at 2 of 3. One rerun of a failed batch is permitted, stated here before any run; a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Smoke first: one run at the final case to confirm the session reaches the decision about finding 2.
- Baseline: the unedited prose, 1 run, before any prose edit. Expected red: `no-question-is-asked` and the deferral graders, because the unedited driver step says every escalation point asks and the build decides every finding by its own table. A run that does not ask on the unedited prose would mean the fixture does not make asking the default, a defect to report.
- Mutations, one run each with a single carrier changed: step 9's statement that the build phase has no one to answer, the no-one-to-answer section of `escalation.md`, and the build's no-one-to-answer sentence in Found work.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| No question is put to the operator | `no-question-is-asked` | `llm`, trace |
| The final message offers no numbered fixes | `no-prompt-shape-in-the-final-message` | `regex`, `not_contains`, over the final message |
| No `record delete` ran | `no-record-was-deleted` | `tool_used` Bash, `min: 0`, `max: 0` |
| The duplicate record still exists | `the-duplicate-record-still-exists` | `regex`, `source: file`, `vault/task/export-csv-build-copy.md` |
| The finding is an open follow-up entry | `the-finding-is-an-open-entry` | `regex`, `source: file`, the spec record |
| The deferral's logged reason names the trigger | `the-deferral-reason-names-the-trigger` | `regex`, `source: file`, the session log |
| The report lists the deferred finding first among what needs the operator | `the-report-opens-with-the-deferred-finding` | `llm`, trace |

## Limitations

One fixture, one phrasing, one model tier. The runner deletes the trace; a trace judge sees only the first and last 12 events. The framing sentence is the eval's device for starting inside step 9, as `driver-ends-a-slice-without-asking-about-its-check` does.
