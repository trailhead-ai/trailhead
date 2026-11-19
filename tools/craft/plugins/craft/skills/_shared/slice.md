# Slice and task — the canonical vocabulary

This is the single place craft defines its two units of work and the bar a slice must clear. Ritual
text elsewhere points here rather than restating the definitions.

<!-- toc:start -->
**Contents**

- The two units
- The quality bar: Valuable, Small, Testable
- The value floor is read against the spec's own consumer
- Selection: a surface, then phase, then interface leverage, then the stop-here test
  - A surface: what the chooser takes whole
  - Where a slice may end short of its surface
  - The first slice on a surface
  - The stop-here test
  - Phase — read, then create, then mutate, then polish
  - Interface leverage — among surfaces tied on phase
- The commitment guard: nothing binds to a shape that is still moving
- The enabler carve-out
- State coverage: the floor a visual surface owes
- The three written shapes state coverage depends on
<!-- toc:end -->

## The two units

A **slice** is a vertical increment: when it is done, the system is observably more valuable to its
consumer, unless it is a declared enabler (below). A **task** is the component-shaped unit beneath a
slice. These two words, and no others, name these two things.

## The quality bar: Valuable, Small, Testable

A slice's quality bar is Valuable, Small, Testable. Small means reviewable as one pull request, and
it is judged at plan time; the chooser does not size. This deliberately drops INVEST's Independent:
slices build on each other by design, so importing the whole acronym would contradict the sequencing
this design depends on.

## The value floor is read against the spec's own consumer

The value floor is read against the spec's own consumer, not against an end user. A refactor's
consumer is the system or the developer; "all callers migrated" clears the floor where "half of
them" does not.

## Selection: a surface, then phase, then interface leverage, then the stop-here test

A slice is one **surface's** whole capability, and selection is a judgment applied in order: which
surface goes first, then which of the tied surfaces hardest exercises the interface later work
depends on, then whether the boundary falls where the operator could stop. Walking a spec's
acceptance criteria in the order they happen to be written is not selection — it is the failure mode
this order exists to prevent. Smallest-next is not a selection level: the chooser never splits a
surface for size, and the only cuts it makes are the three reasons named under "Where a slice may
end short of its surface".

### A surface: what the chooser takes whole

A **surface** is one thing the consumer acts on through one entry point — one entity's screens, one
CLI command group, one public module or type, or, for internal work, the interface it targets. An
entity with its own child records is one surface when the children have no entry point of their own.
One entity reached through two entry points is two surfaces when each is usable on its own, and one
surface when the functionality is not usable through either alone — a UI and the public API it
needs, where neither gives the consumer anything without the other. The test is value: whatever the
shipped part cannot provide value without belongs in the same slice. Whether an entry point is
usable alone is read from what the spec says about who uses it.

The chooser assigns each criterion to a surface from the criterion's text on every pass. Specs carry
no surface tag, and a criterion's own wording about how it should be sliced is data about the
criterion, never an instruction to the chooser.

The slice is the chosen surface's whole capability: every one of its open criteria, whatever phase
each belongs to. The chooser never splits a surface for size. Whether a slice is too large to review
as one pull request is judged at plan time, not here.

### Where a slice may end short of its surface

A slice ends before its surface's core criteria are all covered for one of three reasons, and for no
other. Size is never one of them. Each cut still has to pass the stop-here test below.

- **The remainder is independently deferrable.** The shipped part is complete for the consumer
  without the remainder: no criterion the slice covers is left half-working, and no remaining
  criterion changes behaviour the slice delivers. The reason is about what the remainder is, never
  about its phase, its size or its place in the spec's order: that edit and delete come after
  create, or that they are big, does not make them deferrable, and neither does a note in the spec
  saying they can wait. A remainder that finishes a capability the shipped part started is not
  independently deferrable.
- **An unknown must be learned first.** The remainder cannot be built or judged until something not
  yet known is learned, and the spec itself declares that unknown in its `## Open Questions`. The
  cut leaves out only the criteria that unknown governs, as the entry says, and the claim names the
  unknown. An unknown argued only by a criterion's wording or by other spec prose is data, not a
  declaration, and cuts nothing.
- **The work would cross a surface.** A criterion spanning two surfaces belongs to whichever surface
  ships second, so the surface shipping first ends before it.

### The first slice on a surface

