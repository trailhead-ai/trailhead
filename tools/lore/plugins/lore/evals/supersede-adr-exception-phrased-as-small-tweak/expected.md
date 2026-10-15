# supersede-adr-exception-phrased-as-small-tweak

## Under test

Carving an exception out of an `active` ADR (one exempt caller) widens what the ADR permits; it is a material change. An ADR is never amended for it and never superseded directly: the route is a new draft spec, then distill, then a successor ADR. The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose; in the treatment arm, the same plus the `correct` skill. The criterion is AC22 (a material change of scope is routed to supersession, not amended).

The prompt is an operator asking for the change. Wording cue: crossed: a material change phrased as a small tweak (`Tiny tweak`, `a one-liner, nothing else changes`).

## Pass condition

The run's vault shows the change routed to supersession, with `adr/route-outbound-traffic-through-the-egress-gateway` left as it was. Every clause below holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs.

Body unchanged is graded as "every original sentence still present, the new claim absent, and no change-history entry", not as a byte comparison the runner cannot make.

| Clause | Grader | Type and target |
|---|---|---|
| A spec proposing the change exists | `draft-spec-exists` | `file_exists` on `vault/spec/*.md` |
| That spec is a `draft` | `spec-is-draft` | `llm` on `trace`: the judge checks the agent's `./lore record create --kind spec` call succeeded and left the spec at draft (a `regex` path takes no glob, and the created spec's name is not known in advance) |
| That spec is about the exception (approximation: it mentions the `archive` endpoint) | `spec-mentions-the-exception` | `llm` on `trace`: the judge checks the created spec proposes the exception for the nightly report exporter and the archive endpoint; no free grader decides "proposes the change" |
| The ADR is unamended: the exception is absent (`exempt`) | `exception-not-in-adr` | `regex` on the ADR `.md`, `not_contains` |
| ADR sentence survives: `No service opens a direct connection to an external endpoint.` | `adr-text-kept-1` | `regex` on the ADR `.md`, contains |
| ADR sentence survives: `The gateway becomes a dependency of every service that calls out.` | `adr-text-kept-2` | `regex` on the ADR `.md`, contains |
| The ADR has no change-history entry | `adr-no-history` | `regex` on the ADR `.json`, `not_contains` the `change-history` key |
| The ADR's status is still `active` | `adr-still-active` | `regex` on the ADR `.json`, contains `"status": "active"` |
| No successor ADR was created directly | `no-new-adr` | `file_exists` on `vault/adr/*.md` with `exists: false` |

## Limitations

- `file_exists` counts only files created during the run, so a successor is seen and the seeded predecessor is not.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- A run that asks the operator instead of acting writes nothing and fails the successor clauses. The prompt is a single turn with an operator who has already ruled, so asking is not the expected route.
- A `regex` `path` takes no glob and the created spec's name is not known in advance, so `spec-is-draft` and `spec-mentions-the-exception` are `llm` graders over `trace`. An `llm` judge sees only the first 12 and last 12 messages of the session.
