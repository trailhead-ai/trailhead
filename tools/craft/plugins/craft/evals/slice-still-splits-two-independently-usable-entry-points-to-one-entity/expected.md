# slice-still-splits-two-independently-usable-entry-points-to-one-entity

## Under test

Given `/craft:slice spec/ticket-queue --unattended` on a `ready` spec where one entity (support tickets) is reached through two entry points, the `deskq tickets` command group (AC1-AC3) and a public HTTP API under `/tickets` (AC4-AC6), and the spec's Problem prose says each is used on its own by a different consumer (support staff at terminals, a separate billing system) and delivers value without the other, nothing shipped, the chooser still takes one entry point as the slice: the parent it writes covers exactly one entry point's three criteria and none of the other's. The prose under test is `skills/_shared/slice.md` (fingerprint `cf1033cb66351a7dedb3b74be88290ec2fe5a923` unedited) and `skills/slice/SKILL.md` (fingerprint `d69caaeaad33b56fe461bd25e1c0832a13bdd60b` unedited), as they stand when the baseline runs and after the edit.

**This case is a guard, not a discriminator.** It is **expected green on the baseline**, because the unedited text already says one entity reached through two entry points is two surfaces. It pins that the amended surface definition does not over-merge: two entry points that are each usable alone stay two surfaces.

## Fixture

`fixtures/scaffold.sh` runs the unchanged `seed.sh`, makes `./lore` self-contained, writes the `bin/lore` shim and a `CLAUDE.md` with `## Project Maturity` / `early`, then seeds the spec `ticket-queue` (`ready`). Criteria: AC1 `deskq tickets list`, AC2 `deskq tickets add <title>`, AC3 `deskq tickets edit <id> <title>`, AC4 `GET /tickets`, AC5 `POST /tickets`, AC6 `PATCH /tickets/<id>`, worded plainly and saying nothing about slicing. The statement that the two entry points serve different consumers and are each complete alone lives in the spec's `## Problem`, not in a criterion. There is no `## Slices` ledger and no slice record.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`. No grant was added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- Baseline arm: the unedited prose (`skills/_shared/slice.md` `cf1033cb66351a7dedb3b74be88290ec2fe5a923`, `skills/slice/SKILL.md` `d69caaeaad33b56fe461bd25e1c0832a13bdd60b`), 2 runs as the portage-eval shim decides, **expected green**. A red baseline means the unedited chooser already merges the two entry points or mixes them, which is a prose finding to report.
- Treatment arm: the edited prose, bar 3 of 3 runs; the shim decides at 2, so each arm is recorded as run, not as a met 3 of 3. A run scores the fraction of graders that pass and passes only at 1.0. A red treatment run means the edit over-merges.
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A judge that disagrees across runs is tightened, never rerun away. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`, measuring `skills/_shared/slice.md` and `skills/slice/SKILL.md`.
- Graders read the written parent's `**Covers:**` line (a `Write` of the body) and the `covers_gate.py` call's `--covers` list, two independent observation points; neither reads rule text. No judge grader.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A written body's `**Covers:**` line names AC1, AC2 and AC3 and none of AC4-AC6, or AC4, AC5 and AC6 and none of AC1-AC3 | `the-written-parent-covers-exactly-one-surface` | `tool_used` Write, `min: 1` |
| A `covers_gate.py` call's `--covers` list names one whole entry point's criteria and none of the other's | `the-covers-gate-is-given-exactly-one-surface` | `tool_used` Bash, `min: 1` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

## Limitations

- The body is graded from the `Write` the run issued and the gate call it ran (`tool_used` scans every call, so a failed draft that was later corrected also counts as a match); the record's file name is the agent's choice, so its contents cannot be graded by path.
- A guard on one fixture: it shows the edit does not merge entry points the spec calls independent, not that the chooser judges usability well on subtler specs.
- One fixture, one phrasing, one model tier, two runs as run.
