---
name: flush
description: "Evaluate outstanding session candidates into vault records, then flip the session clean. Runnable at any time — not just at session end. Clean session → nothing to flush. Dirty session → read the candidate log, apply agent judgment to evaluate each outstanding candidate (those after the flushed-at watermark) into a record via `lore record create`, then call `lore flush` to stamp clean, commit, and sync every writable vault. Use for /lore:flush, \"flush the session\", \"evaluate candidates\", \"finalize the session\", \"I'm done\", \"wrap up\", \"close this out\"."
---

# /lore:flush — Evaluate candidates and flip the session clean

`lore:flush` is the **judgment skill** for session finalization. It is runnable
at any time — not just at session end. Flush is the primary path by which findings
become durable vault records: during work, findings are captured as lightweight
session candidates; flush is where agent judgment promotes the keepers (and
discards the noise). The flow:

1. Read the current session (candidate log + status + watermark) via
   `lore session show --json`.
2. Identify **outstanding candidates** — those logged after the last `flushed-at`
   watermark (carried in the JSON's `sidecar.annotations`; not indexed, so it can
   only be read this way).
3. Apply agent judgment to evaluate each outstanding candidate into a durable vault
   record via `lore record create`.
4. Call `lore flush` (the CLI verb) to flip the session `clean`, stamp the new
   `flushed-at` watermark, commit, and — in its **sync tail** — commit, pull, and
   push every writable vault.

The **CLI** (`lore flush`) carries the mechanical flip; this **skill** carries the
judgment (candidate evaluation).

**No separate sync step.** The records step 3 created routinely live in a
*different vault* from the session record, since `lore record create` routes by
scope. `lore flush` handles that itself: its own commit stages the session
record's paths, and the sync tail that follows saves everything else — every
writable vault, committed, pulled, and pushed. Shared (`shared: true`) vaults are
never touched by the tail.

## Scoping

No-arg (current session):
```bash
lore flush
```

All dirty sessions:
```bash
lore flush all
```

A KQL search (e.g. a date window or area filter — "this week" is just a date
filter KQL query, not a dedicated form):
```bash
lore flush 'updated-at:>=2026-06-17'
```

## Process

### Step 1 — Read the session via the CLI

Read THIS worktree's session record through the canonical reader — never by
poking at vault files directly:

```bash
lore session show --json
```

This emits `{record_id, kind, name, sidecar, body}`:
- `sidecar` — the session's status and the un-indexed `annotations` (incl. the
  last `flushed-at` watermark). `annotations` is sidecar-only and never lands in
  the index, so this JSON is the only way to read it; do NOT try a KQL search.
- `body` — the candidate log.

**Outcomes:**
- **Clean session** (`sidecar.status == "clean"`): nothing to flush — report this
  and stop.
- **No session** (the command exits non-zero with a "no session record resolved"
  diagnostic): report that no session exists for this worktree — nothing to do.
- **Dirty session** (`sidecar.status == "dirty"`): proceed to Step 2.

### Step 2 — Read the candidate log and identify outstanding candidates

Read the `body` from the JSON above. Candidate lines have the form:
```
- candidate <ts> kind=<kind> phase=<phase>
```

The body text for each candidate follows its header line.

Identify **outstanding candidates**: those with a `<ts>` timestamp strictly
**after** `sidecar.annotations["flushed-at"]`. Parse the watermark as
ISO-8601 UTC (`%Y-%m-%dT%H:%M:%SZ`). If the key is missing or unparseable,
treat ALL candidates as outstanding — never silently drop candidates.

### Step 3 — Evaluate outstanding candidates into vault records

For each outstanding candidate, apply agent judgment:

