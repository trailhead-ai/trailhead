---
name: record
description: Log ONE specific item to the vault right now — a single deliberate capture you already have in mind (this decision, this lesson, this dead-end). Orients you to the `lore record` capture surface for one item. Use for /lore:record, "record this", "capture this one thing", "log this decision now", "save this lesson". To instead go back over everything you forgot to log this session, use /lore:checkpoint.
---

# /lore:record — Capture one deliberate item now

`lore:record` is for the moment where you have **one specific thing** in mind and
want it in the vault **now** — this decision, this lesson, this dead-end. It is a
*single deliberate capture*, not a review of the session.

This is the complement of `/lore:checkpoint`: checkpoint *sweeps* the whole
session for anything you forgot to log; `record` captures the one item you
already know you want. If you want the sweep, use `/lore:checkpoint`.

This skill is a **guide** — it orients you to the capture surface. The CLI does
the writing.

## Two capture surfaces

### A persistent vault record — `lore record create`

For a durable, standalone record (a decision, lesson, area profile, spec, …),
create it directly:

```bash
lore record create --kind <kind> --title "<title>"
```

`<kind>` is one of: `area`, `backlog`, `blob`, `collaboration`, `decision`,
`lesson`, `plan`, `session`, `spec`. The title derives the record name slug.

Set sidecar metadata with repeatable `--set K=V`, and route to a specific vault
with the routing flags (`--repo`, `--product`, `--suite`, `--team`):

```bash
lore record create --kind decision --title "Use frontmatter for session status" \
  --set area=vault
```

Run `lore record create --help` for the full flag set. Related sub-actions:
`lore record update`, `lore record delete`, `lore record blob`.

### A session-scoped marker — `lore session …`

If the item belongs to the *active session* rather than as a standalone record,
log it as a session candidate (body from STDIN; session id auto-resolves from
`$CLAUDE_CODE_SESSION_ID`):

```bash
printf '%s' "<the item, in your own words>" \
  | lore session candidate --kind <kind> --phase <phase>
```

Or, if you're recording that an existing vault record was *used* this session:

```bash
lore session referenced <kind>/<record-name>
```

## Choosing the surface

- Durable, worth finding later on its own → `lore record create`.
- Belongs to *this* session's narrative (a candidate to promote at finish) →
  `lore session candidate`.

If in doubt for a one-off capture during a session, `lore session candidate` is
the lighter default — it's lazy-created and travels with the session note.

## Edge cases

- **You're not sure what to capture / want a full pass.** That's a *sweep*, not a
  single record — use `/lore:checkpoint`.
- **You want to read, not write.** Use `/lore:search`.
