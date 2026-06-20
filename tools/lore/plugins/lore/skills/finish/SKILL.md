---
name: finish
description: "Canonical end-of-session finish — optionally sweep for any capture-worthy items not yet logged, then run `lore finish` to finalize (status=complete + ended:) and commit the session note. Finalize + commit only. Use for /finish, \"I'm done\", \"wrap up\", \"close this out\", \"finalize the session\"."
---

# Finish — canonical end-of-session finish

`lore:finish` finalizes the active session note. It does an optional final
capture sweep (the same mechanism `/checkpoint` uses) for anything not yet
logged, then calls `lore finish`, which:

1. Resolves the active session note for this worktree (session-id first, then
   worktree-name fallback).
2. Stamps `status: complete` and `ended:` onto it.
3. Commits the note in one commit (explicit path only — unrelated dirty vault
   files are not swept in).

`lore finish` is **finalize + commit only**. It does no candidate expansion,
creates no kind-notes, and surfaces nothing extra — the capture you did during
the session (and in the sweep below) is already in the note.

## Process

### Step 1 — Read what's been captured

The session note is lazy-created on first capture, so it may not exist yet.
Try to read it:

```bash
cat "$LORE_VAULT/$(lore session-note)"
```

If `lore session-note` exits non-zero, nothing was captured this session — that
is the **empty-session** path; jump to Step 3 (`lore finish` handles it cleanly).

### Step 2 — Final capture sweep (optional)

Review the session for any capture-worthy item not already in the note and log
each as a session candidate — same mechanism as `/checkpoint`. Body from STDIN;
session id auto-resolves from `$CLAUDE_CODE_SESSION_ID`:

```bash
printf '%s' "<the captured item>" \
  | lore session candidate --kind <kind> --phase <phase>
```

`<kind>`: `decision`, `lesson`, `dead-end`, `deferred`, `follow-up`, `gotcha`,
`spec`. For an existing record used this session, log a reference instead:

```bash
lore session referenced <kind>/<record-name>
```

Skip this step if the session note is already complete from prior checkpoints.

### Step 3 — Finalize and commit

```bash
lore finish
```

This stamps `status: complete` + `ended:` on the session note and commits it.
Relay any notices it prints (e.g. push failure, no remote).

### Step 4 — Report to the user

```
Finalized `sessions/<file>` (status: complete) and committed the vault.

What we did: <one-line summary>
Committed and pushed (or: committed locally — no remote).
```

## Edge cases

- **Empty session (nothing captured, no note exists).** This is normal and
  handled — not an error. `lore finish` prints a clear notice,
  `notice: no active session note found for worktree '<name>' — nothing to
  finalize.`, and **exits 0**. Relay that notice to the user so they know the
  session *was* handled — it is not a cryptic error and not a silent no-op.
- **Non-git vault.** `lore finish` stamps the session metadata (into the
  `sessions/<id>.json` sidecar) but skips the commit, printing a notice on
  stderr. Relay it.
- **`lore finish` exits non-zero.** Report the error; do not retry silently.
- **Already finalized.** `lore finish` prints `notice: already complete —
  nothing to finalize.` and exits 0. Relay it.
