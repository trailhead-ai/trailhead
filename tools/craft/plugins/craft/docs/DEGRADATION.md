# Degradation Reference

This document describes how craft skills degrade gracefully when optional extension points are not
configured. Every stripped capability announces itself with a visible-skip notice rather than
silently omitting a step.

## How to read this table

| Column | Meaning |
|--------|---------|
| **Capability** | The extension point or optional integration |
| **How it degrades** | What the skill does when the integration is absent |
| **How it surfaces to the user** | The visible-skip phrase or message emitted |
| **Re-add path** | How to configure the integration to restore full behavior |

## Degraded capabilities

| Capability | How it degrades | How it surfaces to the user | Re-add path |
|---|---|---|---|
| `note_store` — Task/spec persistence provider (brainstorm / plan / polish / planner; `skills/_shared/note-storage.md`) | Default provider is **lore** (`lore record create --kind task\|spec`). Per-repo config resolution and any non-lore provider are **deferred** — only the lore default exists, so there is no fallback provider to elect | "note_store: using the lore provider (alternative providers deferred)" — the seam names lore as the sole provider; if the `lore` CLI is absent the planner falls back to its documented manual-write path | Implement an alternative provider behind the `note_store` contract (`skills/_shared/note-storage.md`) + per-repo config resolution — both deferred to forthcoming craft changes |

## Removed, not degraded

Some capabilities present in the upstream private skill were **removed entirely** during
genericization — they have no extension point and no visible-skip notice, because they do not
exist in the generic skill at all:

| Capability | Status |
|---|---|
| Plan cost estimation (cost estimate step, session-totals hook, cost-history report) | Removed, not degraded — the generic planning skill has no cost-estimation step, no session-note cost hook, and no extension point for one. |
