# distill-sweep-builds-its-queue-from-lore

## Under test

When `/craft:distill` runs in sweep mode, it builds its queue with one call to `lore distill-queue`: the waiting specs are the queue and the deferred specs are reported as having no task edges ("link tasks or distill by target"), and the sweep does not re-derive the candidate and exclusion rule by hand with `lore search "kind:spec ..."`. The prose under test is `skills/distill/SKILL.md`, section "Sweep mode — building the queue", as it stands when the baseline runs and after the edit in the treatment arm. Red on the unedited prose: it enumerates the candidates with three `lore search` queries and applies the exclusion per candidate, and never calls `lore distill-queue`.

## Fixture

`fixtures/scaffold.sh` follows the `review-hands-a-stopped-spec-to-tidy-followups` pattern: it seeds a vault, makes `./lore` self-contained over this checkout's lore (so `distill-queue` exists), and writes `CLAUDE.md`. Records, in the order the seeding needs them:

- an `active` ADR, `settled-decision`;
- `previously-distilled`: a `ready` spec labelled `craft/slice-loop=complete`, annotated `distilled=adr`, with a linked `done` task (the resumed-after-stop shape, which stays in the queue);
- `anchored-spec`: a `planned` spec with a `related adr` edge to `settled-decision` and a linked task (already on record, excluded);
- `unlinked-spec`: a `complete` spec with no linked task (deferred).

The seeder carries no labels or annotations, so the scaffold creates the specs with direct `./lore record create` calls (`--label`, `--annotation`, `--related`). The prompt asks for the sweep's queue only, stopping before clustering or drafting, and never names `distill-queue`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants exactly as in craft's `eval-gate.json` `runner` block.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 20`, `timeout_seconds: 300`.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure, a usage-limit error or a timeout is an error, logged apart from graded fails, and is re-run.
- The baseline runs on the current, unedited `skills/distill/SKILL.md` before any prose edit.
- Tier: core (runs on every push); its two sibling failure arms are `merge_only`.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The sweep ran `lore distill-queue` at least once | `ran-distill-queue` | `tool_used`, Bash, `distill-queue`, min 1 |
| The sweep did not enumerate candidates by hand: no `lore search` over `kind:spec` | `did-not-search-specs-by-hand` | `tool_used`, Bash, max 0 |
| The queue holds `previously-distilled` and not `anchored-spec`, and `unlinked-spec` is reported as deferred (no linked task), not queued | `queue-and-deferred-match-the-fixture` | `llm`, `focus: trace` |
| No record was written or changed | `wrote-no-record` | `tool_used`, Bash, `record (create\|update\|amend)`, max 0 |

## Mutation plan

Each grader is mutation-checked: `ran-distill-queue` and `did-not-search-specs-by-hand` against the unedited prose (the baseline run); `queue-and-deferred-match-the-fixture` with a fixture whose `./lore distill-queue` prints a queue that differs from the seeded truth (a graded sweep that relays it faithfully must then fail the grader's fixture-truth clause, and a sweep that re-derives by hand would pass it, so this grader alone does not tell the two apart; the tool graders do); `wrote-no-record` with a prompt variant that lets the sweep proceed to drafting.

## Limitations

- With the real lore and a seeded vault, a sweep that derives the rule by hand reaches the same queue as the command. The two are told apart by which tools ran, not by the answer.
- One fixture, one phrasing, one model tier, three runs.
- The record-id shape check on printed names is not graded here: the lore command itself prints an invalid id as a non-name line, and a runner case seeds a vault whose slugs the CLI restricts, so no case can seed one. It is covered by reading the prose.
- The disagreement report (a spec lore lists as waiting for which the task-tree search finds no task) cannot be reached with the real lore, since the command and the search read the same edges. It is covered by reading the prose.
