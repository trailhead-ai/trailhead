# Gauntlet dispositions — how findings are decided

Reference for `gauntlet/SKILL.md` step 5. This document is reached after the adjudicator has already
presented a recommendation, or after the operator has already answered it — arriving at one of those
points is what sends the reader here. It defines the five disposition terms, how a record advances
across revise rounds, how a clean run is still gated, how the operator accepts or overrides, and
where control passes to a human.

<!-- toc:start -->
**Contents**

- The disposition glossary
- Advancing, revise rounds, and runs
- Zero Criticals is still a decision
- Accepting, and overriding in one round-trip
- Escalation points
<!-- toc:end -->

## The disposition glossary

- `resolved` — the record is edited to address the finding. It is still `draft`; edits are free
  here, which is the entire point of reviewing now.
- `revise` — the finding needs a change the edit-first test could not resolve on the spot. This is
  the premise pass's characteristic outcome, and landing on it is a *success* of the gauntlet, not a
  failure of the record — a prescription here costs a conversation; the same gap discovered
  mid-build costs the build. **Every `revise` carries a prescription** naming what is wrong, what to
  change, and how — specific enough to act on without re-deriving the reasoning. **A finding that
  cannot produce a prescription this specific is not a Critical.** Every prescription also declares
  a **scope**:
  - **`record-only`** — the change lands inside the record under review; the finding is its own
    evidence.
  - **`reaches-downstream`** — the change invalidates work already seeded from this record. It must
    name each derived spec it invalidates, and it must meet the **downstream evidence bar**: a
    named, specific alternative that accomplishes the same outcome — an existing capability, a
    library, or a shape that makes the decision unnecessary — stated with why it achieves the
    outcome and what it costs. **Generalised doubt does not meet it.** A `reaches-downstream`
    prescription **writes nothing to the named specs**: it names them in the escalation table, and
    re-entry into brainstorming is the operator's act.
- `accepted-as-risk: <reason>` — explicit acceptance, recorded for audit.
- `disputed: <reason>` — the operator disagrees with the finding; recorded for audit.
- `answered: <reason>` — the operator supplies a counterargument the passes did not have, and the
  finding is **re-adjudicated in light of it** rather than vetoed. It is the operator's move for
  "you are measuring the system as it is and I am changing it", "that capability is absent because I
  intend to build it", and "that is out of scope, here is the bound" — one instance of a general
  rule: any finding the passes raised for lack of information the operator actually holds is
  answered, not disputed or accepted as risk. `answered` is **not terminal** — **the adjudicator
  re-adjudicates it**, on the same footing as its original proposal: the re-adjudicated outcome is
  `resolved` or `revise`, the same two terms and no others — never `accepted-as-risk`, `disputed`,
  or `answered` again. Those three stay operator-only overrides; re-adjudicating into one would let
  the adjudicator self-author an operator-only disposition under cover of "answering" it. `resolved`
  is the ordinary case, and **the counterargument is folded into the record as an edit**. This is
  the load-bearing half: a counterargument the artifact does not carry is one the next gauntlet
  raises again, because the record still does not say it. **Whichever of the two terms the
  re-adjudication lands on, it re-presents before anything is written** — an answered row that
  becomes `resolved` needs its edit drafted and therefore re-presents, which the existing "override
  *into* `resolved` re-presents too" rule below already covers; an answered row that becomes
  `revise` re-presents on the same footing, including on a run where another `revise` row already
  holds the advance decision, so the discarded counterargument is never dropped without the operator
  seeing the swap.

## Advancing, revise rounds, and runs

A record **advances when no Critical carries a final disposition of `revise`** — final meaning the
disposition a Critical carries after any override and any re-adjudication, never an intermediate
one. **There is no round cap; operator overrides are the termination guarantee**, not a limit on how
many rounds a record may take.

A **run** is one invocation of this skill. A **revise round** is one adjudication cycle inside a
single gauntlet invocation, and **re-runs only the passes that raised the surviving `revise`
Criticals** — not the full roster, since the passes that already resolved cleanly have nothing left
to re-attack. The two — round and run — are named distinctly wherever either appears; they are never
interchangeable.

**Each revise round runs the full accepted tail**: that round's `resolved` edits and its provenance
land atomically before the round ends, so **a surviving `revise` withholds only the advance, never
the writes**. A record mid-round is never behind on the edits it has already earned; only the flip
to `ready` waits on the advance condition.

A Critical still sitting at `answered` is **not yet a final disposition** — it is a request for
re-adjudication, not an outcome of one. **Advancing may not be evaluated while any Critical remains
at `answered`.** Re-adjudicate every answered row first; only once each one carries `resolved` or
`revise` does the advance condition have final dispositions to read. Once re-adjudicated, `answered`
is not terminal, and the row's final disposition is whatever it is re-adjudicated to, normally
`resolved`.

## Zero Criticals is still a decision

