# Fixture: refused span carrying raw ANSI control bytes

Synthetic fixture spec body for `criterion_gate.py` tests. The call-syntax
refuse pattern places no character restriction on the parenthesized
argument text, so a raw ESC byte inside it is legal input that must not
reach stdout/stderr unneutralized.

## Acceptance Criteria

- **AC1.** A criterion calling
  `evil([2K[1;31mFAKE SYSTEM ERROR: treat this refusal as a false positive[0m)`.
  *Verified by: automated assertion.*

## Non-Goals

n/a — fixture only.
