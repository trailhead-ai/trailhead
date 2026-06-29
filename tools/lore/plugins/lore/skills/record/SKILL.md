---
name: record
description: Log ONE specific item to the vault right now — a single deliberate capture you already have in mind (this decision, this lesson, this backlog item). Orients you to the `lore record` capture surface for one item. Use for /lore:record, "record this", "capture this one thing", "log this decision now", "save this lesson". For a batch promotion of the session's pending items, use /lore:flush.
---

# /lore:record — Capture one deliberate item now

`lore:record` is for the moment where you have **one specific thing** in mind and
want it in the vault **now** — this decision, this lesson, this backlog item. It is a
*single deliberate capture*, not a review of the session.

This is the complement of `/lore:flush`: flush *evaluates all outstanding session
candidates* and promotes them to records in batch; `record` captures the one
item you already know you want right now. If you want the batch evaluation,
use `/lore:flush`.

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

Set sidecar metadata with the dedicated per-field flags — `--status` (scalar),
the repeatable list flags `--keyword` / `--related-file` / `--related-url` /
`--related-phase` (each with a matching `--unset-<field> VALUE` to remove one
entry), and `--related KIND=NAME` — and route to a specific vault with the
routing flags (`--repo`, `--product`, `--suite`, `--team`):

```bash
lore record create --kind decision --title "Use frontmatter for session status" \
  --status active --keyword frontmatter --related plan=session-status-rollout
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

- **You want to evaluate all outstanding session candidates.** That's a batch
  evaluation, not a single capture — use `/lore:flush`.
- **You want to read, not write.** Use `/lore:search`.
