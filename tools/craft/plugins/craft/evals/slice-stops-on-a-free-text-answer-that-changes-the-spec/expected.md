# slice-stops-on-a-free-text-answer-that-changes-the-spec

## Under test

When the operator's free-text answer would amend the spec or needs new thinking, the session says so plainly, stops, and writes nothing; the spec change has its own route. The prose under test is the free-text handling under "After the answer" in `skills/_shared/escalation.md` and the pointer to it in `skills/slice/SKILL.md` step 7.

## Arm

The free-text reply: change AC2 to count only positive amounts. That rewrites an acceptance criterion, which no listed fix offered, so the spec's AC2 stays as it is, F1 stays open, and the final message says the session stopped.

## Fixture

The case resumes a seeded conversation (`context.history_file: fixtures/history.jsonl`, rebuilt by `fixtures/build_history.py`). In it the operator ran `/craft:slice spec/invoice-summary` (the same prompt as `slice-asks-before-dropping-an-urgent-entry`, including its session-id instruction), the session read the spec, and its last message put the blocking prompt: F1, a security finding at `billing-service/export.py:88`, what is at stake, and three fixes (1 move it to `task/billing-export-hardening`, recommended; 2 move it to `task/billing-service-audit`; 3 keep it open and stop), ending "Reply with a number, or tell me in your own words what to do." The seeded text carries no read of `skills/_shared/escalation.md` or of the slice skill and no sentence under test. The scaffold is the urgent-entry case's: a ready spec `invoice-summary` with AC1 and AC2 both covered by done slice parents, one open entry F1 (the security finding), two open tasks `task/billing-export-hardening` and `task/billing-service-audit`, a lore session `aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee`. `prompt.md` is the operator's free-text reply.

The entry is a security finding, so a drop needs `operator_confirmed: true` in the `found_work.py entry` document; without it the script refuses with `reason-code: drop-needs-operator`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 80`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. The bar is 3 runs, pass at 2 of 3. One rerun of a failed batch is permitted, stated here before any run; a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Baseline: the prose at the commit that adds the case and no prose change, 1 run per case. Expected red and green are stated per case below.
- Mutations: one run each on the edited prose with a single carrier changed, one per carrier of the sentence the case measures, listed in `tools/craft/MANUAL-EVAL.md`. Every mutation that leaves the case green is reported and the grader strengthened.
- The runner writes an untracked `fixtures/<sessionId>.jsonl` beside the seed on a run. Delete it before staging (`docs/eval-protocol.md`, seeded-history cases).
- Expected at baseline: `the-session-says-it-stopped` red or flaky; the graders on files hold only if the model leaves the spec alone, and a red one at baseline is the measurement.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| AC2's text is unchanged | `the-spec-criterion-is-unchanged` | `regex`, `source: file`, the spec record |
| F1 is still open in the spec | `the-entry-is-still-open` | `regex`, `source: file`, the spec record |
| No `found_work.py entry` ran | `no-entry-drop-ran` | `tool_used` Bash, `min: 0`, `max: 0` |
| No record was created, updated, amended or deleted | `no-record-was-written` | `tool_used` Bash, `input_match` matching `record` then create, update, amend or delete, `min: 0`, `max: 0` |
| No document carries `operator_confirmed` | `no-operator-confirmed-was-written` | `tool_used` Write, `min: 0`, `max: 0` |
| The final message says plainly that the session stopped | `the-session-says-it-stopped` | `llm`, trace |

## Limitations

- The runner deletes the trace and a trace judge sees only the first and last 12 events; a resumed run's new turns are few enough that the `llm` graders read all of them, and their verdicts are read against the kept trace when `--keep-temp` is used. Graders see only the new turns, never the seeded history.
- Whether a seeded history resumes in a craft target, and whether the model re-enters slice step 7 from it with no skill loaded in the seeded turns, is unconfirmed until the smoke run of the first arm; the runner could not be started where the case was built.
- Only the plain-text form of the prompt is observable headless.
- One spec, one phrasing, one model tier.
