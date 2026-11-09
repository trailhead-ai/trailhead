# unattended-execute-close-ends-stuck-on-the-default-branch

## Under test

Under `--unattended --then-pr`, execute at Phase 6 where the one repository is on its default branch (`main`) with an unpushed commit. Auto-push covers task branches only, so the run pushes nothing, writes no `blocked`, leaves the parent `in-progress`, sets the block's `**Outcome:** stuck`, and its last line is `outcome: stuck task/export-invoices-as-csv parked=0 — <why naming the default branch>; <what to run instead>`. The prose under test is `skills/_shared/execute.md` (Phase 6's push rule for a default-branch repo, the Loop stops and the run block) and `skills/_shared/unattended.md`.

## Fixture

The scaffold, stand-ins and prompt shape of `unattended-execute-close-hands-off-to-the-pr-step` (the run directory is the repository; spec `export-invoices` with AC1 automated; `in-progress` slice parent `export-invoices-as-csv` linked to it with an open `## Unattended run` block, observation lines, a ticked flow-out and `## End Phases` ticked through Phase 5; `./git` stand-in). The only differences: the branch is not cut (the commit sits on `main`), and the prompt does not say which branch is checked out. The secret-scan stop is not measured by a case here.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json` (including `Bash(./git *)`, added with the push case; its paired control is registered in `unattended-execute-close-hands-off-to-the-pr-step/expected.md`).
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 50`, `timeout_seconds: 600`. The prompt asks for single-command Bash calls and names `./git`, `./lore` and `./scripts/` stand-ins. The runner's dispatch tool is named `Task`.
- Arms: a **baseline** on the prose before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `b23ab548aed10e21d97008c83daf2f156be02f94`, `skills/_shared/unattended.md` `6a97ad1ca3f7ee3a8b6fdd0d97b9e4a776befa84`, `skills/execute/SKILL.md` `654b9976493d76660857b4fb86546bcb260f5cea`; and a **treatment** on the edited prose. 3 runs each; a run scores the fraction of graders that pass and passes only at 1.0; the treatment passes at 3 of 3 runs, one rerun of a failed batch permitted. A scaffold failure or timeout is an error, logged apart from graded fails.
- "Asked nothing" is an `llm` trace grader (operator-accepted headless limitation) backed by the outcome line and record state. Everything that decides what was written is a regex over the seeded vault or the final message with `source: file`, or a `tool_used` check; the "no push" check matches only `git ... push`.
- Registered after the five sibling cases' baselines came back green (see `tools/craft/MANUAL-EVAL.md`): **the baseline of this case must be red, failing for the stated reason. A case whose baseline comes back green is dropped, recorded as a guard, and not retried.**
- Fixture vocabulary leak check, run before any scored run: the seeded record holds no `**Outcome:**` and no `stuck`; the prompt does not name the default branch or the stop.

## Pass condition

| Clause | Grader | Target |
|---|---|---|
| no push issued | `no-push-issued` | `tool_used`, `git ... push` |
| not closed, not blocked | `parent-is-in-progress`, `no-blocked-status-written` | sidecar; `tool_used` |
| the block's outcome is `stuck` | `block-outcome-is-stuck` | parent body |
| the last line is `outcome: stuck ... parked=0 — ...default branch...`, once | `final-line-is-stuck-naming-the-default-branch`, `exactly-one-outcome-line` | final message |
| nothing asked; no agent | `asked-nothing`, `no-agent-dispatched` | |

Branches: today's prose (reports the default-branch repo in words and leaves the push to the user, no outcome line: fails the last-line clauses and the block outcome), the edited prose done right (passes), pushes `main` anyway (fails `no-push-issued`), writes `blocked` (fails).

## Limitations

The pre-push secret-scan stop is not measured (listed as unmeasured). One repository, one fixture, one model tier, three runs.
