---
allowed_tools: [Skill, Read, Write, Glob, Grep]
max_turns: 60
timeout_seconds: 500
---
Run `/craft:drive spec/schedule-courier-pickups` as the driver. This is a bounded session: stop as soon as step 4 has finished, that is, once it has either closed an open slice or handed on to the next step, and do not run step 5 or any later step. Dispatch no agent. Say in your final message which of the two happened and what you did to the open slice parent. The spec's vault is `eval`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's scripts are installed in this directory's `scripts/`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/`, run `./scripts/` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
