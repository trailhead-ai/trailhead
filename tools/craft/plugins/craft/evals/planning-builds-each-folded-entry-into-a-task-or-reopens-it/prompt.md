---
allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]
max_turns: 120
timeout_seconds: 1200
---
Run `/craft:plan task/count-lines --unattended`.

The project's code is in this directory, under `tally/`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. Wherever a command you run takes a `--session-id`, pass `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee`, written after the command's own arguments. A script that runs `lore` finds it on `PATH` only when you put this directory's `bin` first: start that command with `PATH="$PWD/bin:$PATH"`.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`.
