---
allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]
max_turns: 120
timeout_seconds: 1200
---
Run `/craft:plan task/count-lines --unattended`.

Work from the `craft:plan` skill and the documents under `skills/_shared/` it points to. Do not open any other skill, and do not read any script's source or docstring: run a script only with the arguments one of those documents gives for it.

The project's code is in this directory, under `tally/`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. Only a script that runs `lore` itself (`found_work.py`, `followup_gather.py`, `followup_parent.py`) needs this directory's `bin` first on `PATH`: start each such command with `PATH="$PWD/bin:$PATH"`. Never put that prefix on `./lore` or on any other command.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`.
