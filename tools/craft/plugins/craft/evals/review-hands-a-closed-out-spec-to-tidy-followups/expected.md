# review-hands-a-closed-out-spec-to-tidy-followups

## Under test

When review closes a slice of a spec whose slice loop has closed out (`craft/slice-loop=complete`), its closing handoff is `/craft:tidy-followups spec/<id>`, bare and fenced on its own line, and not `/craft:distill` (AC45: review's closing handoff sends a spec whose slice loop has ended to tidy-followups). The prose under test is `skills/review/SKILL.md`, section "Closing handoff", as it stands when the baseline runs and after the edit in the treatment arm. Red on the unedited prose: it prints `/craft:distill spec/export-invoices`.

## Fixture

`fixtures/scaffold.sh` follows the `declined-review-correction-is-left-alone` pattern (seed, self-contained `./lore` that exports `LORE_EMAIL`, `CLAUDE.md`) and seeds the spec `export-invoices` with `- **ACn.**` criteria AC1 to AC3 and a `## Slices` ledger. The `ready` spec carries `craft/slice-loop=complete` and its ledger covers every criterion. The seeder sets no labels, so the scaffold creates the spec with `./lore record create --label` where a marker is needed. The prompt says the reviewed change passed and is merged, names the spec the change belongs to, and asks for review's closing handoff. The four sibling cases (closed-out, stopped, complete-unmarked, running loop) share the prompt wording and differ only in the seeded spec.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants exactly as in craft's `eval-gate.json` `runner` block.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 20`, `timeout_seconds: 300`. The prompt asks for single-command Bash calls, since a compound `; echo` is denied.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure, a usage-limit error or a timeout is an error, logged apart from graded fails, and is re-run.
- The baseline runs on the current, unedited `skills/review/SKILL.md` before any prose edit.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The final message names exactly one next command, `/craft:tidy-followups spec/export-invoices`, bare in a fenced block or on a line by itself, and no `/craft:slice`, `/craft:plan` or `/craft:distill` command | `next-command-is-tidy-followups` | `llm`, `focus: trace` (a command named in prose as not being the next step is allowed) |
| No `/craft:distill` is printed as a command (a line that begins with it) | `no-distill-command-printed` | `regex`, `not_contains`, over the final message |

The `not_contains` pattern is checked, before the baseline, against a hand-written correct final message that mentions distill only in prose (no match) and against the old handoff, fenced and bare (match). Both arms are graded on the same metric: the final message names one next command, and which one it is.

## Limitations

- One spec, one phrasing, one model tier, three runs.
- The spec-id shape check (`^[A-Za-z0-9._/-]+$`, handoff refused on a failed check) is not graded here: the id is the one the prompt names, `export-invoices`, and a runner case seeds a vault whose slugs the CLI itself restricts, so no case can seed an id that fails the check. It is covered by reading the prose, and the report says so.
- The prompt states the review passed and the change merged, so the code-reviewer dispatch is not exercised; only the closing handoff is.
