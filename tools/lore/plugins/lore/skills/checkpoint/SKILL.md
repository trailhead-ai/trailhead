---
name: checkpoint
description: Mid-session sweep — review the whole in-context session and catch capture-worthy items (decisions, lessons, dead-ends, deferrals, follow-ups, gotchas) that haven't been logged yet, logging each as a session candidate, then commit. Status stays active. This is the "catch what I missed" review of the session, NOT a single deliberate capture. Use for /checkpoint, "checkpoint before clearing", "sweep the session", "what did I forget to capture", "save state before /clear". Offer proactively when a meaningful chunk just completed in a long-running session and the user is about to context-switch or /clear.
---

# Checkpoint — mid-session capture sweep

Review the session *so far* and catch anything capture-worthy that hasn't been
logged yet, before `/clear` or auto-compaction drops it. Each missed item is
logged as a **session candidate**; existing records that were used this session
are logged as **references**. This is a *sweep of the whole session*, not a
single deliberate capture.

Checkpoint does **not** finalize: the session stays `active`. There is no
`ended:` stamp and no commit-and-close — `/finish` does that at end of session.

## Process

### Step 1 — Read what's already been captured

The session note is lazy-created on first capture, so it may not exist yet —
that is normal, not an error. Try to read it so you don't log duplicate
candidates:

```bash
cat "$LORE_VAULT/$(lore session-note)"
```

If `lore session-note` exits non-zero, nothing has been captured yet this
session — treat that as "empty so far," not a failure, and proceed to the sweep.

### Step 2 — Sweep the session for un-logged items

Review the conversation since the start (or the last checkpoint) and identify
capture-worthy items that are **not already in the note**:

- **decision** — a non-obvious architectural or design choice made.
- **lesson** — a domain gotcha or correction worth remembering.
- **dead-end** — an approach tried that didn't work, with the revive condition.
- **deferred** — a thread intentionally set aside, with the trigger to revisit.
- **follow-up** — an external thing to check on later.
- **gotcha** — surprising subsystem behavior that bit us.
- **spec** — a frozen requirement or constraint settled this session.

Synthesize from the conversation — don't re-read the full transcript. Skip
anything already logged.

### Step 3 — Log each missed item as a session candidate

The body comes from STDIN; the session id auto-resolves from
`$CLAUDE_CODE_SESSION_ID`. One call per item:

```bash
printf '%s' "<the captured item, in your own words>" \
  | lore session candidate --kind <kind> --phase <phase>
```

`<kind>` is one of the kinds above (`decision`, `lesson`, `dead-end`,
`deferred`, `follow-up`, `gotcha`, `spec`). `<phase>` is the session phase the
item belongs to (e.g. `Plan`, `Build`, `Review`).

If an **existing** vault record was used this session, log the reference instead
of re-capturing it:

```bash
lore session referenced <kind>/<record-name>
```

### Step 4 — Commit the vault

```bash
lore sync --message "checkpoint(<worktree>): mid-session sweep"
```

### Step 5 — Tell the user

```
Swept the session and logged N candidate(s) to the session note, then committed
the vault. Status is still `active`.

Run `/clear` to wipe context — the candidates are safe in the note. When you
later run `/finish`, it finalizes this same note.
```

You cannot invoke `/clear` yourself — remind the user to run it.

## Edge cases

- **No note yet.** Lazy-create means the note doesn't exist until the first
  capture. `lore session-note` exiting non-zero is normal — the first
  `lore session candidate` call creates it.
- **Nothing new to capture.** Say so and skip the candidate calls; you can still
  `lore sync` if the vault is otherwise dirty, or just report "nothing to sweep."
- **Multiple checkpoints in one session.** Fine — each sweep only logs what isn't
  already in the note.
