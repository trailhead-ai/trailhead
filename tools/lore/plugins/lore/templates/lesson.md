---
type: lesson
project: {{project}}
date: {{date}}
areas: {{areas}}
severity: {{severity}}
status: active
---

<!--
A lesson is a *mistake* (technical, process, or judgment) plus a concrete
prevention check a future plan phase could run. "We did X, it broke Y, the
check that would have caught it is Z."

Distinct from a dead-end (a specific technical approach that failed). A lesson
is broader and preserves the narrative of how the miss happened.

severity: low | medium | high — impact of repeating the mistake.
status: active | superseded — superseded once the prevention is hardened in
code/CI/process such that the mistake can no longer recur.
-->

## What we did wrong
<!-- The mistake. Concrete, specific. -->

## Why it happened
<!-- Root cause and the miss pattern — what made this easy to overlook. -->

## How to prevent recurrence
<!-- The load-bearing section. A concrete check a future plan phase can run. -->

## Related
<!-- Sessions, PRs, dead-ends, decisions, commits. -->
