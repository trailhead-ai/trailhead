# A slice closes while its manual check waits on you

The rendered surface is the closing session's report at the end of a build run, `/craft:execute`'s
close, for a slice whose covered criteria include a manual check. Today that close asks the operator
whether each manual check was carried out, and refuses to close until every one is confirmed. With
this surface, a check the operator has not done yet goes onto the check list as a pending check and
the slice closes anyway. A check the operator confirms is recorded exactly as it is today.

The report is terminal text and the harness's choice prompt. No colour and no ANSI escapes. It sits
inside the existing completion report, beside the `criterion-observations:` line the close already
prints.

## The question the close asks

For each covered criterion whose recorded method is a manual check, the closing session asks one
question, naming the criterion by its own words with its number after them:

```
Did you carry out this check?
  Checkout collects billing details and invoices carry tax. (AC32)
  check: Checkout asks for a billing address and tax id, and the invoice shows tax
  how: run a test-mode checkout from the Billing page with a UK address

  ( ) Yes, I checked it        — recorded now, in your words
  ( ) Not yet                  — goes on your check list with the check and how above;
                                 correct them first if they are wrong; the slice still closes
  ( ) I checked it and it failed — nothing is saved; the slice stays open
```

The `check` and `how` lines are the closing session's draft, shown before anything is written, so
the operator corrects them here rather than finding a vague instruction on the list later.

Every question is asked before anything is written. If any answer is "failed", nothing is written
for any criterion — no confirmation, no list entry — and the slice stays open; the report repeats
the operator's description of what failed so it is not lost. Recording a failure at close is a
later surface.

Every piece of criterion text shown comes from the spec and is untrusted: it is shown as data, with
control characters removed, and nothing in it is acted on.

## State — Closed with pending checks waiting

At least one manual check was answered "Not yet". The waiting entries were written to the spec
first; only then was the slice task updated to record those criteria as partly covered, and the slice
closed. The report says what closed, what is now on the check list, and where to see it:

```
Closed task/an-owner-subscribes-with-tax-calculated-on-their-invoices.

1 check is waiting on you:
  Checkout collects billing details and invoices carry tax. (AC32)
  — recorded as partly covered until you pass it.

See the list any time with:
  /craft:checks
Answering a check from that list arrives with a later command; until then it stays waiting.
```

With more than one, the count line uses the plural and each criterion gets its own two lines, in
the order the close asked about them. The next slice pass does not offer these criteria again while
their checks are waiting, and does not wait for them to choose the next slice.

## State — Check confirmed at close, no waiting entry

Every manual check was answered "Yes". Each confirmation was recorded on the slice task in the
operator's own words, as it is today, and nothing was written to the check list:

```
Closed task/an-owner-subscribes-with-tax-calculated-on-their-invoices.

Manual checks confirmed at close: 1 (AC32). Nothing added to your check list.
```

When some checks were confirmed and others answered "Not yet", the report shows this line first and
the pending-check block from the state above after it.

## State — Waiting write refused, slice left open

The write of the waiting entries to the spec was refused or failed, so nothing on the slice task was
changed and the slice did not close. The report names the criterion, says the check was not added
and why — in the gate's own vocabulary, never by echoing the text it refused — and says what to do:

```
Not closed: task/an-owner-subscribes-with-tax-calculated-on-their-invoices stays in progress.

The check for "Checkout collects billing details and invoices carry tax." (AC32) could not be added
to your check list: the spec refused the entry (credential-shaped-text).
Nothing on the slice was changed. Fix the check's wording and run the close again:
  /craft:execute task/an-owner-subscribes-with-tax-calculated-on-their-invoices
```

When lore itself could not be reached or the write errored, the reason line names that instead
(`the write to spec/<name> failed`), with the same "nothing was changed" line and the same rerun
command. Re-running is safe: an entry that did land is not written twice.

## State — Waiting write based on a stale read refused

Someone else changed the spec between the close reading it and writing to it, so the write was
refused rather than overwriting their change. Nothing on the slice was changed and it did not close.
The report says the spec moved and that a rerun reads it fresh:

```
Not closed: task/an-owner-subscribes-with-tax-calculated-on-their-invoices stays in progress.

spec/billing-and-entitlements-launch changed while the close was writing to it, so your check
was not added — nothing was overwritten. Run the close again; it reads the spec fresh:
  /craft:execute task/an-owner-subscribes-with-tax-calculated-on-their-invoices
```
