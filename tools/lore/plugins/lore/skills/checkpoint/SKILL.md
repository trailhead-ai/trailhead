---
name: checkpoint
description: Mid-session checkpoint — harvest current in-context state into the active session note's standard sections (What we did / Decided / Deferred / Learned / Open questions) and commit. Status stays active. Use for /checkpoint, "checkpoint before clearing", "snapshot the session", "save state before /clear", "preserve context". Offer proactively when a meaningful chunk just completed in a long-running session and the user is about to context-switch or /clear.
---

# Checkpoint — mid-session state harvest

Persists in-context state into the session note *now* so it survives `/clear` or auto-compaction. Does NOT finalize: status stays `active`, no `ended:` timestamp.

## Process

### Step 1 — Locate the session note

```bash
lore stats  # confirms vault + worktree
```

Find the active session note (resolves this session's note by id, falling back
to the worktree name — prints a vault-relative path):
```bash
lore session-note
```

Read it:
```bash
cat "$LORE_VAULT/$(lore session-note)"
```

Check frontmatter `status`. If already `complete` or `shelved`: stop and tell the user.

### Step 2 — Draft new bullets from in-session context

Draft tight, append-ready bullets for each section, covering **only work since the last checkpoint** (anything not already in the note). Synthesize from the conversation — don't re-read the full transcript.

Sections:
- **What we did** — concrete work completed. Files touched, key outcomes.
- **Decided** — non-obvious choices made.
- **Deferred** — threads intentionally set aside.
- **Learned** — domain gotchas surfaced.
- **Open questions** — unresolved threads about to leave context.

Skip any section with nothing new.

### Step 3 — Present the plan

Compact summary before writing:

```
Checkpoint plan for `sessions/<file>`:

Appending to:
  - What we did (N new bullets)
  - Decided (N new bullets)
  - Learned (N new bullets)

OK to proceed?
```

Trivial checkpoints (≤3 total bullets, all in `What we did`) may fast-path without asking.

### Step 4 — Append to the note

For each section with new content:

```bash
lore patch "$LORE_VAULT/$(lore session-note)" "<Section>" --text "<bullets>"
```

One call per section. Do NOT touch frontmatter. Do NOT touch sections reserved for `/finished` (mini retrospective, open questions unless you have new ones).

### Step 5 — Commit

```bash
lore sync --message "checkpoint(<worktree>): mid-session state"
```

### Step 6 — Tell the user

```
Checkpointed N bullets to `sessions/<file>` and committed the vault.

Run `/clear` to wipe context. When you later run `/finished`, it will read this
note and see the checkpointed content alongside whatever happens post-clear.
```

You cannot invoke `/clear` yourself — remind the user to run it.

## Edge cases

- **No session note.** Tell the user; offer to create one manually or wait for the next session start.
- **Session note already `complete`.** No-op with a message.
- **Multiple checkpoints in one session.** Fine — each appends since the last.
