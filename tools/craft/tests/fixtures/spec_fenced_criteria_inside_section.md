# Fixture: fenced example inside the real Acceptance Criteria section

Synthetic fixture spec body for `covers_gate.py` tests — not a real vault record.
The real section below contains a nested fenced example whose `- **ACn.**`
line must not be counted as a real criterion — a fence-blind scanner would
terminate only on `## `, letting the fenced example's bullet ride through as
a real criterion.

## Problem

A fixture problem statement.

## Acceptance Criteria

- **AC1.** A real fixture criterion.

Example of the shape drafters should follow:
```markdown
- **AC99.** A fake criterion inside a fence within the real section.
```

- **AC2.** Another real fixture criterion.

## Non-Goals

n/a — fixture only.
