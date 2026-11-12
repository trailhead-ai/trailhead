# pull-request-without-request-is-not-unattended

## Under test

Without the explicit `--unattended` request, `/portage:pull_request create` behaves as it always has even when the session is headless: it dispatches the updater, launches monitor in the background, takes no merge hold, writes nothing to the slice record's `## Unattended run` block and prints no `outcome:` line. This is the "never inferred from absence" check for the PR step (spec AC4, AC5). The prose under test is `skills/pull_request/SKILL.md` (its unattended section must not engage), `agents/updater.md` and `agents/monitor.md`.

## Fixture

The ready-PR fixture of `unattended-pr-step-stops-at-ready` (`fixtures/params` is the same, built by the shared `evals/pr-step-scaffold.sh`): a pushed slice branch with no PR yet, `./gh`, `./git`, `./portage` and `./lore` stand-ins, and the `done` slice parent `count-words` carrying one execute-left `## Unattended run` block at checkpoint `close`. The prompt runs `/portage:pull_request create` with no flag and no record argument, and says only that nobody is there to answer; it says nothing about unattended mode. Nothing here sits beside `expected.md` where a run can read it.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/portage/plugins/portage`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants exactly the `runner` block of `eval-gate.json`. Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent]`, `max_turns: 100`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. 3 of 3 runs; one rerun of a failed batch is permitted and reported as a rerun.
- Expected **green** on the baseline prose: the attended `create` has no unattended branch to trip. This case is a guard against the PR-step edits, not a detector, and is recorded as such.
- A headless session cannot show a question being asked, so "asked nothing" is not claimed here; this case pins what the unattended mode would have done and did not.

## Pass condition

Every clause holds: [`no-outcome-line`] no line of the final message begins with `outcome:`; [`no-hold-taken`] and [`no-hold-command`] no `portage hold` was recorded or issued; [`no-run-block-write-issued`] and [`no-run-block-written-by-the-file-tool`] no `--diff` or `record update` command and no file write naming the run block; [`slice-sidecar-untouched`] the slice record's sidecar still has equal created and updated stamps; [`updater-dispatched-attended`] the attended path ran far enough to dispatch the updater.

## Limitations

- The absence graders cannot fail on a prose edit that leaves the attended steps alone; the updater clause shows the run reached the attended path. One fixture, one phrasing, one runner model.
