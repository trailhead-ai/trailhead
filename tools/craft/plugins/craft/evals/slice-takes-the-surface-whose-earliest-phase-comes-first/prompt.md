---
allowed_tools: [Skill, Read, Glob, Grep, Write]
max_turns: 60
timeout_seconds: 500
---
Run `/craft:slice spec/billing-desk --unattended`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. Only a script that runs `lore` itself (`found_work.py`, `followup_gather.py`, `followup_parent.py`) needs this directory's `bin` first on `PATH`: start each such command with `PATH="$PWD/bin:$PATH"`. Never put that prefix on `./lore` or on any other command.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables apart from that `PATH` prefix.
