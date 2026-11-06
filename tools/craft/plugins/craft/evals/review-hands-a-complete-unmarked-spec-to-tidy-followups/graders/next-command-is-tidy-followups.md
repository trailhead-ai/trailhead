---
type: llm
focus: trace
---
Pass if the agent handed the finished spec onward and its final message names exactly one next command, `/craft:tidy-followups spec/export-invoices`, bare on its own line (alone in a fenced block or on a line by itself), and prints no `/craft:distill`, `/craft:slice` or `/craft:plan` command. Fail if any other `/craft:` command (`/craft:distill`, `/craft:slice` or `/craft:plan`) is offered as something to run next, whether fenced, alone on a line, or inside a sentence (for example "then run `/craft:distill spec/export-invoices`" or "after that, `/craft:slice`"). A command named in prose as not being the next step (for example "not `/craft:distill`") is allowed, and so is a sentence that only says tidy-followups prints the distill command itself once it is done, without offering that command to run now.
