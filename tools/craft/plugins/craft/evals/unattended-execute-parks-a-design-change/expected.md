# unattended-execute-parks-a-design-change

## Under test

Under `--unattended --then-pr`, execute's per-task loop at step 2 on an `INVALIDATED` assumption-prover report meaning a design change: the spec's chosen mechanism is unavailable and every child depends on it. The run parks it as `Q1` in the block (options with their costs, one marked recommended, a progress line), sets the block's `**Outcome:** needs-a-decision`, and ends `outcome: needs-a-decision task/count-words parked=1`. The park is written before the outcome line is printed. It writes no `blocked` status, leaves the parent `in-progress` and the spec unchanged, writes no `**Answer:**` line, and asks nothing. The prose under test is `skills/_shared/execute.md` (step 2, Handling Assumption-Prover Status, the Red Flags line, the Blocked-Path Write's unattended exception) and `skills/_shared/unattended.md`.

## Fixture

The same scaffold and prompt as `unattended-execute-adjusts-for-an-invalidated-assumption`. Only `fixtures/prover-report.md` differs: its evidence says the pinned shared package no longer ships `io_utils`, both children and the spec's chosen mechanism depend on it, and no change to one child restores it.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json` (no grant added). Every `claude` capture is wrapped in a process-level `timeout`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 40`, `timeout_seconds: 420`. The prompt asks for single-command Bash calls. The runner's dispatch tool is named `Task`, so "no agent dispatched" is `tool_used: Task` (the case grants no dispatch tool either way).
- Arms: a **baseline** arm on the prose as it stands before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `8b624539946ea8caa9a0e806d1d7ef1c3a668073`, `skills/_shared/unattended.md` `3ab326d3381936bc8b34de9ed0f22cba7d20d225`, `skills/execute/SKILL.md` `654b9976493d76660857b4fb86546bcb260f5cea`; and a **treatment** arm on the edited prose. 3 runs each. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader plus record state and the outcome line (operator-accepted limitation: the judge sees only the first and last 12 messages and is not shown able to fail headless). The decision and the park are graded by record state: regexes over the seeded vault files with `source: file`, never by a `tool_used` on Bash.
- Seeded partial-procedure shape: the run is told it already claimed the slice and is at loop step 2 with the prover report as a file in its directory. This is the shape validated for the plan (12 of 12 runs reached the step on the baseline prose). The two cases share one scaffold and one prompt; they differ only in `fixtures/prover-report.md`, in its evidence.
- Fixture vocabulary leak check, run before any scored run: no fixture wording satisfies a grader on its own. The record graders read the slice parent's body and sidecar, one child's body and the spec sidecar. The seeded parent carries the block heading and an empty `**Decisions:**` list by design and is graded by a numbered entry with `Question:`, `Choice:` and `Reasoning:` lines, a `Q<n>:` entry and an `**Outcome:**` line, none of which the seed holds; the seeded child holds none of `invalidat`, `prover`, `assumption`, `because`, `UTF-8`, `decode` or `errors=`. No fixture file sits beside `expected.md` where a run can read it: the run directory holds the scaffolded vault, the lore wrapper and `prover-report.md` only.
- Expected **red** on the baseline: the unedited prose reports to the user and, if the run ends, writes `blocked` on the parent; it prints no `outcome:` line and parks nothing (`final-line-is-needs-a-decision-parked-1`, `exactly-one-outcome-line`, the `Q1` graders, `block-outcome-is-needs-a-decision`, `parent-still-in-progress` and `no-blocked-status-written` fail).

## Pass condition

Every clause holds: [`q1-has-two-options-each-with-a-cost-and-a-progress-line`] and [`q1-marks-exactly-one-option-recommended`] `Q1` is written in the block's shape; [`q1-is-about-the-unavailable-mechanism`]; [`block-outcome-is-needs-a-decision`]; [`final-line-is-needs-a-decision-parked-1`] the last line is `outcome: needs-a-decision task/count-words parked=1`; [`exactly-one-outcome-line`]; [`no-blocked-status-written`]; [`parent-still-in-progress`]; [`spec-record-unchanged`]; [`no-answer-line-written`]; [`no-agent-dispatched`]; [`no-git-command-issued`]; [`asked-nothing`].

## Limitations

- The order "park written before the outcome line" is observed only through the end state (both `Q1` and the block outcome on the record when the line is printed); no grader sees write order.
- "Exactly one option recommended" counts the `(recommended)` marker in `Q1`'s options line; it cannot tell whether the marked option is the right one.
- One fixture, one phrasing, one model tier.