- **Promote to a vault record**: if the candidate describes a durable finding
  worth finding later on its own — a `decision`, `lesson`, `spec`,
  `area` profile, `collaboration` convention, or a `task` (open work item:
  something to revisit, an approach dropped, or an external thing to watch,
  tracked via `status`) — create a record:

  ```bash
  lore record create --kind <kind> --title "<title>"
  ```

  **Ambient capture for `task` outcomes.** At flush, `task` is a first-class
  candidate outcome: the candidate becomes an `open` task with
  **auto-provenance** — no extra judgment call needed for these fields:
  - `--related task=<active-parent-task-name>`, if this session's work sits
    under a parent task — provenance, not membership. This does NOT set
    `--parent`: the new task is not part of the parent's completion scope
    unless later deliberately promoted (setting `--parent` is a separate,
    judgment-driven step).
  - `--related-file <path>` for the file(s)/folder(s) the candidate concerns,
    when known from the session's work.

  Log the new record as referenced by this session:

  ```bash
  lore session referenced <kind>/<record-name>
  ```

- **Discard**: if the candidate is session-local noise not worth preserving,
  skip it — no record is created.

- **Ask the user** (via AskUserQuestion) when disposition is genuinely ambiguous.
  Subagents are recall-blind — run this skill in the main session so the full
  conversation context is available for judgment.

If no outstanding candidates exist (all already evaluated), proceed directly
to Step 4 — a clean session says nothing about whether the vaults themselves are
committed, and the flush's sync tail is what settles that.

### Step 4 — Flip the session clean

```bash
lore flush
```

This stamps `status: clean`, records the new `flushed-at` watermark in
`annotations`, and commits the session record's own explicit paths (that commit,
and only that commit, is scoped to those paths — never `git add -A`). Relay any
notices it prints (e.g. non-git vault, push failure).

It then runs the **sync tail**: the full commit → pull → push flow over every
writable vault, which is what saves the records Step 3 created. Its per-vault
output is `lore sync`'s own — relay it, especially any vault reporting "No origin
remote", whose records exist only on this disk.

**`--no-sync` is the opt-out**, for offline work or when the vaults must not move
yet:

```bash
lore flush --no-sync
```

That form commits the session record(s) only — nothing else — and ends by naming
what it left behind:

```
notice: vault(s) still holding unsynced work — run `lore sync`:
  trailhead: 12 uncommitted change(s); no origin remote — nothing is backed up off-disk
```

Only that notice-and-stop shape needs a follow-up sync; a default flush has
already done it.

### Step 5 — Report to the user

```
Flushed session `<key>` (status: clean).

Evaluated N candidate(s) → M record(s) created, K discarded.
Synced: <per-vault outcomes from the sync tail>.
```

## Edge cases

- **Clean session (nothing outstanding).** `lore flush` exits 0 with "clean —
  nothing to flush". Relay it.
- **No session exists.** `lore flush` exits 0 with a notice about no session
  for this worktree. Relay it.
- **`lore flush` exits non-zero.** Report the error; do not retry silently.
- **Corrupt or missing `flushed-at` watermark.** Treat ALL candidates as
  outstanding (conservative — never drop candidates silently).
- **Non-git vault.** `lore flush` stamps the sidecar but skips the commit,
  printing a notice on stderr. Relay it.
- **The sync tail hit a rebase conflict.** Flush still exits **0** — its commits
  are durable — and the signal is a stderr notice naming the remedy:
  ``the flush is committed locally, but syncing vault 'x' did not complete — to
  settle it, run `lore resolve <vault-dir>` `` (the vault *directory* name, e.g.
  `v-default`). Do not re-run `lore sync`; it would abort straight back out of
  the same conflict. Run the resolve flow in `/lore:sync` instead. Because the
  exit code is 0, this notice is the only thing that tells you — read the tail's
  output rather than trusting the exit code.
- **The sync tail could not reach the network.** Soft, same as `lore sync`: the
  notice says the flush is committed locally and to re-run `lore sync` later.
- **A vault already mid-resolution** is skipped by the tail rather than synced —
  syncing would throw away an in-progress `lore resolve`.
- **`lore flush all` or `lore flush <search>`.** The same evaluation loop applies
  per session. Each session is flushed atomically; a mid-batch failure names the
  failed session and states that already-flushed sessions are clean — a re-run
  safely retries.
