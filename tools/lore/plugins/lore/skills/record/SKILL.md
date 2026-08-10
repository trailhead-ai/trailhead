---
name: record
description: Capture ONE specific item now — a single deliberate capture you already have in mind. Orients you to the two capture surfaces and helps you pick: a `lore session candidate` (the default, for findings that arise during work — promoted to a durable record at flush) versus a direct `lore record` write (the exception, for deliberately authored artifacts or an explicit "pin this one now"). Use for /lore:record, "record this", "capture this one thing", "log this decision now", "save this lesson". For a batch promotion of the session's pending items, use /lore:flush.
---

# /lore:record — Capture one deliberate item now

`lore:record` is for the moment where you have **one specific thing** in mind and
want it captured **now** — this finding, this decision, this authored artifact. It
is a *single deliberate capture*, not a review of the session.

Most findings land as a **session candidate** (the default — see below) and are
promoted at flush; a direct vault record is the exception, reserved for authored
artifacts or an explicit "pin this one now". This skill orients you to both
surfaces and helps you pick.

This is the complement of `/lore:flush`: flush *evaluates all outstanding session
candidates* and promotes them to records in batch; `record` captures the one
item you already know you want right now. If you want the batch evaluation,
use `/lore:flush`.

This skill is a **guide** — it orients you to the capture surface. The CLI does
the writing.

## Two capture surfaces

### A session candidate — `lore session …` (the default)

For an incidental **finding** that arose during work — a decision made in
passing, a dead-end, a deferred item, a gotcha — log it as a session candidate.
It rides the session note and is promoted to a durable record at flush. Body
from STDIN; session id auto-resolves from `$CLAUDE_CODE_SESSION_ID`:

```bash
printf '%s' "<the finding, in your own words>" \
  | lore session candidate --kind <kind> --phase <phase>
```

Or, to note that an existing vault record was *used* this session (a usage
marker, not a candidate — nothing here is promoted at flush):

```bash
lore session referenced <kind>/<record-name>
```

Candidates are cheap and lazy-created — capture liberally. `/lore:flush` applies
judgment later, promoting the keepers into durable records.

### A persistent vault record — `lore record create` (the exception)

Reserved for a deliberately **authored artifact** (a `task`, `spec`, or `area`
profile), or a finding you *explicitly* mean to pin as a standalone record right
now. Create it directly:

```bash
lore record create --kind <kind> --title "<title>"
```

`<kind>` is one of: `adr`, `area`, `blob`, `collaboration`, `decision`,
`lesson`, `session`, `spec`, `task`. The title derives the record name slug.

Set sidecar metadata with the dedicated per-field flags — `--status` (scalar),
the repeatable list flags `--keyword` / `--related-file` / `--related-url` /
`--related-phase` (each with a matching `--unset-<field> VALUE` to remove one
entry), and `--related KIND=NAME` — and route to a specific vault with the
routing flags (`--repo`, `--product`, `--suite`, `--team`):

```bash
lore record create --kind decision --title "Use frontmatter for session status" \
  --status active --keyword frontmatter --related task=session-status-rollout
```

Run `lore record create --help` for the full flag set. Related sub-actions:
`lore record show`, `lore record update`, `lore record delete`.

## Choosing a flag for a value: edge, label, or reserved

A sidecar value falls into one of three shapes — pick the flag by shape, not by
habit:

- **The value names another record** — a task, a decision, an area — it's an
  **edge**, not an attribute. Use `--related KIND=NAME`.
- **The value is a free attribute** with no natural-key collision (e.g.
  `worktree=s5`, `claude-code/model=opus`) — it's a **label**. Use
  `--label KEY=VALUE`.
- **The natural key is itself a record kind or a query field name** (e.g.
  `area`, `phase`, `status`, `kind`) — the write is **refused**. A `labels` key
  may not shadow a first-class record concept. The refusal names a runnable
  fix: `--annotation KEY=VALUE` for a free attribute whose natural name is
  taken, or a namespaced key (`<ns>/<key>`, e.g. `craft/subsystems`) to keep it
  queryable as a label. Already storing the reserved key on an existing
  record? `--unset-label <key>` clears it.

## Choosing the surface

- An incidental finding that arose during work (a decision, lesson, dead-end,
  deferred item, gotcha) → `lore session candidate`. **This is the default** —
  durable records are mostly born at flush, not mid-work.
- A deliberately authored artifact (`task`, `spec`, `area` profile), or a finding
  you *explicitly* mean to pin as a standalone record right now →
  `lore record create`.

When unsure, prefer `lore session candidate` — capture is cheap, and `/lore:flush`
applies the judgment later.

## Edge cases

- **You want to evaluate all outstanding session candidates.** That's a batch
  evaluation, not a single capture — use `/lore:flush`.
- **You want to read, not write.** Use `/lore:search`.
