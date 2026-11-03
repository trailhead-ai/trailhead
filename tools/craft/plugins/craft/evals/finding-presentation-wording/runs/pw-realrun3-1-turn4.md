The tank-log spec is now `ready`. All the accepted edits, the provenance stamp and the full finding detail went in together in one write, and I then read the record back to confirm the stored body matches what you approved. The criterion gate passes on the final body, and the credential scrub found nothing to remove.

What went in:
- **The spec now has 19 criteria, up from 6.** They add one screen showing all four tanks, failed alert emails that are retried and shown on that screen, and a steward-only sign-in. They also add checks on every plot, tank and litre figure, an append-only history where top-ups can be corrected too, and pinned alert timing (19:00 UK time) and days-remaining sum (over 14 days). The criteria that bundled two things are split, and the rewrite adds a spreadsheet export and a nightly backup. The Objectives now include the steward seeing which tank is lowest and a 100-litre reserve.
- **Your accepted risk is recorded in your own words (C1).** The levels will drift from the real water. The record also notes that, as the spec now stands, the steward has no way to set a figure by hand inside the log.
- **A `## Gauntlet` section is appended.** It holds the stamp (7 findings fixed as proposed, 1 overridden by you), each finding in full, and the 7 "worth fixing" and 5 "rough edges" themes.

The spec doesn't supersede another record, so nothing else was retired. To choose the first slice, run this in a fresh session:

```
/craft:slice spec/a-rainwater-tank-log-for-the-marsh-end-allotment-society
```
