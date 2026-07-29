# {{name}}

<!-- ADRs are immutable once `active` (convention-enforced). A forward ADR is drafted and
flipped active by the gauntlet's adr mode; a backward (distilled) ADR is created already
`active` by the distill ritual's write-order disposition — either way, once `active` an edit
means a new, superseding ADR, not a change to this one.
Keep the whole record to roughly one screenful: a decision that doesn't fit a screen is
usually two decisions.

Provenance never goes in prose:
- Source/derived specs, absorbed decisions, and a superseded predecessor are recorded via
  `related:` metadata (e.g. `--related spec=<name>`, `--related decision=<name>`,
  `--related adr=<predecessor-adr>`), never described here.
- Gauntlet review provenance (which passes ran, disposition) goes to annotations
  (`--annotation gauntlet=<...>`), never the body. -->

## Context
<!-- 2-4 sentences: the forces at play that made a decision necessary. -->

## Decision
<!-- Declarative, present tense, quotable: state what IS, not what we're going to do. -->

## Consequences
<!-- What gets easier, what gets harder. Name the constraints this decision now makes
binding. -->

## Alternatives rejected
<!-- One line each: the alternative considered, and the reason it lost. -->
