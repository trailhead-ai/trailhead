---
allowed_tools: [Skill, Read, Glob, Grep, Write]
max_turns: 80
timeout_seconds: 900
---
Run `/craft:slice spec/route-planner --unattended`.

The project's code is in this directory, under `routes/`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. Pass `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to every `./lore session` command and add it to every `found_work.py` command you run that takes it (`reopen-abandoned`, `entry` and `report`; `entries` does not), written after the command's own arguments. A script that runs `lore` finds it on `PATH` only when you put this directory's `bin` first: start that command with `PATH="$PWD/bin:$PATH"`. The CLI itself is always called as `./lore`, never as `lore`, inside a pipe too, and in a pipe the `PATH` prefix goes on the script after the `|`, never on `./lore`.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables apart from that `PATH` prefix.
