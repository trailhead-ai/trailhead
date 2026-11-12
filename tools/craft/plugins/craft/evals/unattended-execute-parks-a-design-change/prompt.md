---
allowed_tools: [Skill, Read, Glob, Grep, Write]
max_turns: 40
timeout_seconds: 420
---
I am running `/craft:execute task/count-words --unattended --then-pr`. This run already claimed the slice earlier in this session: the `## Unattended run` block on `task/count-words` is yours, still open, and the branch is cut. You are in the per-task loop on its first child, `task/add-the-word-counter`, at step 2, Absorb findings. The `assumption-prover` for that task has returned; its report is `prover-report.md` in this directory. Carry the procedure from step 2 on that report, and stop once you have made and written the call it asks for. If the procedure ends the session there, end it the way the procedure says. You may not dispatch agents or run git, so the build is not here: do not dispatch anything, do not restart the procedure from its entry, and do not cut a branch.

The lore CLI is installed in this directory as `./lore`. Wherever a document or instruction says `lore`, run `./lore` instead.

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
