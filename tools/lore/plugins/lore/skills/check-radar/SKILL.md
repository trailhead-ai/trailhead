---
name: check-radar
description: Poll all active radar items in the lore vault, detect state changes, summarize what moved, and update last-checked / last-state. Use for /lore:check-radar, "check the radar", "any movement on my radar", "poll the watchlist", "what's new on my radar".
---

# /lore:check-radar — Poll active radar items

**Recommended tier:** Sonnet/low — parallel polls + diff + note updates. No reasoning depth. (Advisory — no auto-switch.)

Iterate through `$LORE_VAULT/radar/*.md`, poll each `active` item whose `last-checked` is older than its `check` interval (or has never been checked), diff the result against `last-state`, and produce a human-readable digest of what moved.

## When to use

- User says `/lore:check-radar`, "check the radar", "any movement?", "poll my watches"
- Start of a work session where the user wants to know if external blockers have cleared
- On demand whenever the user is deciding what to work on next

## Process

### Step 0 — Resolve the vault

```bash
python3 -c "
import os
from pathlib import Path
raw = os.environ.get('LORE_VAULT', '')
vault = str(Path(raw).expanduser()) if raw else str(Path('~/lore').expanduser())
print(vault)
"
```

Announce: "Checking radar in vault at `<vault>`."

### Step 1 — Select due items

Use the `radar_due` helper (importable from `plugins/lore/scripts/radar_due.py`):

```bash
python3 -c "
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, '<vault-plugins-scripts-path>')
from radar_due import radar_notes_due
result = radar_notes_due(Path('<vault>'), today=date.today())
for p in result.due:
    print('DUE', p)
for p in result.manual:
    print('MANUAL', p)
for p in result.skipped_legacy:
    print('SKIP_LEGACY', p)
"
```

The helper applies the selection predicate:
- Poll iff `status: active` AND (`last-checked` empty → bootstrap, OR `last-checked` older than cadence)
- `source: manual` items → listed at end as "needs human check", not polled
- `status: resolved` / `dropped` → silently skipped
- Off-vocab status (e.g. legacy "snoozed") → skipped and flagged, never crashes

If no items are due, tell the user: "Nothing due on the radar. N items active, next check `<date>`." and stop.

### Step 2 — Poll each item

For each due item, run the fetch appropriate to its `source`:

| source           | command                                                                  |
|------------------|--------------------------------------------------------------------------|
| `npm`            | `npm view <target> version` and `npm view <target> time.modified`        |
| `github-issue`   | `gh issue view <owner/repo> <N> --json state,title,updatedAt,comments`   |
| `github-pr`      | `gh pr view <owner/repo> <N> --json state,title,updatedAt,mergedAt`      |
| `github-release` | `gh release list -R <owner/repo> --limit 5 --json tagName,publishedAt`   |
| `url`            | `WebFetch` with prompt "return the version/status/headline verbatim"     |

**Run polls in parallel** — each one is independent. Batch them into a single assistant turn with multiple tool calls.

If a fetch fails (network, rate limit, 404), record it as "check failed: `<reason>`" and move on. Do **not** update `last-checked` for that item — a failed poll retries on the next run.

### Step 3 — Diff against last-state

For each successful poll, compare the new state string against the note's `last-state`:

- **Unchanged:** bump `last-checked` to today. No user-visible line (roll up in the digest count).
- **Changed:** produce a short summary:
  - `npm`: old version → new version (flag if major bump)
  - `github-issue`: state change (open→closed), new comment count, last updater
  - `github-pr`: opened/merged/closed/draft→ready, mergedAt if relevant
  - `github-release`: new tag name + published date
  - `url`: new headline/version vs old

Update the note's `last-checked` AND `last-state` via direct Write/Edit on the file. Use an in-place text substitution that replaces only the `last-checked:` and `last-state:` lines, leaving all other content byte-identical.

**Only update `last-checked` and `last-state`.** Never write to `status:` — all status changes are user-driven, not skill-driven. This preserves the canonical status vocab (`active | resolved | dropped`) and prevents off-vocab writes.

Example patch (adapt for actual old values):

```python
text = Path(note_path).read_text()
text = text.replace(f"last-checked: {old_checked}", f"last-checked: {today}")
text = text.replace(f"last-state: {old_state}", f"last-state: {new_state}")
Path(note_path).write_text(text)
```

### Step 4 — Report the digest

Structure the user-facing report as:

```
Radar digest — <today's date>

Moved (<N>):
  • <slug> — <old> → <new>
    Why we care: <from note>
    On change: <from note, if any>

Quiet (<N>):
  <slug>, <slug>, <slug>, ...

Failed (<N>):
  • <slug> — <error>

Manual (needs your eyes) (<N>):
  • <slug> — <last-state>

Skipped (off-vocab status) (<N>):
  • <slug> — status: <value> (not in active|resolved|dropped)
```

Keep the "Moved" section rich — this is the whole point of running the skill. Keep "Quiet" as a one-line comma list. Only include sections that are non-empty.

### Step 5 — Suggest follow-ups on movement

For each moved item, if its `## On change` block names a concrete action (reopen a deferred item, remove patches, bump a version), surface it as a suggested next step at the bottom of the digest. Do not take the action automatically — the user decides.

### Step 6 — Commit

Use `/lore:sync`:

```
lore sync --message "check-radar: <date>"
```

## Key principles

- **Parallel polls.** One radar check with 10 items should be one round-trip, not ten.
- **Failures don't advance `last-checked`.** A failed poll retries naturally on the next run.
- **Only report the interesting stuff loudly.** Quiet items get one line. The signal is in what moved.
- **Never mutate `last-state` without diffing against the old value first** — otherwise the next run misses the change.
- **Never write to `status:`.** Status is user-owned. The skill writes only `last-checked` and `last-state`.
- **`$LORE_VAULT` unset:** defaults to `~/lore`. Announce the vault path at Step 0.

## Edge cases

- **Empty `last-checked` (bootstrap).** First poll ever — treat as stale and poll unconditionally. Write `last-checked: <today>` and `last-state: <result>` after the first successful poll.
- **Unknown `check` cadence.** Treat as `daily` (conservative: poll frequently).
- **No radar directory.** Tell the user "No `radar/` directory found in `<vault>`. Nothing to check." and stop.
- **All items quiet.** Emit the "Nothing due" message with the count and the next expected check date.
- **Off-vocab status (e.g. "snoozed").** The `radar_due` helper places these in `skipped_legacy`. Report them in the "Skipped" section of the digest so the user knows to update or remove the note.
