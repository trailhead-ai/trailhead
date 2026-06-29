# Glossary

Project-specific vocabulary. Grows as new terms are encountered. Each entry
is one sentence; link to an area profile or decision note for depth.

## Terms

<!-- Seed entries; expand as needed. Example shape:
- **term** — one-sentence definition. See [[areas/...]] for depth.
-->

## Status vocabulary

Each kind has a canonical `status:` set enforced by the status guard
(`scripts/status_validator.py` and the optional pre-commit hook). The first
value is the default on create. Do not invent statuses outside these sets —
drift makes recall unreliable.

### Work-tracking kinds

- **plan** — `draft` → `ready` → `in-progress` → `complete`. Off-path terminal: `superseded`, `dropped`.
- **spec** — `draft` → `ready` → `planned` → `complete`. Off-path terminal: `superseded`, `dropped`.
- **session** — `dirty` (unflushed candidates) → `clean` (flushed/wrapped).

### Observation kinds

- **backlog** — `open` (actionable, set aside) → `tracking` (watching an external trigger) / `dropped` (abandoned approach or no longer relevant). This one kind covers what used to be split across deferred work, follow-ups, and dead-ends.
- **decision** — `active` → `superseded` / `dropped`.
- **lesson** — `active` → `conditional` (applies only under stated conditions).
- **area**, **blob**, **collaboration** — `active` (single-state).
