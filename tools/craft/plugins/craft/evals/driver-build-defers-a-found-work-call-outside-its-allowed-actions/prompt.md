---
allowed_tools: [Skill, Read, Glob, Grep]
max_turns: 40
timeout_seconds: 420
---
Run `/craft:drive spec/export-invoices` as the driver. This session is already inside the driver's step 9, the build phase: steps 1 to 8 are done and their outcomes are recorded. The spec is `spec/export-invoices` and the chosen slice parent is `task/export-csv-build`. Every task is built, and Phases 1 and 2 passed. The Phase 3 review came back as `review.md` in this directory. Carry the procedure from Phase 3's triage of that review through Phase 5, then give the completion report. The build's code is not in this directory and you may not dispatch agents or run git: where the procedure would send a fix to `executor`, say in one line which finding you would send and go on. Do not close the parent.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. Pass `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to every `./lore session` command and to every script that takes `--session-id`. A script that runs `lore` finds it on `PATH` only when you put this directory's `bin` first: start that command with `PATH="$PWD/bin:$PATH"`.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`.
