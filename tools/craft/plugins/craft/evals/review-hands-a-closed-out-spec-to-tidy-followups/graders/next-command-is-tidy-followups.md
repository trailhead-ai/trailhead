---
type: llm
focus: trace
---
Pass if the agent handed the finished spec onward and its final message names exactly one next command, `/craft:tidy-followups spec/export-invoices`, bare on its own line (alone in a fenced block or on a line by itself), and prints no `/craft:distill`, `/craft:slice` or `/craft:plan` command. A sentence in prose that says the distill command is printed after the tidy-followups step is allowed.
