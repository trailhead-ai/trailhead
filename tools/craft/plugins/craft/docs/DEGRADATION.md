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
| `feature_flags` — Feature flag provider (planning, step 7) | The flag-touchpoint mapping decision still happens (mandatory when the spec declares a flag), but provider-specific naming and the flag-configuration skill dispatch are skipped | "no feature-flag provider configured — see the extend guide" printed at the flag-touchpoint step | Configure a flag provider; add provider-specific naming conventions and a flag-configuration skill to your plugin |
| `observability` — Observability / alerting provider (planning skill, step 7b; planner agent, step 6b + spec template) | The Observability & Failure Visibility decision still happens (mandatory), but provider-specific metric naming, alert-rule generation, and health-check wiring are skipped | "no observability provider configured — see the extend guide" printed at the provider step | Configure an observability provider; add provider-specific metric conventions and an alert-configuration skill to your plugin |
| `issue_tracker` — Issue tracker / project management (planning, step 9) | The plan is written to the vault; no ticket is created or advanced | "no issue tracker configured — status sync skipped" printed at the ticket-advancement step | Configure an issue tracker; add a tracker-sync skill to your plugin and hook it into the plan-write step |
| `feature_flags` — Feature flag provider (execute, Pre-Loop) | The both-states (on/off) test-coverage discipline still applies when the plan declares a flag, but provider SDK detection, flag creation, and first-touch wire-up are skipped | "no feature-flag provider configured — flag setup skipped" printed at the Pre-Loop flag-setup step | Configure a flag provider; add a flag-configuration skill to your plugin and dispatch it at the Pre-Loop step |
| `issue_tracker` — Issue tracker / project management (execute, loop entry + after-all-slices) | Slices are dispatched and verified normally; the work item's status is never advanced (no "in progress" / "complete" transition) | "no issue tracker configured — status transitions skipped" printed at the loop-entry and after-all-slices status steps | Configure an issue tracker; add a tracker-sync skill to your plugin and hook it into the loop-entry and after-all-slices steps |
| `note_store` — Plan/spec persistence provider (brainstorm / plan / polish / planner; `skills/_shared/note-storage.md`) | Default provider is **lore** (`lore record create --kind plan\|spec`). Per-repo config resolution and any non-lore provider are **deferred** — only the lore default exists, so there is no fallback provider to elect | "note_store: using the lore provider (alternative providers deferred)" — the seam names lore as the sole provider; if the `lore` CLI is absent the planner falls back to its documented manual-write path | Implement an alternative provider behind the `note_store` contract (`skills/_shared/note-storage.md`) + per-repo config resolution — both deferred to forthcoming craft changes |

## Removed, not degraded

Some capabilities present in the upstream private skill were **removed entirely** during
genericization — they have no extension point and no visible-skip notice, because they do not
exist in the generic skill at all:

| Capability | Status |
|---|---|
| Plan cost estimation (cost estimate step, session-totals hook, cost-history report) | Removed, not degraded — the generic planning skill has no cost-estimation step, no session-note cost hook, and no extension point for one. |
