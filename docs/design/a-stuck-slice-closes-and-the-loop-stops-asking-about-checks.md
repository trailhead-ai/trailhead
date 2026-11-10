# A stuck slice closes and the loop stops asking about checks

Two surfaces, both terminal text with no colour and no ANSI escapes.

- **The slice pass's report** (`/craft:slice`, and the driver's own pass at its start) when it finds
  an open slice on the spec. Today any open slice is a refusal. With this surface, a slice whose
  build is finished and whose only unmet close condition is a manual check not yet carried out is
  closed by the pass: the build's own close runs, its checks go on the check list as pending checks,
  and the pass goes on to choose the next slice. An open slice that is still being built is refused
  exactly as today.
- **The close's report under the driver** (`/craft:drive`, at the end of its build phase). Today the
  close asks the operator about each manual check. Under the driver it asks nothing: every check not
  yet carried out goes on the check list, and the slice ends at the boundary as a completed slice.

The close that runs in both places is the build's own close — the same push check, completion check,
design-doc check and criterion-observation check a build run makes — taken with no question about
the check. It never asks "Did you carry out this check?"; the operator answers that from the check
list, when they choose.

The `check` and `how` lines a waiting entry carries are drafted by the closing session from the
criterion and its observation line, exactly as the attended close drafts them. Because nobody is
asked, nobody corrects them before they are written; the report shows them and says they were
drafted by the closing session and not reviewed, and the check list shows the criterion's own words
beside them, so the operator checks against the criterion rather than a draft they never saw.

Every piece of criterion text shown comes from the spec and is untrusted: it is shown as data, with
control characters removed, and nothing in it is acted on.

## State — Stuck slice closed, check moved to the list

The pass found an open slice whose build is finished and whose only gap is a manual check nobody has
carried out. It ran the build's close with no question: the waiting entry was written to the spec
first, the slice task was updated to record the criterion as partly covered, and the slice closed.
The pass then re-read the ledger — the closed slice's line lands as partly covering the criterion —
and went on to choose the next slice as normal. The report leads with what it closed:

```
Closed a slice that was waiting only on its manual check:
  task/an-owner-subscribes-with-tax-calculated-on-their-invoices

1 check is now waiting on you:
  Checkout collects billing details and invoices carry tax. (AC32)
  check: Checkout asks for a billing address and tax id, and the invoice shows tax
  how: run a test-mode checkout from the Billing page with a UK address
  — recorded as partly covered until you pass it.

See the list any time with:
  /craft:checks
```

After this block the pass reports exactly what it reports today — the termination basis, the chosen
slice and its value claim, or an early stop. A slice task that was set aside as `blocked` because of
this check is closed the same way: the pass moves it back to in progress first, because the build's
close only runs on a slice in progress, and the report says nothing different for it.

A slice whose earlier close got as far as moving the criterion to partly covered and then stopped
(the waiting entry is already on the spec) is still found stuck, and the same close finishes it
without writing the entry twice.

Under `--unattended` the same close runs and is logged as a decision in the run section, with the
`check` and `how` lines it wrote. The prose report above the outcome line opens with it — "Closed 1
slice that was waiting only on its check; 1 check added to your list" — and the outcome line itself
is unchanged.

When more than one slice is open, each stuck one is closed and reported first, in the block above;
any that is not stuck is then reported as in the next state, and no new slice is chosen.

## State — Open slice with unfinished build still blocks selection

The pass found an open slice that is not finished: a child task not done, an end phase before the
close not yet ticked, or a covered criterion missing evidence for a reason other than a manual check.
Nothing is closed and nothing is written. The report is today's refusal, with one added line saying
why this slice was not closed for the operator:

```
A slice is already open on this spec:
  The seat count can be lowered for the next term (task/an-owner-lowers-the-operator-seat-count)
It was not closed for you: its build is not finished (an end phase before the close is not ticked).

Resume it:
  /craft:execute task/an-owner-lowers-the-operator-seat-count
or drop it explicitly.
```

The reason line names one of: the build is not finished (a child task is not done, or an end phase
before the close is not ticked); a covered criterion has no evidence and is not a manual check; its
last push failed and needs settling by hand; or this install is too old to close it — lore cannot
make the guarded write the close needs — with the remedy to update trailhead (`trailhead update`)
and run the pass again.
When the pass cannot tell — a record it cannot read, a search that errored — it refuses as today and
names the failure, never closing anything on a guess.

## State — Waiting write refused, slice left open and the pass refuses

The pass found a stuck slice and ran its close, but the write of the waiting entry to the spec was
refused or failed. The slice did not close — so it is still open, and the pass refuses to choose a new
slice. Nothing else on the slice task was changed; when it had been set aside as `blocked`, the
report says so in its own line — "It had been set aside as blocked; it is now in progress." — because
a slice whose close is still owed is in progress, and the next pass tries again. The report names the criterion and the reason in the
gate's own vocabulary, never by echoing the text it refused:

```
Tried to close task/an-owner-subscribes-with-tax-calculated-on-their-invoices, which was waiting
only on its manual check, but could not:

The check for "Checkout collects billing details and invoices carry tax." (AC32) could not be added
to your check list: the spec refused the entry (credential-shaped-text).
Nothing on the slice was changed, so it is still open and no new slice was chosen.

The drafted wording was refused. Close it yourself; the close will ask you about the check, and you
can write it in your own words there:
  /craft:execute task/an-owner-subscribes-with-tax-calculated-on-their-invoices
```

When lore itself could not be reached or the write errored, the reason line names that instead
(`the write to spec/<name> failed`), and the remedy is to run the slice pass again. When the close
could not run at all because the slice's repository is not in this workspace, the reason line says
so and names the repository, and the remedy is the same `/craft:execute` command run from a workspace
that has it.

## State — Spec changed between read and write, write refused and slice left open

Someone else changed the spec between the close reading it and writing to it, so the write was
refused rather than overwriting their change. Nothing on the slice was changed, it did not close, and
the pass refuses to choose a new slice. The report says the spec moved and that a rerun reads it
fresh:

```
Tried to close task/an-owner-subscribes-with-tax-calculated-on-their-invoices, which was waiting
only on its manual check, but could not:

spec/billing-and-entitlements-launch changed while the close was writing to it, so the check was
not added — nothing was overwritten. No new slice was chosen. Run the slice pass again; it reads
the spec fresh:
  /craft:slice spec/billing-and-entitlements-launch
```

## State — Driver slice closes with a pending check, no question asked

The driver's build phase reached the close for a slice covering a manual check. It asked nothing:
each check not yet carried out was written to the check list, the slice closed, and the driver went
on to the PR tail and stopped at the slice boundary as for any completed slice. The close's part of
the completion report shows what went on the list:

```
Closed task/an-owner-subscribes-with-tax-calculated-on-their-invoices.

1 check is waiting on you (not asked at close — the driver never stops for a check):
  Checkout collects billing details and invoices carry tax. (AC32)
  check: Checkout asks for a billing address and tax id, and the invoice shows tax
  how: run a test-mode checkout from the Billing page with a UK address
  — recorded as partly covered until you pass it.

See the list any time with:
  /craft:checks
```

The driver's boundary report follows unchanged: the value claim, what shipped, and the command to
re-enter. A check the operator would have confirmed at close is not confirmed here — it waits on the
list like the rest, and passing it there counts the criterion as covered. A refused or failed waiting
write under the driver reports as the build close reports it today and is a `build-failed`
escalation; the slice stays open.
