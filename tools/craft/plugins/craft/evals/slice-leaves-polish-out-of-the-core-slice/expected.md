# slice-leaves-polish-out-of-the-core-slice

## Under test

Given `/craft:slice spec/recipe-box --unattended` on a `ready` spec with one surface (the `recipes` command group) carrying four core criteria (list, add, edit, delete) and two polish criteria that the core can be met without (sort the list by a chosen column, colour the entries by tag), nothing shipped, the chooser takes the four core criteria as the slice and leaves both polish criteria out (spec AC: AC9). The prose under test is `skills/_shared/slice.md` (fingerprint `0869634f4df7482894d53ffbd61eaad8f18668e2` before the core and polish rule) and `skills/slice/SKILL.md` (fingerprint `d69caaeaad33b56fe461bd25e1c0832a13bdd60b` before it), as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` runs the unchanged `seed.sh`, makes `./lore` self-contained, writes the `bin/lore` shim and a `CLAUDE.md` with `## Project Maturity` / `early`, then seeds the spec `recipe-box` (`ready`). One surface, one entry point: AC1 `recipes list`, AC2 `recipes add`, AC3 `recipes edit <id>`, AC4 `recipes delete <id>` (core), AC5 `recipes list --sort <column>` and AC6 `recipes list` colouring each recipe by its tag (polish). The `## Problem` says plainly that the cook wants to find, add, correct and drop recipes, and that sorting and colouring are conveniences for a long list which is usable without them, so whether the core can be met without the two is read from the spec and not left to taste. No criterion's wording says anything about slicing, no `## Open Questions` section exists, there is no `## Slices` ledger and no slice record. The prompt runs the slice with `--unattended`, so nothing is asked and no approval is waited for.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`. No grant was added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- Baseline arm: the unedited prose, as the `portage-eval` shim runs it (it stops once the gate's 2-of-3 bar decides, so the arm may run fewer than 3 runs; that is recorded as run), **expected red**. The unedited rule says "the slice is the chosen surface's whole capability: every one of its open criteria, whatever phase each belongs to" and "the chooser never splits a surface for size", so it takes all six criteria: the written `**Covers:**` line and the covers-gate list name AC5 and AC6 and fail both polish-absent clauses. Confidence about red: moderate to high; a run that already holds polish back on its own judgment (the Interface leverage section's polish remarks) would make the case green on the baseline and fail to discriminate, which is then reported plainly and not forced.
- Treatment arm: the edited prose, bar 3 of 3 runs, which the shim decides at 2 and which is recorded as run and not as a met 3 of 3. A run scores the fraction of graders that pass and passes only at 1.0.
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A judge that disagrees across runs is tightened, never rerun away. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`, measuring `skills/_shared/slice.md` and `skills/slice/SKILL.md`.
- Graders read the written parent's `**Covers:**` line (a `Write` of the body) and the covers-gate call's `--covers` list, two independent observation points; neither reads the skill's text and none is a judge.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A written body's `**Covers:**` line names AC1, AC2, AC3 and AC4 and neither AC5 nor AC6 | `the-written-parent-covers-the-four-core-criteria-and-neither-polish-criterion` | `tool_used` Write, `min: 1` |
| A `covers_gate.py` call's `--covers` list names AC1, AC2, AC3 and AC4 and neither AC5 nor AC6 | `the-covers-gate-is-given-the-four-core-criteria-and-neither-polish-criterion` | `tool_used` Bash, `min: 1` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

## Limitations

- The body is graded from the `Write` the run issued and the gate call it ran (`tool_used` scans every call, so a draft that was later corrected counts only if some call matches); the negative lookaheads apply per call, so a draft that took all six and a corrected call that took four would match on the corrected call. The record's file name is the agent's choice, so its contents cannot be graded by path.
- One fixture, one phrasing, one model tier, as many runs as the shim decides on.
- The Problem's sentence that the list is usable without sorting and colour is the fixture's statement of the polish fact. A run that takes all six on the edited prose is a prose finding only if that sentence was read; a run that ignores it is a fixture finding and the fixture is tightened, not graded conditionally.
- The case cannot show that the polish pair is offered afterwards; the next case does.
