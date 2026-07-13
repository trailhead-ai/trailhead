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
| `design_mockup` — UI mockup generation (formerly brainstorm step 4) | Removed, not degraded — brainstorm settles UI direction in conversation and writes it verbally into the spec's UI Direction section. There is no mockup dispatch and no seam for one (pinned by `test_brainstorm_generic.py::test_brainstorm_does_not_dispatch_design_mockup_provider`). |
| `feature_flags` — feature-flag provider (formerly brainstorm step 5) | Removed, not degraded — craft's brainstorm has no rollout/gating step, so there is nothing to gate on a provider. |
| `observability` — observability / alerting provider (formerly brainstorm step 5b) | Removed, not degraded — craft's brainstorm has no observability step. Failure visibility is instead grilled for in step 2 (*Failure visibility*) and lands in the spec as ordinary acceptance criteria. |
| `issue_tracker` — issue tracker / project-management sync (formerly brainstorm exit gate) | Removed, not degraded — the exit gate writes the spec to the note store and hands off to the gauntlet; it syncs no ticket. |

> These four were documented as *degraded* seams back when `brainstorm` was a lore skill. Brainstorm
> now ships in craft, and the seams were removed rather than carried over — they have no
> visible-skip notice because there is no step left to skip. `note_store` is the one seam brainstorm
> retains (see the table above).
