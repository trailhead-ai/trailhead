# execute-without-request-is-not-unattended

## Under test

Without the explicit `--unattended` request, `/craft:execute task/<slice>` behaves as it always has even when the session is headless: on a slice parent that has no children it refuses in the attended way, naming `/craft:plan` as the next step, prints no `outcome:` line and writes no `## Unattended run` block. This is the "never inferred from absence" check for execute (spec AC3, AC5). The prose under test is `skills/_shared/execute.md`, `skills/execute/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

The unplanned-slice fixture of `unattended-execute-refuses-an-unplanned-slice` (`fixtures/scaffold.sh` seeds the `ready` spec `word-count-tool` and the childless `in-progress` slice parent `count-words`). The prompt runs `/craft:execute task/count-words` with no flag and says only that nobody is there to answer; it says nothing about unattended mode.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`. Every `claude` capture is wrapped in a process-level `timeout`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- Arms: a **baseline** arm on the unedited prose, fingerprinted `skills/_shared/execute.md` `6967612a5e610d22a0bbc9122172eb5df2fdea11`, `skills/_shared/unattended.md` `e9d5ce9a1df747a1c7d0d5f9cf6be22014329aca`, `skills/_shared/refine.md` `7df1fcb540a2ade91472849ca6872181c5451932`, `skills/execute/SKILL.md` `bffe525c2e0d7718ed356e4bc96282a85cd09c9f` (each a `git hash-object` of the file as it stood at `9913a2fb` and in the working tree before any edit), and a **treatment** arm on the edited prose. 3 runs each. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Expected **green**: unedited prose has no unattended branch to trip and its attended refusal already names `/craft:plan`. This case is a guard against the edit, not a detector, and is recorded as such.
- A headless eval session never offers `AskUserQuestion`, so a `tool_used AskUserQuestion` grader would pass whether or not the ritual wanted to ask. "Asked nothing" is graded by behaviour: the typed outcome line, the record state, the issued commands, and an `llm` trace grader confirming the last message poses no question and waits on no answer (operator-accepted headless limitation: that judge is not shown able to fail headless).
- Fixture vocabulary leak check, run before any scored run: no word of the fixture satisfies a grader on its own. The graders read the final message, the task record's body and sidecar, and the issued commands; the fixture's records carry none of `refused`, `parked=`, `needs-a-decision`, `Refine — unresolved` or `--then-pr`, and the one record that carries the `## Unattended run` heading by design (the held slice) is graded by a count of two headings and by the absence of an `**Outcome:**` line, never by presence. No fixture file sits beside `expected.md` where the run can read it: the run directory holds only the scaffolded vault and the lore wrapper, and the sandbox denies reads of the plugin's `evals/` directory.
- Regression arms (run once for the whole change, on the treatment, 3 runs each, 3 of 3): `build-triages-found-work` (runner case), `execute-cuts-a-task-branch-off-base` and `execute-preflight-refuses-stale-base` (clean-room hand-shaped cases, every variant). Attended execute must stay unchanged (spec AC5).

## Pass condition

Every clause holds: [`no-outcome-line`] no line of the final message begins with `outcome:`; [`no-run-block-written`] the slice body has no `## Unattended run` heading; [`slice-sidecar-untouched`]; [`no-run-block-written-by-the-file-tool`], [`no-diff-write-issued`]; [`refusal-names-plan-first`] the final message names `/craft:plan`.

## Limitations

- The absence graders cannot fail on a prose edit that keeps the attended refusal; the `/craft:plan` clause shows the run reached the attended refusal. One fixture, one phrasing.
