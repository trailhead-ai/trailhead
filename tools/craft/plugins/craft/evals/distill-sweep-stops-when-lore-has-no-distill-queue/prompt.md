---
allowed_tools: [Skill, Read, Glob, Grep]
max_turns: 20
timeout_seconds: 300
---
Run a `/craft:distill` sweep, but only its queue: tell me which specs are waiting to be distilled and which are deferred, then stop. Do not cluster, draft, or write anything.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
