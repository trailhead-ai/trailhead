---
allowed_tools: [Skill, Read, Write, Glob, Grep]
max_turns: 60
timeout_seconds: 500
---
Run `/craft:execute task/pickups-can-be-booked-and-confirmed` as the closing session. The build is done and you are resuming at Phase 6: every task is done, the flow-out checklist is ticked, the branch's commits are already on the remote, and the parent has no `## Enumerated states`. Carry out Phase 6 from its start: the push check, the manual-check discharge, the observation gate, the close write and the completion report. Leave out the run-metrics row and the measurement tally. The parent's vault is `eval`, and its spec is `spec/schedule-courier-pickups`.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's scripts are installed in this directory's `scripts/`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/`, run `./scripts/` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
