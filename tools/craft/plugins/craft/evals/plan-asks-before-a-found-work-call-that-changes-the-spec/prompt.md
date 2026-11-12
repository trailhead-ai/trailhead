---
allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]
max_turns: 120
timeout_seconds: 900
---
Run `/craft:plan task/count-lines`. You have my approval for the approach you recommend and for every disposition you recommend on a council finding.

Work from the `craft:plan` skill and the documents under `skills/_shared/` it points to. Do not open any other skill, and do not read any script's source or docstring: run a script only with the arguments one of those documents gives for it.

The project's code is in this directory, under `tally/`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. Pass `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to every `./lore session` command and add it to every `found_work.py` command you run that takes it (`entry` and `report`; `entries` does not), written after the command's own arguments. A script that runs `lore` finds it on `PATH` only when you put this directory's `bin` first: start that command with `PATH="$PWD/bin:$PATH"`.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`.
