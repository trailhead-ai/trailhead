# stale-write-leaves-the-slice-open

## Under test

When `pending_check.py` exits non-zero at a slice's close because the spec changed between the writer's read and its write (`reason-code: stale-spec`, exit 2), the closing session does not run the observation gate or the close write, leaves the parent `in-progress`, writes no attestation, does not retry the writer itself, and reports that the spec changed and nothing was overwritten, gives the re-run command, and does not report the slice closed. The prose under test is `skills/_shared/execute.md`, Phase 6's manual-check discharge and completion report, as it stands when the baseline runs and after the edit.

## Fixture

The same seeded spec `schedule-courier-pickups`, slice parent `pickups-can-be-booked-and-confirmed`, `./lore`, `./scripts/` wrappers and real script copies as `close-defers-an-unconfirmed-check` (see its `expected.md` for how they work), with one difference: the scaffold creates `stale-mode`, and while that file exists and `stale-done` does not, the first `record update` on a spec that the real writer makes through `bin/lore` is preceded by a real concurrent edit (a new bullet appended to the spec's `## Slices`), so the real writer's `--base` write is refused by lore and the real script, reading the revision again and finding it changed, exits 2 with `reason-code: stale-spec`. The edit leaves `stale-done` behind, so a retry by the agent would succeed; that is what the "ran once" clause catches. The prompt is the same as the sibling's: the operator confirmed AC2 and answered "Not yet" for AC3, stated as replies. See that case for the limit this choice carries.

## Conditions, sample and bar

- Runner, limits and the scoring rule as in `close-defers-an-unconfirmed-check`. The case passes at 3 of 3 runs after the change; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such.
- The baseline runs on the unedited prose before any prose edit and is expected red: today's prose never runs the writer, so the writer clause fails.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The writer ran | `writer-ran` | `regex` on `lore-calls.log`, `pending_check.py --spec`, contains |
| The writer ran once (no retry) | `writer-ran-once` | `regex` on `lore-calls.log`, `--entries` twice, not_contains |
| No close write | `no-close-write` | `regex` on `lore-calls.log`, `--status done`, not_contains |
| The parent is still `in-progress` | `parent-still-in-progress` | `regex` on `vault/task/pickups-can-be-booked-and-confirmed.json`, contains |
| No attestation written | `no-attestation-written` | `regex` on the parent's body, `## Operator attestations`, not_contains |
| The spec holds no waiting entry | `spec-has-no-waiting-entry` | `regex` on the spec's body, `## Waiting`, not_contains |
| The reply gives the re-run command | `gives-the-rerun-command` | `regex` over the final message, `/craft:execute task/pickups-can-be-booked-and-confirmed` |
| The reply says the spec changed, the check was not added, nothing was overwritten, does not say the slice closed, and names the reason as `stale-spec` | `reply-reports-the-stale-spec` | `llm`, `focus: trace` |

The attestation clause is a reading of the prose, pre-registered here: the attestation line is appended only after the writer and `verify` succeed, so a refused writer leaves nothing half-written.

### Each pass line against each branch, checked before any arm ran

Branches: today's prose (never runs the writer), the edited prose done right, and the ways to get the edit wrong: retry the writer after exit 2; close anyway; write the attestation first and then fail.

| Branch | writer-ran | ran-once | no-close-write | still in-progress | no-attestation | no waiting entry | rerun command | reply | Whole run |
|---|---|---|---|---|---|---|---|---|---|
| Today's: gate refuses AC3, no writer | **fail** | pass | pass | pass | pass | pass | **fail** (re-runs the close only if it thinks to) | **fail** (no `stale-spec`) | fails |
| Edited, done right | pass | pass | pass | pass | pass | pass | pass | pass | passes |
| Retries the writer, which now succeeds, and closes | pass | **fail** | **fail** | **fail** | pass or fail | **fail** (entry landed) | pass | **fail** | fails |
| Closes despite the exit 2 | pass | pass | **fail** | **fail** | pass or fail | pass | pass | **fail** | fails |
| Attests AC2 first, then the writer fails | pass | pass | pass | pass | **fail** | pass | pass | pass | fails |

Only the edited prose done right satisfies every clause. The write clauses read the call log and the vault.

## Limitations

- One spec, one phrasing, one model tier, three runs; the stale edit is a scaffold-made concurrent write, not a real one.
- The replies are stated in the prompt, not asked; the fixture section of `close-defers-an-unconfirmed-check/expected.md` states the limit.
- Only `stale-spec` is exercised; the other refusal and failure codes share one prose path and are not separately run.
- The `llm` clause judges the first and last 12 events of the run.
