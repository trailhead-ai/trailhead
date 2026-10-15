# supersede-spec-change-orphaning-an-objective-phrased-as-small-tweak

## Under test

Dropping the only criterion that delivers one of a `ready` spec's objectives leaves that objective with nothing delivering it; it is a material change and goes to a successor spec, with the predecessor not yet flipped. The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose; in the treatment arm, the same plus the `correct` skill. The criterion is AC22 (a material change of scope is routed to supersession, not amended).

The prompt is an operator asking for the change. Wording cue: crossed: a material change phrased as a small tweak (`Small cleanup`, `just drop that criterion`).

## Pass condition

The run's vault shows the change routed to supersession, with `spec/notify-owners-of-stale-records` left as it was. Every clause below holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs.

Body unchanged is graded as "every original sentence still present, the new claim absent, and no change-history entry", not as a byte comparison the runner cannot make.

| Clause | Grader | Type and target |
|---|---|---|
| A successor spec exists | `successor-spec-exists` | `file_exists` on `vault/spec/*.md` |
| The successor is a `draft` | `successor-is-draft` | `llm` on `trace`: the judge checks the agent's `./lore record create --kind spec` call succeeded and left the spec at draft (see Limitations) |
| The predecessor is not yet flipped: status still `ready` | `predecessor-still-ready` | `regex` on `vault/spec/notify-owners-of-stale-records.json`, contains `"status": "ready"` |
| Predecessor criterion survives: AC3 (`never flagged as stale again`) | `predecessor-text-kept-1` | `regex` on the predecessor `.md`, contains |
| Predecessor criterion survives: AC1 (`untouched for 60 days`) | `predecessor-text-kept-2` | `regex` on the predecessor `.md`, contains |
| Predecessor objective survives: `stop hearing about records they have retired` | `predecessor-text-kept-3` | `regex` on the predecessor `.md`, contains |
| The predecessor's change history has no new entry | `predecessor-no-history` | `regex` on the predecessor `.json`, `not_contains` the `change-history` key |

## Limitations

- `file_exists` counts only files created during the run, so a successor is seen and the seeded predecessor is not.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- A run that asks the operator instead of acting writes nothing and fails the successor clauses. The prompt is a single turn with an operator who has already ruled, so asking is not the expected route.
- A `regex` `path` takes no glob and the successor's name is not known in advance, so `successor-is-draft` is an `llm` grader over `trace`. An `llm` judge sees only the first 12 and last 12 messages of the session.
- The second objective is delivered only by AC3 in the fixture, so removing AC3 by amend would leave it with nothing delivering it.
