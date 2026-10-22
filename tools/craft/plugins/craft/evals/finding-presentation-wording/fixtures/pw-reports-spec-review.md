# Pass reports: the telescope loan desk spec

Eight passes each read `pw-spec.md` in parallel, from lanes that could not see each other, and
returned the report below. The four lens reports are in the form the council's prompt template fixes;
the others are in their own forms. Resolved maturity: `maturity: production (basis: stamp)`. No
stand-downs.

The numbered claims the fact pass was given:

1. The club lends six telescopes.
2. Loans are kept in a paper ledger in the clubhouse cupboard.
3. A member can cancel a loan up to 48 hours before it starts.
4. Charging a hire fee is outside this spec.

### Fact verification

1. REFUTED — the club's equipment register lists seven telescopes, not six.
2. CONFIRMED — the loan officer's handover note names a paper ledger in the cupboard.
3. UNVERIFIABLE — nothing written says what the club does today; the claim needs the loan officer's own
   account.
4. CONFIRMED — the spec's Non-Goals list charging a hire fee.

### Premise

## Findings
- [Critical] The spec lends a whole telescope for the dates asked, but a member who wants the club's
  largest scope for one clear night holds it for a weekend while others wait weeks for a night the
  scope sat unused. AC1 and AC2 both tie a loan to the telescope and its start date. Suggested: lend by
  the night and let a member add nights, instead of by start date and return date.
- [Important] The problem is stated as the ledger living on paper, but the complaint underneath is that
  two members ask for the same scope; AC2 answers that, and AC1 to AC6 are a larger build than the
  complaint needs. Suggested: say in Objectives that preventing double loans is the goal.
- [Minor] "Six telescopes" is not a number the spec relies on anywhere.

## Confidence
medium — how often a loan is for a single night is not in the spec.

### Builder

## Findings
- [Important] Nothing says who tells the system a waiting-list offer was taken, so AC3 has no way to
  close an offer. Suggested: add a criterion that an accepted offer removes the member from the waiting
  list.
- [Important] AC4 and AC6 both change what the loan history shows; the spec does not say whether the
  history is stored or worked out from the events. Suggested: say it is worked out from the events.
- [Minor] The spec could name the mail provider.

## Confidence
medium — no design exists yet to check against.

### Reliability

## Findings
- [Critical] AC4 records a condition note only when the loan officer is there on the return date; a
  telescope left in the cupboard by a member is shown as free with no note, so a scratched mirror is
  found by the next member and nobody can say who had it. Suggested: show a telescope as free only
  after a condition note is recorded, and until then show it as returned and unchecked.
- [Important] A reminder sent to a mistyped mailbox is never seen, and the case goes back without its
  finder cap. Suggested: add a criterion that a bounced reminder is shown to the loan officer.
- [Minor] AC3 does not say which clock the 48 hours run on.

## Confidence
high — the unchecked return is the commonest way a shared instrument ends up damaged.

### Security

## Findings
- [Important] AC3 does not say how a cancel is tied to the member who borrowed, so anyone holding a
  forwarded confirmation email could cancel the loan. Suggested: state in AC3 that a cancel asks the
  member to confirm.
- [Important] AC6 puts a condition note on a history every member can read, where it may carry personal
  detail. Suggested: show members only that a telescope was returned.
- [Minor] The confirmation email states the loan, which tells whoever reads it which dates a scope is
  already taken.

## Confidence
medium — the spec names no roles beyond the loan officer.

### Advocate

## Findings
- [Critical] A member who is not told the case must hold the finder cap will hand a telescope back
  without one and be asked to replace it; AC1's confirmation email gives only the loan and its return
  date. Suggested: add the list of what goes back in the case to the confirmation email in AC1.
- [Important] AC1 to AC5 leave out how a member sees a loan's current state after the first email.
  Suggested: add a page or a reply-to-email that shows it.
- [Minor] "Waiting list" in the history reads as jargon; say "next in line".

## Confidence
medium — no members have been asked.

### Consistency

## Findings
- [Important] The Objectives promise that a member can "see where the loan stands", but no criterion
  after the confirmation email in AC1 delivers it. Suggested: add a criterion or drop the words.
- [Important] AC6 lists waiting-list offers as events, yet AC3 never says an offer is recorded.
  Suggested: add "and recorded" to AC3.
- [Minor] AC4 says "loan calendar" and AC6 says "loan history"; the two are not the same page.

## Confidence
high — each item is a direct comparison of two passages.

### Divergence

## Findings
- [Important] One build lets a member hold two telescopes on the same dates; another stops them after
  the first. Both satisfy AC1. Pin: a member holds at most one loan on any date.
- [Important] One build counts the 48 hours of AC3 from midnight on the start date; another from noon.
  Both satisfy AC3. Pin: from noon.
- [Minor] One build shows the history to every member; another only to the club's committee. Both
  satisfy AC6. Pin: every member.

## Confidence
high — each pair of builds was written out and checked against every criterion.
