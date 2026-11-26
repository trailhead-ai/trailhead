# slice-states-a-polish-slice-as-a-recommendation-with-its-reason

## Under test

Given `/craft:slice spec/recipe-box --unattended` on a `ready` spec whose four core criteria (list, add, edit, delete) are shipped on the `## Slices` ledger and whose two polish criteria (sort the list by a column, colour entries by tag) remain, the chooser takes the two polish criteria together as the slice and states that slice to the operator as a recommendation with its reason (spec AC: AC10). The prose under test is `skills/_shared/slice.md` (fingerprint `0869634f4df7482894d53ffbd61eaad8f18668e2` before the core and polish rule) and `skills/slice/SKILL.md` (fingerprint `d69caaeaad33b56fe461bd25e1c0832a13bdd60b` before it), as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` runs the unchanged `seed.sh`, makes `./lore` self-contained, writes the `bin/lore` shim and a `CLAUDE.md` with `## Project Maturity` / `early`, then seeds the spec `recipe-box` (`ready`) with the same six criteria and `## Problem` as the polish-out-of-core case, plus a `## Slices` ledger line `Recipe basics ... (task/recipe-basics, closed 2026-11-01, covers AC1, AC2, AC3, AC4)`, and the matching `done` slice parent `task/recipe-basics` with `**Covers:** AC1, AC2, AC3, AC4` and the `craft/slice-parent` label, so the reconcile step finds the ledger and the record agreeing. Only AC5 and AC6 are open. The prompt runs the slice with `--unattended`; because an unattended run does not wait for an answer and the trace judge sees only the first and last 12 messages, the prompt also tells the run to restate its statement to the operator in its final message before the outcome line, so the judged text is in the final message.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`. No grant was added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- Baseline arm: the unedited prose, as the `portage-eval` shim runs it (it stops once the gate's 2-of-3 bar decides). **Expected green, so the case may not discriminate.** The unedited Interface leverage section already says to "state a polish-phase pick and its reason to the operator as a recommendation rather than presenting it as derived", and with only polish criteria left on one surface the whole-capability rule takes both. Confidence that the covers graders are green on the baseline: high; that the judge is green: moderate, since the unedited text does not require the statement to carry a recommendation outside the "polish-phase pick" wording. A green baseline is reported plainly and the case stays as a guard on the edited rule (the core and polish rule must not break the offer of the polish slice); a red baseline would be a finding about step 8's statement and is reported as such.
- Treatment arm: the edited prose, bar 3 of 3 runs, which the shim decides at 2 and which is recorded as run and not as a met 3 of 3. A run scores the fraction of graders that pass and passes only at 1.0.
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A judge that disagrees across runs is tightened, never rerun away. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`, measuring `skills/_shared/slice.md` and `skills/slice/SKILL.md`.
- Graders read the written parent's `**Covers:**` line and the covers-gate call's `--covers` list, and a judge reads the trace.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A written body's `**Covers:**` line names AC5 and AC6 and none of AC1 to AC4 | `the-written-parent-covers-both-polish-criteria-and-no-core-criterion` | `tool_used` Write, `min: 1` |
| A `covers_gate.py` call's `--covers` list names AC5 and AC6 and none of AC1 to AC4 | `the-covers-gate-is-given-both-polish-criteria-and-no-core-criterion` | `tool_used` Bash, `min: 1` |
| The final message covers AC5 and AC6, recommends the slice rather than presenting it as derived, and gives a concrete reason about those two criteria; a bare "next slice: polish", a generic reason or a partial match fails | `the-statement-offers-the-polish-slice-as-a-recommendation-with-its-reason` | `llm`, `focus: trace` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

## Limitations

- The statement is judged from the final message, which the prompt asks the run to make carry the restated statement; a run that ignores that line fails the judge for a reason that is about the prompt, not the prose, and is reported as such. The judge cannot see the statement the run made earlier in the trace if it sits outside the first and last 12 messages.
- The judge is written to fail a bare naming of the slice, a generic reason ("next", "left", "small") and a derived-sounding pick, and to give no credit for a partial match; a judge that disagrees across runs is tightened.
- The restate line in the prompt makes the final message more likely to carry a recommendation than an unprompted run would, so a green baseline is weak evidence that the unedited text produces the statement on its own.
- One fixture, one phrasing, one model tier, as many runs as the shim decides on.

## Deviation from pre-registration (2026-11-25): the run directory supplies the session

The prompt told the model to add `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to its lore and script commands. Across the cases whose prompts carried that instruction, `claude-sonnet-5-5` runs sometimes left the flag off, so their entries went to the runner's own session, where no grader reads, and some placed it after a stdin redirect, which the run's permission grants refused: the prompt's extra argument, not the procedure under test, decided those runs. The case's lore wrappers now set the case's session themselves, as Claude Code sets a real session's, and the prompt no longer names the flag, so the model runs each command as the procedure writes it. No grader, fixture record or bar changed.
