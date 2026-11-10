---
allowed_tools: [Skill, Read, Write, Glob, Grep]
max_turns: 60
timeout_seconds: 500
---
Run `/craft:drive spec/schedule-courier-pickups` as the driver. This session is already inside the driver's step 9, the build phase: steps 1 to 8 are done and their outcomes are recorded. The elected vault is `eval`, the spec is `spec/schedule-courier-pickups`, and the chosen slice parent is `task/pickups-can-be-booked-and-confirmed`. The build is finished, every task is done, the flow-out checklist is ticked, the branch's commits are already on the remote, and the parent has no `## Enumerated states`. Carry out step 9 from where Phase 6 of the shared procedure begins: the push check, the manual-check discharge, the observation gate, the close write and the completion report. Leave out the run-metrics row and the measurement tally.

This is a bounded session: stop as soon as step 9 has finished, dispatch nothing for the PR tail, and say in your final message how step 9 ended.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead. The plugin's scripts are installed in this directory's `scripts/`: wherever a document says `${CLAUDE_PLUGIN_ROOT}/scripts/`, run `./scripts/` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
