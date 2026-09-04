# {{name}}

## Problem
<!-- What situation or gap is this spec addressing? Why does it matter now? -->

## Objectives
<!-- Measurable outcomes this work achieves. Each bullet is a concrete goal. -->

## Acceptance Criteria
<!-- The bar that must be met for this to be considered done. Specific and
testable: name the observation that distinguishes pass from fail.

One top-level `- ` bullet per criterion, each prefixed `**ACn.**`. A `###`
sub-heading may group criteria without itself being one. A nested sub-bullet
qualifies its parent rather than forming a criterion of its own.

Atomicity bar: a criterion carries exactly one independently deliverable
assertion — one half could ship, alone, and be useful. The test is
deliverability, never surface conjunction: an "and" does not itself make a
criterion compound, and a criterion with no "and" can still be compound.

  - Compound (split it): "a reviewer can approve a submission, and the
    submitter is notified." The halves land in different phases and either
    ships useful without the other — two ACs.
  - Not compound (leave it): "a manager can change a shift's start and end
    times, with validation against the store's opening hours." The
    validation is not separately shippable from the edit it guards — one AC.

No implementation identifier bar: a criterion names the artifact it is about by its role
or purpose, never by its literal identifier — no function name, file path, endpoint, or
symbol. One move covers every shape: say what the artifact does or means, not where it
lives.

  - Refused (names the endpoint): "- **AC1.** The `POST /users` endpoint returns `201`.
    *Verified by: automated assertion.*"
  - Conformant (names the role): "- **AC1.** A client creating a user gets a response
    confirming the user was created. *Verified by: automated assertion.*"

Verification method bar: every criterion names exactly one of three sanctioned
verification methods, appended as a trailer — `*Verified by: <method>.*` — where
`<method>` is `automated assertion`, `design doc review`, or `manual check`. The trailer
is required on every criterion, including one an automated assertion verifies — there is
no exemption for that case. Free text ("verified by: manual testing") is not sanctioned.

  - Refused (omits the trailer): "- **AC1.** A client creating a user gets a response
    confirming the user was created."
  - Conformant (states the method): "- **AC1.** A client creating a user gets a response
    confirming the user was created. *Verified by: automated assertion.*"

Named limits: the identifier bar above only inspects inline code spans — a path or symbol
written in bare prose, without backticks, is a false negative it accepts. A spec predating
the `**ACn.**` identifier convention is exempt from both bars until it adopts identifiers.
And naming a status or error value by its role trades away the criterion's ability to pin
that value's exact literal wire spelling — when the literal spelling itself is the
contract, a human still maps role to literal when writing the assertion. -->

## Required Interfaces
<!-- Name each boundary this spec implies and the acceptance criteria that boundary
must satisfy. This section does not define an interface's shape — a boundary is
named here and defined at slice time, once a slice actually needs to build it. -->

## Non-Goals
<!-- Explicitly out of scope. Prevents scope creep and clarifies boundaries. -->

## Constraints
<!-- Hard limits: time, cost, compatibility, team capacity, external dependencies. -->

## UI Direction
<!-- If this touches a user-facing surface: wireframe sketch, interaction notes.
n/a — describe here if there is no UI surface involved. -->

## Open Questions / Risks
<!-- Unresolved decisions, unknowns, or risks that could affect the design.
Each item is either a decision already made or a deliberate deferral naming both
an owner and a revisit condition — no other shape is sanctioned.

Use one of these forms for a deliberate deferral rather than inventing your own:
  - `Accepted risk: <risk>. Owner: <who>. Mitigation: <mitigation>. Revisit if <condition>.`
  - `Deferred with revisit conditions: <what is deferred>. Owner: <who>. Reopen if <condition>.`
An item using one of these two forms carries an owner and a revisit condition, and is a
deliberate deferral rather than a smuggled requirement.

`Settled: <decision already made, and why>.` is not a deferral — it records a decision
that has already been made, so it names no owner and carries no revisit condition. -->

## Related
<!-- Links to related specs, decisions, or plans. Use [[wikilink]] or bare path. -->
