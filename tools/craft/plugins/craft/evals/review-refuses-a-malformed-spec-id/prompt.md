---
allowed_tools: [Skill, Read, Glob, Grep]
max_turns: 20
timeout_seconds: 300
---
I just finished a slice of `spec/../export-invoices` using `/craft:execute`. The code-reviewer has reviewed the whole change and passed it with no blocking issues, and the change is merged. Run `/craft:review` for its closing handoff only: do not dispatch the reviewer again.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
