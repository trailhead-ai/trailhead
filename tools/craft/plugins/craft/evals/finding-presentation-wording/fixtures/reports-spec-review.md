# Pass reports: the allotment waiting list spec

Eight passes each read `spec.md` in parallel, from lanes that could not see each other, and returned
the report below. The four lens reports are in the form the council's prompt template fixes; the
others are in their own forms. Resolved maturity: `maturity: production (basis: stamp)`. No
stand-downs.

The numbered claims the fact pass was given:

1. About 340 people are on the paper waiting list.
2. Plots come free a few times a month.
3. The warden rings people in order today.
4. Plot swaps between tenants are outside this spec.

### Fact verification

1. REFUTED — the committee minutes of 3 March record 412 names on the list, not about 340.
2. CONFIRMED — the society's plot register shows 31 plots freed in the last twelve months, which is
   fewer than "a few times a month" suggests but of that order.
3. UNVERIFIABLE — nothing written says the warden keeps to the order; the claim needs the warden's
   own account.
4. CONFIRMED — the spec's Non-Goals list plot swaps.

### Premise

## Findings
- [Critical] AC1 asks for an email address, but a large part of the paper list is older people who
  have no email: when the paper list is retired they cannot join, and the people who waited longest
  drop off the list. Suggested: let the warden enter a person on the list with a phone number
  instead of an email, and offer them by phone.
- [Important] The problem is stated as the list living on paper, but the complaint underneath is
  that nobody can check the order was followed; AC6 answers that, and AC1 to AC5 are a larger build
  than the complaint needs. Suggested: say in Objectives that checking the order is the goal.
- [Minor] "A few times a month" is not a number the spec relies on anywhere.

## Confidence
medium — the share of the list without email is not in the spec.

### Builder

## Findings
- [Important] Nothing says who tells the system a plot has come free, so AC2 has no trigger.
  Suggested: add a criterion that a warden marks a plot freed.
- [Important] AC3 and AC5 both change a person's place, and AC1 states the place in an email; the
  spec does not say whether a place is stored or worked out from join order. Suggested: say it is
  worked out from join order.
- [Minor] The spec could name the mail provider.

## Confidence
medium — no design exists yet to check against.

### Reliability

## Findings
- [Critical] An offer sent to a mistyped or full mailbox stays open for 14 days: the plot sits empty
  for two weeks, then the person goes to the back of the queue having never seen the offer.
  Suggested: add a criterion that a bounced offer passes to the next person at once and does not
  move the first person.
- [Important] Nothing says what happens when a plot is freed and the queue for its site is empty.
  Suggested: state that the plot stays unallocated and the warden is told.
- [Minor] AC3 does not say when during the day the 14 days end.

## Confidence
high — the mailbox failure is the commonest way an email offer goes wrong.

### Security

## Findings
- [Important] Any plot warden can grant a plot out of turn and the typed reason is the only control;
  nobody else sees a grant until someone reads the history. Suggested: tell the committee chair by
  email whenever a grant is made.
- [Minor] The confirmation email states a person's place in the queue, which tells whoever reads it
  how many people are ahead.

## Confidence
medium — the spec names no roles beyond plot warden.

### Advocate

## Findings
- [Critical] A person whose offer email never arrives is moved to the back of a 340-person queue
  with no message at all, after waiting for years. Suggested: send a warning email on day 10 and a
  message when the offer lapses.
- [Important] AC1 to AC5 leave out how a person sees their current place after the first email.
  Suggested: add a page or a reply-to-email that shows it.
- [Minor] "Lapse" in the history reads as jargon; say "expired".

## Confidence
medium — no members have been asked.

### Consistency

## Findings
- [Important] The Objectives promise that a person can "see their place", but no criterion after the
  confirmation email in AC1 delivers it. Suggested: add a criterion or drop the words.
- [Important] AC6 lists acceptance and departure as events, yet AC2 and AC5 never say they are
  recorded. Suggested: add "and recorded" to those two criteria.
- [Minor] AC4 says "queue's history" and AC6 says "the queue's history"; the term is the same.

## Confidence
high — each item is a direct comparison of two passages.

### Divergence

## Findings
- [Important] One build lets a person join the queues of several sites; another stops them after the
  first. Both satisfy AC1. Pin: a person is on at most one queue at a time.
- [Important] One build counts the 14 days from when the offer is sent; another from when it is
  first opened. Both satisfy AC2 and AC3. Pin: from when it is sent.
- [Minor] One build shows the history to everyone who joins; another only to the committee. Both
  satisfy AC6. Pin: the committee only.

## Confidence
high — each pair of builds was written out and checked against every criterion.
