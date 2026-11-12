# unattended-execute-close-hands-off-to-the-pr-step

## Under test

Under `--unattended --then-pr`, execute at Phase 6 on a slice with every gate satisfied (one covered criterion, an automated observation, flow-out ticked) and one unpushed commit on its task branch with a bare local origin. The push lands, the parent is `done`, and the block's `**Checkpoint:** close` rides the same close write with no `**Outcome:**` line. Execute writes nothing after `done`, prints the completion report's phase lines but no outcome line, no line beginning `/portage:pull_request` or `/craft:`, and no question, and hands off to the PR step. The prose under test is `skills/_shared/execute.md` (Phase 6's push and close, Hand-off at close, the completion report) and `skills/_shared/unattended.md`.

**This case is designed as a guard.** The entry task already delivered the hand-off sentences, so the unedited prose is expected to pass; the case pins that this task's Phase 6 edits (the manual-check discharge, the push-stop branches, the found-work report) do not break the success path. If the baseline is red, the failing grader says which hand-off clause was missing and the treatment closes it.

## Fixture

`fixtures/scaffold.sh` seeds the same spec (one criterion, AC1, automated) and an `in-progress` slice parent `export-invoices-as-csv` as the sibling case, with `## Criterion observations` for AC1, `## End Phases` ticked through Phase 5, a ticked flow-out and an open `## Unattended run` block (checkpoint `workspace ready`). The run directory is itself the repository: `git init`, a bare `origin.git` beside it holding `main` only, and the branch `export-invoices-as-csv` checked out with one commit not on the remote. There is no `manifest.json`, so the procedure's vanilla "single current repo" applies. `./git` is a stand-in that execs a real git by absolute path (the runner's macOS sandbox cannot run the platform git shim, the same accommodation portage's cases make) and the prompt tells the run to use it wherever the procedure says `git`.

**A grant is added.** `Bash(./git *)` joins craft's `runner` block, which has no git grant; portage's block already carries it. The grant is anchored to a path only these cases' scaffolds create, so no existing case's run can use it. Paired control, registered here before any run: `unattended-execute-refuses-a-held-slice`, the existing case whose `no-git-command-issued` grader is the one a git grant could change, is run 3 times on the grant and must stay 3 of 3 (it was 3 of 3 on the same prose).

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json` plus `Bash(./git *)` (see Fixture).
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 50`, `timeout_seconds: 600`. The prompt asks for single-command Bash calls. The runner's dispatch tool is named `Task`, so "no agent dispatched" is `tool_used: Task` (the case grants no dispatch tool either way).
- Arms: a **baseline** arm on the prose as it stands before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `b23ab548aed10e21d97008c83daf2f156be02f94`, `skills/_shared/unattended.md` `6a97ad1ca3f7ee3a8b6fdd0d97b9e4a776befa84`, `skills/execute/SKILL.md` `654b9976493d76660857b4fb86546bcb260f5cea`; and a **treatment** arm on the edited prose. 3 runs each. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader (operator-accepted limitation: the judge sees only the first and last 12 messages and is not shown able to fail headless) backed by record state and, where the procedure ends the session, the outcome line. Everything that decides what was written is a regex over the seeded vault files or a script log with `source: file`, never a `tool_used` on Bash, except the two "no git command" and "no agent" checks, which are `tool_used` by design.
- Fixture vocabulary leak check, run before any scored run: the seeded parent holds no `Checkpoint: close`, no `**Outcome:**`, and the origin holds no `export-invoices-as-csv` ref; the prompt names the behaviours to carry out, not the end state. The graded files are the origin's branch ref, the parent's body and sidecar and the final message.
- Expected on the baseline: green, as a guard. If it is red, the reason is recorded in `tools/craft/MANUAL-EVAL.md` and the clause it names is the gap this task closes.

## Pass condition

Every clause holds.

| Clause | Grader | Target |
|---|---|---|
| the push landed | `push-landed` | origin's branch ref holds a commit id |
| the parent is `done` | `parent-is-done` | parent sidecar |
| the block's checkpoint is `close` | `block-checkpoint-is-close` | parent body |
| the block has no `**Outcome:**` line | `block-has-no-outcome` | parent body, not_contains |
| no `outcome:` line in the final message | `no-outcome-line` | final message, not_contains |
| no line begins `/portage:pull_request` or `/craft:` | `no-command-line` | final message, not_contains |
| no question to the operator; no agent | `asked-nothing`, `no-agent-dispatched` | |

### Each pass line against each branch, checked before any arm ran

The record clauses were run offline against a hand-run push and close write in a scaffolded directory, and the two message clauses against a message with and without an `outcome:` line and a command line.

| Branch | push | done | checkpoint close | no outcome in block | no outcome line | no command line | Whole run |
|---|---|---|---|---|---|---|---|
| Edited or today's, done right | pass | pass | pass | pass | pass | pass | passes |
| Attended-style report (prints the "No PR was opened" paragraph and the command) | pass | pass | pass | pass | pass | **fail** | fails |
| Prints `outcome: pr-ready ...` | pass | pass | pass | pass | **fail** | pass | fails |
| Writes the block's outcome at close | pass | pass | pass | **fail** | pass | pass | fails |
| Closes without pushing | **fail** | pass | pass | pass | pass | pass | fails |

## Limitations

- One repository, one commit, a local bare origin: the secret scan's hit branch, the default-branch refusal and a failed push are not exercised, so the `stuck` ends for those three stops rest on the prose and review.
- "Execute writes nothing to the record after `done`" is observed only through the end state, not write order.
- The run-metrics row and tally are left out of the prompt.
- One fixture, one phrasing, one model tier.
