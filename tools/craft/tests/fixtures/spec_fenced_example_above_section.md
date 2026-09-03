# Fixture: fenced worked example above the real Acceptance Criteria section

Synthetic fixture spec body for `covers_gate.py` tests — not a real vault record.
The fenced example below sits flush left (not indented), above the real
section, and declares a fake `AC1` — a fence-blind parser would treat the
fenced heading as the real anchor and certify the fabricated identifier.

## Problem
Example of the shape:
```markdown
## Acceptance Criteria
- **AC1.** A fake criterion inside a fence.
```

## Acceptance Criteria

- **AC2.** The real criterion.
