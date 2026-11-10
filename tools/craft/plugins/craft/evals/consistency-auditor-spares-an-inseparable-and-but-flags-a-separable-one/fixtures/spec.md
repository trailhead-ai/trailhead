# spec/queue-claiming

status: draft

## Problem

Support engineers pull work from a shared unassigned queue. Today two engineers
regularly start on the same ticket: there is no claim step, so the only signal that
someone is already working an issue is a comment they may not have posted yet.
Duplicated triage effort is the second-largest source of wasted engineer time in the
last quarter's time study.

The queue view itself ships: engineers can open it and read the unassigned list,
served end to end against the `tickets` service.

## Objectives

Stop two engineers starting the same ticket, and make current ownership visible to
everyone reading the queue.

## Acceptance Criteria

1. Claiming a ticket assigns it to the claiming engineer and removes it from the
   unassigned queue.
2. The queue view shows each claimed ticket's owner, and sends that owner a daily
   digest of the tickets they still hold.
3. An engineer can release a ticket they hold back to the unassigned queue.

## Required Interfaces

- **Ticket ownership** — must record which engineer holds a ticket, and must be
  readable by the queue view.

## Non-goals

- Automatic assignment or round-robin routing.
- Ownership of tickets outside the shared queue.

## Constraints

- Claiming must be atomic under concurrent requests from two engineers.

## UI Direction

n/a

## Open Questions / Risks

- Settled: a released ticket returns to the unassigned queue with its original
  arrival time, not the release time, so releasing does not lose queue position.
