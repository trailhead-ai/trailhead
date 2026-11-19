---
type: llm
focus: trace
---
The vault holds three specs: `previously-distilled` (waiting to be distilled), `anchored-spec` (already anchored to an active ADR, so not waiting), and `unlinked-spec` (no linked task, so deferred). Pass if the final message presents `previously-distilled` as waiting in the queue, does NOT list `anchored-spec` as waiting or queued (it may be absent or named as excluded), and reports `unlinked-spec` as deferred because it has no linked task (distill it by target or link tasks), not as queued. Fail if `unlinked-spec` or `anchored-spec` appears in the queue, if `previously-distilled` is missing from it, or if the sweep went on to cluster or draft.
