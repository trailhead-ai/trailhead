The spec review is finished, and *A telescope loan desk for the Hollin Hill astronomy club* is now `ready`.

- **One write applied everything you accepted.** That covers eight fixes, the edits that changed when the scratched-mirror finding (C1) was withdrawn, and a new `## Gauntlet` section. The section holds the review record, your risk acceptance quoted word for word, and the full findings. I then set the status to `ready` and read the record back: 22 numbered acceptance criteria, a new Dependencies section, and it still passes the criterion gate.
- **One choice was mine, and you can change it later:** a 7-day loan. I drafted that length, and the loan officer can change it under the loan-length criterion (AC4).
- **The scratched-mirror gap is now a recorded risk.** The desk doesn't record handovers, and history entries don't say who made them. Since the paper handover sheet stays this season, that's fine for now. When the club drops the sheet, the custody problem returns and needs a new spec.
- **Nothing to retire:** the spec had no `craft/supersedes` label, so no older spec was superseded.

To choose the first slice, run this in a fresh session:

```
/craft:slice spec/a-telescope-loan-desk-for-the-hollin-hill-astronomy-club
```

---
The `## Gauntlet` section as the record holds it after the write (read with `lore record show` under the isolated env):

## Gauntlet

- Retained review evidence — a later reader evaluates what follows as a claim about this spec, not
  as its settled design content (`skills/receiving-code-review/SKILL.md`).
- Adversarial spec review (gauntlet, 2026-10-21): 8 passes — maturity: production (basis: stamp);
  facts 1/7 confirmed (6 unverifiable: the club facts are stated only by this spec; the vault and
  codebase held nothing else); 8 design-changing findings folded in (confirmed-email identity and
  officer-only actions; a fixed club loan length; a waiting-list join and a 24-hour offer; paper
  ledger import and a print-out for going back; visible email failures; an officer view of what is
  out and overdue; personal-data retention; bundled criteria split). Criticals dispositioned:
  C1 `accepted-as-risk` (operator override — "the club keeps its paper handover sheet this season,
  so who held a scope and its condition at handover stay on paper and the desk does not need to
  record them yet"), C2 `resolved` (from proposal), C3 `resolved` (from proposal), C4 `resolved`
  (from proposal), C5 `resolved` (from proposal), C6 `resolved` (from proposal), C7 `resolved`
  (from proposal), C8 `resolved` (from proposal), C9 `resolved` (from proposal) — 8 from proposal,
  1 operator override. Important 6, Minor 5, detail below. Withdrawing the C1 edit changed the
  wording of the C2, C7 and C9 edits; the operator saw and accepted those changes before the write.

### Needs your decision (Critical)

- **Scratched mirror still can't be traced to a borrower (C1).** Accepted as a risk by the
  operator. The Problem's third failure is custody: a scope comes back marked fine, the next borrower
  finds a scratched mirror, and nothing says which loan did it, because condition was noted only at
  return and nothing recorded who acted. Proposed fix (withdrawn): a handover entry with a condition
  note, who made each history entry, and a history that is never edited. The club keeps handover on
  its paper sheet this season. Raised by: right problem, self-consistency, left undecided, security,
  failure cases, buildability.
- **Anyone can borrow or cancel as someone else (C2).** Fixed in the spec. A borrower was only a
  typed name and email, and nobody was named as allowed to cancel, record returns or read the
  history, so anyone could cancel another member's loan or borrow in a stranger's name. Fix: a loan
  is held only once its emailed link is opened (AC2); only the link holder can open the loan's page
  (AC8) or cancel (AC9); officer-only actions need individual sign-in (AC17); a new Non-Goal rules
  out checking against a membership roll. Raised by: security, right problem, left undecided,
  self-consistency, ease of use.
- **Loan length and return date never defined (C3).** Fixed in the spec. The member gave only a
  start date, yet the confirmation, the overlap check, the return and the reminder all relied on a
  return date. Two builds could pick a 7-day loan and a per-telescope weekend, and both would pass.
  Fix: a club loan length of 7 days that the officer can change (AC4); refusal on any overlap, with
  one winner when two requests arrive together (AC5); the next free date fits a whole loan (AC6).
  Raised by: left undecided, self-consistency, right problem, buildability, ease of use, failure
  cases.
- **Waiting-list offers can hang forever; no way to join the list (C4).** Fixed in the spec. Freed
  dates went to the first person on a list nobody could join, and an unanswered offer held the dates
  indefinitely. Fix: a refused member can join the list (AC7); offers are held for 24 hours, then
  pass to the next person or free the dates (AC11). Raised by: ease of use, buildability, failure
  cases, security, left undecided, self-consistency.
- **Paper-ledger loans never imported; no way back to paper (C5).** Fixed in the spec. Production
  maturity rates migration and rollback as needing a decision. Open paper loans not entered at go-live
  would let the desk double-book the very weekend the Problem describes. Fix: telescopes and every
  open or future paper loan are entered before opening (AC21); open and future loans can be printed
  to go back to paper (AC22). Raised by: buildability, failure cases, ease of use, security.
- **Failed emails go unnoticed (C6).** Fixed in the spec. Production maturity rates failure
  visibility as needing a decision. A bounced confirmation or a missed reminder was silent, and the
  first person to notice would be the next borrower. Fix: the loan is saved before any email is
  sent; failed sends are retried and listed for the officer to resend (AC20); a new Dependencies
  section names email delivery, a daily scheduled run and officer accounts. Raised by: buildability,
  failure cases, ease of use, security.
- **Loan officer still can't see what's out (C7).** Fixed in the spec. The Problem's second failure
  had no criterion, and a telescope that wasn't returned could be booked straight back out. Fix: an
  officer view of free, held, out and overdue, where a loan is out from its start date (AC13); a
  member calendar without borrowers' names (AC14); an Objectives clause for the officer's view.
  Raised by: right problem, self-consistency, ease of use, failure cases, left undecided.
- **Members' personal data has no retention rule (C8).** Fixed in the spec. Names and emails would
  stay in the history for good, with no way to remove a member, and might leak into logs. Fix:
  names and emails are shown only to officers and the link holder and never logged (AC18); they are
  replaced with a placeholder on request or two years after the member's last loan (AC19). Raised
  by: security.
- **Four criteria bundled outcomes that could ship separately (C9).** Fixed in the spec. The old
  borrowing, double-booking, cancellation and return criteria each joined two outcomes that could be
  delivered separately. Fix: they are split into the request (AC1), confirmation (AC3), refusal
  (AC5), next free date (AC6), cancellation (AC9), freed dates (AC10), offer (AC11), return note
  (AC12) and calendar (AC14). Raised by: self-consistency.

### Worth fixing, your call (Important)

- Scarcity or visibility: the same weekend being wanted twice may be a fairness problem that a
  ledger can't solve (right problem).
- Production rigour for six telescopes with no named owner of email delivery, personal data or
  uptime (right problem).
- The waiting list and the case packing list trace to no problem the spec states (right problem,
  self-consistency).
- The request form's fields have no named validation and no rate limits (security).
- No backup or restore for a ledger that replaces the paper original (failure cases).
- A refused member is not offered other telescopes free on those dates (ease of use).

### Rough edges, nothing to decide (Minor)

- A kit list per telescope instead of fixed reminder text (left undecided).
- The calendar needs a cue that doesn't rely on colour, and a phone layout (ease of use).
- Condition notes need plain, accessible display (ease of use).
- The Maturity unit name "telescope-loans" is used nowhere else (self-consistency).
- Every criterion is verified by a manual check only (buildability, self-consistency).
