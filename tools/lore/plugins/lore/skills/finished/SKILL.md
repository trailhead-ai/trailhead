---
name: finished
description: "Canonical end-of-session finish — fill in the session note sections (What we did / Decided / Learned / Open questions), run `lore finish` to set status=complete, expand harvest-pending into vault notes, and commit. Use for /finished, \"I'm done\", \"wrap up\", \"close this out\", \"finalize the session\"."
---

# Finished — canonical end-of-session finish

`lore:finished` is the canonical end-of-session finish. It fills the session
note from in-context synthesis, then calls `lore finish`, which:

1. Sets `status: complete` and stamps `ended:`.
2. Reads `harvest-pending.md` and the session note's `## Harvest candidates`
   block; expands each typed one-liner of the five in-scope types
   (`deferred` / `decision` / `dead-end` / `radar` / `lesson`) into a full
   templated note in the matching vault directory.
3. Surfaces `gotcha` entries in the finish report for manual
   `/lore:area` patching — they are NOT auto-expanded and remain in
   `harvest-pending.md`.
4. Retains malformed or unmarked lines in `harvest-pending.md` with a
   warning rather than silently consuming them.
5. Commits the session note + new notes + the rewritten `harvest-pending.md`
   in a single atomic commit (explicit paths only — unrelated vault files are
   not swept in).

The heavy lifting (expansion, dedup, commit) is all in the CLI. The skill's
job is to draft the session note sections, write them, and call `lore finish`.

## Process

### Step 1 — Locate and check the session note

Find the active session note for this session (resolves by id, falling back to
the worktree name — prints a vault-relative path). Resolution is bucket-aware:
session notes live either flat at `sessions/` or date-bucketed in
`sessions/YYYY-MM/`, and `lore session-note` scans both:

```bash
lore session-note
```

This prints the note's full path. Read it:
```bash
cat "$LORE_VAULT/$(lore session-note)"
```

Check frontmatter `status`. If already `complete` or `shelved`: report "Already finalized" and stop.

### Step 2 — Draft sections from in-session context

You lived through this session. Draft from the conversation — not by re-reading the transcript.

If a section already has bullets from a prior `/checkpoint`, **extend** (do not duplicate) them.

Sections:
- **What we did** — concrete work completed. Files touched, key outcomes.
- **Decided** — non-obvious choices made and their reasoning.
- **Deferred** — threads intentionally set aside (reference any `deferred/` notes created).
- **Learned** — domain gotchas, corrections, dead-end links.
- **Open questions** — unresolved threads for the next session.

Keep bullets tight — a future reader should skim in 30 seconds.

### Step 3 — Write the sections

For each section with content:

```bash
lore patch "$LORE_VAULT/$(lore session-note)" "<Section>" --text "<bullets>"
```

One call per section. Sections with nothing new are left untouched.

Alternatively, open the note in an Edit call and fill in the sections directly if that is cleaner for the amount of content.

### Step 4 — Finalize and commit

```bash
lore finish
```

This finalizes the session note, expands the harvest-pending entries into vault
notes, and commits everything in one shot. Relay any notices printed (push
failure, no remote, surfaced gotchas).

**Gotchas in the report:** if `lore finish` prints surfaced `gotcha` entries,
tell the user — they need a manual `/lore:area` patch to record them.

### Step 5 — Report to the user

```
Finalized `sessions/<file>`.

What we did: <one-line summary>
Harvested: <N notes written> (or: nothing new to harvest).
Committed and pushed (or: committed locally — no remote).
```

If gotchas were surfaced, append them so the user can act on each one.

## Edge cases

- **No session note.** Unusual — the SessionStart hook should have created one. Tell the user and offer to create one manually before calling `lore finish`.
- **Session note already `complete`.** `lore finish` still checks for unharvested pending entries; if pending is clear it exits cleanly. Report the notice.
- **Non-git vault.** `lore finish` will write the frontmatter update and expand notes but skip the commit (notice on stderr). Relay the notice.
- **`lore finish` exits non-zero.** Report the error; do not retry silently. Expanded notes are already on disk and pending is unchanged, so a re-run after fixing the issue re-expands idempotently.
- **Malformed/unmarked lines in `harvest-pending.md`.** `lore finish` warns about them and leaves them in place. Relay the warning so the user can clean them up manually.
