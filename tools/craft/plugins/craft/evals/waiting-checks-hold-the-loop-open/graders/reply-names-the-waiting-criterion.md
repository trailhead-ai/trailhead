---
type: llm
focus: trace
---
Pass if the agent's final message does not report the spec complete and does not report the slice loop stopped or blocked, and it says one criterion is waiting on a check, naming it by its own words ("The printed label matches the on-screen preview", or close to it) with its number AC3 after them, and it points the operator to `/craft:checks` for the list and to re-running `/craft:slice spec/print-shipping-labels` afterwards. Fail if the message says every criterion is covered, calls the spec complete, or says the loop stopped for lack of a choosable slice.
