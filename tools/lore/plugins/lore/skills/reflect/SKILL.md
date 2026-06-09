---
name: reflect
description: Narrative synthesis of lore vault activity over a period — themes that recurred, learnings that stuck, graduations from radar/deferred, and noise that fizzled. Use for /lore:reflect, "reflect on the month", "reflect on the period", "what did we learn this month", "monthly reflection".
---

# /lore:reflect — Narrative synthesis

**Recommended tier:** Sonnet/medium — a subagent does the reading; this skill stitches output into a narrative note. (Advisory — no auto-switch.)

**Inputs NOT gathered by this skill:** ai-memory feedback files (app-layer, not in the lore vault) and daily briefings (a separate global artifact, not stored in `$LORE_VAULT`). The reflection will be thinner on those fronts compared to an app-coupled ritual. See `docs/DEGRADATION.md` for context.

Step back and look at the shape of a period: what recurred, what stuck, what graduated, what fizzled. Complements `/lore:review` (which prunes) by producing a narrative artifact (which reflects).

**Cadence:** periodic — usually at the end of a month, a quarter, or a significant project arc.

## Relationship to other rituals

| Ritual | Cadence | Pressure | Output |
|--------|---------|----------|--------|
| `/lore:review` | Weekly | Force re-justify or close | Review note + mutations |
| `/lore:reflect` | Periodic | Narrative synthesis | Reflection note |

/lore:review prunes bloat. /lore:reflect asks *what did we actually learn*. Both touch the same data, different pressures.

## When to use

- End of a calendar month, reflecting on the prior month
- End of a quarter or a significant project arc
- After a long stretch of heavy work and it feels like the vault should be consolidated

Idempotent within a day — a same-day second run overwrites the reflection file.

## Process

### Step 0 — Determine the period

- Default: the calendar month prior to today.
- User can override: "reflect on March 2026" → target `2026-03`.
- Period written as `YYYY-MM` for monthly; `YYYY-Q<N>` for quarterly.
- Date range: `window_start` = first day of period, `window_end` = last day of period (ISO dates, e.g. `2026-05-01` / `2026-05-31`).

### Step 0.5 — Resolve vault and guard

Resolve the vault:

```bash
python3 -c "
import os
from pathlib import Path
raw = os.environ.get('LORE_VAULT', '')
vault = str(Path(raw).expanduser()) if raw else str(Path('~/lore').expanduser())
print(vault)
"
```

Announce: "Reflecting over vault at `<vault>`."

Check for sessions in the window:

```bash
python3 -c "
import sys
sys.path.insert(0, '<vault-plugins-scripts-path>')
from reflect_sessions import sessions_in_window
from pathlib import Path
sessions = sessions_in_window(Path('<vault>'), '<period>', '<window_start>', '<window_end>')
print(len(sessions))
"
```

If the vault has zero sessions in the window, stop: "No finalized sessions found in `<vault>` for `<period>`. Nothing to reflect on — run some sessions and finalize them first."

### Step 1 — Gather artifacts (mechanical, parallel)

Pull everything created or modified in the period window. Use file-date filtering (frontmatter date fields or filename date prefix). Run in parallel:

- **Sessions finalized**: `$LORE_VAULT/sessions/*.md` with frontmatter `status: complete` (or back-compat `status: finalized`) AND `ended` in window — use `reflect_sessions.sessions_in_window()` (importable from `plugins/lore/scripts/reflect_sessions.py`).
- **Decisions**: `$LORE_VAULT/decisions/*.md` with filename date in window.
- **Deferred created**: `$LORE_VAULT/deferred/*.md` with frontmatter `raised` in window.
- **Deferred closed**: `$LORE_VAULT/deferred/*.md` where `status` is `resolved`/`dropped`/`graduated` AND file mtime in window (proxy for when closed).
- **Dead-ends**: `$LORE_VAULT/dead-ends/*.md` with filename date in window.
- **Radar added**: `$LORE_VAULT/radar/*.md` with frontmatter `added` in window.
- **Radar closed**: `$LORE_VAULT/radar/*.md` where `status` is `resolved`/`dropped` AND mtime in window.
- **Area updates**: `$LORE_VAULT/areas/*.md` with frontmatter `last-touched` in window.

Skip `active`-status session notes — they'll be picked up by the next reflection.

### Step 2 — Subagent synthesis

Dispatch **one** `lore:lore-librarian` subagent with the gathered paths. If `lore:lore-librarian` is not configured, write the reflection yourself from the gathered paths; note in the output that the synthesis pass was skipped and results may be shallower.

Brief:

