# review-hands-a-stopped-spec-to-tidy-followups

## Under test

When review closes a slice of a spec whose slice loop stopped early (`craft/slice-loop=stopped`), its closing handoff is `/craft:tidy-followups spec/<id>` as the one fenced command, plus one prose line saying the slice ritual can be re-run once the blocker clears; slice is not a second fenced command (AC45). The prose under test is `skills/review/SKILL.md`, section "Closing handoff", as it stands when the baseline runs and after the edit in the treatment arm. Red on the unedited prose: it hands back to `/craft:slice` only.

## Fixture

`fixtures/scaffold.sh` follows the `declined-review-correction-is-left-alone` pattern (seed, self-contained `./lore` that exports `LORE_EMAIL`, `CLAUDE.md`) and seeds the spec `export-invoices` with `- **ACn.**` criteria AC1 to AC3 and a `## Slices` ledger. The `ready` spec carries `craft/slice-loop=stopped`, its ledger leaves AC3 uncovered and its body notes the blocker. The seeder sets no labels, so the scaffold creates the spec with `./lore record create --label` where a marker is needed. The prompt says the reviewed change passed and is merged, names the spec the change belongs to, and asks for review's closing handoff. The four sibling cases (closed-out, stopped, complete-unmarked, running loop) share the prompt wording and differ only in the seeded spec.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants exactly as in craft's `eval-gate.json` `runner` block.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 20`, `timeout_seconds: 300`. The prompt asks for single-command Bash calls, since a compound `; echo` is denied.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure, a usage-limit error or a timeout is an error, logged apart from graded fails, and is re-run.
- The baseline runs on the current, unedited `skills/review/SKILL.md` before any prose edit.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The final message names exactly one fenced next command, `/craft:tidy-followups spec/export-invoices`, and one prose line that re-running slice is the alternative once the blocker clears, with no fenced or bare `/craft:slice`, `/craft:plan` or `/craft:distill` command | `next-command-is-tidy-followups-with-slice-in-prose` | `llm`, `focus: trace` |
| No `/craft:distill` and no `/craft:slice` is printed as a command (a line that begins with it); a prose sentence that names slice is allowed | `no-distill-or-slice-command-printed` | `regex`, `not_contains`, over the final message |

The `not_contains` pattern is checked, before the baseline, against a hand-written correct final message that mentions slice only in prose, with the command inside a sentence (no match), and against a hand-written slice-only handoff and a distill handoff (match both). Both arms are graded on the same metric: the final message names one next command, and which one it is.

## Limitations

- One spec, one phrasing, one model tier, three runs.
- The spec-id shape check (`^[A-Za-z0-9._/-]+$`, handoff refused on a failed check) is not graded here: the id is the one the prompt names, `export-invoices`, and a runner case seeds a vault whose slugs the CLI itself restricts, so no case can seed an id that fails the check. It is covered by reading the prose, and the report says so.
- The prompt states the review passed and the change merged, so the code-reviewer dispatch is not exercised; only the closing handoff is.
