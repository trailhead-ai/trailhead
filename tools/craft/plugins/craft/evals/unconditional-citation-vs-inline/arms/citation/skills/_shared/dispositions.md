# Accepting, and overriding in one round-trip

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
