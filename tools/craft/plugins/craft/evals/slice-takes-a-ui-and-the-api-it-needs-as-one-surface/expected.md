# slice-takes-a-ui-and-the-api-it-needs-as-one-surface

## Under test

Given `/craft:slice spec/reservation-book --unattended` on a `ready` spec where one entity (reservations) is reached through two entry points, a web page (AC1, AC2) and a public HTTP API (AC3, AC4), and the spec's Problem prose makes plain that the page does nothing without the API and the API has no consumer of its own yet, nothing shipped, the chooser treats the two entry points as one surface because neither is usable alone, and takes the whole of it as one slice: the parent it writes covers all four criteria. The prose under test is `skills/_shared/slice.md` (fingerprint `cf1033cb66351a7dedb3b74be88290ec2fe5a923` unedited) and `skills/slice/SKILL.md` (fingerprint `d69caaeaad33b56fe461bd25e1c0832a13bdd60b` unedited), as they stand when the baseline runs and after the edit.

This is the case that pins the amended surface definition: one entity reached through two entry points is one surface when the functionality is not usable through either alone, and the test is value (whatever the shipped part cannot provide value without belongs in the same slice).

## Fixture

`fixtures/scaffold.sh` runs the unchanged `seed.sh`, makes `./lore` self-contained, writes the `bin/lore` shim and a `CLAUDE.md` with `## Project Maturity` / `early`, then seeds the spec `reservation-book` (`ready`). The criteria are worded plainly and say nothing about how they should be sliced: AC1 the Reservations page lists every reservation, AC2 the page has a form that adds one, AC3 `GET /api/reservations` returns every reservation, AC4 `POST /api/reservations` creates one. The statement that the page is only a front end for the API (so does nothing without it) and that the API has no consumer of its own yet lives in the spec's `## Problem`, not in a criterion. There is no `## Slices` ledger and no slice record. The prompt runs the slice with `--unattended`, so nothing is asked and no approval is waited for.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`. No grant was added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- Baseline arm: the unedited prose (`skills/_shared/slice.md` `cf1033cb66351a7dedb3b74be88290ec2fe5a923`, `skills/slice/SKILL.md` `d69caaeaad33b56fe461bd25e1c0832a13bdd60b`), 2 runs as the portage-eval shim decides, **expected red**. The unedited definition says one entity reached through two entry points is two surfaces and the chooser takes one surface, so it writes AC1 and AC2 or AC3 and AC4 and both covers graders fail.
- Treatment arm: the edited prose, bar 3 of 3 runs; the shim decides at 2, so each arm is recorded as run, not as a met 3 of 3. A run scores the fraction of graders that pass and passes only at 1.0.
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A judge that disagrees across runs is tightened, never rerun away. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`, measuring `skills/_shared/slice.md` and `skills/slice/SKILL.md`.
- Graders read the written parent's `**Covers:**` line (a `Write` of the body, which is then redirected into `lore record create`) and the `covers_gate.py` call's `--covers` list, two independent observation points; neither reads rule text. No judge grader: the records answer the question.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A written body's `**Covers:**` line names AC1, AC2, AC3 and AC4 | `the-written-parent-covers-the-page-and-the-api-together` | `tool_used` Write, `min: 1` |
| A `covers_gate.py` call's `--covers` list names AC1, AC2, AC3 and AC4 | `the-covers-gate-is-given-the-page-and-the-api-together` | `tool_used` Bash, `min: 1` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

## Limitations

- The body is graded from the `Write` the run issued and the gate call it ran (`tool_used` scans every call, so a failed draft that was later corrected also counts as a match); the record's file name is the agent's choice, so its contents cannot be graded by path. A `Write` that carries AC1-AC4 on its `**Covers:**` line while a later one narrows it would still pass the written-parent grader.
- One fixture, one phrasing, one model tier, two runs as run.
- If the chooser still splits the page from the API on the edited prose in some run, that is a prose finding, not a fixture defect, as long as the spec's Problem stays as explicit as above.
- The baseline is red by design. A green baseline (the chooser merged the two entry points on today's text by reading the Problem prose) means the fixture is strengthened or the case is reported as non-discriminating, never kept silently.
