# unattended-pr-step-starts-fresh-after-a-finished-plan

## Under test

The latest-block entry check of `skills/pull_request/SKILL.md` (`create --unattended task/<slice>`). The claim: rituals run in the order slice, plan, execute, PR step, so a record whose latest block ended `plan-ready` is an earlier ritual's finished outcome. The PR step is not refused; it goes on as a fresh run, appends its own block, leaves the plan's block as written, opens the PR, and ends `pr-ready`. The run reaches `agents/updater.md` (opens the PR) and `agents/monitor.md` (reports ready), so a change to either also bears on it.

## Fixture

The shared `evals/pr-step-scaffold.sh` with `RECORD_STATE=plan-ready-earlier`: the slice branch `count-words` is pushed with no PR, and `task/count-words` is a `done` slice parent whose only block is a plan ritual's, ended `**Outcome:** plan-ready`. The tool stand-ins are those of `unattended-pr-step-stops-at-ready`.

## Conditions, sample and bar

Runner, arms and bar as in `unattended-pr-step-stops-at-ready`: `claude plugin eval`, target `tools/portage/plugins/portage`, `--ablation none`, the `runner` block of `eval-gate.json`, 3 runs, passing only at 1.0 on every run. Case limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent]`, `max_turns: 100`, `timeout_seconds: 900`.

## Pass condition

Every clause holds: [`not-refused`] no `outcome: refused` line; [`new-block-appended`] and [`exactly-two-blocks`] one new block beside the plan's; [`earlier-block-untouched`] the plan block is byte-for-byte as seeded; [`new-block-outcome-is-pr-ready`]; [`pr-created`]; [`final-line-is-pr-ready`]; [`no-merge-recorded`].

Predicted baseline (red): the latest-block check refuses the finished `plan-ready` outcome, so no block is appended and no PR is opened.

## Limitations

- One fixture, one phrasing, one model tier for the runner.
- Monitor and the forge are stand-ins; this measures the skill's entry check and block write, not the CI watch.
- Evals do not run in the authoring sandbox; the operator runs this case.
