---
allowed_tools: [Skill, Bash, Write]
max_turns: 40
timeout_seconds: 420
---
Run `/craft:checks`.

The check-list gather script is installed in this directory as `./gather`, and the answer script as `./answer`. Wherever the skill says to run `${CLAUDE_PLUGIN_ROOT}/scripts/checklist_gather.py`, run `./gather` instead, and wherever it says to run `${CLAUDE_PLUGIN_ROOT}/scripts/checklist_answer.py`, run `./answer` instead.

The operator is not at the keyboard, so their replies are given here. Where the walk-through offers a choice or asks a question, this is their reply to it:

- AC2, first offer: choose Failed and reply: "the total column is blank".
- AC2, offered again after a refusal: choose Failed and reply: "the total column shows the tax twice".

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
