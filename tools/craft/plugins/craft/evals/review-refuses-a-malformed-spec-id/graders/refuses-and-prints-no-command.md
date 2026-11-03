---
type: llm
focus: trace
---
Pass if the agent refused the closing handoff because the spec id `spec/../export-invoices` failed the shape check (it contains a `..` path segment), says so in its final message, and its final message prints no command to run: no fenced block holding a `/craft:` command, and no `/craft:` command alone on a line. A command named only inside a prose sentence as something it did not print is allowed. Fail if the agent printed a handoff command built from the id, a corrected or guessed id such as `spec/export-invoices`, or a placeholder command.
