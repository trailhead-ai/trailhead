# Slice and task — the canonical vocabulary

This is the single place craft defines its two units of work and the bar a slice must
clear. Ritual text elsewhere points here rather than restating the definitions.

## The two units

A **slice** is a vertical increment: when it is done, the system is observably more valuable to its consumer, unless it is a declared enabler (below). A **task** is the
component-shaped unit beneath a slice. These two words, and no others, name these two
things.

## The quality bar: Valuable, Small, Testable

A slice's quality bar is Valuable, Small, Testable. This deliberately drops INVEST's
Independent: slices build on each other by design, so importing the whole acronym
would contradict the sequencing this design depends on.

## The value floor is read against the spec's own consumer

The value floor is read against the spec's own consumer, not against an end user. A
refactor's consumer is the system or the developer; "all callers migrated" clears the
floor where "half of them" does not.

## Selection: smallest-next above the value floor

Slices are chosen smallest-next above the value floor. The selection question is "what
is the next smallest thing shippable that still delivers some value," answered against
current information. This is a per-cycle local choice; a pre-committed global value
ranking is not the rule.

## The enabler carve-out

A slice may deliver no consumer value only if it carries a written justification
naming what it enables and why that cannot be folded into the slice needing it, and
only if the slice consuming it comes next. Naming the consuming slice is prose, not a
commitment: it writes no record. This is the one permitted forward reference, which is
why the no-stored-sequence rule is scoped to records rather than to any mention of
future work.

## State coverage: the floor a visual surface owes

A slice that introduces or changes a visual surface owes state coverage against a
floor keyed by slice archetype. The floor is a minimum, not a ceiling — it is
explicitly non-exhaustive; a slice's actual states govern beyond it.

- A **read-only collection** owes zero, one, many, and a collection-level failure.
- A **single-record view** owes found, not-found, and a record-level failure.
- A **mutation** owes success, validation failure, and concurrent-change.
- A **long-running action** owes in-flight, completed, and failed.

Every archetype whose surface is reachable by more than one principal additionally
owes an unauthorized state, and the slice that first makes that surface reachable
ships its access check in that same slice rather than deferring it to a later one.

A state arrives with the slice introducing the surface it belongs to, and never
earlier.

## The two written shapes state coverage depends on

The parent task's `## Enumerated states` section is one `- <name>` bullet per state.
The design doc carries one `## State — <name>` section per enumerated state, and
each `<name>` is that bullet's text verbatim.
