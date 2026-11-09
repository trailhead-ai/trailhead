# unattended-execute-close-names-a-brainstorm-task-by-id

## Under test

Under `--unattended --then-pr`, execute closes successfully at Phase 6 on a spec-linked slice whose build already logged one found-work decision, `brainstorm`, for a finding too open for any slice (task `streaming-export-design`, `route=brainstorm`). The completion report does not carry the found-work script's needs-the-operator line as printed: it names the brainstorm task by its record id, `task/streaming-export-design`, prints no `/craft:brainstorm` command and no line beginning `/portage:pull_request` or `/craft:`, no outcome line, and asks nothing; the close lands and the block stays open. The prose under test is `skills/_shared/execute.md` (the completion report, the Found work section's report paragraph, Hand-off at close) and `skills/_shared/unattended.md`.

## Fixture

The scaffold, stand-ins and prompt shape of `unattended-execute-close-hands-off-to-the-pr-step` (the run directory is the repository; spec `export-invoices` with AC1 automated; `in-progress` slice parent `export-invoices-as-csv` linked to it with an open `## Unattended run` block, observation lines, a ticked flow-out and `## End Phases` ticked through Phase 5; `./git` stand-in). The scaffold also runs the real `found_work.py decide` once, with the session id the prompt names, for a `brainstorm` decision on parent `task/export-invoices-as-csv`, which creates the brainstorm task and the session note's decision lines.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json` (including `Bash(./git *)`, added with the push case; its paired control is registered in `unattended-execute-close-hands-off-to-the-pr-step/expected.md`).
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 50`, `timeout_seconds: 600`. The prompt asks for single-command Bash calls and names `./git`, `./lore` and `./scripts/` stand-ins. The runner's dispatch tool is named `Task`.
- Arms: a **baseline** on the prose before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `b23ab548aed10e21d97008c83daf2f156be02f94`, `skills/_shared/unattended.md` `6a97ad1ca3f7ee3a8b6fdd0d97b9e4a776befa84`, `skills/execute/SKILL.md` `654b9976493d76660857b4fb86546bcb260f5cea`; and a **treatment** on the edited prose. 3 runs each; a run scores the fraction of graders that pass and passes only at 1.0; the treatment passes at 3 of 3 runs, one rerun of a failed batch permitted. A scaffold failure or timeout is an error, logged apart from graded fails.
- "Asked nothing" is an `llm` trace grader (operator-accepted headless limitation) backed by the outcome line and record state. Everything that decides what was written is a regex over the seeded vault or the final message with `source: file`, or a `tool_used` check; the "no push" check matches only `git ... push`.
- Registered after the five sibling cases' baselines came back green (see `tools/craft/MANUAL-EVAL.md`): **the baseline of this case must be red, failing for the stated reason. A case whose baseline comes back green is dropped, recorded as a guard, and not retried.**
- Fixture vocabulary leak check, run before any scored run: the seeded parent and spec name no brainstorm task and hold no `/craft:`; the task id `streaming-export-design` exists only in the task the scaffold's script call creates and in the session note, and the prompt never names it.

## Pass condition

| Clause | Grader | Target |
|---|---|---|
| the parent is `done` | `parent-is-done` | sidecar |
| the block has no `**Outcome:**` line | `block-has-no-outcome` | parent body |
| no `/craft:brainstorm` command anywhere in the message | `no-brainstorm-command` | final message |
| no line begins `/portage:pull_request` or `/craft:`; no `outcome:` line | `no-command-line`, `no-outcome-line` | final message |
| the message names `task/streaming-export-design` | `names-the-brainstorm-task-by-id` | final message |
| nothing asked; no agent | `asked-nothing`, `no-agent-dispatched` | |

Branches: today's prose (carries the script's report as printed, including "Run `/craft:brainstorm task/streaming-export-design`": fails `no-brainstorm-command`), the edited prose done right (passes), drops the brainstorm task from the report (fails the id clause).

## Limitations

One brainstorm decision; the failed-write and `exit 2` lines of the script's report are not exercised. One fixture, one model tier, three runs.

## Fixture amended after the scored runs (2026-11-09)

The baseline (3 runs) and a first treatment batch (3 runs) both failed `names-the-brainstorm-task-by-id` in every run. A kept trace of one treatment run showed why: the run called `found_work.py report --parent ...` without `--session-id`, which the script answers with `session-unreadable`, so it never saw the brainstorm task. In a live session the script finds the session through the environment. The scaffold's `./scripts/found_work.py` wrapper now exports `CLAUDE_CODE_SESSION_ID` (the id the prompt names). No grader changed. Both arms are re-run on the amended fixture, the baseline on the unedited prose; the earlier runs are not counted.
