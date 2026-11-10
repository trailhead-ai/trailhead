# Spec — replace the numeric account id with a UUID

**Problem.** The accounts table's primary key is a 32-bit integer nearing exhaustion.
Every consumer service joins on this column directly.

**Objectives.** Move every account row to a new UUID primary key without an outage and
without breaking a consumer that still joins on the old column mid-rollout.

## Acceptance Criteria

- **AC1.** A backfill job copies every existing account's new UUID into a shadow column,
  run once against the production table with no batching, no resumability, and no
  dry-run mode — it either finishes clean or the operator re-runs it from row zero.
- **AC2.** Once the backfill finishes, the old integer column is dropped in the same
  deploy that cuts every consumer over to the UUID column, with no flag to reintroduce
  it and no documented path back to the integer column if a consumer breaks.
- **AC3.** Consumers are notified of the cutover in the team channel.

## Maturity

- accounts-service: production

## Non-Goals

- Waives: migration and backfill — the shadow-column backfill was already proven safe in a
  rehearsal against a full production snapshot last quarter, and this spec only wires up the
  cutover that rehearsal already validated.

