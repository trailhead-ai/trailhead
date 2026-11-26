# plan-narrowing-keeps-the-access-check-the-surface-needs

## Under test

Given `/craft:plan task/export-the-orders-to-a-csv-file --unattended` on a slice parent whose coverage is too large for one reviewable pull request and whose surface is reachable by more than one principal, plan's narrowing keeps the criterion that `skills/_shared/slice.md`'s state-coverage floor makes core: the slice that first makes a surface reachable by more than one principal ships its access check in that same slice. The cut is chosen by the stop-here test, and the floor decides what the kept part owes, so an access criterion that is deferrable by usefulness is still kept. The prose under test is `skills/plan/SKILL.md` step 7's cut rule and `skills/_shared/slice.md` as they stand when the baseline runs and after the edit.

## Fixture

The shared scaffold with `VARIANT=access` (`unattended-plan-narrows-an-oversized-slice/fixtures/scaffold.sh`): the `stop` slice (AC1 to AC5, intended cut AC4 and AC5) plus a sixth criterion, AC6, and a roles paragraph. `CLAUDE.md` says two people run the command line, the operator and the bookkeeper, told apart by `SHOP_ROLE`, and that the orders carry customer names only the operator may take out. The spec's Problem says the new `export` command is the first thing in the tool that writes order data, customer names included, to a file. AC6: `shop export` with `SHOP_ROLE=bookkeeper` or no role is refused with "not permitted" and writes no file; only the operator may export. Its own text carries a planning note that a plan short of room can leave AC6 for a later slice (data, moves no cut). The parent covers AC1 to AC6.

- **Pre-registered cut: keep AC1, AC2, AC3 and AC6; drop AC4 and AC5.** The `**Covers:**` line is `AC1, AC2, AC3, AC6`, the `**Narrowed:**` line is `dropped AC4, AC5 — <reason>`, the gate call certifies `AC1, AC2, AC3, AC6`. AC6 is owed by the floor: the kept part makes `shop export` reachable by a second principal, so its access check ships in the same slice. AC4 and AC5 are independently deferrable exactly as in `stop`.
- **Why the fixture can tempt a wrong cut.** AC6 is last in the list, is the one criterion the operator's own weekly use does not need, and says in its own text that it can be left for later. A cut by the stop-here test alone (usefulness to the operator; each dropped criterion deferrable on its own) would plausibly put the boundary at AC3 and drop AC4, AC5 and AC6 together, as the smallest orders-only export.
- **Honest expectation on the baseline.** Today's cut rule (`skills/plan/SKILL.md` step 7) names the stop-here test and never mentions the state-coverage floor, so it is likely red: the prose gives nothing that moves AC6 into the kept set, and the fixture makes the usefulness reading attractive. A baseline that keeps AC6 anyway would be a run that applied the floor from reading `slice.md` unprompted; that would be reported, not hidden. Because the tightened `stop` fixture already makes the AC1 to AC3 cut appear without the narrowing prose (see the limitation note in `tools/craft/MANUAL-EVAL.md`), what this case measures is only whether AC6 survives the cut.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`. Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 900`. Core tier.
- A run scores the fraction of graders that pass and passes only at 1.0. One rerun of a failed batch is permitted, stated here before any run. A scaffold failure or timeout is an error, logged apart from graded fails.
- Arms: baseline (today's prose) 3 runs, expected red; treatment 3 runs, bar 3 of 3.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| A written body carries `**Covers:** AC1, AC2, AC3, AC6` | `covers-line-keeps-the-access-check` | `tool_used` Write, `min: 1` |
| No written `**Narrowed:**` line names AC6 in its dropped list | `narrowed-line-does-not-name-the-access-check` | `tool_used` Write, `max: 0` |
| A written `**Narrowed:**` line reads `dropped AC4, AC5 — <reason>` | `narrowed-line-names-the-dropped-criteria-with-a-reason` | `tool_used` Write, `min: 1` |
| No written `**Covers:**` line names AC4 or AC5 | `no-write-gives-a-covers-line-a-dropped-criterion` | `tool_used` Write, `max: 0` |
| A `covers_gate.py` call certifies `--covers "AC1, AC2, AC3, AC6"` | `covers-gate-certified-the-reduced-list-with-the-access-check` | `tool_used` Bash, `min: 1` |
| No child is titled for removing, dropping or deferring the access check | `no-child-task-is-titled-for-removing-the-access-check` | `tool_used` Bash, `max: 0` |
| No child body delivers the access check's removal or deferral | `no-child-body-delivers-the-access-check-removal` | `tool_used` Write, `max: 0` |
| A child body delivers the access check (`SHOP_ROLE` or "not permitted") | `a-child-body-delivers-the-access-check` | `tool_used` Write, `min: 1` |
| The closing line names exactly AC4 and AC5 | `final-line-names-ac4-and-ac5` | `regex` over the final message |

## Limitations

- A Write or Bash input reaches a `tool_used` grader JSON-escaped; a newline is a literal backslash-n and an em dash is its `—` escape, which is why `narrowed-line-does-not-name-the-access-check` reads the dropped list only (it stops at the dash). A reason that mentions AC6 is not matched.
- The gate call is graded by being made with the list, not by its exit code.
- The child-body graders are keyword signatures, not judgments.
- One spec, one phrasing, one model tier.
