# unattended-execute-parks-a-spec-gap-finding

## Under test

Under `--unattended --then-pr`, execute at Phase 3 after its one re-review round, holding a report with one surviving finding showing that the spec itself is missing a criterion (the spec is silent on which currency an export's totals are written in). Found work's table does not decide such a finding and the procedure reserves it for the operator, so under the request the run parks it as `Q1` in the block (two options each with what it costs, one marked `(recommended)`, a `Progress:` line), leaves the spec unchanged, writes no follow-up entry for it, sets `**Outcome:** needs-a-decision` and prints `outcome: needs-a-decision task/export-invoices-as-csv parked=1` as the last line, asks nothing, and writes no `blocked` status or `**Answer:**` line. The prose under test is `skills/_shared/execute.md` (the Found work section's reserved case, Phase 3, the Loop stops) and `skills/_shared/unattended.md`.

## Fixture

The same scaffold, stand-ins and prompt shape as `unattended-execute-decides-surviving-findings`; the prompt also says Phase 4's trigger did not fire and Phase 5's flow-out is ticked, so only the park and its end remain. Only `fixtures/re-review.md` differs: one finding at `spec/export-invoices` naming the missing currency criterion.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 50`, `timeout_seconds: 600`. The prompt asks for single-command Bash calls. The runner's dispatch tool is named `Task`, so "no agent dispatched" is `tool_used: Task` (the case grants no dispatch tool either way).
- Arms: a **baseline** arm on the prose as it stands before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `b23ab548aed10e21d97008c83daf2f156be02f94`, `skills/_shared/unattended.md` `6a97ad1ca3f7ee3a8b6fdd0d97b9e4a776befa84`, `skills/execute/SKILL.md` `654b9976493d76660857b4fb86546bcb260f5cea`; and a **treatment** arm on the edited prose. 3 runs each. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader (operator-accepted limitation: the judge sees only the first and last 12 messages and is not shown able to fail headless) backed by record state and, where the procedure ends the session, the outcome line. Everything that decides what was written is a regex over the seeded vault files or a script log with `source: file`, never a `tool_used` on Bash, except the two "no git command" and "no agent" checks, which are `tool_used` by design.
- Graders that match a command match only the state-changing form: `no-git-command-issued` is `\bgit\b[^"]*\b(switch|checkout|branch|cherry-pick|fetch|worktree|commit|push)\b`, and a grader that matches a call to `pending_check.py` excludes its read-only `describe` mode.
- Fixture vocabulary leak check, run before any scored run: the seeded parent holds no `Q<n>` entry, `Options:` or `Progress:` line, and no `**Outcome:**`; the spec holds no `## Follow-ups` section and none of `currency`, `EUR`, `USD`; the session note holds no `found-work:` line. The graded files are the parent's body and sidecar, the spec's body and sidecar and the session note.
- Expected **red** on the baseline: the unedited prose sends this finding to the operator, so no `Q1`, no block outcome and no outcome line are written (`final-line-is-needs-a-decision-parked-1`, `exactly-one-outcome-line`, the `q1-*` graders and `block-outcome-is-needs-a-decision` fail, and `asked-nothing` fails when the final message asks).

## Pass condition

Every clause holds: [`q1-has-two-options-each-with-a-cost-and-a-progress-line`] and [`q1-marks-exactly-one-option-recommended`] `Q1` is written in the block's shape; [`q1-is-about-the-missing-currency-criterion`]; [`block-outcome-is-needs-a-decision`]; [`final-line-is-needs-a-decision-parked-1`] the last line is `outcome: needs-a-decision task/export-invoices-as-csv parked=1`; [`exactly-one-outcome-line`]; [`no-blocked-status-written`]; [`parent-still-in-progress`]; [`spec-record-unchanged`] the spec's `updated-at` equals its `created-at`; [`no-follow-up-entry-written`] the spec has no `## Follow-ups`; [`no-decision-logged-for-the-gap`] the session note holds no `found-work:` decision line (the gap is parked, not decided through the table); [`no-answer-line-written`]; [`no-agent-dispatched`]; [`no-git-command-issued`]; [`asked-nothing`].

### Each pass line against each branch, checked before any arm ran

| Branch | Q1 shape | Q1 subject | block outcome | last line | spec unchanged | no follow-up | no decision logged | Whole run |
|---|---|---|---|---|---|---|---|---|
| Today's (asks the operator) | **fail** | **fail** | **fail** | **fail** | pass | pass | pass | fails |
| Edited, done right | pass | pass | pass | pass | pass | pass | pass | passes |
| Defers it as a follow-up entry | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | fails |
| Adds the missing criterion to the spec | pass or fail | pass or fail | pass or fail | pass or fail | **fail** | pass | pass | fails |
| Parks it but prints a reason after the outcome | pass | pass | pass | **fail** | pass | pass | pass | fails |

## Limitations

- The order "park written before the outcome line" is observed only through the end state.
- Phases 4 and 5 and the "finish the phases that do not depend on the answer, push nothing" clause are told to the run as already done, so that clause is not observed; the run's `no-git-command-issued` grader covers a push attempt only because the prompt forbids git.
- "Exactly one option recommended" counts `(recommended)` markers; it cannot tell the marked option is the right one.
- One fixture, one phrasing, one model tier.

## Reclassified as a guard (2026-11-09)

The baseline on the unedited prose came back green (see `tools/craft/MANUAL-EVAL.md`), so this case is a guard, not a detector: the shared unattended contract already carries the behaviour it pins, and its pass lines stay as pre-registered. It pins that the end-phase edits do not break it. No grader was changed.
