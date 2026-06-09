# Glossary

Project-specific vocabulary. Grows as new terms are encountered. Each entry
is one sentence; link to an area profile or decision note for depth.

## Terms

<!-- Seed entries; expand as needed. Example shape:
- **term** — one-sentence definition. See [[areas/...]] for depth.
-->

## Status vocabulary

Each note type has a canonical `status:` set enforced by the status guard
(`scripts/status_validator.py` and the optional pre-commit hook). Do not
invent statuses outside these sets — drift makes recall unreliable.

### Work-tracking note types

- **plans/** — `draft` → `ready` → `in-progress` → `complete`. Side-state: `shelved`. Off-path terminal: `superseded`, `dropped`.
- **specs/** — `draft` → `ready` → `planned` → `complete`. Side-state: `shelved`. Off-path terminal: `superseded`, `dropped`.
- **sessions/** — `active` (in flight) → `complete` (wrapped). Side-state: `shelved` (handed off, awaiting pickup).

### Observation note types

- **deferred/** — `open` → `resolved` / `dropped` / `graduated`. Variant: `scheduled` (date-bound `open` — resurface on/after a set date). Edge: `resurfaced` (trigger condition met, action pending).
- **radar/** — `active` → `resolved` / `dropped`.
- **lessons/** — `active` → `superseded` (when guarded structurally).
- **dead-ends/** — `active` → `archived` (when the revive condition is obsolete).

### `shelved` semantics

`shelved` is a cross-note-type side-state meaning *"this work is paused;
resume by going back to its prior in-flight state."* Set when work is handed
off (on the active session note plus any linked in-progress plan) and cleared
on pickup (flips back to the prior in-flight status). Distinct from `dropped`
(not coming back) and individual `deferred/*` notes (a specific question to
revisit, not a whole work-stream).
