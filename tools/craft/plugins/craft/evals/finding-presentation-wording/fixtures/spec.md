# Spec: a waiting list for allotment plots

**Problem.** The Hawthorn Lane allotment society keeps its waiting list on paper in the warden's
shed. About 340 people are on it. Plots come free a few times a month, the warden rings people in
order, and nobody can see where they stand or whether the order was followed.

**Objectives.** Put the waiting list online so that a person can join it and see their place, so
that a freed plot is offered to the right person in order, and so that the order can be checked
afterwards.

## Acceptance Criteria

- **AC1.** A person joins the waiting list for one site by giving a name, an email address and the
  site they want, and receives a confirmation email stating their place in that site's queue.
- **AC2.** When a plot is freed, the person at the front of that site's queue is sent an offer by
  email, and the offer stays open for 14 days.
- **AC3.** An offer not accepted within 14 days passes to the next person in the queue, and the
  person who did not answer goes to the back of the queue.
- **AC4.** A plot warden can give a freed plot to a named person out of turn, but only by recording
  a reason, which then shows in the queue's history.
- **AC5.** A person can leave the queue at any time, and the people behind them each move up one
  place.
- **AC6.** The queue's history lists every join, offer, acceptance, lapse, out-of-turn grant and
  departure, newest first, with the date.

## Maturity

- allotment-portal: production

## Non-Goals

- Paying the annual plot fee online.
- Plot swaps between existing tenants.
