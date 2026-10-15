# amend-drifted-mechanism-detail-phrased-as-serious-error

## Under test

A mechanism detail inside an `active` decision (how a key is built) has drifted from what shipped; correcting it stays within the decision's scope and is an amend. The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose; in the treatment arm, the same plus the `correct` skill. The criteria are AC21 (an in-scope correction is amended in place, not superseded and not given a successor), AC26 (the corrected text is gone from the body) and AC27 (no blockquote banner or revised note is added).

The prompt is an operator asking for the correction. Wording cue: crossed: an in-scope fix phrased as a serious error (`badly wrong`, `serious inaccuracy`), so severity of tone does not signal supersession.

## Pass condition

The run's vault ends with `decision/drop-duplicate-inbound-events` corrected in place, by amendment. Every clause below holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| The corrected text (a payload `digest`) is present | `corrected-text-present` | `regex` on `vault/decision/drop-duplicate-inbound-events.md`, contains |
| The wrong text (`sequence number`) is absent | `wrong-text-gone` | `regex` on `vault/decision/drop-duplicate-inbound-events.md`, `not_contains` |
| Untouched text survives: `Keys are held for 24 hours.` | `unrelated-text-kept-1` | `regex` on `vault/decision/drop-duplicate-inbound-events.md`, contains |
| Untouched text survives: `Senders retry on timeout` | `unrelated-text-kept-2` | `regex` on `vault/decision/drop-duplicate-inbound-events.md`, contains |
| No blockquote line was added | `no-blockquote` | `regex` on `vault/decision/drop-duplicate-inbound-events.md`, `not_contains` a line starting with `>` |
| No revised/amended/correction note was added | `no-correction-note` | `regex` on `vault/decision/drop-duplicate-inbound-events.md`, `not_contains` revised/amended/correction/corrected/erratum/updated: |
| The sidecar's change history carries a new entry | `change-history-entry` | `regex` on `vault/decision/drop-duplicate-inbound-events.json`, contains the `change-history` key (seeded records have none) |
| Status unchanged (`active`) | `status-unchanged` | `regex` on `vault/decision/drop-duplicate-inbound-events.json`, contains `"status": "active"` |
| No new record file of any kind was created | `no-new-record` | `file_exists` on `vault/*/*.md` with `exists: false` |

## Limitations

- "No new record of any kind" is graded by `file_exists` with `exists: false` over a vault glob. `file_exists` counts only files created during the run, so it cannot see seeded records, which is what this clause wants.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- A run that asks the operator instead of acting writes nothing and fails the corrected-text clause. The prompt is a single turn with an operator who has already ruled, so asking is not the expected route.