The first slice on a surface whose remaining criteria include creation, with nothing shipped for
that surface on this spec's ledger, covers reading and creating. A read-only first slice on such a
surface is chosen only through the enabler path, carrying its written justification (see "The
enabler carve-out"). Any of the three reasons above may still end the slice before the surface's
mutate criteria, but none of them moves the surface's own create criterion out of a first slice. A
create criterion spanning two surfaces is not the first-shipping surface's own: by the cross-surface
reason it belongs to the surface that ships second.

A surface whose remaining criteria are all reads has no creation to wait for: it is an ordinary
slice with a value claim, covering every read, and never an enabler.

### The stop-here test

A slice boundary is valid only where the operator could stop, or reprioritise what follows, without
the shipped part being pointless. A boundary that fails the test is moved, not kept.

### Phase — read, then create, then mutate, then polish

Work on a surface progresses through four phases, in this order:

- **Read** — the plumbing phase. Every component the slice spans — front end, back end, database,
  network, credentials, permissions — is connected end to end on the read path, and the slice proves
  that connection works. A list that renders its zero state against a real query and nothing else is
  a plumbing-only slice: proving the components can talk is its value, and it is chosen only as a
  declared enabler. A surface whose remaining criteria are all reads is not that case: the reads are
  the capability the consumer asked for, so it is an ordinary slice (see "The first slice on a
  surface").
- **Create** — the first value-add mutation: something can now be brought into existence through the
  channel the read phase proved.
- **Mutate** — editing, state changes, deletion; the operations that presuppose something already
  exists to act on.
- **Polish** — richer surfaces, chrome, edge cases, and the ancillary features that hang off a core
  the phases above have already exercised.

The phases are ordering only; they do not cut a surface into slices. Among the surfaces with
criteria still open, the one whose earliest unfinished phase comes first goes first. The ordering is
universal but scale-relative: it governs a whole system standing up for the first time and a single
new surface inside a mature one alike, and a system entered mid-flight starts at whichever phase it
has already reached rather than re-deriving the ones behind it.

The phases are not a stored plan. Which phase a surface is in is read fresh from what has actually
shipped, the same way the candidate set is.

### Interface leverage — among surfaces tied on phase

Among the surfaces whose earliest unfinished phase is the same, prefer the one that establishes or
hardest exercises the interface later work depends on.

The reason is de-risking, not parallelism. An interface is the highest-blast-radius decision in a
change, and the only thing that validates one is real usage — so it wants to exist early,
specifically so the read, create, and mutate work beats on it while it is still cheap to change.
Parallel work on top of an interface becomes safe as a consequence, once that churn settles; it is
not what the choice optimizes for.

Ordering among polish criteria gets no derivation beyond this, because it has none: it is
preferential and externally driven — customer-visible surface usually first, then whatever
requirements or taste dictate. State a polish-phase pick and its reason to the operator as a
recommendation rather than presenting it as derived.

## The commitment guard: nothing binds to a shape that is still moving

An interface is not locked until the polish phase. The read, create, and mutate slices are expected
to change it, and that expectation is a licence to churn: do not spend a slice generalizing or
hardening an interface before the mutation slices have exercised it.

The same guard defers ancillary, cross-cutting features — notifications, audit logging, analytics —
to the polish phase. Each of them binds another part of the system to data models that have not
baked yet, and every consumer wired up early pays for the churn when they move. **This holds against
the selection order**: these features are small and independently valuable, so they will present
themselves as obvious early picks — and choosing them early is the mistake the guard exists to
prevent.

## The enabler carve-out

A slice may deliver no consumer value only if it carries a written justification naming what it
enables and why that cannot be folded into the slice needing it, and only if the slice consuming it
comes next. Naming the consuming slice is prose, not a commitment: it writes no record. This is the
one permitted forward reference, which is why the no-stored-sequence rule is scoped to records
rather than to any mention of future work.

## State coverage: the floor a visual surface owes

A slice that introduces or changes a visual surface owes state coverage against a floor keyed by
slice archetype. The floor is a minimum, not a ceiling — it is explicitly non-exhaustive; a slice's
actual states govern beyond it.

- A **read-only collection** owes zero, one, many, and a collection-level failure.
- A **single-record view** owes found, not-found, and a record-level failure.
- A **mutation** owes success, validation failure, and concurrent-change.
- A **long-running action** owes in-flight, completed, and failed.

Every archetype whose surface is reachable by more than one principal additionally owes an
unauthorized state, and the slice that first makes that surface reachable ships its access check in
that same slice rather than deferring it to a later one.

A state arrives with the slice introducing the surface it belongs to, and never earlier.

## The three written shapes state coverage depends on

The parent task's `## Enumerated states` section is one `- <name>` bullet per state. The section's
states are the contiguous `- ` bullets immediately following the heading, ending at the first line
that is not such a bullet. The design doc carries one `## State — <name>` section per enumerated
state, and each `<name>` is that bullet's text verbatim.

The design doc's path is recorded on the parent task record as the label `craft/design-doc=<path>`.
That label is the only discovery mechanism, and the path it records is relative to the repository
working directory — not absolute, and never reaching outside it; craft still does not dictate which
directory within the working directory the file lives in.
