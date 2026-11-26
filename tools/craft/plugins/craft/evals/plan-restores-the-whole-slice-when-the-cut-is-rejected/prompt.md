---
allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]
max_turns: 120
timeout_seconds: 900
---
Run `/craft:plan task/export-the-orders-to-a-csv-file`. You have my approval for the approach you recommend and for every disposition you recommend on a council finding; where the skill would ask me a clarifying question, decide it on your own recommendation instead.

I answer the plan's approval step now, in advance: if the plan cuts criteria out of the slice, my answer to that cut is "keep the whole slice" as one large pull request. Carry that answer out as the approval step says, then stop: build nothing and do not run `/craft:execute`.

The project's code is in this directory, under `shop/`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. Pass `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to every `./lore session` command and add it to every `found_work.py` command you run that takes it (`entry` and `report`; `entries` does not), written after the command's own arguments. A script that runs `lore` finds it on `PATH` only when you put this directory's `bin` first: start that command with `PATH="$PWD/bin:$PATH"`.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables apart from that `PATH` prefix.
