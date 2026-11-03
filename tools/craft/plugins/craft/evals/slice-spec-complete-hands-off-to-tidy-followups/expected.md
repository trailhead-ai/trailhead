# slice-spec-complete-hands-off-to-tidy-followups

## Under test

When the slice ritual finds a spec whose every acceptance criterion is already covered by a closed slice, it reports the spec complete and ends with one next command: `/craft:tidy-followups spec/<id>`, bare on its own line, not `/craft:distill` (AC44: the slice loop's spec-complete outcome hands a finished spec to tidy-followups rather than distill). The one-line reason says tidy-followups decides which of the spec's follow-ups to do now and prints the distill command after. The prose under test is `skills/slice/SKILL.md` (Outcome, "Spec complete"), as it stands when the baseline runs and after the edit in the treatment arm. The early-stop handoff is unchanged and is not measured here.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `export-invoices` with `- **ACn.**` criteria AC1 to AC3, a `## Slices` ledger covering every criterion, and two `done` slice parents carrying the `craft/slice-parent` label, `--related spec=export-invoices` and a `**Covers:**` line. The seeder sets no labels, so the scaffold creates the parents with `./lore record create --label`. The `./lore` wrapper exports `LORE_EMAIL`, since the sandbox's git has no email. The prompt runs `/craft:slice spec/export-invoices`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 40`, `timeout_seconds: 500`, sized from the KU2 probe run (about 0.6 USD a run). The prompt asks for single-command Bash calls, since a compound `; echo` is denied.
- Grants: the termination path needs `Bash(*/scripts/ledger_gate.py *)`, `Bash(*/scripts/candidate_set.py *)`, `Bash(mktemp)` and `Bash(mktemp *)` in craft's `eval-gate.json` runner block, on top of the existing four. No deletion grant and no `Write` is given; the ritual's temp-file cleanup is denied and that is harmless. The final message may mention the denied cleanup, so no grader looks for its absence.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- The baseline runs on the current, unedited slice prose before any prose edit; it is expected red, because that prose prints the distill command.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The final message names exactly one next command, `/craft:tidy-followups spec/export-invoices`, bare on its own line | `next-command-is-tidy-followups` | `llm`, `focus: trace` |
| No `/craft:distill` is printed as a command (a line that begins with it); prose that mentions distill is allowed | `no-distill-command-printed` | `regex`, `not_contains`, over the final message |
| The run reached the spec-complete path: the spec's sidecar carries `craft/slice-loop` = `complete` | `spec-loop-marked-complete` | `regex` on `vault/spec/export-invoices.json`, contains |

The `not_contains` pattern is checked, before the baseline, against a hand-written correct final message that mentions distill only in prose (no match), and against the old handoff, fenced or bare (match).

## Limitations

- One spec, one phrasing, one model tier, three runs.
- The marker grader is green on both arms by design: it proves the run reached the path the handoff belongs to, so a red on the other two clauses is a handoff defect, not a run that never got there. Against a seed that does not reach spec-complete (the scaffold's spec before any run) the sidecar carries no marker.
- Drive's spec-complete outcome reports slice's handoff verbatim (`skills/drive/SKILL.md` step 6); it is not run here.