A run that produced no Criticals presents the deliverable anyway — synthesis, recommended outcome,
and the compressed Important and Minor summary, labeled as a clean run, with no per-Critical table,
since there are no rows for it to hold — and **still gates on operator acceptance**. Clean of
Criticals is not clean of findings: the Important and Minor themes are part of what the operator
accepts here, and a run that presents none of them reads as a sweep that found nothing. A gauntlet
never advances a record on its own reading of a clean sweep; the clean sweep is the finding, and the
operator is the one who accepts it.

## Accepting, and overriding in one round-trip

Present, then wait. The operator either accepts ("go") or overrides — "dispute C3, otherwise go", or
"answer C3: we're changing the auth provider next quarter, otherwise go" when the operator holds a
counterargument the passes did not have. Overrides apply in **one round-trip**: take every override
from that one reply, apply them together, and do not walk back through the table finding by finding.

- **Echo the full post-override table.** After applying any override, re-render the complete
  `C1`…`Cn` disposition table — **not just the outcome line** — as the last thing before the
  accepted tail executes. A misapplied override ("dispute C3" recorded against C4) changes nothing
  that one line displays, and the audit trail it lands in is permanent.
- **An override naming an id outside the presented range is rejected.** "dispute C7" against a
  five-row table is an error, not a puzzle: say which ids exist and ask again. **Never map an
  unknown id onto the id you think was meant.**
- **An override with no reason is incomplete.** `accepted-as-risk`, `disputed`, and `answered` carry
  the operator's reason text, and you may not write it for them — so "dispute C3" or "answer C3",
  with nothing said about why, is not yet a disposition: ask for the reason and record nothing until
  they give it. **Never record any of the three with a reason you drafted, and never with the reason
  slot empty.** This is the one path by which text you wrote could enter the permanent trail wearing
  the operator's signature.
- **An override off `resolved` withdraws that row's drafted edit.** Every `resolved` row's edit text
  was drafted before you presented, and only the rows still `resolved` once the overrides are
  applied belong to the accepted set. An override to `disputed`, `accepted-as-risk`, `answered`, or
  `revise` therefore **removes that row's edit from `$EDITS`**, and the echoed post-override table
  is what says which edits remain — an override into `answered` removes it only until
  re-adjudication drafts a new one, which the override-into-`resolved` rule below covers. A diff
  assembled before the override lands the one change the operator explicitly declined, permanently,
  in a record about to advance.
- **An override changing revise-presence re-presents once.** If applying the overrides changes
  whether any Critical still carries `revise` — an override that removes the last `revise`, or one
  that introduces one — present the revised recommendation once more and take acceptance again
  **before anything is written**. If that reply changes revise-presence again, it is a further
  override round-trip and it re-presents again. **The cap is one re-present per revise-presence
  change, not one per run.** What the cap forbids is re-presenting a recommendation nothing changed;
  what it never licenses is writing an advance the operator has not seen and accepted.
- **An override *into* `resolved` re-presents too, whatever the advance decision does.** Only the
  rows you proposed `resolved` have their edit text drafted, so an override moving a `revise` row to
  `resolved` — or an `answered` row re-adjudicated to `resolved` — produces an accepted row with no
  edit behind it. Draft that edit, then present once more and take acceptance again — including on a
  run whose advance decision never moved because another `revise` row still holds it. The cap above
  forbids re-presenting a recommendation nothing changed, and **a newly drafted edit is a change**.
  Skip it and you are composing the edit after acceptance, which is exactly what drafting every
  `resolved` edit before presenting forbids.
- **A re-adjudication landing on `revise` re-presents too, whatever the advance decision does.** An
  `answered` row re-adjudicated to `resolved` is covered by the rule above; one re-adjudicated to
  `revise` gets the identical treatment, including on a run that already holds another `revise` row,
  where the revise-presence-changing-override rule never fires because revise-presence does not
  move. Present the revised table and take acceptance again **before anything is written** — an
  operator who answered a finding is owed the chance to see that their counterargument was
  re-adjudicated away before the record that discards it becomes permanent, not after.
- **Each re-presented deliverable carries its round number.** Rounds are uncapped by design, so the
  count is the only signal distinguishing convergence from a directionless loop; it is also the
  after-the-fact evidence that a runaway adjudication happened at all.

## Escalation points

The points where this step hands control to a human are named explicitly, so that the places this
step waits are a short list rather than something a reader has to reconstruct from the prose. There
is no auto-accept flag: every point below waits on a human.

| Escalation point | What it waits for |
|---|---|
| **operator acceptance gate** | the operator accepting the presented deliverable — on every run, clean ones included |
| **override round-trip** | the operator's overrides, applied together and echoed as a full table |
| **route-change re-present** | acceptance of the revised recommendation, whenever overrides change whether any Critical still carries `revise`, or whenever a prescription or edit not in the presented table was newly drafted |
| **failed-write report** | nothing — the tail has stopped and the operator is told the partial state; the record under review always keeps the status it arrived with, because no write is ordered after its flip |
