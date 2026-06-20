# Degradation ledger

This document lists capabilities that are currently degraded or not yet
active in lore. It is the canonical reference for adopters who encounter
a silent-skip and need to understand why.

Each entry states: what is off, why, how it surfaces, and how to turn it on.

---

## /lore:check-in — legacy "snoozed" status skipped, not polled

Follow-up notes carrying `status: snoozed` (not in lore's canonical `active | resolved | dropped` vocab) are skipped and flagged in the digest's "Skipped" section; the user must update or remove them manually.

---

## Mid-conversation subsystem recall (classifier deferred)

**What is off:** The UserPromptSubmit classifier that would trigger vault
recall mid-conversation — matching the current user prompt against subsystem
and deferred note surfaces — is not yet ported to lore.

**Why:** Porting the classifier is a Tier-1.5 item (P1.5 / P-later). It
requires a UserPromptSubmit hook, which depends on infrastructure not yet
in place.

**How to turn it on:** Port the UserPromptSubmit classifier hook, then flip
the capability flag in `plugins/lore/scripts/config.py`:

```python
RECALL_CLASSIFIER_ENABLED = True
```
