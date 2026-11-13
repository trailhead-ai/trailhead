# unattended-pr-step-refuses-a-default-branch-label

## Under test

The unattended branch of `skills/pull_request/SKILL.md` (`create --unattended task/<slice>`). The updater and monitor agents are not under test: this run ends before the skill dispatches any subagent, which the `no-agent-dispatched` grader pins, so no change to either agent can move its result. The claim: with the record's `craft/branch` label naming the repo's default branch (`develop` here, which `origin/HEAD` names and the pushed check alone would accept), the run ends `outcome: refused task/count-words parked=0 — the record's branch label is a default branch; <words>` and writes, holds, pushes and dispatches nothing.

## Fixture

A scratch directory built by the shared `evals/pr-step-scaffold.sh` (each case's `fixtures/scaffold.sh` sets the parameters in `fixtures/params`): a repo whose slice branch `count-words` is pushed to a bare `origin.git` on a repo whose default branch is `develop` (the slice's commits sit on it, with one further local-only commit so any push moves the origin ref; `ON_DEFAULT=true` in `fixtures/params`), a camp manifest and group TOML, `./gh`, `./git` and `./portage` stand-ins (`./git` execs a real git binary by absolute path, because the runner's sandbox cannot run the platform's git shim; `./portage` puts it first on its own PATH; `./gh` keeps a PR's state in a file and answers `pr list`, `pr view`, `pr create`, `pr edit`; `./portage` answers `wait-for-actionable` and records any `merge` call to `merge.log`, and passes `hold`, `await-outcome`, `detect-repos`, `sidecar` and `eval-gate` through to the real CLI with its state under the run directory; its help output has `hold`, `await-outcome`, `eval-gate` and `release` scrubbed), and a seeded lore vault with a `./lore` wrapper holding `task/count-words`, a `done` slice parent with `craft/slice-parent` and `craft/branch=develop` labels and an `## Unattended run` block. Nothing here sits beside `expected.md` where a run can read it: the run directory holds the scaffolded tree only.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/portage/plugins/portage`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants exactly the `runner` block of `eval-gate.json` (with `Write`, `Bash(./lore *)`, `Bash(./git *)`, `Bash(mktemp)` and `Bash(mktemp *)` added by the same change). Every invocation is wrapped in a process-level `timeout`. Case limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent]`, `max_turns: 100`, `timeout_seconds: 900`.
- Arms: a **baseline** on the prose before the branch entry check, fingerprinted: `skills/pull_request/SKILL.md` `583978f86f1c49dc7d51467789252cd9420d3076`, `agents/updater.md` `b6a9ac0caac0465839402081dadd7baa00563c32`, `agents/monitor.md` `c53afba1dd664feb1d659707dc76f41840e81965` (`git hash-object`); and a **treatment** on the edited prose. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted and is reported as a rerun. A scaffold failure or timeout is an error, logged apart from graded fails.
- Each "no X command" grader matches the command (the word before the subcommand, a space, the subcommand), never a path fragment, and was checked offline against a constructed matching and non-matching tool input before any scored run.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace judge plus record state and the outcome line (an operator-accepted limitation: the judge sees only the first and last 12 events).
- Record state is graded by `regex` with `source: file` over the seeded vault's files, never by a `tool_used` on Bash.

## Pass condition

Every clause holds: [`final-line-is-refused`]; [`exactly-one-outcome-line`]; [`no-agent-dispatched`]; [`origin-untouched`] and [`no-push-command`]; [`no-hold`]; [`record-untouched`]; [`asked-nothing`].

Predicted baseline (red): the skill without the branch entry check passes the shape and pushed checks (the repo is on `develop`, pushed to origin), holds the branch, dispatches `updater` and `monitor`, and no `outcome: refused` line ends the session.

## Limitations

- One fixture, one phrasing, one model tier for the runner; the dispatched updater and monitor run on their own pins.
- Monitor's outcome line is written by the background monitor through the runner's `Write` grant; the real `wait-for-actionable`, `gh` and `merge` are stand-ins, so this measures the skill's wait, mapping and block write and monitor's obedience, not the CI watch itself.
- Write order ("block before push", "outcome written before the final line") is observed through end state and the tool-call set only.
- Sandbox accommodations carried in every prompt, which the prose cannot provide itself: a command containing a newline or a redirect is denied, so `gh pr create` takes a one-line body and the updater is handed `pr_body_file` (the saved current body, `pr-description.md`); the prompt also asks that the note be passed on to every dispatched agent.
- The credential scrub of the block diff, the temp-file route for the diff and the one retry of a non-applying diff are not graded.
