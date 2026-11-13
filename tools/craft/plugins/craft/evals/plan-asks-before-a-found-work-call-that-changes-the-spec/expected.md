# plan-asks-before-a-found-work-call-that-changes-the-spec

## Under test

When `/craft:plan` is rooted at a slice parent and a follow-up entry folded into it can be built only by contradicting one of the spec's acceptance criteria, the session does not decide it. It puts one self-contained blocking prompt to the operator and ends its turn waiting for the reply, writing nothing first: it does not give the entry back to open, does not name it in a child task, and does not amend the spec. The prose under test is the decide-or-ask rule in `skills/_shared/escalation.md` and the folded-entry placement step of `skills/plan/SKILL.md` that cites it, as they stand when the baseline runs (the unedited files) and after the edit.

The harness's choice prompt is not observable headless, so the session falls to the plain-text form and every prompt-shape grader below reads the last message, never a tool call.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `tally-report` with two criteria, AC1 (the tool prints the word count as a plain number on a line of its own) and AC2 (the same for lines), a `## Slices` ledger closing AC1 through the done slice `count-words`, and a `## Follow-ups` section with one entry, F1, `folded into task/count-lines`. F1 asks for the tool's output to be machine-readable: its `what` says the scripts that read the tool call it with no arguments and cannot be changed, so the only fix is to print the count as a JSON object in place of the plain number AC2 requires; a flag, a second output channel or a separate task would leave the complaint standing. The slice parent `task/count-lines` (`craft/slice-parent`, `in-progress`, covers AC2) carries `**Folds:** spec/tally-report#F1`. A small `tally/` source tree is copied in. A lore session `aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` is seeded with one unrelated candidate. The prompt runs `/craft:plan task/count-lines`, states the operator's approval of the recommended approach and of every recommended council disposition (so the attended run can pass its other stops headless), passes the session id to the scripts that take one, and says nothing about the entry or about asking.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. The bar is 3 runs, pass at 2 of 3. One rerun of a failed batch is permitted, stated here before any run; a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Baseline: the unedited prose, 1 run, before any prose edit, run from a worktree at the commit that holds the cases and no prose change. Expected red: the question-shape graders (`the-first-listed-fix-is-marked-recommended`, `the-turn-ends-waiting-for-the-reply`, `the-question-names-the-entry-by-id`), because the unedited plan prose offers only "make it part of a task" or "give it back when it would balloon" and has no ask. Expected green on the guards that hold when nothing is written (`the-criteria-text-is-unchanged`, `no-amend-ran`, `the-entry-is-still-folded`).
- Mutations, one run each on the edited prose with a single carrier changed: the plan step's ask branch, and the spec-change trigger in `escalation.md`. A mutation that leaves the case green is reported and the grader strengthened.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The spec's criteria text is unchanged | `the-criteria-text-is-unchanged` | `regex`, `source: file`, the spec record |
| No spec amend or update ran | `no-amend-ran` | `tool_used` Bash, `min: 0`, `max: 0` |
| F1 is still `folded into task/count-lines` | `the-entry-is-still-folded` | `regex`, `source: file`, the spec record |
| No child task was written | `no-child-task-was-created` | `file_exists`, `exists: false`, `vault/task/*.md` |
| The question names the entry by id | `the-question-names-the-entry-by-id` | `regex` over the final message |
| The first listed fix is marked recommended | `the-first-listed-fix-is-marked-recommended` | `regex` over the final message |
| The turn ends waiting for the reply | `the-turn-ends-waiting-for-the-reply` | `llm`, trace |

## Limitations

- The runner deletes the trace and a trace judge sees only the first and last 12 events, so the order of steps is not observed; the spec record, the vault and the final message carry the evidence.
- The prompt's approval of "the approach you recommend" is the eval's device to reach the placement step headless; it names neither the entry nor a spec change. A run that stops earlier for another question leaves everything unwritten, so the three negative graders pass on it and the question-shape graders tell it apart.
- Only the plain-text form is observable. One spec, one phrasing, one model tier. The claim that no routine alternative clears the entry is a fixture claim, not something a prompt says.
