# amend-narrow-one-criterion-in-ready-spec

## Under test

Narrowing one acceptance criterion of a `ready` spec, where every objective still has a criterion delivering it, is an amend. The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose; in the treatment arm, the same plus the `correct` skill. The criteria are AC21 (an in-scope correction is amended in place, not superseded and not given a successor), AC26 (the corrected text is gone from the body) and AC27 (no blockquote banner or revised note is added).

The prompt is an operator asking for the correction. Wording cue: a plain scope decision phrased as a narrowing.

## Pass condition

The run's vault ends with `spec/export-the-activity-feed` corrected in place, by amendment. Every clause below holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| The corrected text (`31 days`) is present | `corrected-text-present` | `regex` on `vault/spec/export-the-activity-feed.md`, contains |
| The wrong text (the unqualified claim `any date range the team lead chooses.`; post-baseline correction, see `tools/lore/MANUAL-EVAL.md`) is absent | `wrong-text-gone` | `regex` on `vault/spec/export-the-activity-feed.md`, `not_contains` |
| Untouched text survives: AC1 (`as a spreadsheet file`) | `unrelated-text-kept-1` | `regex` on `vault/spec/export-the-activity-feed.md`, contains |
| Untouched text survives: AC3 (`records who requested it and when`) | `unrelated-text-kept-2` | `regex` on `vault/spec/export-the-activity-feed.md`, contains |
| Untouched text survives: the second objective (`traceable to the person who requested it`) | `unrelated-text-kept-3` | `regex` on `vault/spec/export-the-activity-feed.md`, contains |
| The narrowed criterion keeps its number, edited in place | `criterion-kept-in-place` | `regex` on `vault/spec/export-the-activity-feed.md`, contains `**AC2.**` |
| No blockquote line was added | `no-blockquote` | `regex` on `vault/spec/export-the-activity-feed.md`, `not_contains` a line starting with `>` |
| No revised/amended/correction note was added | `no-correction-note` | `regex` on `vault/spec/export-the-activity-feed.md`, `not_contains` revised/amended/correction/corrected/erratum/updated: |
| The sidecar's change history carries a new entry | `change-history-entry` | `regex` on `vault/spec/export-the-activity-feed.json`, contains the `change-history` key (seeded records have none) |
| Status unchanged (`ready`) | `status-unchanged` | `regex` on `vault/spec/export-the-activity-feed.json`, contains `"status": "ready"` |
| No new record file of any kind was created | `no-new-record` | `file_exists` on `vault/*/*.md` with `exists: false` |

## Limitations

- "No new record of any kind" is graded by `file_exists` with `exists: false` over a vault glob. `file_exists` counts only files created during the run, so it cannot see seeded records, which is what this clause wants.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- A run that asks the operator instead of acting writes nothing and fails the corrected-text clause. The prompt is a single turn with an operator who has already ruled, so asking is not the expected route.
- The narrowed criterion still delivers the first objective together with AC1, so no objective is orphaned; that is why this is in scope.
