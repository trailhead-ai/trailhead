---
allowed_tools: [Skill, Read, Glob, Grep, Write]
max_turns: 60
timeout_seconds: 500
---
Run `/craft:slice spec/word-count-tool --unattended`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
