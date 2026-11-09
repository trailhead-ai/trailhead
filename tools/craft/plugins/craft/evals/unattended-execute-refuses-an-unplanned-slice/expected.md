# unattended-execute-refuses-an-unplanned-slice

## Under test

Under `--unattended --then-pr`, `/craft:execute task/<slice>` on a slice parent that has no children is refused with `outcome: refused task/<name> parked=0 — <why>; <what to run instead>`, the remedy naming the planning step. The refusal writes nothing: no `## Unattended run` heading, no `--diff` write, no record update at all, no branch cut. The prose under test is `skills/_shared/execute.md`, `skills/execute/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `word-count-tool` (two criteria) and an `in-progress` slice parent `count-words` (label `craft/slice-parent`, linked to the spec, covers AC1) with no children and no `craft/branch` label. The prompt runs `/craft:execute task/count-words --unattended --then-pr`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`. Every `claude` capture is wrapped in a process-level `timeout`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- Arms: a **baseline** arm on the unedited prose, fingerprinted `skills/_shared/execute.md` `6967612a5e610d22a0bbc9122172eb5df2fdea11`, `skills/_shared/unattended.md` `e9d5ce9a1df747a1c7d0d5f9cf6be22014329aca`, `skills/_shared/refine.md` `7df1fcb540a2ade91472849ca6872181c5451932`, `skills/execute/SKILL.md` `bffe525c2e0d7718ed356e4bc96282a85cd09c9f` (each a `git hash-object` of the file as it stood at `9913a2fb` and in the working tree before any edit), and a **treatment** arm on the edited prose. 3 runs each. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Expected **red**: the unedited prose has no unattended branch, so it prints no `outcome:` line (`final-line-is-refused`, `remedy-names-planning` fail). `no-run-block-written` and the sidecar graders pass vacuously, because the attended refusal also writes nothing.
- A headless eval session never offers `AskUserQuestion`, so a `tool_used AskUserQuestion` grader would pass whether or not the ritual wanted to ask. "Asked nothing" is graded by behaviour: the typed outcome line, the record state, the issued commands, and an `llm` trace grader confirming the last message poses no question and waits on no answer (operator-accepted headless limitation: that judge is not shown able to fail headless).
- Fixture vocabulary leak check, run before any scored run: no word of the fixture satisfies a grader on its own. The graders read the final message, the task record's body and sidecar, and the issued commands; the fixture's records carry none of `refused`, `parked=`, `needs-a-decision`, `Refine — unresolved` or `--then-pr`, and the one record that carries the `## Unattended run` heading by design (the held slice) is graded by a count of two headings and by the absence of an `**Outcome:**` line, never by presence. No fixture file sits beside `expected.md` where the run can read it: the run directory holds only the scaffolded vault and the lore wrapper, and the sandbox denies reads of the plugin's `evals/` directory.
- Regression arms (run once for the whole change, on the treatment, 3 runs each, 3 of 3): `build-triages-found-work` (runner case), `execute-cuts-a-task-branch-off-base` and `execute-preflight-refuses-stale-base` (clean-room hand-shaped cases, every variant). Attended execute must stay unchanged (spec AC5).

## Pass condition

Every clause holds: [`final-line-is-refused`] last line `outcome: refused task/count-words parked=0 — <why>`; [`remedy-names-planning`] the remedy names planning; [`no-run-block-written`] the slice body has no `## Unattended run` heading (`source: file`); [`slice-sidecar-untouched`] the slice's `updated-at` still equals `created-at`; [`no-run-block-written-by-the-file-tool`] and [`no-diff-write-issued`] no `Write` mentions the block and no Bash call carries `--diff`; [`no-git-command-issued`] no git command was run (no branch cut); [`asked-nothing`] the `llm` trace judge.

## Limitations

- One refusal site of several (the other refusal sites share the outcome shape and are not each run); one fixture, one phrasing, one model tier. No git repository is in the run directory, so "no branch cut" is graded from the issued commands only.
