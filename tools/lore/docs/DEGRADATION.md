# Degradation ledger

This document lists capabilities that are currently degraded or not yet
active in lore. It is the canonical reference for adopters who encounter
a silent-skip and need to understand why.

Each entry states: what is off, why, how it surfaces, and how to turn it on.

---

## Mid-conversation subsystem recall (classifier deferred)

**What is off:** The UserPromptSubmit classifier that would trigger vault
recall mid-conversation — matching the current user prompt against area and
backlog record surfaces — is not yet ported to lore.

**Why:** Porting the classifier is a Tier-1.5 item (P1.5 / P-later). It
requires a UserPromptSubmit hook, which depends on infrastructure not yet
in place.

**How to turn it on:** Port the UserPromptSubmit classifier hook, then flip
the capability flag in the config domain (`plugins/lore/lore/config/`):

```python
RECALL_CLASSIFIER_ENABLED = True
```
