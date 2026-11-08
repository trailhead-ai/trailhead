---
allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]
max_turns: 120
timeout_seconds: 1500
---
Run `/craft:slice spec/report-export --unattended --then-plan`, then run `/craft:plan` on the slice it chose with `--unattended`, in this same session.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
