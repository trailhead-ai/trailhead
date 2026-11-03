---
type: llm
focus: trace
---
Pass if the final message names exactly one fenced (or alone-on-a-line) next command, `/craft:tidy-followups spec/export-invoices`, AND carries one prose line saying that re-running the slice ritual is the alternative once the blocker clears, AND prints no `/craft:slice`, `/craft:plan` or `/craft:distill` as a fenced or alone-on-a-line command. Fail if slice is offered as a second fenced command, or if the prose alternative is missing.
