---
allowed_tools: [Skill, Read, Write, Glob, Grep]
max_turns: 120
timeout_seconds: 900
---
Run `/craft:slice spec/parcel-labels --unattended`.

The project's code is in this directory, under `labels/`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. A script that runs `lore` finds it on `PATH` only when you put this directory's `bin` first: start that command with `PATH="$PWD/bin:$PATH"`.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
