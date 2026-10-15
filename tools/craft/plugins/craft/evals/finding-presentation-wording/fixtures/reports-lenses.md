# Reviewer reports: the allotment waiting list plan

Four members of the council each reviewed `plan.md` against `spec.md` in parallel and returned the
report below. The reports are in the form the council's prompt template fixes. Resolved maturity:
`maturity: production (basis: stamp)`. No stand-downs.

### Builder

## Findings
- [Critical] Queue position is stored as a number and renumbered on every leave: a person leaving
  during the 02:00 offer run leaves two rows with `place` 1, and both people get an offer email for
  the one freed plot. Suggested: store the join time and order by it; never store a place number.
- [Important] Task 3 records the grant but nothing removes the granted person from the queue, so the
  nightly job will offer them a second plot. Suggested: have the grant close that person's queue row.
- [Minor] Tasks 2 and 5 both format dates for display; share one helper.

## Confidence
high — the renumbering step is written out in Task 1.

### Reliability

## Findings
- [Critical] The Task 4 import loads about 340 rows in one run with no dry run and no way to undo
  it: running it twice doubles every person's row, and a run that stops halfway leaves no marker of
  where it stopped. Suggested: import under a batch id with a preview first, so a bad run can be
  removed whole.
- [Critical] Renumbering `place` on a leave while the 02:00 job reads it gives two rows the same
  place, so one freed plot is offered to two people. Suggested: order by join time instead of a
  stored place.
- [Important] Nothing signals that the 02:00 job did not run; offers silently stop and nobody
  notices until people write in. Suggested: send the warden an email when the job finishes, and
  another if it has not by 03:00.
- [Minor] No test covers an offer that is exactly 14 days old.

## Confidence
high — both failures follow from steps the plan states.

### Security

## Findings
- [Important] The offer link accepts the offer when opened, with nothing tying it to the person it
  was sent to: a forwarded email, or a mail scanner that follows links, accepts the plot for them.
  Suggested: make the link open a page that asks the person to confirm.
- [Important] The spreadsheet for Task 4 holds 340 names and email addresses and will sit on the
  warden's laptop. Suggested: delete it once the import is verified and say so in the task.
- [Minor] The reason a warden types for an out-of-turn grant shows in the history page, which any
  committee member can read; it may carry personal detail.

## Confidence
medium — no authentication design is written down yet.

### Advocate

## Findings
- [Critical] People may find the queue confusing once it is online. Suggested: add a help page.
- [Important] The offer email does not say that ignoring it for 14 days sends the person to the back
  of the queue, so people will lose their place without knowing. Suggested: say it in the first
  paragraph of the offer email.
- [Important] Leaving the queue gives no confirmation, so a person who clicked by mistake cannot
  tell whether they are still on it. Suggested: show and email a one-line confirmation.
- [Minor] The history page calls an expired offer a "lapse", which reads as jargon.

## Confidence
medium — based on the emails and screens as written, none built yet.
