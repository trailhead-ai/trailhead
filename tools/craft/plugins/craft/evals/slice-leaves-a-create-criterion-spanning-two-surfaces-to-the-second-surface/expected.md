# slice-leaves-a-create-criterion-spanning-two-surfaces-to-the-second-surface

## Under test

Given `/craft:slice spec/team-notes --unattended` on a `ready` spec with two independently usable surfaces, the `notes` command group (AC1 list, AC2 show, AC3 add) and a separate web feed (AC4 post an announcement, AC5 remove one), nothing shipped, where AC3 spans both surfaces ("saves a new note and also publishes it to the web feed") and the command group is the one that goes first, the chooser takes the command group's own reads and leaves the spanning criterion to the surface that ships second: the parent it writes covers AC1 and AC2 and none of AC3, AC4 and AC5. The prose under test is `skills/_shared/slice.md` (fingerprint `cf1033cb66351a7dedb3b74be88290ec2fe5a923` unedited) and `skills/slice/SKILL.md` (fingerprint `d69caaeaad33b56fe461bd25e1c0832a13bdd60b` unedited), as they stand when the baseline runs and after the edit.

This is the case that pins the reconciliation of the first-slice rule with the cross-surface reason: a create criterion that spans two surfaces stays with the surface that ships second, so the first-shipping surface's first slice does not pull it in.

## Fixture

`fixtures/scaffold.sh` runs the unchanged `seed.sh`, makes `./lore` self-contained, writes the `bin/lore` shim and a `CLAUDE.md` with `## Project Maturity` / `early`, then seeds the spec `team-notes` (`ready`). The command group has two reads of its own (AC1, AC2) and no create criterion other than the spanning AC3, so the only thing that can pull AC3 into the command group's slice is the first-slice rule ("covers reading and creating ... none of them moves the create criterion out of a first slice"). The web feed's own criteria are a create (AC4) and a mutate (AC5), so the command group's earliest unfinished phase (read) comes before the feed's (create) and the command group ships first by phase alone. The spec's Problem says both surfaces are usable on their own (the notes file is edited by hand today, so list and show are useful alone; staff post announcements to the feed without the command) and that the command group is the one to build first. That statement lives in the Problem, not in a criterion. There is no `## Slices` ledger and no slice record.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`. No grant was added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- Baseline arm: the unedited prose (`skills/_shared/slice.md` `cf1033cb66351a7dedb3b74be88290ec2fe5a923`, `skills/slice/SKILL.md` `d69caaeaad33b56fe461bd25e1c0832a13bdd60b`), 2 runs as the portage-eval shim decides, **expected red, with moderate confidence**. The unedited first-slice rule says the first slice on a surface whose remaining criteria include creation covers reading and creating and that no reason moves the create criterion out, so it should write AC1, AC2 and AC3 and fail both covers graders. The unedited prose also holds a reason ("the work would cross a surface ... belongs to whichever surface ships second"), so a run that applies that reason to AC3 would write AC1 and AC2 and pass: the baseline may be green in some runs, and if it is green in both the case is reported as non-discriminating rather than kept as proof.
- Treatment arm: the edited prose, bar 3 of 3 runs; the shim decides at 2, so each arm is recorded as run, not as a met 3 of 3. A run scores the fraction of graders that pass and passes only at 1.0.
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A judge that disagrees across runs is tightened, never rerun away. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`, measuring `skills/_shared/slice.md` and `skills/slice/SKILL.md`.
- Graders read the written parent's `**Covers:**` line (a `Write` of the body) and the `covers_gate.py` call's `--covers` list, two independent observation points; neither reads rule text. No judge grader.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A written body's `**Covers:**` line names AC1 and AC2 and none of AC3, AC4, AC5 | `the-written-parent-covers-the-cli-reads-and-not-the-spanning-create` | `tool_used` Write, `min: 1` |
| A `covers_gate.py` call's `--covers` list names AC1 and AC2 and none of AC3, AC4, AC5 | `the-covers-gate-is-given-the-cli-reads-and-not-the-spanning-create` | `tool_used` Bash, `min: 1` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

## Limitations

- The body is graded from the `Write` the run issued and the gate call it ran (`tool_used` scans every call, so a draft that was later corrected counts only if some call matches); the record's file name is the agent's choice, so its contents cannot be graded by path.
- On the edited prose the command group's remaining criteria are all reads, so its slice is an ordinary all-reads slice, not a read-and-create first slice; the case does not grade the value claim.
- The fixture deliberately gives the command group no create criterion of its own, so it cannot show a slice that covers the command group's own create and still leaves the spanning one; it pins only that the spanning criterion is not pulled in.
- If the chooser takes the web feed first, the covers graders fail in every arm; that is a fixture defect (plan unknown U1) and the fixture is tightened, not rerun away.
- One fixture, one phrasing, one model tier, two runs as run.

## Deviation from pre-registration (2026-11-25): the run directory supplies the session

The prompt told the model to add `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to its lore and script commands. Across the cases whose prompts carried that instruction, `claude-sonnet-5-5` runs sometimes left the flag off, so their entries went to the runner's own session, where no grader reads, and some placed it after a stdin redirect, which the run's permission grants refused: the prompt's extra argument, not the procedure under test, decided those runs. The case's lore wrappers now set the case's session themselves, as Claude Code sets a real session's, and the prompt no longer names the flag, so the model runs each command as the procedure writes it. No grader, fixture record or bar changed.
