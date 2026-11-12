# unattended-pr-step-resumes-after-an-answered-correction

## Under test

The unattended branch of `skills/pull_request/SKILL.md` (`create --unattended task/<slice>`), with the `agents/updater.md` and `agents/monitor.md` it dispatches. The claim: when the parked correction has been answered (an `**Answer:** (a)` line directly under its entry, matching an option letter), a resumed PR step finds nothing left to decide and ends `pr-ready` on a green PR, appending its own block and leaving the earlier block and its answer as written. Spec: AC47.

## Fixture

the shared scaffold with the PR already open on the stand-in `gh`, and the record body replaced so the one earlier block ended `needs-a-decision` and holds `Q1` with `**Answer:** (a)` under its entry. No `--resume` token: a block that has an outcome resumes on its own. The corpus's shared `evals/pr-step-scaffold.sh` builds the rest (a repo whose slice branch is pushed to a bare origin, a camp manifest and group TOML, `./gh`, `./git` and `./portage` stand-ins, a seeded lore vault with a `./lore` wrapper holding `task/count-words`, a `done` slice parent with `craft/slice-parent` and `craft/branch=count-words` labels); its parameters are in `fixtures/params`. The stand-in `./portage` records any `merge` call to `merge.log` and `hold` calls to `portage.log`. Nothing here sits beside `expected.md` where a run can read it: the run directory holds the scaffolded tree only.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/portage/plugins/portage`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants exactly the `runner` block of `eval-gate.json`. Case limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent]`, `max_turns: 100`, `timeout_seconds: 900`.
- Arms: a **baseline** on the prose as it stands before any edit, fingerprinted (`git hash-object`): `skills/pull_request/SKILL.md` `04a673f92c92f361ded894abfe0279c96f41841c`, `agents/updater.md` `b6a9ac0caac0465839402081dadd7baa00563c32`, `agents/monitor.md` `c53afba1dd664feb1d659707dc76f41840e81965`; and a **treatment** on the edited prose. A run scores the fraction of graders that pass and passes only at 1.0. Bar: treatment 3 of 3; baseline expected 3 of 3. One rerun of a failed batch is permitted and is reported as a rerun. A scaffold failure or timeout is an error, logged apart from graded fails.
- Each "no X command" grader matches the command (the word before the subcommand, a space, the subcommand), never a path fragment. Record state is graded by `regex` with `source: file` over the seeded vault's files, never by a `tool_used` on Bash. The outcome line is graded on the session's final message.
- A headless eval session never offers `AskUserQuestion`; "asked nothing" is an `llm` trace judge where present, plus the outcome line.

## Pass condition

Every clause holds: [`final-line-is-pr-ready`]; [`new-block-appended`], [`exactly-two-blocks`] and [`new-block-outcome-is-pr-ready`]; [`no-needs-a-decision-outcome-in-new-block`]; [`earlier-block-and-answer-untouched`]; [`updater-dispatched-in-update-mode`]; [`no-merge-command`] and [`no-merge-recorded`].

## Pre-registered result on the unedited skill

PASS (expected: this case is a guard, not a red case). The unedited skill appends after a block that has an outcome and ends `pr-ready`; it exists so the new answered-question rule cannot make the PR step end `needs-a-decision` forever. A baseline pass is the pre-registered result; a baseline fail would mean the seed or a grader is wrong.

## Limitations

- One fixture, one phrasing, one model tier for the runner; the dispatched updater and monitor run on their own pins.
- The real `wait-for-actionable`, `gh` and `merge` are stand-ins, so this measures the skill's entry checks, block writes, wait and mapping, not the CI watch.
- Sandbox accommodations carried in every prompt (single-line commands, `pr_body_file`) are the same as the corpus's other unattended PR-step cases.
