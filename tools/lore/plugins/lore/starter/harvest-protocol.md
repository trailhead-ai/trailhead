# Harvest-candidates protocol

When a subagent surfaces something worth keeping in the vault — a lesson,
dead-end, deferred item, radar entry, decision, or gotcha — it appends a
`## Harvest candidates` block to its final message. A PostToolUse hook routes
the entries to `harvest-pending.md`, where the session wrap-up consumes them.

## Output block format

At the end of your final message, **and only if you have observations worth
preserving**, append:

```markdown
## Harvest candidates

- lesson: <one-line observation>. Why it matters: <constraint or invariant>. Confidence: high|medium.
- dead-end: tried <approach>. Failed because <reason>. Revive if: <condition>.
- deferred: <work item>. Trigger to revisit: <condition>.
- radar: <external thing to watch>. Cadence: weekly|monthly. Why: <reason>.
- decision: chose <X> over <Y> because <reason>. Reversibility: easy|hard.
- gotcha: <area>: <surprising behavior>. Where it bit: <file:line or scenario>.
```

## Rules

1. **Omit the section entirely if you have nothing.** Empty headers are noise.
2. **One entry per line.** If it doesn't fit on a line, it's not yet distilled enough.
3. **Self-filter.** Only emit candidates durable, non-obvious, and actionable. Mid-investigation noise stays out.
4. **Confidence: medium is fine.** Curation happens at session wrap-up, not in the subagent.
5. **The block must be the LAST thing in your final message** — the hook locates it by suffix.

## Type guide

- **lesson** — a mistake (technical, process, or judgment) plus a concrete prevention check a future plan phase could run. "We did X, it broke Y, the check that would have caught it is Z."
- **dead-end** — an approach that didn't work, with the revive condition. No prevention check.
- **deferred** — work intentionally set aside, with a clear trigger to revisit.
- **radar** — external state to watch periodically (upstream issue, dependency release, vendor status).
- **decision** — a non-obvious architectural or design choice.
- **gotcha** — surprising area behavior worth patching into an area profile.

## Pipeline

```
subagent emits block
  → PostToolUse hook
  → appends entries to harvest-pending.md with a metadata header
  → session wrap-up reads the pending file, presents for promotion
  → consumed entries removed from the pending file
```
