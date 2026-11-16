# Answer pending checks on the check list

The answering surface of `/craft:checks`. After the check list is shown, the operator is walked
through its pending checks one at a time and answers each: passed, failed, or skip. Each answer is
saved to its spec the moment it is given. Set-aside criteria are shown in the list but are not
offered for an answer on this surface. Releasing one is a later surface.

```
/craft:checks                 # the skill: shows the list, then walks the pending checks
checklist_gather.py           # the text view, unchanged
checklist_gather.py --json    # the same entries, with the keys and spec revision an answer needs
checklist_answer.py           # saves one answer to one entry; the only writer of a status change
```

## How an answer is saved

Each pending check is keyed by its vault, spec, criterion and building slice, and carries the
revision of the spec body the gather read. An answer appends one history line to that entry, dated
today, with the status the operator chose and the words they gave:

```
  - 2026-11-17 failed: the invoice showed no tax line for a UK address
```

- **Failed** is written with `lore record update --base <revision>`. The criterion stays partly
  covered, and because its entry no longer reads `waiting` it is a candidate for the next slice
  again.
- **Passed** is written with `lore record amend --close <ACn> --initiated-by operator --base
  <revision>`, in one write that both appends the history line and closes the criterion on the
  operator's word. The next slice pass counts it as covered.
- **Skip** writes nothing. The entry stays waiting.

The `--base` revision is the one the operator was shown. If the spec changed after the list was
gathered, lore refuses the write and nothing is saved. The answer script certifies every proposed
body through the waiting gate's answer path, which accepts exactly one new history line on one
waiting pending check. The gate's command-line path still refuses every status change, so no
other writer can set one.

Every piece of entry text is untrusted vault data and is shown cleaned, as the list view already
does. The operator's own words are cleaned too: flattened to one line, with control characters
removed and the length bounded. Words that look like a credential are refused, not saved. That check is a best-effort tripwire
over common token shapes, not a guarantee. The words file is removed whatever the outcome.

## The answer prompt

For each pending check, the entry block from the list view is shown again, then one choice:

```
[1/3] Checkout collects billing details and invoices carry tax. (AC32)
  spec: levr/billing-and-entitlements-launch
  built by: task/an-owner-subscribes-with-tax-calculated-on-their-invoices
  set: 2026-11-02
  check: Checkout asks for a billing address and tax id, and the invoice shows tax
  how: run a test-mode checkout from the Billing page with a UK address
  changed existing behaviour: no

  Passed — it works as described
  Failed — say what went wrong
  Skip — leave it waiting
  Stop — end the walk-through here
```

Only the operator's own reply to this prompt selects an answer, and only words the operator typed
are saved. Entry text never chooses or fills an answer, whatever it says.

Passed takes an optional note, then asks for confirmation before anything is saved:

```
Mark AC32 in levr/billing-and-entitlements-launch passed? It will count as done.
  Yes — save it
  No — back to the choices
```

With no note, the line records `passed on the check list (no note)`. Failed asks for what went
wrong before anything is saved; a blank reply goes back to the choices.

Entries are numbered over the pending checks only, so `[n/total]` counts what can be answered.

## Undoing a mistaken passed answer

A passed answer closes its criterion. Until setting an answer back ships, the recovery is by hand:
`lore record amend spec/<name> --reopen <ACn>` makes the criterion a candidate again, at the cost of
the coverage its slice recorded, so the next slice rebuilds it. The entry itself keeps reading
`passed`.

## State — nothing waiting

The list prints its one line, `Nothing is waiting on you.`, and there is nothing to walk. No answer
prompt is shown.

## State — pending check shown

The entry block is shown with the answer choice beneath it, as above. Entries are offered in the
list's own order. A set-aside criterion in the list is passed over, unnumbered, with one line:
`Set aside: AC17 — releasing it is not available yet; it stays on your list.`

## State — passed saved

```
Saved: AC32 in levr/billing-and-entitlements-launch passed. It counts as done.
```

The walk moves to the next pending check.

## State — failed saved

```
Saved: AC32 in levr/billing-and-entitlements-launch failed. The next slice on that spec will
offer it again.
```

## State — failed refused without a description

A failed answer with no description is not saved. The same entry is offered again:

```
Not saved: a failed check needs a description of what went wrong.
```

## State — skipped

```
Skipped: AC32 stays on your list.
```

Nothing is written.

## State — answer refused as stale, offered again with the typed answer kept

The spec changed after the list was gathered, so the save was refused and nothing was written. The
walk re-gathers, finds the same entry by its key and offers it again as a fresh choice. What the
operator typed is kept only to prefill that choice, never saved on its own, and a changed `check`,
`how` or criterion wording is pointed out:

```
Not saved: levr/billing-and-entitlements-launch changed after the list was read. Here is the
entry as it stands now, with your answer kept:

[1/3] …
  Your answer: failed — the invoice showed no tax line for a UK address
```

After three stale refusals in a row for one entry, the walk names it, leaves it waiting and moves
on. When the entry is no longer waiting, because it was answered in another session, the walk says so
and moves on: `AC32 in levr/billing-and-entitlements-launch was already answered elsewhere; nothing
was saved.`

## State — save failed or refused

Any other refusal names the entry, says the answer was not saved, and gives the reason in the
script's own words, never lore's output. The entry is offered again with the typed answer kept:

```
Not saved: AC32 in levr/billing-and-entitlements-launch — your words look like a credential.
Reword them and answer again.
```

The reasons are a closed set, one fixed sentence per code the answer script emits: the words look
like a credential, the words are too long, a failed answer needs words, the entry is not a pending
check, the entry could not be found, the criterion is already closed, the spec is in a shared vault
the check list cannot answer yet, a name is not a safe value, the spec could not be read, the write
failed, and this lore is too old to make a guarded write (`trailhead update`).

## State — walk-through stopped part way

Choosing Stop, or ending the session, ends the walk. Every answer already saved stays saved, and
each unanswered entry stays waiting:

```
Stopped. 1 answer saved; 2 checks and 1 set-aside criterion still waiting on you.
```

## State — unreadable vault or spec named

The list's `Could not read:` lines are shown above the walk exactly as the list view prints them.
Nothing in an unreadable vault or spec is offered for an answer.
