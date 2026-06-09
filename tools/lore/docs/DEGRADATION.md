# Degradation ledger

This document lists capabilities that are currently degraded or not yet
active in lore. It is the canonical reference for adopters who encounter
a SessionStart banner or a silent-skip and need to understand why.

Each entry states: what is off, why, how it surfaces, and how to turn it on.

---

## /lore:reflect — ai-memory and daily briefings not gathered

**What is off:** The reflect skill does not read ai-memory feedback files (app-layer, not stored in the lore vault) or daily briefings (a separate global artifact). The reflection synthesizes only lore vault content: sessions, decisions, deferred, dead-ends, radar, and subsystem updates.

**Why:** These inputs are app-specific or global (outside `$LORE_VAULT`) and cannot be gathered in a generic, portable ritual. The brain `/monthly-reflection` original gathered them from machine-local paths.

**How it surfaces:** The reflect SKILL.md opens with an explicit "Inputs NOT gathered" callout so the user is not silently surprised by a thinner reflection.

**How to turn it on:** Not applicable for the generic skill. Wrap `/lore:reflect` in an app-layer skill that gathers the additional inputs and passes them to the subagent synthesis step.

---

## /lore:check-radar — legacy "snoozed" status skipped, not polled

Radar notes carrying `status: snoozed` (not in lore's canonical `active | resolved | dropped` vocab) are skipped and flagged in the digest's "Skipped" section; the user must update or remove them manually.

---

## Mid-conversation subsystem recall (classifier deferred)

**What is off:** The UserPromptSubmit classifier that would trigger vault
recall mid-conversation — matching the current user prompt against subsystem
and deferred note surfaces — is not yet ported to lore.

**Why:** Porting the classifier is a Tier-1.5 item (P1.5 / P-later). It
requires a UserPromptSubmit hook and session-context injection, which
depend on infrastructure not yet in place.

**How it surfaces:** Every SessionStart context block includes a banner:

> Mid-conversation subsystem recall is not active (classifier deferred).
> Branch/keyword recall fires at SessionStart only.

**How to turn it on:** Port the UserPromptSubmit classifier hook, then flip
the capability flag in `plugins/lore/scripts/config.py`:

```python
RECALL_CLASSIFIER_ENABLED = True
```

Setting this flag to `True` suppresses the banner and signals that
mid-conversation recall is active.
