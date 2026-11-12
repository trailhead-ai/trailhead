---
type: llm
focus: trace
---
Pass only if, before the agent wrote the drop of F1 (the transition document and the `found_work.py entry` call), one of its messages stated in a single line how it read the operator's reply, beginning "Reading your answer as:" and saying the reply asks to move F1 to task/billing-export-hardening. Fail if no such line appears, or if it appears only after the drop was written.
