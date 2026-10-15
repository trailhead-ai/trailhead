# supersede-reversed-decision

## Under test

Reversing an `active` decision (inline attachments become an object store) is a material change and goes to supersession. The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose; in the treatment arm, the same plus the `correct` skill. The criterion is AC22 (a material change of scope is routed to supersession, not amended).

The prompt is an operator asking for the change. Wording cue: plain; the operator states the reversal outright.

## Pass condition

The run's vault shows the change routed to supersession, with `decision/keep-attachments-inside-the-record-row` left as it was. Every clause below holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs.

Body unchanged is graded as "every original sentence still present, the new claim absent, and no change-history entry", not as a byte comparison the runner cannot make.

| Clause | Grader | Type and target |
|---|---|---|
| A new decision exists | `successor-decision-exists` | `file_exists` on `vault/decision/*.md` |
| The predecessor's status is `superseded` | `predecessor-superseded` | `regex` on `vault/decision/keep-attachments-inside-the-record-row.json`, contains `"status": "superseded"` |
| Predecessor sentence survives: `stored inline in the same row` | `predecessor-text-kept-1` | `regex` on the predecessor `.md`, contains |
| Predecessor sentence survives: `no second store to operate` | `predecessor-text-kept-2` | `regex` on the predecessor `.md`, contains |
| The reversal was not written into the predecessor (`object store` absent) | `reversal-not-in-predecessor` | `regex` on the predecessor `.md`, `not_contains` |
| The predecessor's change history has no new entry | `predecessor-no-history` | `regex` on the predecessor `.json`, `not_contains` the `change-history` key |

## Limitations

- `file_exists` counts only files created during the run, so a successor is seen and the seeded predecessor is not.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- A run that asks the operator instead of acting writes nothing and fails the successor clauses. The prompt is a single turn with an operator who has already ruled, so asking is not the expected route.
