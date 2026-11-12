---
allowed_tools: [Skill, Read, Glob, Grep]
max_turns: 60
timeout_seconds: 500
---
Run `/craft:execute task/count-words --unattended --then-pr`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
