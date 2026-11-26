---
allowed_tools: [Skill, Read, Glob, Grep, Write]
max_turns: 60
timeout_seconds: 600
---
I am running `/craft:execute` on `task/count-lines`, which is linked to `spec/tally-report`. Its one child task, `task/count-lines-build`, is built and `done`, and Phases 1 to 3 passed: the Phase 3 whole-change review came back as `review.md` in this directory, with no findings. Phase 4 does not apply (nothing security-sensitive changed). Carry the procedure from Phase 5's final sweep over found work through the completion report. Skip the other Phase 5 flow-out items, do not close the parent, and do not dispatch agents or run git.

Work from the `craft:execute` skill and the documents under `skills/_shared/` it points to. Do not open any other skill, and do not read any script's source or docstring: run a script only with the arguments one of those documents gives for it.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The elected vault is `eval`. Only a script that runs `lore` itself (`found_work.py`, `followup_gather.py`, `followup_parent.py`) needs this directory's `bin` first on `PATH`: start each such command with `PATH="$PWD/bin:$PATH"`. Never put that prefix on `./lore` or on any other command.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`.
