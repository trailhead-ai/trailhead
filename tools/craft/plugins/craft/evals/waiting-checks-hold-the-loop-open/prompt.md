---
allowed_tools: [Skill, Read, Glob, Grep]
max_turns: 40
timeout_seconds: 500
---
Run `/craft:slice spec/print-shipping-labels`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's pending-check script is installed as `./scripts/pending_check.py`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/pending_check.py`, run `./scripts/pending_check.py` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
