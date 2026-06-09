---
type: plan
project: {{project}}
status: draft
slug: {{slug}}
created: {{date}}
related-areas: {{related-areas}}
related-spec: {{related-spec}}
---

# {{name}} Implementation Plan

**Goal:** <!-- One or two sentences: what this plan achieves when complete. -->

**Architecture:** <!-- High-level design: components, data flow, key constraints.
Keep it brief — the slices below carry the detail. -->

**Given Axioms (ground truth this plan rests on):**
<!-- List the facts this plan depends on — verified API behavior, confirmed
library capabilities, proven assumptions from research or spike work. -->

**Rollout & Gating:**
<!-- Does this ship behind a feature flag or gating mechanism?
If yes: describe the flag strategy and kill-switch path.
If no: explain why gating is unnecessary (n/a — <reason>). -->

**Observability & Failure Visibility:**
<!-- What signal appears when this breaks?
Describe the observable symptom and where it appears. If no production runtime
is involved, note n/a — <reason>. -->

**Known Unknowns:**
<!-- Open questions that must be resolved before or during implementation.
Each item should be a checkbox so it can be ticked off as resolved. -->
- [ ] <!-- describe unknown -->

**Slices:**

### Slice 1: <!-- name -->

**Delivers:** <!-- What is complete and testable after this slice. -->
**Test contract:** <!-- The behaviors tests must verify. -->
**Files:** <!-- Files created or modified. -->
