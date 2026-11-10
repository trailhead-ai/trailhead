# spec/support-ticket-triage

status: draft

## Problem

Support engineers triage inbound tickets from a shared queue. Finding a customer's
prior tickets means copying their email into a separate admin tool and reading the
results by hand, which costs several minutes per ticket and is skipped under load —
so engineers routinely answer a ticket without seeing that the same customer
reported the same fault last week.

The ticket queue itself now ships: engineers can open the queue, see unassigned
tickets, and claim one, served end to end against the `tickets` service.

## Objectives

Cut the time an engineer spends assembling a customer's history, and stop duplicate
faults being triaged as if they were new.

## Acceptance Criteria

1. Support engineers can search tickets by customer email, with results ranked by
   the relevance model.
2. A ticket over the 25 MB attachment limit is rejected, and the engineer is shown
   the reason it was rejected.
3. An engineer can assign a ticket to another engineer.
4. The queue shows each ticket's age in business hours rather than calendar hours.

## Required Interfaces

- **Ticket search** — must accept a customer email and return that customer's
  tickets, and must be callable by the queue view without a second round trip.

## Non-goals

- Customer-facing ticket search.
- Merging duplicate tickets automatically.

## Constraints

- Search must return within 400 ms at the 95th percentile.

## UI Direction

n/a

## Open Questions / Risks

- Settled: ranking is applied server-side rather than in the queue view, so the
  ordering is identical for every client.
