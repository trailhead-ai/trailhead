---
allowed_tools: [Skill, Bash, Write, Edit]
max_turns: 6
timeout_seconds: 180
---
Run `/craft:checks`.

The check-list gather script is installed in this directory as `./gather`. Wherever the skill says to run `${CLAUDE_PLUGIN_ROOT}/scripts/checklist_gather.py`, run `./gather` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
