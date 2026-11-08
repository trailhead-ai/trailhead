# one-failed-check-saves-nothing

## Under test

When the operator's replies at a slice's close are "Not yet" for one covered `manual-check` criterion and "I checked it and it failed" for another, the closing session writes nothing: it does not run `pending_check.py`, appends no attestation, does not close the parent, and the completion report says nothing was saved and the slice stays open, repeating the operator's description of what failed. The "Not yet" criterion comes first, so a session that writes each answer as it arrives writes the entry before it sees the failure. The prose under test is `skills/_shared/execute.md`, Phase 6's manual-check discharge and completion report, as it stands when the baseline runs and after the edit.

## Fixture

The same seeded spec, slice parent, `./lore`, `./scripts/` wrappers and real script copies as `close-defers-an-unconfirmed-check` (see its `expected.md`). The prompt states the replies: AC2 "Not yet", AC3 "I checked it and it failed", with a failure description ("the 9:30am slot overlaps the 9am slot on a phone") that does not occur in the spec. See that case for the limit this choice carries.

## Conditions, sample and bar

- Runner, limits and the scoring rule as in `close-defers-an-unconfirmed-check`. The case passes at 3 of 3 runs after the change; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such.
- **This case is a guard, not a detector.** The unedited prose already writes nothing for an unconfirmed criterion and stops at the gate, so the baseline is expected green on every mechanical clause; the clause that may fail on it is the reply's repetition of the failure description. It exists to catch an edit that writes the "Not yet" entry before it has every answer.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The writer did not run | `writer-did-not-run` | `regex` on `lore-calls.log`, `pending_check.py (--spec|verify)`, not_contains |
| No close write | `no-close-write` | `regex` on `lore-calls.log`, `--status done`, not_contains |
| The parent is still `in-progress` | `parent-still-in-progress` | `regex` on `vault/task/pickups-can-be-booked-and-confirmed.json`, contains |
| No attestation written | `no-attestation-written` | `regex` on the parent's body, `## Operator attestations`, not_contains |
| The parent's observations are unchanged | `observations-unchanged` | `regex` on the parent's body, `pending-check`, not_contains |
| The spec holds no waiting entry | `spec-has-no-waiting-entry` | `regex` on the spec's body, `## Waiting`, not_contains |
| The reply repeats the failure description | `repeats-the-failure` | `regex` over the final message, `9:30` |
| The reply says nothing was saved and the slice stays open, and does not say anything was added to the check list | `reply-reports-nothing-saved` | `llm`, `focus: trace` |

### Each pass line against each branch, checked before any arm ran

| Branch | writer did not run | no close | in-progress | no attestation | observations unchanged | no waiting entry | repeats failure | reply | Whole run |
|---|---|---|---|---|---|---|---|---|---|
| Today's: gate refuses, run stops | pass | pass | pass | pass | pass | pass | pass if it echoes the description, else **fail** | pass if it says the slice stays open and nothing was added | fails or passes on the reply alone |
| Edited, done right | pass | pass | pass | pass | pass | pass | pass | pass | passes |
| Writes the "Not yet" entry as it arrives | **fail** | pass | pass | pass | **fail** | **fail** | pass | **fail** (something was added) | fails |
| Writes AC2's "Not yet" entry, then stops at the failure | **fail** | pass | pass | pass | **fail** | **fail** | pass | **fail** | fails |
| Closes anyway | **fail** | **fail** | **fail** | pass | pass | pass | pass | **fail** | fails |

## Limitations

- One spec, one phrasing, one model tier, three runs; the replies are stated, not asked.
- The failure-description clause is a regex on one token of the operator's words; a paraphrase fails it.
- A mixed arm where the failed criterion comes first is not run: the order that would expose a write-as-you-go session is the one chosen.

## Grader correction after the first post-edit batch

The first post-edit batch scored 0 of 3, every run failing only `writer-did-not-run`. That grader matched any `pending_check.py` line in the call log, and the edited prose runs the read-only `describe` mode before asking, which the wrapper logs like any other call, so the grader failed a correct run. The pattern was narrowed after that batch to the writer's and `verify`'s call shapes, `pending_check.py (--spec|verify)`; `describe --spec` does not match it because `describe` precedes `--spec`. This is a post-hoc grader change, disclosed here and in `MANUAL-EVAL.md`; the clause still fails the two branches it was written for (writes the entry as it arrives, writes it then stops), because both run the writer.
