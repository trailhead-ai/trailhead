# Spec: a telescope loan desk for the Hollin Hill astronomy club

**Problem.** The Hollin Hill astronomy club lends its six telescopes from a paper ledger kept in the
clubhouse cupboard. Two members regularly ask for the same telescope on the same weekend, the loan
officer cannot see which telescopes are out, and nobody can tell afterwards who had a scope when its
mirror was scratched.

**Objectives.** Put the loan ledger online so that a member can borrow a telescope and see where the
loan stands, so that a loan and its return date reach the right member, and so that every change to a
telescope's record can be checked afterwards.

## Acceptance Criteria

### Borrowing

- **AC1.** A member borrows a telescope by giving a name, an email address, the telescope and a start
  date, and receives a confirmation email that states the loan and its return date.
- **AC2.** A telescope is on loan to one member at a time. A second request for the same telescope and
  dates is refused and the next free start date is offered instead.
- **AC3.** A member can cancel a loan up to 48 hours before it starts. The dates are freed and offered
  to the first member on that telescope's waiting list.

### Returns and care

- **AC4.** On the return date the loan officer records the telescope as back, with a note of its
  condition, and the telescope shows as free on the club's loan calendar from that day.
- **AC5.** Two days before the return date, the member receives a reminder listing what goes back in
  the case: the telescope, its eyepieces, and the finder cap.
- **AC6.** The loan history lists every loan, cancellation, return and waiting-list offer for a
  telescope, newest first, with the date and the condition note.

## Maturity

- telescope-loans: production

## Non-Goals

- Charging members a hire fee.
- Booking the clubhouse for viewing nights.
