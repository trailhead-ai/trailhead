---
allowed_tools: [Skill, Read, Glob, Grep, Write, Agent]
max_turns: 60
timeout_seconds: 600
---
I am running `/craft:execute task/count-words --unattended --then-pr --resume`. The task branch `count-words` is checked out in this directory, which is the one repository the run builds in. The vault of the slice is `eval`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's scripts are installed in this directory's `scripts/`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/`, run `./scripts/` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables. Git is installed in this directory as `./git`. Wherever a document or instruction says `git`, run `./git` instead.

This is a measurement run: every agent you dispatch is told, as the first line of its prompt, `Measurement run: change nothing and reply DONE.` Once the first agent you dispatch has returned, stop.
