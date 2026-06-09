---
name: pickup
description: Resume a shelved work chain — surface the recorded git state and pickup hints from a prior /forge:handoff. Use for /forge:pickup, /forge:pickup <slug>, "pick up where I left off", "resume the work on X".
---

# Pickup

Resume work shelved by `/forge:handoff` — locate the shelved session note (or local forge handoff file), surface its `## Pickup hints` and recorded git state, and flip the note back to active. A `<slug>` argument targets a specific shelved chain; with no argument, pickup surfaces the most recent one.

## Announce FIRST (no-rebase — required)

Before doing anything else, surface this to the user verbatim, as the very first output line:

> **Code is NOT restored/rebased — pickup surfaces your hints + RECORDED (possibly stale) git state only; restore your working tree yourself.**

The recorded branch / ahead-count / commit list is a snapshot taken at handoff time, not live git state — treat it as a memory aid, not the truth of your working tree.

## Step 1 — Which backend (declare it)

Detect the lore backend with the same 3-state probe handoff uses:

```bash
if command -v lore >/dev/null 2>&1 && [ -n "$LORE_VAULT" ] && lore stats >/dev/null 2>&1; then
  LORE_BACKEND=working
else
  LORE_BACKEND=degraded
fi
```

- `command -v lore` succeeds **AND** `$LORE_VAULT` is non-empty **AND** `lore stats` exits 0 → **working**. Announce: **"Searching lore vault at `$LORE_VAULT`"**.
- Otherwise (lore absent, `$LORE_VAULT` unset, or `lore stats` non-zero) → **degraded**. Announce: **"lore unavailable — reading local forge handoff at `~/.forge/handoffs/`"**.

Never fall through to the CLI default vault: an unset `$LORE_VAULT` is treated as degraded, not as `~/lore` (the wrong shadow vault).

## Step 2 — Find the shelved work

### Working backend (lore)

List shelved/handoff notes most-recent-first — each row carries the note path, its timestamp, and a context fragment (first Pickup-hints line) so the list is scannable:

```bash
lore shelved                   # all shelved/handoff notes
lore shelved --slug "<slug>"   # narrow to one worktree slug
```

- If a `<slug>` was given, pass `--slug "<slug>"` and resume that note directly.
- Otherwise present the list and let the user choose. With a single entry, resume it.

### Degraded backend (no lore)

Read from the SAME location handoff wrote to — `~/.forge/handoffs/` (out of any repo). Use the helper:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pickup_resume.py"                 # most-recent forge handoff
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pickup_resume.py" --slug "<slug>" # a specific one
```

With no slug it surfaces the **most-recent** forge handoff file and prints its path — never a lore lookup that silently returns nothing.

## Step 3 — Surface the hints + recorded git state

Show the chosen note's (or handoff file's) `## Pickup hints` section — the next-action / blocker and the captured git state (branch, ahead-count, dirty flag, recent commits). On the degraded path the helper already extracts and prints the `## Pickup hints` body.

## Step 4 — Resume

### Working backend (lore)

Flip the note `shelved` → `active` so the session is live again:

```bash
lore resume "<slug-or-note-path>"
```

`lore resume` accepts the worktree slug or an explicit note path; it prints a confirmation, or a clear "not shelved" notice if the note is already active/complete.

### Degraded backend (no lore)

There is no status to flip — the forge handoff file is a plain artifact. The hints are surfaced; the file stays in place for re-reading.

## Empty state

If **nothing is shelved AND no forge handoff file exists**, say so plainly — **"nothing to resume"** — not an empty list or a traceback. On the lore path `lore shelved` already prints a clear nothing-shelved message; on the degraded path the helper prints `nothing to resume — no forge handoff file found`.
