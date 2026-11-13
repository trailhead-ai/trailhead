# slice-carries-out-a-free-text-answer-to-its-question

## Under test

When the operator answers a blocking prompt in free text, the session reads the answer back in one line before acting ("Reading your answer as: ..."), and a reading that stays inside the listed fixes' actions is carried out and the session continues from where it stopped. The prose under test is the free-text handling under "After the answer" in `skills/_shared/escalation.md`, and the pointer to it in `skills/slice/SKILL.md` step 7. The decision log records the choice as the operator's: the drop is written with `operator_confirmed` set, from a reply in a turn after the question.

## Arm

The free-text reply: move it to `billing-export-hardening`, that is where it belongs. It names listed fix 1 in the operator's own words, so the reading is one of the offered fixes and is carried out: F1 is dropped with a reason naming `task/billing-export-hardening`, and the pass goes on.

## Fixture

The case resumes a seeded conversation (`context.history_file: fixtures/history.jsonl`, rebuilt by `fixtures/build_history.py`). In it the operator ran `/craft:slice spec/invoice-summary` (the same prompt as `slice-asks-before-dropping-an-urgent-entry`, including its session-id instruction), the session read the spec, and its last message put the blocking prompt: F1, a security finding at `billing-service/export.py:88`, what is at stake, and three fixes (1 move it to `task/billing-export-hardening`, recommended; 2 move it to `task/billing-service-audit`; 3 keep it open and stop), ending "Reply with a number, or tell me in your own words what to do." The seeded text carries no read of `skills/_shared/escalation.md` or of the slice skill and no sentence under test. The scaffold is the urgent-entry case's: a ready spec `invoice-summary` with AC1 and AC2 both covered by done slice parents, one open entry F1 (the security finding), two open tasks `task/billing-export-hardening` and `task/billing-service-audit`, a lore session `aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee`. `prompt.md` is the operator's free-text reply.

The entry is a security finding, so a drop needs `operator_confirmed: true` in the `found_work.py entry` document; without it the script refuses with `reason-code: drop-needs-operator`.

This is the case's only arm that expects an action. Its siblings are `slice-asks-again-on-a-free-text-answer-that-cannot-be-undone` and `slice-amends-the-spec-on-a-free-text-answer-that-changes-it`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 80`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. The bar is 3 runs, pass at 2 of 3. One rerun of a failed batch is permitted, stated here before any run; a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Baseline: the prose at the commit that adds the case and no prose change, 1 run per case. Expected red and green are stated per case below.
- Mutations: one run each on the edited prose with a single carrier changed, one per carrier of the sentence the case measures, listed in `tools/craft/MANUAL-EVAL.md`. Every mutation that leaves the case green is reported and the grader strengthened.
- The runner writes an untracked `fixtures/<sessionId>.jsonl` beside the seed on a run. Delete it before staging (`docs/eval-protocol.md`, seeded-history cases).
- Expected at baseline (today's prose, which says nothing of a free-text answer): `the-entry-is-dropped-with-a-reason-naming-the-task`, `the-drop-carries-the-operators-confirmation`, `the-session-went-on-after-the-drop` and `no-record-was-deleted` may be green, because a model usually acts on a plain answer.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| F1 is dropped with a reason that names the task the operator chose | `the-entry-is-dropped-with-a-reason-naming-the-task` | `regex`, `source: file`, the spec record, `dropped: .*task/billing-export-hardening` |
| The drop was written with the operator's confirmation | `the-drop-carries-the-operators-confirmation` | `tool_used` Write, `input_match` of `"operator_confirmed": true` (its quotes may arrive escaped in the serialized input), `min: 1` |
| The session went on after the drop | `the-session-went-on-after-the-drop` | `llm`, trace |
| Nothing was deleted | `no-record-was-deleted` | `tool_used` Bash, `input_match: record delete`, `min: 0`, `max: 0` |

## Limitations

- The reason's wording is not graded: `found_work.py` may rewrite a security finding's reason, so the regex needs only the task id.
- The read-back is not graded in this case: the session says the line mid-run, before the drop, outside what a grader observes (the trace judge's window holds only the first and last 12 events of a 28 to 35 turn run, so the line, and the drop's Write, usually fall outside it). The read-back (AC17) is graded by `slice-asks-again-on-a-free-text-answer-that-cannot-be-undone`, where the turn ends on it, and is kept as a manual check.
- The runner deletes the trace and a trace judge sees only the first and last 12 events; a resumed run's new turns are few enough that the `llm` graders read all of them, and their verdicts are read against the kept trace when `--keep-temp` is used. Graders see only the new turns, never the seeded history.
- Whether a seeded history resumes in a craft target, and whether the model re-enters slice step 7 from it with no skill loaded in the seeded turns, is unconfirmed until the smoke run of the first arm; the runner could not be started where the case was built.
- Only the plain-text form of the prompt is observable headless.
- One spec, one phrasing, one model tier.
