---
allowed_tools: [Skill, Read, Glob, Grep, Write]
max_turns: 50
timeout_seconds: 600
---
I am running `/craft:execute task/export-invoices-as-csv --unattended --then-pr`. This run already claimed the slice earlier in this session: the `## Unattended run` block on `task/export-invoices-as-csv` is yours and still open, and this directory is the one repository the run builds in. The parent is linked to `spec/export-invoices`, and its vault is `eval`. The build is done and you are resuming at Phase 6: every task is done, the flow-out checklist is ticked, the one commit is not pushed yet, and the parent has no `## Enumerated states`. Carry out Phase 6: the push, the observation gate, the close write and the completion report. Leave out the run-metrics row and the measurement tally.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's scripts are installed in this directory's `scripts/`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/`, run `./scripts/` instead. Pass `--session-id aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` to every `./lore session` command and to every script that takes `--session-id`.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables. Git is installed in this directory as `./git`. Wherever a document or instruction says `git`, run `./git` instead.