> Produce a reflection for <period> as a narrative markdown document, NOT a list. Aim for reading-time ≤5 minutes.
>
> Inputs (paths listed below): finalized sessions, decisions, deferred (new + closed), dead-ends, radar (added + closed), area updates.
>
> Structure:
>
> ## Shape of the period
> 2-4 sentences framing the period. What was it *about*? Biggest arcs. Use concrete details — specific decisions, areas — not generic phrases like "productive period."
>
> ## Themes that recurred
> 3-5 themes across sessions. For each: what the theme was, which sessions/decisions touched it, what changed. Bullet per theme, 2-3 sentences each. Focus on patterns that appeared in **multiple** sessions — a single one-off isn't a theme.
>
> ## Learnings that stuck
> Decisions and area gotchas from this period that were **referenced later** in the same period (proxy: mentioned in subsequent sessions). Those are the load-bearing ones. List 3-6 with a one-line description each.
>
> ## Graduated
> What moved out of active state this period: deferred → resolved, radar → resolved, plans → completed. One sentence per graduation. If nothing graduated, say so — graduation-free periods are a signal worth naming.
>
> ## Noise that fizzled
> Items raised (deferred, radar, open questions) that were **never actioned and never re-justified** — candidates for /lore:review closure. One-liner each with a suggested closure reason.
>
> ## Missed signals (optional)
> If the subagent spots something that *should* have been caught earlier — a gotcha hit twice before being captured, a decision made implicitly before being written down, an area that accumulated activity without a profile — call it out. 0-3 items. Skip the section entirely if nothing qualifies; don't pad.
>
> ## Next period's focus
> 1 sentence: based on open deferred items + active radar, what's the center of gravity going into the next period?
>
> Use concrete references for everything mentioned. Keep concrete, avoid corporate-retro language ("synergies", "pivoted"). Budget 90 seconds of reading, 400-800 words total.

Pass all gathered paths as explicit lists in the subagent prompt.

### Step 3 — Compose and save the reflection

Write to `$LORE_VAULT/reviews/<period>-reflection.md` with frontmatter:

```markdown
---
type: reflection
period: <period>
generated: <ISO8601>
counts:
  sessions-finalized: <N>
  decisions: <N>
  deferred-opened: <N>
  deferred-closed: <N>
  dead-ends: <N>
  radar-opened: <N>
  radar-closed: <N>
---

<subagent output from Step 2>
```

Create the file with the Write tool. Overwrites any prior same-period reflection (supports re-runs after vault edits).

Ensure `$LORE_VAULT/reviews/` exists:

```bash
mkdir -p "$LORE_VAULT/reviews"
```

### Step 4 — Present and offer iterations

Show the reflection to the user. Ask if anything needs tweaking — themes mis-characterized, learnings missed, phrasing off. Edit the file per their feedback.

If the user spots items in "Noise that fizzled" they want to close now, offer to close them inline using `/lore:review`'s mutation pattern.

### Step 5 — Commit

Use `/lore:sync`:

```
lore sync --message "reflection: <period>"
```

### Step 6 — Report

```
Reflection saved to reviews/<period>-reflection.md. <N themes, M learnings, K graduations, P noise candidates>.
```

## Key principles

- **Narrative, not list.** The reflection reads like an end-of-period note you'd write in a journal, not a dashboard. Themes are sentences, not bullet points with metrics.
- **Concrete over generic.** Name the decision, the area. "Resolved the background-job retry deferred" beats "shipped reliability work."
- **Counts live in frontmatter, not prose.** The narrative shouldn't read like a stats page. The `counts:` frontmatter is there for aggregation; the body is for reflection.
- **Graduation-free periods are data.** If nothing moved out of active state, say so plainly — it's a signal about either scope or follow-through.
- **Missed signals are optional.** Don't force them. Pad-the-retro energy is worse than honest omission.
- **Complement, don't duplicate `/lore:review`.** This ritual reflects; `/lore:review` prunes. If the reflection surfaces closure candidates, note them — but don't execute closures inline (that's `/lore:review`'s job, unless the user asks specifically).

## Edge cases

- **First reflection (no prior period's file).** Fine — just generate for the requested period. Frontmatter `generated` timestamps the first run.
- **Subagent synthesis fails or times out.** Fallback: write a minimal reflection with just the Counts frontmatter and the gathered paths under a `## Raw inputs` section. Tell the user the narrative pass failed and offer to retry.
- **Period with no data.** If the gather in Step 1 turns up zero artifacts, write a one-sentence reflection: "No vault activity in <period>." Still commit the file — absence is a data point.
- **Partial-period run.** If the user runs `/lore:reflect` mid-month, warn that the window is partial and ask whether to proceed (useful for year-end reviews pulling multiple months).
- **Multi-period reflection.** User can ask for a range ("reflect on Q1 2026"). Expand the gather window; use the same narrative structure at the quarter scale. Save to `reviews/YYYY-Q<N>-reflection.md`.
- **`$LORE_VAULT` unset.** Defaults to `~/lore`. Announce the vault path at Step 0.5 and stop if zero sessions found — don't emit an empty reflection silently.
