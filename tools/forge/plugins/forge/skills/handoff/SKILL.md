---
name: handoff
description: Record your current git state and shelve a session note so a future session can resume. Use for /forge:handoff, "hand off this work", "save my progress", "I'm done for now".
---

# Handoff

Capture a read-only snapshot of your git state, compose pickup hints, and shelve a session note (via the `lore` CLI when available, otherwise a local forge file). A future `/forge:pickup` resurfaces it.

## Announce FIRST (code safety — required)

Before doing anything else, surface this to the user verbatim:

> **This RECORDS your git state and shelves the session note — it does NOT commit or push your code. Commit/push separately before relying on this.**

The git capture below is strictly read-only. This ritual never commits, pushes, rebases, or otherwise mutates your working repos.

## Step 1 — Which backend (declare it)

Detect the lore backend with the 3-state probe:

```bash
if command -v lore >/dev/null 2>&1 && [ -n "$LORE_VAULT" ] && lore stats >/dev/null 2>&1; then
  LORE_BACKEND=working
else
  LORE_BACKEND=degraded
fi
```

- `command -v lore` succeeds **AND** `$LORE_VAULT` is non-empty **AND** `lore stats` exits 0 → **working**. Announce: **"Using lore vault at `$LORE_VAULT`"**.
- Otherwise (lore absent, `$LORE_VAULT` unset, or `lore stats` non-zero) → **degraded**. Announce: **"lore unavailable — writing local forge handoff at `~/.forge/handoffs/<slug>.md`"**.

Never fall through to the CLI default vault: an unset `$LORE_VAULT` is treated as degraded, not as `~/lore` (that would be the wrong shadow vault).

## Step 2 — Capture git state (read-only, bounded)

Use the helper — it runs the guarded, merge-base-bounded git survey so a non-git cwd degrades gracefully with no stderr leak:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff_capture.py" --capture .
```

(Or invoke the helper functions directly.) Under the hood it runs, all guarded `2>/dev/null`:

- `git branch --show-current`
- `git status --porcelain` (dirty flag)
- `git log <base>..HEAD --oneline` where `<base> = git merge-base HEAD <default-branch>`, falling back to a bounded `HEAD~N` range — **never an unbounded `git log`**.

This is single-repo or generic multi-repo. There is **no sibling-repo enumeration and no worktree path-shape assumption** — it works on whatever repos the user names.

## Step 3 — Compose `## Pickup hints`

Build a `## Pickup hints` section from the captured git state plus the user's stated next-action and current blocker.

**Empty-hints guard:** if the user gave no next-action and no blocker, warn before finalizing — a handoff with no forward signal is hard to resume. Ask for at least one.

## Step 4 — Shelve

### Working backend (lore)

Write the composed `## Pickup hints` body to a temp file, then hand off atomically in one call:

```bash
HINTS_TMP=$(mktemp /tmp/forge-handoff-hints.XXXXXX)
printf '%s\n' "$HINTS" > "$HINTS_TMP"
lore handoff --pickup-hints-file "$HINTS_TMP"
rm -f "$HINTS_TMP"
```

`lore handoff --pickup-hints-file` writes the hints under `## Pickup hints` AND shelves the note in one atomic operation — hints and shelving happen in a single call.

After `lore handoff` returns:

- **Exit 0:** echo the resolved vault path it wrote to — **"Shelved to `$LORE_VAULT`"** — so a wrong/shadow `$LORE_VAULT` can't silently misdirect the note.
- **Non-zero exit (mid-failure):** the git state was captured but the note was NOT shelved. Emit **"handoff FAILED — your session note is NOT shelved"** and return non-zero. Do **not** report success.

### Degraded backend (no lore)

Write the fallback file — **out of any repo** so captured git state never lands in a possibly-public user repo:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff_capture.py" --degraded --slug "<slug>" --hints "$HINTS"
```

This writes `~/.forge/handoffs/<slug>.md` (creating `~/.forge/handoffs/` if missing) containing the hints + captured git state.

## Step 5 — Confirm + pickup discoverability

Close with the landing location (vault path or `~/.forge/handoffs/<slug>.md`) and the resume hint:

> To resume: `/forge:pickup [slug]`
