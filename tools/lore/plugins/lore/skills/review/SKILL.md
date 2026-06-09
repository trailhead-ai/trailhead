---
name: review
description: Weekly migration ritual for the lore vault — force each open deferred, radar, dead-end, and active lesson to be re-justified in fresh words or closed. Also catches taxonomy drift, graduation candidates, and stale area profiles. Use for /lore:review, "run vault review", "weekly review", "migration pass".
---

# /lore:review — Weekly migration pass

**Recommended tier:** Sonnet/medium — the report generator does the heavy lifting; the skill is interactive re-justification and approved mutations.

Weekly migration pass for the lore vault. Not an audit — a **migration**. Every open item must be actively rewritten to stay open. If restating it in fresh words is hard, that's evidence the item has stopped mattering.

**Cadence:** weekly (or whenever the vault feels bloated).

## Purpose

Over time the vault accumulates open items that no longer matter. Without active pressure, they stay open — you scroll past them every morning, they lose signal, eventually the vault is mostly noise. The migration ritual forces decisions:

- **Keep**: restate the trigger/revisit condition in fresh words. The act of rewriting forces reassessment.
- **Close**: mark resolved/dropped/graduated with a one-line reason.
- **Schedule**: set `revisit-after: <date>` to get out of the migration loop until that date.
- **Consolidate**: merge clusters of related deferred items into umbrella notes.

The friction is the feature. If you can't restate *why this still matters* in one sentence, it probably doesn't.

## Process

### Step 1 — Announce

"Running weekly migration pass. Every open deferred, radar, dead-end, and active lesson gets re-justified in fresh words or closed. I'll also catch taxonomy drift, graduation candidates, and stale area profiles."

### Step 2 — Generate the report

Run the helper:

```bash
lore review [--since <window>]
```

The `--since` flag controls the lookback window for the activity section (e.g. `--since 14d` or `--since 2026-04-01`). Default: since the most recent review note in `reviews/`, or 7 days if no reviews yet. The **migration sections (open deferred, dead-ends, radar, lessons) are not time-filtered**: they cover every open item regardless of age.

The report contains:

1. **Activity since last review** — git log + files touched, grouped by top-level dir
2. **Action taxonomy drift** — near-duplicate action names (token overlap ≥ 50%)
3. **Graduation candidates** — active collaboration notes older than 30 days
4. **Stale area profiles** — profiles with `last-touched` older than 60 days
5. **Open deferred items** — every one, for migration
6. **Dead-ends** — for revive-condition review
7. **Open radar items** — every one, for migration
8. **Active lessons** — every one, for migration

### Step 3 — Walk the user through each section

Present each section with your take. The migration sections (5–8) are load-bearing — don't rush them.

**Activity** (section 1): one-sentence summary. Quick, move on.

**Drift** (section 2): for each near-duplicate pair, propose a merge or explain why they're distinct. Wait for decision.

**Graduation candidates** (section 3): for each aged collaboration note, assess whether the pattern has stabilized enough to become a memory feedback rule.

**Stale area profiles** (section 4): for each, check recent git activity in the relevant code. If we've been working there, propose updates. If not, it's dormant — fine.

**Open deferred items** (section 5) — **migration**: for each open item, restate it:

> "Item: `deferred/...` — [one-sentence summary]. Current trigger: [paraphrase]. Restated: [rewrite in fresh words why we'd revisit this]. Still worth keeping?"

User answers: **Yes, keep** / **Yes, but reframe** / **Schedule** / **Close**.

If the user can't readily answer — press: "If you can't articulate why in one sentence, it probably doesn't matter anymore. Drop?"

**Dead-ends** (section 6): for each, restate: "We tried X. It failed because Y. Revive if Z." Ask: does Z still make sense? Keep / update / mark `status: archived`.

**Open radar items** (section 7): "Watching X. Last state: Y. Why we care: Z." Still worth polling? Keep / update / close.

**Active lessons** (section 8): for each, restate "We did X wrong. Found out by Y. Prevention check: Z." Two questions:
1. Is the prevention check still meaningful (or superseded by tooling)?
2. Are the `areas:` cross-links current?

### Step 4 — Execute approved changes

For each thing the user approves, use the `mcp__*` vault tools (or `lore patch` / `lore set-status`) to apply the mutations. Every mutation is a separate sequential call — don't batch dependent writes.

Common mutations:
- **Deferred kept** — patch `last-reviewed: <today>` into frontmatter.
- **Deferred reframed** — update `next-check` or `revisit-after`. Bump `last-reviewed`.
- **Deferred closed** — set `status: resolved` / `dropped` / `graduated` with a one-line reason.
- **Lesson kept** — patch `last-reviewed: <today>`.
- **Lesson superseded** — set `status: superseded`, append a `## Closed` section explaining what hardened the prevention.
- **Dead-end archived** — set `status: archived`.
- **Radar closed** — set `status: resolved` or `dropped`.

### Step 5 — Write the review note

Create `reviews/YYYY-MM-DD-HHmm.md`:

```yaml
---
type: review
date: YYYY-MM-DD
since: <window>
findings:
  drift: <count>
  graduation: <count>
  stale: <count>
  deferred: <count>
  dead-ends: <count>
  lessons: <count>
actions-taken:
  - <short description>
---
```

### Step 6 — Commit

Use `/lore:sync` to commit the review note and all mutations:

```
lore-review: <date> — N actions taken
```

### Step 7 — Report back

"Review complete. N findings surfaced, M actions taken. Report at `reviews/YYYY-MM-DD-HHmm`."

## Edge cases

- **First-ever run.** No `reviews/` dir — helper falls back to "last 7 days". Everything looks fresh. That's expected.
- **Empty sections.** If a dir has no content, the report says "none" for that section and moves on — no crash.
- **Vault not found.** `lore review` exits non-zero with a clear error message.
