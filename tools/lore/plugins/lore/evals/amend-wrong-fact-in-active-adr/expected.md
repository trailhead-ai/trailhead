# amend-wrong-fact-in-active-adr

## Under test

A wrong fact in an `active` ADR (a retention figure) is corrected by amend, in place. The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose; in the treatment arm, the same plus the `correct` skill. The criteria are AC21 (an in-scope correction is amended in place, not superseded and not given a successor), AC26 (the corrected text is gone from the body) and AC27 (no blockquote banner or revised note is added).

The prompt is an operator asking for the correction. Wording cue: plain and factual, a stated mistake with the right figure given.

## Pass condition

The run's vault ends with `adr/cache-lookups-in-a-regional-store` corrected in place, by amendment. Every clause below holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| The corrected text (`15 minutes`) is present | `corrected-text-present` | `regex` on `vault/adr/cache-lookups-in-a-regional-store.md`, contains |
| The wrong text (`90 seconds`) is absent | `wrong-text-gone` | `regex` on `vault/adr/cache-lookups-in-a-regional-store.md`, `not_contains` |
| Untouched text survives: `Each region keeps its own lookup cache.` | `unrelated-text-kept-1` | `regex` on `vault/adr/cache-lookups-in-a-regional-store.md`, contains |
| Untouched text survives: `Reads stay local and cheap.` | `unrelated-text-kept-2` | `regex` on `vault/adr/cache-lookups-in-a-regional-store.md`, contains |
| No blockquote line was added | `no-blockquote` | `regex` on `vault/adr/cache-lookups-in-a-regional-store.md`, `not_contains` a line starting with `>` |
| No revised/amended/correction note was added | `no-correction-note` | `regex` on `vault/adr/cache-lookups-in-a-regional-store.md`, `not_contains` revised/amended/correction/corrected/erratum/updated: |
| The sidecar's change history carries a new entry | `change-history-entry` | `regex` on `vault/adr/cache-lookups-in-a-regional-store.json`, contains the `change-history` key (seeded records have none) |
| Status unchanged (`active`) | `status-unchanged` | `regex` on `vault/adr/cache-lookups-in-a-regional-store.json`, contains `"status": "active"` |
| No new record file of any kind was created | `no-new-record` | `file_exists` on `vault/*/*.md` with `exists: false` |

## Limitations

- "No new record of any kind" is graded by `file_exists` with `exists: false` over a vault glob. `file_exists` counts only files created during the run, so it cannot see seeded records, which is what this clause wants.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- A run that asks the operator instead of acting writes nothing and fails the corrected-text clause. The prompt is a single turn with an operator who has already ruled, so asking is not the expected route.
