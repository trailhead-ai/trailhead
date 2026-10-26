# Spec: Holds and pickup for the Lantern Row lending library

Status: ready

## Acceptance Criteria

### Placing a hold

- **AC1.** A borrower can place a hold on an item that is out on loan, from the catalogue page or at the desk.

- **AC2.** A borrower can hold at most five items at once, and a sixth hold is refused with a message saying why.

- **AC3.** Holds on the same item are filled in the order they were placed.

- **AC4.** A borrower can cancel a hold at any time before it is filled, and the next hold in that item's queue moves up.

### Filling a hold

- **AC5.** When a held item is returned, it is set aside for the first borrower in its queue and shown as on the hold shelf.

- **AC6.** The first borrower is sent one message that the item is waiting, naming the pickup deadline.

- **AC7.** A hold not collected within seven days of that message lapses, and the item goes to the next borrower in the queue.

### Collecting at the desk

- **AC8.** The desk screen lists the holds waiting for a borrower as soon as their card is scanned.

- **AC9.** Checking a held item out to its borrower clears the hold, and checking it out to anyone else is refused.

- **AC10.** The weekly holds report lists every item on the hold shelf with the days it has waited.

## Non-goals

Holds across branches of the library.

## Slices

- **The first hold slice** — lets the system take a hold on a loaned item and keep each item's holds in the order they were placed. (`task/the-first-hold-slice`, closed 2026-09-18, covers AC1, AC3, partially covers AC2)
