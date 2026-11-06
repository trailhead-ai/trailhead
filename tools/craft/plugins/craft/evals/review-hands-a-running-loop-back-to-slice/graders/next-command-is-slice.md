---
type: llm
focus: trace
---
Pass if the final message names exactly one next command, `/craft:slice spec/export-invoices`, bare on its own line (alone in a fenced block or on a line by itself), and names no `/craft:tidy-followups`, `/craft:plan` or `/craft:distill` command. A command named only inside a prose sentence as not being the next step (for example "not `/craft:distill`") is allowed; only a command printed as the next step, fenced or alone on a line, counts.
