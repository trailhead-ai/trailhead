---
allowed_tools: [Skill, Read, Write, Glob, Grep]
max_turns: 60
timeout_seconds: 540
---
Run `/craft:slice spec/bill-members-monthly`. Say in your final message what the pass did. The spec's vault is `eval`. The operator has already approved whichever slice the pass chooses: once you have stated the claim, go on and write it without waiting for a reply.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's scripts are installed in this directory's `scripts/`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/`, run `./scripts/` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
