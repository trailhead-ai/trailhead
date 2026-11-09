---
allowed_tools: [Skill, Read, Glob, Grep, Agent]
max_turns: 100
timeout_seconds: 900
---
Run `/portage:pull_request create --unattended task/count-words`.

The command-line tools here are local stand-ins in the working directory. Wherever an instruction calls for `portage`, run `./portage`; for `gh`, run `./gh`; for `lore`, run `./lore`; for `git`, run `./git` (use `./git -C <path>`); always in that relative form, from the working directory root. Pass this same note on in the prompt of every agent you dispatch. The camp group is `g`, the worktree slug is `s`, the camp manifest is `manifest.json` and the group TOML is `group.toml`, both in your current working directory (pass their absolute paths).

Make each Bash call a single command: no `;`, `&&`, `echo` or `cd`, and no shell variables.
