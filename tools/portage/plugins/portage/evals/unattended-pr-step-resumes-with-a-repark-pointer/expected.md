# unattended-pr-step-resumes-with-a-repark-pointer

## Under test

The unattended branch of `skills/pull_request/SKILL.md` (`create --unattended task/<slice>`), with the `agents/updater.md` and `agents/monitor.md` it dispatches. The claim: a question that was re-parked by a pointer line in a later block (`- Q1: still open — answer goes under the original Q1 entry`) is one question, not two, and is answered when its answer sits under the ORIGINAL entry in the earlier block: a resumed PR step on a green PR ends `pr-ready`, not `needs-a-decision`. Spec: AC47.

## Fixture

the shared scaffold with the PR already open, and a record body of two earlier blocks: block 1 ended `needs-a-decision` with `Q1` and `**Answer:** (a)` under it; block 2 has no outcome and carries only the re-park pointer for `Q1` (the run was interrupted after the answer landed under the original). The prompt adds `--resume`. The corpus's shared `evals/pr-step-scaffold.sh` builds the rest (a repo whose slice branch is pushed to a bare origin, a camp manifest and group TOML, `./gh`, `./git` and `./portage` stand-ins, a seeded lore vault with a `./lore` wrapper holding `task/count-words`, a `done` slice parent with `craft/slice-parent` and `craft/branch=count-words` labels); its parameters are in `fixtures/params`. The stand-in `./portage` records any `merge` call to `merge.log` and `hold` calls to `portage.log`. Nothing here sits beside `expected.md` where a run can read it: the run directory holds the scaffolded tree only.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/portage/plugins/portage`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants exactly the `runner` block of `eval-gate.json`. Case limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent]`, `max_turns: 100`, `timeout_seconds: 900`.
- Arms: a **baseline** on the prose as it stands before any edit, fingerprinted (`git hash-object`): `skills/pull_request/SKILL.md` `04a673f92c92f361ded894abfe0279c96f41841c`, `agents/updater.md` `b6a9ac0caac0465839402081dadd7baa00563c32`, `agents/monitor.md` `c53afba1dd664feb1d659707dc76f41840e81965`; and a **treatment** on the edited prose. A run scores the fraction of graders that pass and passes only at 1.0. Bar: treatment 3 of 3; baseline expected 0 of 3. One rerun of a failed batch is permitted and is reported as a rerun. A scaffold failure or timeout is an error, logged apart from graded fails.
- Each "no X command" grader matches the command (the word before the subcommand, a space, the subcommand), never a path fragment. Record state is graded by `regex` with `source: file` over the seeded vault's files, never by a `tool_used` on Bash. The outcome line is graded on the session's final message.
- A headless eval session never offers `AskUserQuestion`; "asked nothing" is an `llm` trace judge where present, plus the outcome line.

## Pass condition

Every clause holds: [`final-line-is-pr-ready`]; [`new-block-outcome-is-pr-ready`] and [`exactly-three-blocks`]; [`no-needs-a-decision-outcome-in-new-block`]; [`original-answer-untouched`]; [`asked-nothing`]; [`no-merge-command`] and [`no-merge-recorded`].

## Pre-registered result on the unedited skill

FAIL. The unedited skill's entry check 1 refuses the `--resume` token with `outcome: refused` before reading the record: [`final-line-is-pr-ready`], [`new-block-outcome-is-pr-ready`] and [`exactly-three-blocks`] fail. (Without the token the unedited skill would also end `pr-ready`, since it never reads parked questions; the `--resume` token is what makes the case red now, and the pointer rule is what a treatment that over-counts the pointer would break.)

## Limitations

- One fixture, one phrasing, one model tier for the runner; the dispatched updater and monitor run on their own pins.
- The real `wait-for-actionable`, `gh` and `merge` are stand-ins, so this measures the skill's entry checks, block writes, wait and mapping, not the CI watch.
- Sandbox accommodations carried in every prompt (single-line commands, `pr_body_file`) are the same as the corpus's other unattended PR-step cases.
