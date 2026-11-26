# slice-takes-the-other-surface-after-one-has-shipped

## Under test

Given `/craft:slice spec/front-desk --unattended` on a `ready` spec where two surfaces where the first-listed one (AC1-AC3) already shipped on the ledger and the second (AC4-AC6) is fresh, the chooser takes one surface's whole capability as the slice (spec AC: AC3): the parent it writes covers AC4, AC5 and AC6. The prose under test is `skills/_shared/slice.md` (fingerprint `4a88c9748e5b3e713d8e5c615f5266a7ac016eb7` unedited) and `skills/slice/SKILL.md` (fingerprint `1cf2b2672b21e69445ba90940f48bcd9313ed010` unedited), as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` runs the unchanged `seed.sh`, makes `./lore` self-contained, writes the `bin/lore` shim and a `CLAUDE.md` with `## Project Maturity` / `early`, then seeds the spec `front-desk` (`ready`). The spec's `## Slices` ledger lists the shipped slice (`done`, seeded through `./lore record create` with the `craft/slice-parent` label and the `spec=` edge, as `slice-chooses-and-drops-follow-up-entries-after-the-criteria-are-covered` does). Surface assignment is meant to be unambiguous from the criterion text: one entity per surface, one entry point per surface, worded plainly, and the two surfaces (where there are two) are a CLI command group and an HTTP endpoint group over different entities. The prompt runs the slice with `--unattended`, so nothing is asked and no approval is waited for.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`. No grant was added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- Baseline arm: the unedited prose (`skills/_shared/slice.md` `4a88c9748e5b3e713d8e5c615f5266a7ac016eb7`, `skills/slice/SKILL.md` `1cf2b2672b21e69445ba90940f48bcd9313ed010`), 3 runs, **expected red**. The unedited rule takes the read phase of what is left: it chooses AC4 alone, so the `**Covers:**` line lacks AC5 and AC6 and the fixed-surface graders fail.
- Treatment arm: the edited prose, 3 runs, bar 3 of 3. A run scores the fraction of graders that pass and passes only at 1.0.
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A judge that disagrees across runs is tightened, never rerun away. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`.
- Graders read the written parent's `**Covers:**` line (a `Write` of the body, which is then redirected into `lore record create`) and the `covers_gate.py` call's `--covers` list, two independent observation points; neither reads the skill's text.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A written body's `**Covers:**` line names AC4, AC5 and AC6 and none of AC1-AC3 | `the-written-parent-covers-the-unshipped-surface-alone` | `tool_used` Write, `min: 1` |
| A `covers_gate.py` call's `--covers` list names AC4, AC5 and AC6 and none of AC1-AC3 | `the-covers-gate-is-given-the-unshipped-surface-alone` | `tool_used` Bash, `min: 1` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

## Limitations

- The body is graded from the `Write` the run issued and the gate call it ran (`tool_used` scans every call, so a failed draft that was later corrected also counts as a match for the present-criteria clauses); the record's file name is the agent's choice, so its contents cannot be graded by path.
- One fixture, one phrasing, one model tier, three runs.
- A fixture whose wording lets the three runs disagree on the surface is an authoring defect (plan unknown U1) and is tightened, not rerun away.
- The baseline is red by design: the old rule takes one phase of a surface, which the `**Covers:**` graders cannot match. A green baseline means the fixture is strengthened, not the case kept (plan unknown U2).

## Deviation from pre-registration (2026-11-25): the run directory supplies the session

The prompt told the model to add `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to its lore and script commands. Across the cases whose prompts carried that instruction, `claude-sonnet-5-5` runs sometimes left the flag off, so their entries went to the runner's own session, where no grader reads, and some placed it after a stdin redirect, which the run's permission grants refused: the prompt's extra argument, not the procedure under test, decided those runs. The case's lore wrappers now set the case's session themselves, as Claude Code sets a real session's, and the prompt no longer names the flag, so the model runs each command as the procedure writes it. No grader, fixture record or bar changed.
