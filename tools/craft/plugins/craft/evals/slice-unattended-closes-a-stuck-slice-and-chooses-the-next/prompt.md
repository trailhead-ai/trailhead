---
allowed_tools: [Skill, Read, Write, Glob, Grep]
max_turns: 120
timeout_seconds: 540
---
Run `/craft:slice spec/schedule-courier-pickups --unattended`. The spec's vault is `eval`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's scripts are installed in this directory's `scripts/`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/`, run `./scripts/` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
