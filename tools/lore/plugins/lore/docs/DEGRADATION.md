# Degradation Reference

This document describes how lore skills degrade gracefully when optional extension points are not
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
| `design_mockup` — UI mockup generation (brainstorm, step 4) | UI direction is settled in conversation only; no rendered design artifact is produced | "design-mockup tool not configured" printed in step 4; skill continues with verbal UI description | Configure a design-mockup tool in your environment and reference it in a local skill or extension; see your plugin's extend guide |
| `feature_flags` — Feature flag provider (brainstorm, step 5; planning, step 7) | The Rollout & Gating decision still happens (mandatory), but provider-specific flag naming, rollout configuration, and flag-management skill dispatch are skipped | "no feature-flag provider configured — see the extend guide" printed at the flag configuration step | Configure a flag provider; add provider-specific naming conventions and a flag-configuration skill to your plugin |
| `observability` — Observability / alerting provider (brainstorm, step 5b; planning, step 7b) | The Observability & Failure Visibility decision still happens (mandatory), but provider-specific metric naming, alert rule generation, and health-check wiring are skipped | "no observability provider configured — see the extend guide" printed at the provider configuration step | Configure an observability provider; add provider-specific metric conventions and an alert-configuration skill to your plugin |
| `issue_tracker` — Issue tracker / project management (brainstorm, exit gate; seed, throughout) | The spec is written and status is updated in the vault; no ticket is created or updated | "no issue tracker configured — status sync skipped" printed at the ticket-advancement step | Configure an issue tracker; add a tracker-sync skill to your plugin and hook it into the exit gate |
| `issue_tracker` — Issue tracker linkage from seed (seed, step 4) | The spec is promoted and archived normally; no ticket is created, linked, or updated from the inbox doc | "no issue tracker configured — ticket linkage skipped" printed at step 4; seed continues to archive the inbox doc | Configure an issue tracker integration; add a tracker-sync skill to your plugin and call it from seed step 4 after the spec is frozen |
| `forge/planning` — Planning skill for implementation slicing (brainstorm, exit gate) | Brainstorming completes and the spec is frozen; the handoff to implementation planning cannot proceed automatically | "the `planning` skill lives in the forge plugin — install forge to continue into planning, or write your implementation slices directly from this spec" printed at the exit gate | Install the forge plugin; the `planning` skill becomes available as `/forge:planning` |
