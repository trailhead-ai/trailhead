---
allowed_tools: [Skill, Read, Glob, Grep, Write]
max_turns: 60
timeout_seconds: 600
---
I am running `/craft:execute` on `task/count-lines`, which is linked to `spec/tally-report`. Its one child task, `task/count-lines-build`, is built and `done`, and Phases 1 to 3 passed: the Phase 3 whole-change review came back as `review.md` in this directory, with no findings. Phase 4 does not apply (nothing security-sensitive changed). Carry the procedure from Phase 5's final sweep over found work through the completion report. Skip the other Phase 5 flow-out items, do not close the parent, and do not dispatch agents or run git.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The elected vault is `eval`. Pass `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to every `./lore session` command and add it to every `found_work.py` command you run that takes it (`decide`, `entry` and `report`; `entries` does not), written after the command's own arguments. A script that runs `lore` finds it on `PATH` only when you put this directory's `bin` first: start that command with `PATH="$PWD/bin:$PATH"`.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`.
