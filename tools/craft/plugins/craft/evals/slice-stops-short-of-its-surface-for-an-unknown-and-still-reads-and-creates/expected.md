# slice-stops-short-of-its-surface-for-an-unknown-and-still-reads-and-creates

## Under test

Given `/craft:slice spec/shelf-notes --unattended` on a `ready` spec with one surface (the `notes` command group) carrying read, create, edit and delete criteria, nothing shipped, and an `## Open Questions` entry declaring an unknown that edit and delete depend on and that has to be learned first, the chooser ends the slice short of its surface for that reason: the parent it writes covers reading and creating (AC6) and neither the edit nor the delete criterion, and the claim it states names the unknown-must-be-learned-first reason (AC5). The prose under test is `skills/_shared/slice.md` (fingerprint `6b1c75d0c50634a5af9e56aff9cb44663e5d47eb` before the split-reason and first-slice rules) and `skills/slice/SKILL.md` (fingerprint `6444508502a5691cd5c2e94aacd1e3dca632606e` before them), as they stand when the baseline runs and after the edit.

This is the case that pins the first-slice rule: the whole-surface rule alone takes all four criteria, so only a rule that lets the unknown cut the surface, and a first-slice rule that keeps reading and creating on the near side of the cut, produces this answer.

## Fixture

`fixtures/scaffold.sh` runs the unchanged `seed.sh`, makes `./lore` self-contained, writes the `bin/lore` shim and a `CLAUDE.md` with `## Project Maturity` / `early`, then seeds the spec `shelf-notes` (`ready`). One surface: AC1 `notes list`, AC2 `notes add`, AC3 `notes edit <id>`, AC4 `notes delete <id>`. The criteria are worded plainly and say nothing about how they should be sliced. The spec's `## Open Questions` holds one real unknown: whether a note keeps its id after the notes file is synced between two machines. The entry says plainly that it has to be learned before anything that finds a note by id is built, that `notes edit` and `notes delete` both address a note by id, and that `notes list` and `notes add` do not depend on it. The dependency lives in the Open Questions section and not in any criterion's wording (plan unknown U3: the chooser must actually cut on the edited prose in every run; if a run keeps all four, the fixture is made more explicit and the case is not graded conditionally). There is no `## Slices` ledger and no slice record.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`. No grant was added.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500` (inside the 540 s merge-only budget). The prompt asks for single-command Bash calls.
- Baseline arm: the prose as the whole-surface rule left it (`skills/_shared/slice.md` `6b1c75d0c50634a5af9e56aff9cb44663e5d47eb`, `skills/slice/SKILL.md` `6444508502a5691cd5c2e94aacd1e3dca632606e`), **expected red**. It names no reason a slice may end short of its surface and says the chooser never splits a surface, so it takes AC1 to AC4 (or, less likely, still ends at the read phase): the written `**Covers:**` line and the covers-gate list fail the "reading and creating only" clauses, and the claim grader fails for naming no unknown-first reason.
- Treatment arm: the edited prose, bar 3 of 3 runs. A run scores the fraction of graders that pass and passes only at 1.0. As run, the `portage-eval` shim stops a case once the gate's 2-of-3 bar decides it, so each arm runs 2 runs; that is recorded as a disclosed deviation, not a met 3 of 3.
- One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A judge that disagrees across runs is tightened, never rerun away. A scaffold failure or timeout is an error, logged apart from graded fails.
- The case is `merge_only` in `eval-gate.json`, measuring `skills/_shared/slice.md` and `skills/slice/SKILL.md`.
- Graders read the written parent's `**Covers:**` line (a `Write` of the body) and the covers-gate call's `--covers` list, two independent observation points, and judge the stated claim from the trace.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A written body's `**Covers:**` line names AC1 and AC2 and neither AC3 nor AC4 | `the-written-parent-covers-reading-and-creating-and-not-the-unknown-dependent-criteria` | `tool_used` Write, `min: 1` |
| A `covers_gate.py` call's `--covers` list names AC1 and AC2 and neither AC3 nor AC4 | `the-covers-gate-is-given-reading-and-creating-only` | `tool_used` Bash, `min: 1` |
| The statement names the id-after-sync question as the reason editing and deleting are left, says it must be learned first, and ties it to edit and delete; any other reason, or the bare word "unknown", fails | `the-claim-gives-the-unknown-as-the-reason-the-slice-stops-short` | `llm`, `focus: trace` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| A `lore record create --kind task` command is issued for the slice parent | `a-slice-parent-is-created-through-the-cli` | `tool_used` Bash, `min: 1` |

## Limitations

- The body is graded from the `Write` the run issued and the gate call it ran (`tool_used` scans every call, so a draft that was later corrected counts only if some call matches); the record's file name is the agent's choice, so its contents cannot be graded by path.
- The claim grader is a judge. It is written to fail every reason other than the declared unknown, and a judge that disagrees across runs is tightened.
- One fixture, one phrasing, one model tier, two runs as run.
- If the unknown is not read at all, the run takes all four criteria and fails the covers graders: that is a prose finding (the chooser must read the Open Questions), not a fixture defect, as long as the fixture's dependency stays as explicit as above.

## Deviation from pre-registration (2026-11-25): the run directory supplies the session

The prompt told the model to add `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to its lore and script commands. Across the cases whose prompts carried that instruction, `claude-sonnet-5-5` runs sometimes left the flag off, so their entries went to the runner's own session, where no grader reads, and some placed it after a stdin redirect, which the run's permission grants refused: the prompt's extra argument, not the procedure under test, decided those runs. The case's lore wrappers now set the case's session themselves, as Claude Code sets a real session's, and the prompt no longer names the flag, so the model runs each command as the procedure writes it. No grader, fixture record or bar changed.
