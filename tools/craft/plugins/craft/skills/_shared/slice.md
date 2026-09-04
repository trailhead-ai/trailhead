# Slice and task — the canonical vocabulary

This is the single place craft defines its two units of work and the bar a slice must
clear. Ritual text elsewhere points here rather than restating the definitions.

<!-- toc:start -->
**Contents**

- The two units
- The quality bar: Valuable, Small, Testable
- The value floor is read against the spec's own consumer
- Selection: phase, then interface leverage, then smallest-next
  - Level 1: phase — read, then create, then mutate, then polish
  - Level 2: interface leverage — within the phase
  - Level 3: smallest-next above the value floor — the tiebreak
- The commitment guard: nothing binds to a shape that is still moving
- The enabler carve-out
- State coverage: the floor a visual surface owes
- The three written shapes state coverage depends on
<!-- toc:end -->

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

## Selection: phase, then interface leverage, then smallest-next

Selection is a **three-level judgment, applied in order**. Each level narrows what the
level below it chooses from, and
smallest-next decides only what the two above it leave tied.
Walking a spec's acceptance criteria in the order they happen to be written is
not selection — it is the failure mode these levels exist to prevent.

### Level 1: phase — read, then create, then mutate, then polish

Work on a surface progresses through four phases, in this order:

- **Read** — the plumbing phase. Every component the slice spans — front end, back
  end, database, network, credentials, permissions — is
  connected end to end on the read path, and the slice proves that connection works.
  A list that
  renders its zero state against a real query is the whole deliverable; proving the
  components can talk is the value, and there is no feature yet.
- **Create** — the first value-add mutation: something can now be brought into
  existence through the channel the read phase proved.
- **Mutate** — editing, state changes, deletion; the operations that presuppose
  something already exists to act on.
- **Polish** — richer surfaces, chrome, edge cases, and the ancillary features that
  hang off a core the phases above have already exercised.

Take candidates from the earliest incomplete phase. The ordering is universal but
scale-relative: it governs a whole system standing up for the first time and a single
new surface inside a mature one alike, and a system entered mid-flight
starts at whichever phase it has already reached rather than re-deriving the ones
behind it.

The phases are not a stored plan. Which phase a surface is in is read fresh from what
has actually shipped, the same way the candidate set is.

### Level 2: interface leverage — within the phase

Among the candidates in the earliest incomplete phase, prefer the one that establishes or hardest
exercises the interface later work depends on.

The reason is de-risking, not parallelism. An interface is the highest-blast-radius
decision in a change, and the only thing that validates one is real usage — so it wants
to exist early, specifically so the read, create, and mutate slices beat on it while it is still cheap to change. Parallel work on top of an interface becomes safe as a
consequence, once that churn settles; it is not what the choice optimizes for.

### Level 3: smallest-next above the value floor — the tiebreak

Among what the two levels above leave, slices are chosen smallest-next above the value
floor. The question is "what is the next smallest thing shippable that still delivers
some value," answered against current information. This is a per-cycle local choice; a
pre-committed global value ranking is not the rule.

Ordering within the polish phase gets no derivation beyond this, because it has none:
it is preferential and externally driven — customer-visible surface usually first, then
whatever requirements or taste dictate. State a polish-phase pick and its reason to the
operator as a recommendation rather than
presenting it as derived.

## The commitment guard: nothing binds to a shape that is still moving

An interface is not locked until the polish phase. The read, create, and mutate slices
are expected to change it, and that expectation is a licence to churn: do not spend a slice generalizing or hardening an interface before the mutation slices have exercised
it.

The same guard defers ancillary, cross-cutting features — notifications, audit
logging, analytics — to the polish phase. Each of them binds another part of the
system to data models that have not baked yet, and every consumer wired up early pays
for the churn when they move. **This is an explicit exception to smallest-next**: these
features are small and independently valuable, they score well on the tiebreak, and so
they will present themselves as obvious early picks — and choosing them
early is the mistake the guard exists to prevent.

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

## The three written shapes state coverage depends on

The parent task's `## Enumerated states` section is one `- <name>` bullet per state.
The section's states are the contiguous `- ` bullets immediately following the
heading, ending at the first line that is not such a bullet. The design doc carries
one `## State — <name>` section per enumerated state, and each `<name>` is that
bullet's text verbatim.

The design doc's path is recorded on the parent task record as the label
`craft/design-doc=<path>`. That label is the only discovery mechanism, and the path
it records is relative to the repository working directory — not absolute, and
never reaching outside it; craft still does not dictate which directory within the
working directory the file lives in.
