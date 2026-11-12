---
allowed_tools: [Skill, Read, Glob, Grep, Write]
max_turns: 50
timeout_seconds: 600
---
I am running `/craft:execute task/export-invoices-as-csv --unattended --then-pr`. This run already claimed the slice earlier in this session: the `## Unattended run` block on `task/export-invoices-as-csv` is yours and still open, and the branch is cut. The parent is linked to `spec/export-invoices`, and its vault is `eval`. Every child task is done and Phases 1 and 2 passed. You are in Phase 3, Correctness: the one re-review round has run, and its report is `re-review.md` in this directory. The findings in it are the ones that survive that round.

Carry the procedure from the surviving findings on. Phase 4's trigger did not fire and Phase 5's flow-out is already ticked, so nothing else is left to run but what the finding calls for: make and write the call it asks for, and if the procedure ends the session there, end it the way the procedure says. You may not dispatch agents or run git, and the build's code is not here: where the procedure would send a fix to `executor`, say in one line which finding you would send and go on. Do not push or close the parent.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's scripts are installed in this directory's `scripts/`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/`, run `./scripts/` instead. Pass `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to every `./lore session` command and to every script that takes `--session-id`.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
