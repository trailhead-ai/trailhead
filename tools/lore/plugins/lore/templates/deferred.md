---
type: deferred
project: {{project}}
status: {{status}}
surfaces: {{surfaces}}
raised: {{date}}
next-check: {{next-check}}
revisit-after: {{revisit-after}}
---

## What
<!-- The thing being set aside. One or two sentences. -->

## Why deferred
<!-- Why now is not the right time. -->

## When to revisit
<!-- Specific trigger condition, e.g. "next time we touch the auth pipeline" or "when library X hits v2.0".
The SessionStart hook uses `surfaces` + `next-check` to decide whether to resurface.

Optional: set `revisit-after: YYYY-MM-DD` in frontmatter for a time-based trigger.
Trigger condition and date can coexist — whichever fires first. -->

## Context
<!-- Optional: links to related specs, decisions, code locations. -->
