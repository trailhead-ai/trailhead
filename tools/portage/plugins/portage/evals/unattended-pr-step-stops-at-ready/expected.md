# unattended-pr-step-stops-at-ready

## Under test

The unattended branch of `skills/pull_request/SKILL.md` (`create --unattended task/<slice>`), the one-line `create` fix in `agents/updater.md`, and the `stop_at_ready` / outcome-file behaviour of `agents/monitor.md` that the skill dispatches. The claim: with the request and a valid record, the PR step continues execute's open block at `close`, holds the branch, asks nothing, opens the PR (the branch is pushed and has none), dispatches monitor in the background with `stop_at_ready: true` and an outcome file, waits with `portage await-outcome`, and on `READY` sets the block's outcome and ends `outcome: pr-ready task/count-words parked=0` carrying the PR link, never merging.

## Fixture

A scratch directory built by the shared `evals/pr-step-scaffold.sh` (each case's `fixtures/scaffold.sh` sets the parameters in `fixtures/params`): a repo whose slice branch `count-words` is pushed to a bare `origin.git` with one further local-only commit (so any push moves the origin ref), a camp manifest and group TOML, `./gh` and `./portage` stand-ins (`./gh` keeps a PR's state in a file and answers `pr list`, `pr view`, `pr create`, `pr edit`; `./portage` answers `wait-for-actionable` and records any `merge` call to `merge.log`, and passes `hold`, `await-outcome`, `detect-repos`, `sidecar` and `eval-gate` through to the real CLI with its state under the run directory; its help output has `hold`, `await-outcome`, `eval-gate` and `release` scrubbed), and a seeded lore vault with a `./lore` wrapper holding `task/count-words`, a `done` slice parent with `craft/slice-parent` and `craft/branch=count-words` labels and an `## Unattended run` block. Nothing here sits beside `expected.md` where a run can read it: the run directory holds the scaffolded tree only.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/portage/plugins/portage`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants exactly the `runner` block of `eval-gate.json` (with `Write`, `Bash(./lore *)`, `Bash(mktemp)` and `Bash(mktemp *)` added by the same change). Every invocation is wrapped in a process-level `timeout`. Case limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent]`, `max_turns: 100`, `timeout_seconds: 900`.
- Arms: a **baseline** on the prose as it stands before any edit, fingerprinted: `skills/pull_request/SKILL.md` `ff9aabc925036499c1f614bac9babcac682f57a8`, `agents/updater.md` `89518f4ec7996ef36e713d53c4fd6e4d89d4febb`, `agents/monitor.md` `b1da45e2cbbe62ebb5ad30b7ff9f6f8ce3f44f56` (`git hash-object`); and a **treatment** on the edited prose. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted and is reported as a rerun. A scaffold failure or timeout is an error, logged apart from graded fails.
- Each "no X command" grader matches the command (the word before the subcommand, a space, the subcommand), never a path fragment, and was checked offline against a constructed matching and non-matching tool input before any scored run.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace judge plus record state and the outcome line (an operator-accepted limitation: the judge sees only the first and last 12 events).
- Record state is graded by `regex` with `source: file` over the seeded vault's files, never by a `tool_used` on Bash.

## Pass condition

Every clause holds: [`final-line-is-pr-ready`] the last line is `outcome: pr-ready task/count-words parked=0`; [`outcome-line-carries-the-pr-link`] it carries the PR link; [`exactly-one-outcome-line`]; [`block-outcome-is-pr-ready`] the block's `**Outcome:** pr-ready` is on the done record; [`one-block-continued`] and [`rituals-name-the-pr-step`] execute's block was continued, not a second one opened; [`parent-still-done`]; [`no-merge-recorded`] and [`no-merge-command`]; [`branch-was-held`]; [`monitor-dispatched-with-stop-at-ready`], [`monitor-dispatched-in-background`] and [`waited-with-await-outcome`]; [`asked-nothing`].

Predicted baseline (red): the committed skill has no `--unattended` branch; it dispatches updater and monitor attended, tells the user it will notify them, prints no `outcome:` line and holds nothing, so every clause but `parent-still-done`, `no-merge-recorded` and `no-merge-command` fails.

## Limitations

- One fixture, one phrasing, one model tier for the runner; the dispatched updater and monitor run on their own pins.
- Monitor's outcome line is written by the background monitor through the runner's `Write` grant; the real `wait-for-actionable`, `gh` and `merge` are stand-ins, so this measures the skill's wait, mapping and block write and monitor's obedience, not the CI watch itself.
- Write order ("block before push", "outcome written before the final line") is observed through end state and the tool-call set only.
- The credential scrub of the block diff, the temp-file route for the diff and the one retry of a non-applying diff are not graded.
