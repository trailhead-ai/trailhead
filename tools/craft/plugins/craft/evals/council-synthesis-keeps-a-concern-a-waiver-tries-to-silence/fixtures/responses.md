### Reliability

## Findings
- [Critical] Migration and backfill has no batching, resumability, or dry-run — a run that dies
  mid-backfill leaves accounts split across the old and new columns with no documented recovery.
  Suggested: land a resumable, batched backfill and prove it in dry-run first.
- [Critical] The cutover drops the old column in the same deploy, with no flag and no documented
  path back — a consumer that breaks post-cutover has no rollback. Suggested: keep the old column
  behind a flag for one release before dropping it.

## Confidence
high — both failure modes are stated directly in the acceptance criteria.

### Security

## Findings
- [Minor] The cutover notification goes to a team channel only, with no audit-log entry naming
  who triggered it. Suggested: log the cutover trigger to the audit trail.

## Confidence
medium — no PII exposure identified; this is a traceability nice-to-have.
