---
allowed_tools: [Skill, Read, Glob, Grep, Agent]
max_turns: 90
timeout_seconds: 900
---
Run `/craft:execute task/delete-old-exports-after-a-retention-window --unattended --then-pr`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
