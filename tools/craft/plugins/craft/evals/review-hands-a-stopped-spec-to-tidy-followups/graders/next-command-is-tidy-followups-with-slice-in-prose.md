---
type: llm
focus: trace
---
Pass if the final message names exactly one fenced (or alone-on-a-line) next command, `/craft:tidy-followups spec/export-invoices`, AND carries one prose line saying that re-running the slice ritual is the alternative once the blocker clears, AND prints no `/craft:slice`, `/craft:plan` or `/craft:distill` as a fenced or alone-on-a-line command. Fail if slice is offered as a second fenced command, or if the prose alternative is missing. Fail if `/craft:distill` or `/craft:plan` is offered as something to run next in any form, fenced, alone on a line, or inside a sentence (for example "then run `/craft:distill spec/export-invoices`"). The one allowed mention of `/craft:slice` is the prose line that re-running it is the alternative once the blocker clears; offering it as a second command to run now, fenced or alone on a line, fails. A command named in prose as not being the next step (for example "not `/craft:distill`") is allowed.
