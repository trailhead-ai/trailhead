# Phases

A second classification axis, orthogonal to `areas/`. Area says
**what** you're working on; phase says **where in the cycle** you are. Used
for skill triggering, context loading, and session classification.

## Primary phases (ordered)

Six states a session moves through. `phase:` frontmatter records the
most-recent phase — same scalar, latest-wins semantics as `status:`.

### 1. Orient

Entry to a session. "Where am I, what's on my plate?"

**Signals:** fresh session, no explicit task engaged yet. Catching up on
triage, handoffs, or the radar.

**Default at session start.** Every session begins here until an action
transitions it.

### 2. Frame

Defining the work. "What are we doing, why, how?"

**Signals:** brainstorming or planning; discussion with "what if", "should
we", "how might we"; a spec/plan being drafted; architecture discussion
before any code change.

### 3. Build

Doing the work. Four internal sub-phases (below).

**Signals:** code edits, test runs, environment setup for a specific task.

### 4. Review

Exchanging feedback. "Is this work good?"

**Signals:** code review requested, review comments surfaced to address.

### 5. Ship

Landing the work.

**Signals:** PR active, CI in motion, focus on merge readiness.

### 6. Close

Ending the session. Saving state, capturing learnings.

**Signals:** handoff or wrap-up invoked, status flipping to a terminal value.

## Build sub-phases

When `phase: Build`, `sub_phase` can refine the state:

- **Prepare** — env setup, deps, scaffolding.
- **Implement** — writing code, the test-driven loop.
- **Debug** — test failure, stack trace, unexpected behavior.
- **Verify** — pre-handoff checks.

Loops: Implement ↔ Debug is expected. Verify is the gate toward Review/Ship.

## Cross-cutting bands

Not phases — recognizable by the command invoked, fireable from inside any
active phase.

### Capture

Knowledge-graph writes. Fires mid-session from Build, Debug, Review, or Close.
The capture commands: defer, decision, dead-end, radar, area.

### Maintain

Vault upkeep. Usually scheduled or triggered off-cycle: periodic review,
reflection, cleanup.

## Canonical constants

```
AVAILABLE_PHASES = [Orient, Frame, Build, Review, Ship, Close]
BUILD_SUB_PHASES = [Prepare, Implement, Debug, Verify]
```

## What NOT to tag by phase

Tag by phase only when a bit of knowledge is genuinely relevant during one
phase. Most area gotchas, user preferences, and tool quirks are
phase-agnostic — leave them untagged.
